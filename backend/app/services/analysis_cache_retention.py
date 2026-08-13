"""Retention adapter for project-scoped Redis and Chroma analysis caches."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


async def purge_project_analysis_caches(
    project_id: str, *, cutoff: datetime, execute: bool
) -> dict[str, int]:
    """Count or delete cache entries older than the raw/artifact cutoff."""
    counts = {"redis": 0, "semantic": 0}
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
        payload: dict[str, Any] = await asyncio.to_thread(
            collection.get, include=["metadatas"]
        )
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
        counts["semantic"] = len(expired)
        if execute and expired:
            await asyncio.to_thread(collection.delete, ids=expired)
    except Exception as exc:
        logger.warning("retention_semantic_cache_purge_failed", error=str(exc))
    return counts
