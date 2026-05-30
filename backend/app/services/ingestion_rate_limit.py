"""Per-project token-bucket rate limit for live-stream ingestion.

The /api/v1/stream/ingest and /api/v1/stream/events/batch endpoints
accept arbitrary write volume. Without a gate, a single misbehaving
project (CI loop firing 10K events/sec, a runaway SDK) can saturate
the Redis stream consumer + per-run buffers + Celery worker pool, and
the resulting Redis OOM takes down the dashboard alongside ingestion.

The limiter is *per project*, intentionally. We're not trying to slow
down a particular user — we're isolating projects so one team's noise
doesn't degrade another team's UX. JWT-authenticated users still see
the global rate limit on auth endpoints (P5-8); this is additional and
specific to live-stream writes.

Design notes
------------
* **Per-minute fixed bucket** (INCR + EXPIRE). Cheaper than a true
  sliding-window or leaky-bucket implementation, and the granularity
  (1 minute) is the right magnitude — bursts within a minute are
  allowed up to the budget, and the budget refreshes every minute.
* The limit is on **batches**, not events. SDK batches typically carry
  ~100 events; at the default 200 batches/min/project that's about
  20K events/min/project. Configurable via env var.
* When the limit is exceeded, we raise ``HTTPException(429)`` with a
  ``Retry-After`` header so the SDK can back off precisely instead of
  guessing.
* The bucket lives in Redis so multiple FastAPI workers see the same
  count — a single project can't cheat by being routed to different
  pods.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import structlog
from fastapi import HTTPException, status

from app.core.config import settings

logger = structlog.get_logger(__name__)

# Configurable via ``settings.INGEST_RATE_LIMIT_PER_MINUTE`` — see
# ``app/core/config.py``. The keys land under the ``testlookup:rate:``
# namespace so a single ``KEYS testlookup:rate:*`` reveals every active
# bucket during an incident.
_KEY_PREFIX = "testlookup:rate:ingest:{project_id}:{minute_bucket}"

# Reject-counter key for /api/v1/health/ingestion to surface the recent
# reject rate. Same minute granularity as the bucket itself so the two
# numbers compose cleanly into a "tried N, rejected M" story.
_REJECT_KEY_PREFIX = "testlookup:rate:reject:{minute_bucket}"


def _current_minute_bucket() -> str:
    """ISO minute string used as the bucket id. UTC + minute resolution
    is the contract; the ``Retry-After`` math below depends on it."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")


def _seconds_until_next_minute() -> int:
    """Whole-second countdown to the start of the next minute. Used as
    the ``Retry-After`` value so the SDK retries exactly when the
    bucket refreshes — no thundering-herd at the second boundary, no
    needless extra delay."""
    now = time.time()
    return max(1, 60 - int(now) % 60)


async def enforce_ingest_rate_limit(
    project_id: str,
    *,
    cost: int = 1,
) -> None:
    """Charge ``cost`` tokens against the project's bucket. Raise 429
    when the bucket overflows.

    Parameters
    ----------
    project_id:
        Stringified UUID of the project being charged. UUID validation
        is the caller's responsibility — we don't want to repeat a
        ``uuid.UUID(...)`` parse in the hot path.
    cost:
        How many tokens this call consumes. Defaults to 1 (one batch =
        one token); reserved for future use if we want to charge by
        event count.

    Raises
    ------
    HTTPException(429):
        When the project's bucket exceeds the configured budget. The
        ``Retry-After`` header is set to the number of seconds until
        the bucket refreshes.
    """
    limit = settings.INGEST_RATE_LIMIT_PER_MINUTE
    if limit <= 0:
        # Limit disabled — typically only in tests or dev.
        return

    from app.db.redis_client import get_redis

    redis = get_redis()
    bucket = _current_minute_bucket()
    key = _KEY_PREFIX.format(project_id=project_id, minute_bucket=bucket)

    # ``INCRBY key cost`` returns the new count atomically. The
    # ``EXPIRE`` is set only on the first write of the minute (when
    # count == cost) to avoid resetting the TTL each call — the bucket
    # naturally expires after 70s, well past the next minute boundary.
    try:
        count = await redis.incrby(key, cost)
        if count == cost:
            await redis.expire(key, 70)
    except Exception as exc:
        # Redis is degraded — fail OPEN. We'd rather ingest than block
        # the user when our rate-limiter backend is itself down. The
        # adaptive backpressure path catches the worse failure modes.
        logger.warning(
            "rate_limit_redis_failed_fail_open",
            project_id=project_id,
            error=str(exc),
        )
        return

    if count <= limit:
        return

    # Over budget. Bump the global reject counter so /health/ingestion
    # surfaces the rate. Best-effort — never let a stats write fail
    # the user's request path (which is already failing for a different
    # reason).
    try:
        reject_key = _REJECT_KEY_PREFIX.format(minute_bucket=bucket)
        await redis.incr(reject_key)
        await redis.expire(reject_key, 70)
    except Exception:  # pragma: no cover — stats only
        pass

    retry_after = _seconds_until_next_minute()
    logger.info(
        "ingest_rate_limit_exceeded",
        project_id=project_id,
        count=count,
        limit=limit,
        retry_after=retry_after,
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=(
            f"Ingest rate limit exceeded for this project "
            f"({count}/{limit} batches in the current minute). "
            f"Retry in {retry_after}s."
        ),
        headers={"Retry-After": str(retry_after)},
    )
