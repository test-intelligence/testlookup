"""Adaptive backpressure for the live-stream ingestion endpoints.

The per-project rate limit in ``ingestion_rate_limit.py`` isolates
one noisy project from the rest, but if *every* project is operating
within budget, aggregate load can still push Redis past its memory
ceiling. This module is the second gate: a system-wide kill switch
that returns ``503 Service Unavailable`` to NEW ingest requests once
Redis is too full to safely accept more.

Behaviour
---------
* When Redis ``used_memory`` exceeds ``INGEST_REDIS_MEMORY_THRESHOLD_PCT``
  of ``maxmemory`` (default 75%), reject with ``503 + Retry-After: 5``.
* The check is **cached for 5 seconds** so the path stays O(1) per
  HTTP request even under a burst. Redis ``INFO memory`` is cheap but
  not free, and calling it on every request multiplies the load we're
  trying to manage.
* When ``maxmemory`` is 0 (not configured), the percentage check is
  meaningless. We fall back to an absolute byte ceiling
  (``INGEST_REDIS_MEMORY_ABSOLUTE_BYTES``) so the gate still has
  teeth on a dev machine where Redis has no configured cap.
* Failures to read ``INFO`` fail OPEN — same principle as the rate
  limiter. We'd rather over-accept under degradation than over-reject.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import structlog
from fastapi import HTTPException, status

from app.core.config import settings

logger = structlog.get_logger(__name__)

# Module-level cache. The pattern is intentionally simple — we don't
# need a per-key cache or LRU; there's exactly one value worth
# remembering and it's "what was Redis using last time we checked".
@dataclass
class _MemorySnapshot:
    used_bytes: int
    max_bytes: int  # 0 when ``maxmemory`` is unset
    captured_at: float  # monotonic seconds

    @property
    def used_pct(self) -> float:
        if self.max_bytes <= 0:
            return 0.0
        return (self.used_bytes / self.max_bytes) * 100.0


_CACHE: Optional[_MemorySnapshot] = None
_CACHE_TTL_SECONDS = 5.0


async def _read_memory_snapshot() -> Optional[_MemorySnapshot]:
    """Read fresh ``INFO memory`` from Redis. Returns ``None`` when the
    call fails — callers must treat that as fail-OPEN."""
    from app.db.redis_client import get_redis

    redis = get_redis()
    try:
        info = await redis.info(section="memory")
    except Exception as exc:
        logger.warning("backpressure_redis_info_failed_fail_open", error=str(exc))
        return None

    used_bytes = int(info.get("used_memory") or 0)
    # ``maxmemory`` is 0 when not configured (Redis defaults). Tracking
    # this explicitly lets the threshold logic switch to an absolute
    # byte ceiling instead of a meaningless percentage.
    max_bytes = int(info.get("maxmemory") or 0)
    return _MemorySnapshot(
        used_bytes=used_bytes,
        max_bytes=max_bytes,
        captured_at=time.monotonic(),
    )


async def get_redis_memory_snapshot(*, force_refresh: bool = False) -> Optional[_MemorySnapshot]:
    """Return the cached memory snapshot, refreshing it if expired.

    Public for use by ``/health/ingestion`` — the endpoint surfaces the
    same number the gate uses, so the user sees what the system saw at
    decision time."""
    global _CACHE
    now = time.monotonic()
    if not force_refresh and _CACHE is not None and (now - _CACHE.captured_at) < _CACHE_TTL_SECONDS:
        return _CACHE
    snapshot = await _read_memory_snapshot()
    if snapshot is not None:
        _CACHE = snapshot
        return snapshot
    if force_refresh:
        # The caller explicitly asked for a *fresh* reading and there isn't
        # one. Returning ``_CACHE`` here handed ``/health/ingestion`` a
        # minutes-old number with no indication of its age, so a dashboard
        # showing 41% memory could be describing a Redis that has since become
        # unreachable. The admission gate (which calls without force_refresh)
        # still gets the cache, because there a slightly stale number is far
        # better than failing open.
        return None
    return _CACHE


async def enforce_redis_memory_backpressure() -> None:
    """Reject the request with 503 when Redis is over the memory
    threshold. No-op when threshold is 0 (disabled) or Redis is
    unreachable (fail OPEN — see module docstring)."""
    threshold_pct = settings.INGEST_REDIS_MEMORY_THRESHOLD_PCT
    threshold_abs = settings.INGEST_REDIS_MEMORY_ABSOLUTE_BYTES

    if threshold_pct <= 0 and threshold_abs <= 0:
        return  # disabled

    snapshot = await get_redis_memory_snapshot()
    if snapshot is None:
        return  # Redis unreachable → fail OPEN

    over_pct = (
        threshold_pct > 0
        and snapshot.max_bytes > 0
        and snapshot.used_pct >= threshold_pct
    )
    over_abs = (
        threshold_abs > 0
        and snapshot.used_bytes >= threshold_abs
    )
    if not over_pct and not over_abs:
        return

    # The reject reason is encoded into the detail so support can pin
    # which gate fired without diff'ing logs.
    reason = "memory_pct" if over_pct else "memory_absolute"
    logger.warning(
        "ingest_backpressure_triggered",
        reason=reason,
        used_bytes=snapshot.used_bytes,
        max_bytes=snapshot.max_bytes,
        used_pct=round(snapshot.used_pct, 1),
        threshold_pct=threshold_pct,
        threshold_abs=threshold_abs,
    )

    # Bump the reject counter so /health/ingestion surfaces the rate.
    try:
        from app.db.redis_client import get_redis
        from datetime import datetime, timezone

        bucket = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
        redis = get_redis()
        await redis.incr(f"testlookup:rate:reject:{bucket}")
        await redis.expire(f"testlookup:rate:reject:{bucket}", 70)
    except Exception:  # pragma: no cover — stats only
        pass

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "TestLookup ingestion is shedding load to protect the live-stream "
            f"buffer (reason={reason}). Retry in 5 seconds."
        ),
        headers={"Retry-After": "5"},
    )
