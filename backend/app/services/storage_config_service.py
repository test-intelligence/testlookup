"""Authoritative runtime resolution for object and vector storage settings."""
from __future__ import annotations

import json
from typing import Any

import structlog
from sqlalchemy import select

from app.core.config import settings

logger = structlog.get_logger("services.storage_config")

_SCOPE = "storage_config"
_CACHE_KEY = "config:storage_effective"
_CACHE_TTL_SECONDS = 60


def _environment_defaults() -> dict[str, Any]:
    return {
        "storage_backend": settings.STORAGE_BACKEND,
        "minio_endpoint": settings.MINIO_ENDPOINT,
        "minio_bucket_name": settings.MINIO_BUCKET_NAME,
        "minio_use_ssl": settings.MINIO_USE_SSL,
        "chroma_host": settings.CHROMA_HOST,
        "chroma_port": settings.CHROMA_PORT,
        "chroma_collection": settings.CHROMA_COLLECTION,
    }


def _validated_config(candidate: dict[str, Any]) -> dict[str, Any]:
    """Reject stale/legacy values that no current settings write can create."""
    from app.models.schemas import StorageConfigUpdate

    validated = StorageConfigUpdate.model_validate(candidate).model_dump(exclude_none=True)
    defaults = _environment_defaults()
    defaults.update(validated)
    return defaults


async def get_effective_storage_config(*, use_cache: bool = True) -> dict[str, Any]:
    """Resolve shared DB overrides with Redis caching and env fallback.

    ``use_cache=False`` skips the Redis read *and* the write-back, resolving
    from Postgres and the environment only. Health probes pass it, and the
    reason is not speed but attribution: the cache calls are wrapped in
    ``except Exception``, which catches a refusal but cannot shorten a
    **hang**. An unreachable Redis blocks for ``socket_connect_timeout``
    (5s), so the MinIO and ChromaDB probes -- which call this purely to learn
    which endpoint to probe -- blew their 2s budget waiting on Redis and were
    reported ``degraded`` while both were perfectly healthy. Measured during a
    Redis outage: minio 2001.6ms, chromadb 2003.4ms, both false. An endpoint
    whose job is naming the dependency that died implicated three.
    """
    if use_cache:
        try:
            from app.db.redis_client import get_redis

            cached = await get_redis().get(_CACHE_KEY)
            if cached:
                return _validated_config(dict(json.loads(cached)))
        except Exception:  # noqa: BLE001 - Redis is an optional cache
            pass

    config = _environment_defaults()
    try:
        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import AppSetting

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(AppSetting).where(AppSetting.key == _SCOPE))
            row = result.scalar_one_or_none()
            if row is not None and row.value:
                config = _validated_config({**config, **dict(row.value)})
    except Exception as exc:  # noqa: BLE001 - env defaults keep storage available
        logger.warning("storage_config_db_load_failed", error=str(exc))

    if use_cache:
        try:
            from app.db.redis_client import get_redis

            await get_redis().setex(_CACHE_KEY, _CACHE_TTL_SECONDS, json.dumps(config))
        except Exception:  # noqa: BLE001 - Redis is an optional cache
            pass
    return config


async def invalidate_storage_config_cache() -> None:
    """Invalidate the shared cache after the authoritative DB commit."""
    try:
        from app.db.redis_client import get_redis

        await get_redis().delete(_CACHE_KEY)
    except Exception:  # noqa: BLE001 - TTL bounds staleness if Redis is degraded
        pass
