"""
Tool: Embed error messages and cluster them semantically using ChromaDB + cosine similarity.
Groups test failures by root cause before deep investigation (reduces O(n) → O(k) LLM calls).

Nothing is stored. The messages of one call are embedded into an in-memory
collection that exists for that call only (as ``duplicate_detection_service``
does). They used to be upserted into one ``failure_clusters`` collection on the
Chroma server, shared by every project with no project recorded: the messages
outlived the project they came from, and every clustering call searched all
projects' documents, so one project's uploads could push another project's own
neighbours out of the result window and split its clusters.
"""
import asyncio
import hashlib
import logging
import uuid
from typing import Any, cast

from langchain_core.tools import tool

logger = logging.getLogger("tools.embed_and_cluster")

_COLLECTION = "failure_clusters"

# ChromaDB uses L2 distance; ≤ this ≈ "same root cause" for our error text.
_SIMILARITY_DISTANCE = 0.5
# Per-error nearest-neighbour lookup size. This MUST be large enough that a
# single root cause hitting many tests is returned as one neighbourhood — the
# old cap of 5 meant a 30-test outage could only ever link 4 others per seed and
# so fragmented into ≥6 clusters, defeating the O(n)→O(k) goal and multiplying
# downstream per-cluster deep-investigation LLM cost. Bounded to keep the
# returned distance matrix (O(n·cap)) from blowing up on pathological runs;
# beyond it, clustering is knowingly approximate (logged, not silent).
_MAX_NEIGHBOR_QUERY = 100


def _cluster_from_neighbours(
    ids: list[str],
    result_ids: list[list[str]],
    result_distances: list[list[float]],
    threshold: float = _SIMILARITY_DISTANCE,
) -> list[list[int]]:
    """Greedy star-clustering over a nearest-neighbour result: for each
    unassigned error, absorb every neighbour within ``threshold``.

    Correctness depends on each error's neighbour list being complete enough to
    contain all its similar errors — see ``_MAX_NEIGHBOR_QUERY`` for why the
    lookup size matters. Uses an id→index map for O(1) resolution (the prior
    ``ids.index()`` was O(n) per neighbour). Pure + deterministic."""
    id_to_index = {cid: i for i, cid in enumerate(ids)}
    assigned = [False] * len(ids)
    clusters: list[list[int]] = []
    for i in range(len(ids)):
        if assigned[i]:
            continue
        cluster = [i]
        assigned[i] = True
        for j_id, dist in zip(result_ids[i], result_distances[i]):
            j = id_to_index.get(j_id)
            if j is not None and not assigned[j] and dist <= threshold:
                cluster.append(j)
                assigned[j] = True
        clusters.append(cluster)
    return clusters


def _exact_neighbours(
    ids: list[str], stored_ids: list[str], stored_embeddings: Any, n_results: int,
) -> dict[str, list[list[Any]]]:
    """Each id's ``n_results`` nearest ids in the batch, nearest first, with the
    squared L2 distances a collection query reports, computed exactly.

    The collection's own ``query`` is an approximate (HNSW) search. On a batch
    of near-identical messages it sometimes returned one neighbour short: one
    message was missing from every row, its own included, and ended up in a
    cluster of one. About 3 calls in 100 on a 40-message batch, differently on
    every run. The batch is small (the caller caps the row width), so the
    exact distances cost little and the grouping is the same on every run.
    """
    import numpy as np

    by_id = {stored_id: vector for stored_id, vector in zip(stored_ids, stored_embeddings)}
    missing = [cid for cid in ids if cid not in by_id]
    if missing:
        raise ValueError(f"{len(missing)} message(s) have no stored embedding")
    vectors = np.asarray([by_id[cid] for cid in ids], dtype=np.float32)
    squared = (vectors * vectors).sum(axis=1)
    out_ids: list[list[Any]] = []
    out_distances: list[list[Any]] = []
    step = 256  # rows per block: bounds the distance block at step x len(ids)
    for start in range(0, len(ids), step):
        block = vectors[start:start + step]
        distances = squared[start:start + step, None] + squared[None, :] - 2.0 * (block @ vectors.T)
        np.maximum(distances, 0.0, out=distances)
        for offset in range(len(block)):
            distances[offset, start + offset] = 0.0  # a message is its own nearest neighbour
        order = np.argsort(distances, axis=1, kind="stable")[:, :n_results]
        for offset in range(len(block)):
            row = order[offset]
            out_ids.append([ids[int(k)] for k in row])
            out_distances.append([float(distances[offset, int(k)]) for k in row])
    return {"ids": out_ids, "distances": out_distances}


def _scratch_collection_name() -> str:
    """A name no other call can have: the collection lives for one call."""
    return f"{_COLLECTION}_{uuid.uuid4().hex}"


def _simple_cluster(texts: list[str], threshold: float = 0.75) -> list[list[int]]:
    """
    Greedy single-linkage clustering based on token-overlap similarity.
    Used as a fallback when ChromaDB embedding is unavailable.
    """
    def _jaccard(a: str, b: str) -> float:
        sa, sb = set(a.lower().split()), set(b.lower().split())
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / len(sa | sb)

    clusters: list[list[int]] = []
    assigned = [False] * len(texts)
    for i, text in enumerate(texts):
        if assigned[i]:
            continue
        cluster = [i]
        assigned[i] = True
        for j in range(i + 1, len(texts)):
            if not assigned[j] and _jaccard(text, texts[j]) >= threshold:
                cluster.append(j)
                assigned[j] = True
        clusters.append(cluster)
    return clusters


@tool
async def embed_and_cluster(error_messages_json: str) -> str:
    """
    Embed a JSON array of error messages and group them into semantic failure clusters.

    Input: JSON string with keys:
      - test_ids: list of test case ID strings (parallel array with error_messages)
      - error_messages: list of error message strings

    Returns: JSON string with clusters array, each cluster having:
      - cluster_id, label, member_test_ids, representative_error, size
    """
    import json

    try:
        payload = json.loads(error_messages_json)
        test_ids: list[str] = payload.get("test_ids", [])
        errors: list[str] = payload.get("error_messages", [])
    except (json.JSONDecodeError, AttributeError) as exc:
        return json.dumps({"error": f"Invalid input JSON: {exc}"})

    if not errors:
        return json.dumps({"clusters": []})

    # Try ChromaDB-backed embedding; fall back to Jaccard clustering
    cluster_indices: list[list[int]] = []
    try:
        from app.services import chroma_collections

        # W12: only the ACTIVE generation's embedding function (the local bundled
        # one on the legacy generation), named explicitly like every other
        # collection's. The stored name is not used: nothing is stored.
        target = await chroma_collections.resolve(_COLLECTION)
        # IDs derived from the content hash
        ids = [hashlib.md5(e.encode()).hexdigest()[:16] for e in errors]
        # The lookup size must be large enough not to fragment a big same-cause
        # cluster (see _MAX_NEIGHBOR_QUERY); beyond the cap, log rather than
        # silently truncate.
        n_query = min(len(errors), _MAX_NEIGHBOR_QUERY)
        if len(errors) > _MAX_NEIGHBOR_QUERY:
            logger.info(
                "Clustering neighbour lookup capped at %d of %d errors; clusters "
                "larger than the cap may fragment.",
                _MAX_NEIGHBOR_QUERY, len(errors),
            )

        def _embed_and_query() -> dict:
            import chromadb

            # In memory, in this process, for this call: no server is contacted,
            # so no other project's (or earlier run's) documents can be among
            # the neighbours, and nothing is left to purge.
            client = chromadb.EphemeralClient()
            name = _scratch_collection_name()
            collection = client.create_collection(name, **target.create_kwargs())
            try:
                collection.upsert(ids=ids, documents=errors)
                # Each error's nearest neighbours in the batch, computed exactly
                # from the stored embeddings (see _exact_neighbours for why the
                # collection's approximate query is not used).
                stored = collection.get(ids=ids, include=["embeddings"])
                return _exact_neighbours(ids, stored["ids"], stored["embeddings"], n_query)
            finally:
                # In-memory collections live as long as the process: drop this one.
                try:
                    client.delete_collection(name)
                except Exception:  # noqa: BLE001 - cleanup only; the result stands
                    logger.debug("Could not drop the in-memory clustering collection %s", name)

        results = await asyncio.to_thread(_embed_and_query)
        cluster_indices = _cluster_from_neighbours(
            ids, results["ids"], results["distances"],
        )
    except Exception as exc:
        logger.warning("ChromaDB unavailable, falling back to Jaccard clustering: %s", exc)
        cluster_indices = _simple_cluster(errors)

    clusters = []
    for idx, members in enumerate(cluster_indices):
        rep_error = errors[members[0]][:300]
        # Generate a short label from the first meaningful part of the error
        label = rep_error.split("\n")[0][:80] if rep_error else f"Cluster {idx + 1}"
        clusters.append({
            "cluster_id": f"cl_{idx + 1:03d}",
            "label": label,
            "member_test_ids": [test_ids[m] for m in members if m < len(test_ids)],
            "representative_error": rep_error,
            "size": len(members),
        })

    # Sort by size descending (largest cluster first)
    clusters.sort(key=lambda c: cast(int, c["size"]), reverse=True)
    return json.dumps({"clusters": clusters})
