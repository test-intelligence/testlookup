"""
Tool: Embed error messages and cluster them semantically using ChromaDB + cosine similarity.
Groups test failures by root cause before deep investigation (reduces O(n) → O(k) LLM calls).
"""
import asyncio
import hashlib
import logging
from typing import Any, cast

from langchain_core.tools import tool

from app.db.chroma import get_chroma_client

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


def _get_chroma_client() -> Any:
    return get_chroma_client()


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
        client = await asyncio.to_thread(_get_chroma_client)
        collection = await asyncio.to_thread(
            client.get_or_create_collection, _COLLECTION
        )
        # Store with unique IDs derived from content hash
        ids = [hashlib.md5(e.encode()).hexdigest()[:16] for e in errors]
        await asyncio.to_thread(
            collection.upsert, ids=ids, documents=errors
        )
        # Query each error against the collection to find nearest neighbours.
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
        results = await asyncio.to_thread(
            collection.query,
            query_texts=errors,
            n_results=n_query,
            include=["distances"],
        )
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
