"""Auto-detection of high-volume projects (Phase 4.1).

A project is flagged ``high_volume`` when it sustains ≥ 1000 test
events per minute for ≥ 3 consecutive minutes. The flag drives the
Phase 4.2 sampler, which stores only 1-of-2 ``test_cases`` rows for
flagged projects (aggregates remain accurate because they're sourced
from the ``HINCRBY`` counters in the live-state hash, not from
per-test rows).

Why auto-detection and not an explicit operator setting?
---------------------------------------------------------

* Most projects never trip the threshold, so the flag is invisible
  to them. Auto means no manual provisioning.
* Volume bursts are spiky — a CI matrix run can push 5K events/min
  for 5 minutes and then go quiet. A static "this project is heavy"
  flag would over-sample on the heavy project's quiet days.
* Operator override is still available via the Settings → Project
  Data page (forces ``high_volume=true|false`` regardless of detector).

Implementation
--------------

Per-minute counters live in Redis under
``testlookup:high_volume:tests_per_minute:<project_id>:<yyyymmddTHHMM>``
with a 10-minute TTL — the detector only needs the last 3 minutes,
but a longer TTL is cheap and gives the future Grafana panel some
backfill.

The flag itself lives under
``testlookup:high_volume:flagged:<project_id>`` with a 10-minute
TTL. Each detector check refreshes the TTL when the project is
still over budget; when it drops, the flag auto-expires and the
project goes back to full-fidelity persistence on the next run.

A read-side helper (``is_high_volume``) lets the persister consult
the flag without re-running the detection logic. Falls back to
``False`` on Redis errors (fail OPEN — fidelity over throughput).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

_COUNTER_KEY = "testlookup:high_volume:tests_per_minute:{project_id}:{bucket}"
_FLAG_KEY = "testlookup:high_volume:flagged:{project_id}"
_OVERRIDE_KEY = "testlookup:high_volume:override:{project_id}"

# Minute bucket TTL — 10 min is plenty for a 3-min lookback. The
# extra headroom means even if a beat tick is delayed, we still
# have fresh data to decide.
_COUNTER_TTL_SECONDS = 600
_FLAG_TTL_SECONDS = 600


def _bucket(when: Optional[datetime] = None) -> str:
    """ISO minute string used as the bucket id. UTC + minute
    resolution matches the rate-limit bucket convention from Phase 1
    so the two systems' Redis keys stay easy to correlate."""
    when = when or datetime.now(timezone.utc)
    return when.strftime("%Y%m%dT%H%M")


def _previous_bucket(minutes_ago: int) -> str:
    from datetime import timedelta
    return _bucket(datetime.now(timezone.utc) - timedelta(minutes=minutes_ago))


async def record_test_events(project_id: str, count: int) -> None:
    """Bump the current-minute counter for ``project_id`` by ``count``.

    Called from the ingest hot path. ``count`` is the number of
    ``test_result`` events in the batch — non-test_result events
    (heartbeats, run_start, etc.) don't count toward the throughput
    threshold.

    Best-effort: a Redis failure is logged but never propagates,
    because the detector is observability + optimisation — a missed
    bump just means a slower flag flip, not data loss.
    """
    if not settings.HIGH_VOLUME_AUTO_DETECT_ENABLED or count <= 0:
        return
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        key = _COUNTER_KEY.format(project_id=project_id, bucket=_bucket())
        # ``INCRBY`` returns the new count atomically. Set TTL only on
        # the first bump of the minute to avoid resetting it each call.
        new_count = await redis.incrby(key, count)
        if new_count == count:
            await redis.expire(key, _COUNTER_TTL_SECONDS)
        # Also check if we've crossed the flag threshold. Cheap when
        # below threshold (single GET); only does the multi-bucket
        # check when the current minute is itself over.
        if new_count >= settings.HIGH_VOLUME_TESTS_PER_MINUTE:
            await _maybe_flip_flag(redis, project_id)
    except Exception as exc:
        logger.warning(
            "high_volume_counter_failed",
            project_id=project_id,
            error=str(exc),
        )


async def _maybe_flip_flag(redis, project_id: str) -> None:
    """Inspect the last N consecutive minutes and set the flag if all
    of them are over threshold. N defaults to 3 from settings."""
    consecutive = settings.HIGH_VOLUME_CONSECUTIVE_MINUTES
    threshold = settings.HIGH_VOLUME_TESTS_PER_MINUTE
    # Check the previous N complete minutes (skip the in-flight one
    # at index 0 — we want SUSTAINED throughput, not a 1-min spike).
    over_threshold = True
    for i in range(1, consecutive + 1):
        bucket = _previous_bucket(i)
        key = _COUNTER_KEY.format(project_id=project_id, bucket=bucket)
        raw = await redis.get(key)
        count = int(raw) if raw else 0
        if count < threshold:
            over_threshold = False
            break
    if not over_threshold:
        return

    flag_key = _FLAG_KEY.format(project_id=project_id)
    # Use SETEX so the TTL is always refreshed — a project that
    # remains over threshold keeps the flag fresh; one that drops
    # off the flag expires after ``_FLAG_TTL_SECONDS``.
    await redis.setex(flag_key, _FLAG_TTL_SECONDS, "1")
    logger.info(
        "high_volume_flagged",
        project_id=project_id,
        threshold=threshold,
        consecutive_minutes=consecutive,
    )


async def is_high_volume(project_id: str) -> bool:
    """Read-side check used by the sampler. Returns True when the
    project is currently flagged. Respects operator overrides (set
    in Settings → Project Data) before falling back to the detector.

    Override key takes precedence so an operator can force-disable
    sampling for a project that the detector thinks is high-volume,
    or force-enable it for a project they know will spike soon.

    Fails safe (returns False) on Redis errors — the sampler should
    keep full fidelity when the detector is degraded.
    """
    if not settings.HIGH_VOLUME_AUTO_DETECT_ENABLED:
        return False
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        # Operator override wins. ``"on"`` forces sampling, ``"off"``
        # forces no sampling, anything else / missing falls through.
        override = await redis.get(_OVERRIDE_KEY.format(project_id=project_id))
        if override is not None:
            decoded = override.decode() if isinstance(override, bytes) else override
            if decoded == "on":
                return True
            if decoded == "off":
                return False
        flag = await redis.get(_FLAG_KEY.format(project_id=project_id))
        return flag is not None
    except Exception as exc:
        logger.warning(
            "high_volume_check_failed_fail_open",
            project_id=project_id,
            error=str(exc),
        )
        return False


async def set_operator_override(project_id: str, *, force_on: Optional[bool]) -> None:
    """Set or clear the operator override for ``project_id``.

    * ``force_on=True`` — force-flag the project as high-volume.
    * ``force_on=False`` — force-disable sampling.
    * ``force_on=None`` — clear the override; detector takes over.

    The override TTL matches the auto-flag (10 min) so operators don't
    leave a forgotten override on a project that no longer needs it.
    Re-set the override periodically to keep it active.
    """
    from app.db.redis_client import get_redis
    redis = get_redis()
    key = _OVERRIDE_KEY.format(project_id=project_id)
    if force_on is None:
        await redis.delete(key)
        return
    value = "on" if force_on else "off"
    await redis.setex(key, _FLAG_TTL_SECONDS, value)
