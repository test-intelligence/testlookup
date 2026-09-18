"""
Shared Redis caching utility for analytics and dashboard queries.

P3-6: Caches expensive aggregation results in Redis with configurable TTLs.
Cache keys are namespaced and include project_id + parameters to avoid collisions.
All cache operations are non-blocking — failures silently fall through to the DB.

Usage:
    result = await cache_get("dashboard_summary", project_id, days=7)
    if result is None:
        result = await _expensive_query(...)
        await cache_set("dashboard_summary", result, project_id, ttl=60, days=7)
"""
import json
import logging
from typing import Any

logger = logging.getLogger("services.cache")

# TTL presets (seconds) — tuned for each data type's change frequency
CACHE_TTL_DASHBOARD = 60       # In-progress runs change frequently
CACHE_TTL_FLAKY = 300          # Changes slowly (only on new run completion)
CACHE_TTL_COVERAGE = 600       # Changes only when new runs complete
CACHE_TTL_ANALYTICS = 300      # General analytics


def _build_cache_key(namespace: str, project_id: str | None, **kwargs: Any) -> str:
    """Build a deterministic cache key from namespace + params."""
    parts = [f"analytics:{namespace}", project_id or "all"]
    for k, v in sorted(kwargs.items()):
        parts.append(f"{k}={v}")
    return ":".join(parts)


async def cache_get(namespace: str, project_id: str | None, **kwargs: Any) -> Any | None:
    """Fetch a cached result. Returns None on miss or Redis unavailability."""
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        key = _build_cache_key(namespace, project_id, **kwargs)
        raw = await redis.get(key)
        if raw is not None:
            return json.loads(raw)
    except Exception:
        pass
    return None


async def cache_set(
    namespace: str,
    value: Any,
    project_id: str | None,
    ttl: int = CACHE_TTL_ANALYTICS,
    **kwargs: Any,
) -> None:
    """Store a value in the cache. Non-blocking — failures are silently ignored."""
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        key = _build_cache_key(namespace, project_id, **kwargs)
        await redis.set(key, json.dumps(value, default=str), ex=ttl)
    except Exception:
        pass


async def invalidate_analytics_cache(project_id: str | None = None) -> None:
    """Invalidate all analytics cache entries for a project (or all projects).

    Called after a new test run completes to ensure fresh dashboard data.
    Uses SCAN to find matching keys — safe for production Redis.
    """
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        patterns = (
            (f"analytics:*:{project_id}:*", "analytics:*:all:*")
            if project_id
            else ("analytics:*",)
        )
        for pattern in patterns:
            cursor = 0
            while True:
                cursor, keys = await redis.scan(cursor, match=pattern, count=100)
                if keys:
                    await redis.delete(*keys)
                if cursor == 0:
                    break
    except Exception:
        pass
