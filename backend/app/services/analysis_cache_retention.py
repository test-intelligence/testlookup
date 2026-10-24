"""Retention adapter for project-scoped Redis and Chroma analysis caches."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _expired_ids(payload: dict[str, Any], cutoff: datetime) -> list[str]:
    expired: list[str] = []
    normalized_cutoff = cutoff
    if normalized_cutoff.tzinfo is None:
        normalized_cutoff = normalized_cutoff.replace(tzinfo=UTC)
    else:
        normalized_cutoff = normalized_cutoff.astimezone(UTC)
    for item_id, metadata in zip(
        payload.get("ids") or [], payload.get("metadatas") or []
    ):
        raw = (metadata or {}).get("cached_at")
        try:
            cached_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            cached_at = datetime.min.replace(tzinfo=UTC)
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=UTC)
        else:
            cached_at = cached_at.astimezone(UTC)
        if cached_at < normalized_cutoff:
            expired.append(str(item_id))
    return expired


async def _purge_collection(collection: Any, *, cutoff: datetime, execute: bool) -> int:
    payload: dict[str, Any] = await asyncio.to_thread(
        collection.get, include=["metadatas"]
    )
    expired = _expired_ids(payload, cutoff)
    if execute and expired:
        await asyncio.to_thread(collection.delete, ids=expired)
    return len(expired)


async def _purge_inactive_generations(project_id: str, *, cutoff: datetime, execute: bool) -> None:
    """W12 SRCH-02: the same purge on a building or retiring generation's cache
    collection, when one exists. Best effort; the count is the active cache's."""
    if not execute:
        return
    try:
        from app.services import chroma_collections
        from app.services.semantic_cache import _get_existing_collection

        generations = await chroma_collections.current_generations()
        for generation in generations.visit_order():
            if generation == generations.active:
                continue
            collection = await _get_existing_collection(project_id, generation)
            if collection is not None:
                await _purge_collection(collection, cutoff=cutoff, execute=True)
    except Exception as exc:  # cache outage must not block durable-store purge
        logger.warning("retention_semantic_cache_generation_purge_failed", error_type=type(exc).__name__)


async def purge_project_analysis_caches(
    project_id: str, *, cutoff: datetime, execute: bool
) -> dict[str, int | None]:
    """Count or delete cache entries older than the raw/artifact cutoff.

    A sub-store that could not be reached reports **None**, not 0 — an
    outage and an empty cache are opposite findings, and the caller renders
    this number. Starting at None means a failure before the assignment
    leaves None rather than a zero nobody wrote on purpose.

    W12: the ACTIVE generation's cache collection is counted (as before); a
    building or retiring generation's is purged the same way when it exists.
    """
    counts: dict[str, int | None] = {"redis": None, "semantic": None}
    try:
        from app.db.redis_client import get_redis

        redis = get_redis()
        keys = [key async for key in redis.scan_iter(match=f"*:proj:{project_id}")]
        counts["redis"] = len(keys)
        if execute and keys:
            await redis.delete(*keys)
    except Exception as exc:  # cache outage must not block durable-store purge
        logger.warning("retention_redis_cache_purge_failed", error=str(exc))

    try:
        from app.services.semantic_cache import _get_or_create_collection

        collection = await _get_or_create_collection(project_id)
        counts["semantic"] = await _purge_collection(collection, cutoff=cutoff, execute=execute)
    except Exception as exc:
        logger.warning("retention_semantic_cache_purge_failed", error=str(exc))
    await _purge_inactive_generations(project_id, cutoff=cutoff, execute=execute)
    return counts
