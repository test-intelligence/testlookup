"""
Semantic similarity cache for AI analysis results via ChromaDB.

Extends the P1 exact-match Redis cache with a vector-similarity fallback:
  1. Exact match (Redis hash) — checked first in agent.py
  2. Semantic match (ChromaDB) — checked here when Redis misses

If a new failure's error signature is semantically close to a previously
cached analysis, the cached result is returned instead of re-running the LLM,
saving compute time and token costs.

The cache collection stores the error signature as the document and the
full analysis JSON as metadata. On query, if the nearest neighbor's distance
is below the similarity threshold, the cached analysis is returned.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, cast

from app.core.config import settings

logger = logging.getLogger("services.semantic_cache")

_COLLECTION_NAME = "ai_analysis_cache"

# Above this similarity a reused narrative is treated as describing the same
# failure; below it the verdict is still returned, but flagged for review.
NEAR_EXACT_REUSE_SIMILARITY = 0.98


async def _get_chroma_client():
    from app.db.chroma import get_configured_chroma_client
    return await get_configured_chroma_client()


async def _get_or_create_collection(project_id: Optional[str] = None):
    # Per-project collection so a cached analysis (which embeds project-specific
    # Splunk/OCP evidence on the slow path) can't be matched/served across
    # tenants. A single global collection would leak one project's evidence to
    # another on a semantically-similar failure.
    #
    # It used to fall back to the shared, unscoped collection when there was no
    # project (re-audit M15, same class as the analysis cache). The triage
    # agent guards its lookup but not its store, so unscoped analyses were
    # written into one collection shared by every tenant -- nothing reads it
    # today, which makes it a leak waiting for its first unguarded reader.
    # With no project there is no safe collection, so there is none at all.
    if not project_id:
        raise ValueError("semantic cache requires a project scope")
    name = f"{_COLLECTION_NAME}_{project_id}"
    client = await _get_chroma_client()
    return await asyncio.to_thread(
        client.get_or_create_collection,
        name,
        metadata={"hnsw:space": "cosine"},
    )


async def purge_all_m11_semantic_cache_collections() -> int:
    """Delete derived AI cache collections that may contain pre-M11 evidence."""
    client = await _get_chroma_client()
    collections = await asyncio.to_thread(client.list_collections)
    names = [
        collection.name if hasattr(collection, "name") else str(collection)
        for collection in collections
    ]
    targets = [
        name
        for name in names
        if name == _COLLECTION_NAME or name.startswith(f"{_COLLECTION_NAME}_")
    ]
    for name in targets:
        await asyncio.to_thread(client.delete_collection, name=name)
    return len(targets)


def _build_signature(test_name: str, error_message: str, stack_trace: str) -> str:
    """Build a searchable text signature from the failure inputs."""
    parts = [test_name.strip()]
    if error_message:
        parts.append(error_message.strip()[:500])
    if stack_trace:
        # Extract the most distinctive part of the stack trace (first 3 lines + error)
        trace_lines = stack_trace.strip().splitlines()
        parts.append(" ".join(line.strip() for line in trace_lines[:5])[:500])
    return " | ".join(parts)


async def semantic_cache_lookup(
    test_name: str,
    error_message: str,
    stack_trace: str,
    project_id: Optional[str] = None,
) -> Optional[dict]:
    """
    Query ChromaDB for a semantically similar cached analysis.

    Returns the cached analysis dict if a match is found above the
    similarity threshold, or None on miss / error. ``project_id`` scopes the
    collection so matches never cross tenants.
    """
    if not error_message and not stack_trace:
        return None
    if not project_id:
        # No tenant scope, no cache (re-audit M15).
        return None

    try:
        collection = await _get_or_create_collection(project_id)
        signature = _build_signature(test_name, error_message, stack_trace)

        results = await asyncio.to_thread(
            collection.query,
            query_texts=[signature],
            n_results=1,
            include=["documents", "distances", "metadatas"],
        )

        ids_list = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]

        if not ids_list or not distances:
            return None

        # ChromaDB cosine distance: 0 = identical, 2 = opposite
        # Convert to similarity: 1 - (distance / 2)
        distance = distances[0]
        similarity = 1.0 - (distance / 2.0)

        if similarity < settings.SEMANTIC_SIMILARITY_THRESHOLD:
            logger.debug(
                "Semantic cache miss for '%s' (similarity=%.3f < threshold=%.3f)",
                test_name[:40], similarity, settings.SEMANTIC_SIMILARITY_THRESHOLD,
            )
            return None

        # Extract cached analysis from metadata
        meta = metadatas[0]
        cached_json = meta.get("analysis_json")
        if not cached_json:
            return None

        analysis = cast(dict[str, Any], json.loads(cached_json))
        analysis["semantic_cache_hit"] = True
        analysis["semantic_similarity"] = round(similarity, 4)

        # F-12: this narrative was written about a DIFFERENT failure. Reusing it
        # is the whole point of the cache; asserting it at the source's
        # confidence is not. At the 0.85 threshold we accept up to 15%
        # dissimilarity at zero confidence cost, and the caller clears
        # ``evidence_references`` -- so a borrowed root cause arrives with full
        # confidence and nothing cited that could contradict it. That is the
        # exact shape of a confident wrong answer.
        #
        # Three things make reuse honest instead of invisible: scale confidence
        # by how far the match actually is, name the test the prose came from so
        # it is traceable, and hand anything short of a near-exact match to a
        # human. Note what this does NOT do -- it does not stop reuse, because
        # whether a 0.85 neighbour's narrative should be reused at all is a
        # product call, not a bug fix.
        analysis["semantic_source_test"] = str(meta.get("test_name") or "") or None
        raw_confidence = analysis.get("confidence_score")
        if isinstance(raw_confidence, (int, float)) and not isinstance(raw_confidence, bool):
            analysis["confidence_score_before_reuse"] = raw_confidence
            analysis["confidence_score"] = int(round(float(raw_confidence) * similarity))
        if similarity < NEAR_EXACT_REUSE_SIMILARITY:
            analysis["requires_human_review"] = True
        logger.info(
            "Semantic cache hit for '%s' (similarity=%.3f, cached_id=%s)",
            test_name[:40], similarity, ids_list[0],
        )
        return analysis

    except Exception as exc:
        logger.debug("Semantic cache lookup failed (non-critical): %s", exc)
        return None


async def semantic_cache_store(
    test_name: str,
    error_message: str,
    stack_trace: str,
    analysis: dict,
    project_id: Optional[str] = None,
) -> None:
    """
    Store an analysis result in the ChromaDB semantic cache.

    The error signature is embedded as the document, and the full
    analysis JSON is stored in metadata for retrieval on cache hit.
    ``project_id`` scopes the collection to the tenant.
    """
    if not error_message and not stack_trace:
        return
    if not project_id:
        # No tenant scope, no cache (re-audit M15). The triage agent called
        # this unguarded, so this is where unscoped analyses were being stored.
        return

    try:
        collection = await _get_or_create_collection(project_id)
        from app.services.evidence_sanitizer import (
            sanitize_persistence_payload,
            sanitize_reference_text,
        )
        from app.services.ingestion_sanitization import sanitize_test_result_payload

        safe_failure = sanitize_test_result_payload({
            "error_message": error_message,
            "stack_trace": stack_trace,
        })
        signature, _, _ = sanitize_reference_text(
            _build_signature(
                test_name,
                safe_failure["error_message"] or "",
                safe_failure["stack_trace"] or "",
            ),
            limit=6000,
        )

        # Build a unique ID from the signature hash
        import hashlib
        doc_id = hashlib.sha256(signature.encode()).hexdigest()[:32]

        # Strip transient fields before caching
        cacheable = {
            k: v for k, v in analysis.items()
            if k not in (
                "cache_hit", "semantic_cache_hit", "semantic_similarity",
                "evidence_references",
            )
        }
        cacheable, stats = sanitize_persistence_payload(cacheable)
        if stats.omitted_items or stats.truncated_strings:
            return

        await asyncio.to_thread(
            collection.upsert,
            ids=[doc_id],
            documents=[signature],
            metadatas=[{
                "analysis_json": json.dumps(cacheable, default=str),
                "test_name": test_name[:200],
                "failure_category": analysis.get("failure_category", "UNKNOWN"),
                "confidence_score": str(analysis.get("confidence_score", 0)),
                "cached_at": datetime.now(timezone.utc).isoformat(),
            }],
        )
        logger.debug("Stored analysis in semantic cache: id=%s test=%s", doc_id, test_name[:40])
        await _enforce_size_cap(collection, project_id)

    except Exception as exc:
        logger.debug("Semantic cache store failed (non-critical): %s", exc)


# Prune to this fraction of the cap when it is exceeded, so eviction runs
# occasionally in batches instead of on every single store once at the ceiling.
_PRUNE_TO_FRACTION = 0.9


async def _enforce_size_cap(collection, project_id: Optional[str] = None) -> None:
    """Evict the oldest entries when the collection exceeds its configured cap.

    ``SEMANTIC_CACHE_MAX_DOCUMENTS`` documented itself as "cap ChromaDB
    collection size" and was never read by anything, so the per-tenant cache
    grew without bound for the life of the deployment. Eviction is by
    ``cached_at`` — the metadata the store path already writes.

    Best-effort: the caller treats cache failures as non-critical, and an
    eviction problem must never fail the analysis that triggered it.
    """
    cap = int(getattr(settings, "SEMANTIC_CACHE_MAX_DOCUMENTS", 0) or 0)
    if cap <= 0:
        return  # 0/absent = uncapped, an explicit operator choice
    try:
        count = await asyncio.to_thread(collection.count)
        if count <= cap:
            return

        target = max(1, int(cap * _PRUNE_TO_FRACTION))
        overflow = count - target
        existing = await asyncio.to_thread(collection.get, include=["metadatas"])
        ids = existing.get("ids") or []
        metas = existing.get("metadatas") or []
        if not ids:
            return
        # Missing/unparseable cached_at sorts oldest, so malformed rows are
        # evicted first rather than becoming immortal.
        paired = [(m.get("cached_at") or "" if isinstance(m, dict) else "", i)
                  for i, m in zip(ids, metas)]
        paired.sort(key=lambda pair: pair[0])
        doomed = [i for _, i in paired[:overflow]]
        if doomed:
            await asyncio.to_thread(collection.delete, ids=doomed)
            logger.info(
                "semantic_cache_evicted",
                extra={"evicted": len(doomed), "count_before": count,
                       "cap": cap, "project_id": project_id},
            )
    except Exception as exc:  # noqa: BLE001 — never fail the caller
        logger.debug("Semantic cache size-cap enforcement failed (non-critical): %s", exc)


async def semantic_cache_invalidate(
    test_name: str,
    error_message: str,
    stack_trace: str,
    project_id: Optional[str] = None,
) -> None:
    """Evict the cached analysis for a specific error signature.

    Called when a human corrects an AI classification (feedback_service) so the
    now-known-wrong verdict isn't re-served from the semantic cache to this or a
    similar test. Rebuilds the same ``doc_id`` ``semantic_cache_store`` used and
    deletes it from the tenant's collection. Best-effort — never raises."""
    if not error_message and not stack_trace:
        return
    if not project_id:
        # Nothing scoped was ever stored without a project (re-audit M15).
        return
    try:
        collection = await _get_or_create_collection(project_id)
        signature = _build_signature(test_name or "", error_message or "", stack_trace or "")
        import hashlib
        doc_id = hashlib.sha256(signature.encode()).hexdigest()[:32]
        await asyncio.to_thread(collection.delete, ids=[doc_id])
        logger.debug("Invalidated semantic cache entry id=%s test=%s", doc_id, (test_name or "")[:40])
    except Exception as exc:
        logger.debug("Semantic cache invalidate failed (non-critical): %s", exc)


async def get_semantic_cache_stats() -> dict:
    """Return semantic cache health metrics across the per-project collections.

    This used to open the single shared collection and count it. Since re-audit
    M15 there is no shared collection to open -- the cache is per project, and
    the helper refuses a missing project -- so the old version reported
    "unavailable" for a perfectly healthy store. It asks the client instead:
    reachability, how many project collections exist, and their documents.

    It also reports what is left in the LEGACY shared collection. Unscoped
    analyses were written there before M15 and nothing reads it any more, but
    those entries may belong to any tenant, so a non-zero count is a prompt to
    purge it rather than a number to ignore.
    """
    try:
        client = await _get_chroma_client()
        await asyncio.to_thread(client.heartbeat)
        collections = await asyncio.to_thread(client.list_collections)

        prefix = f"{_COLLECTION_NAME}_"
        project_count = 0
        document_count = 0
        legacy_documents = 0
        for collection in collections:
            name = collection if isinstance(collection, str) else collection.name
            if name == _COLLECTION_NAME:
                handle = (
                    collection
                    if not isinstance(collection, str)
                    else await asyncio.to_thread(client.get_collection, name)
                )
                legacy_documents = await asyncio.to_thread(handle.count)
            elif name.startswith(prefix):
                handle = (
                    collection
                    if not isinstance(collection, str)
                    else await asyncio.to_thread(client.get_collection, name)
                )
                project_count += 1
                document_count += await asyncio.to_thread(handle.count)

        return {
            "status": "healthy",
            "collection": _COLLECTION_NAME,
            "project_collections": project_count,
            "document_count": document_count,
            "legacy_unscoped_documents": legacy_documents,
            "similarity_threshold": settings.SEMANTIC_SIMILARITY_THRESHOLD,
        }
    except Exception as exc:
        # None, not 0: an outage and an empty cache are opposite findings, and
        # a zero here reads as "nothing cached" rather than "could not look".
        return {
            "status": "unavailable",
            "error": str(exc),
            "collection": _COLLECTION_NAME,
            "project_collections": None,
            "document_count": None,
            "legacy_unscoped_documents": None,
        }
