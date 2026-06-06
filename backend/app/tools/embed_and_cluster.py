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
        # Query each error against the collection to find nearest neighbours
        results = await asyncio.to_thread(
            collection.query,
            query_texts=errors,
            n_results=min(len(errors), 5),
            include=["distances"],
        )
        # Build adjacency from distance threshold (ChromaDB uses L2; ≤0.5 ≈ similar)
        assigned = [False] * len(errors)
        for i in range(len(errors)):
            if assigned[i]:
                continue
            cluster = [i]
            assigned[i] = True
            distances = results["distances"][i]
            idxs = results["ids"][i]
            for j_id, dist in zip(idxs, distances):
                if j_id in ids:
                    j = ids.index(j_id)
                    if not assigned[j] and dist <= 0.5:
                        cluster.append(j)
                        assigned[j] = True
            cluster_indices.append(cluster)
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
