"""
Shared Redis caching utility for analytics and dashboard queries.

P3-6: Caches expensive aggregation results in Redis with configurable TTLs.
Cache keys are namespaced and include project_id + parameters to avoid collisions.
All cache operations are non-blocking — failures silently fall through to the DB.

VIZ-212: every key also carries the project's **analytics epoch**, a counter
that every mutation path bumps after its commit. Bumping is one ``INCR``; the
old entries are never read again and age out on their own TTL. A reader must
read the epoch ONCE, before its query, and pass the same value to both
``cache_get`` and ``cache_set``:

    epoch = await get_analytics_epoch(project_id)
    result = await cache_get("dashboard_summary", project_id, epoch=epoch, days=7)
    if result is None:
        result = await _expensive_query(...)
        await cache_set("dashboard_summary", result, project_id, ttl=60, epoch=epoch, days=7)

Reading it again at ``cache_set`` time would be wrong: a mutation that commits
while the query runs bumps the epoch, and a value computed from the old
snapshot would then be stored under the NEW epoch and served as fresh.

``epoch=None`` means the epoch could not be read (Redis unreachable, or a
corrupt counter). Both calls then do nothing: a value whose epoch is unknown
is never served, and the caller computes from the database.

Live runs: the live-session drainer commits a batch of streamed results
roughly every 30 s and bumps after each commit, so while a live run is in
flight its project's dashboards recompute about every 30 s instead of being
served from the cache. That is accepted: the dashboard TTL is 60 s anyway,
and a live run's numbers are exactly what must not be served stale.
"""
import asyncio
import json
import time
import uuid
from typing import Any, Iterable

import structlog

logger = structlog.get_logger("services.cache")

# TTL presets (seconds) — tuned for each data type's change frequency
CACHE_TTL_DASHBOARD = 60       # In-progress runs change frequently
CACHE_TTL_FLAKY = 300          # Changes slowly (only on new run completion)
CACHE_TTL_COVERAGE = 600       # Changes only when new runs complete
CACHE_TTL_ANALYTICS = 300      # General analytics

# Epoch counters live OUTSIDE the ``analytics:`` namespace on purpose:
# ``invalidate_analytics_cache`` SCAN+DELETEs ``analytics:*``, and deleting a
# counter would reset it to 0 — re-validating every entry written under epoch 0.
#
# They are created by INCR and never given a TTL. Redis runs ``volatile-lru``,
# which only evicts keys that HAVE a TTL, so a TTL-less counter cannot be
# evicted under memory pressure (an evicted counter would also restart at 0).
ANALYTICS_EPOCH_PREFIX = "analytics_epoch:"
# Bumped by every project bump: all-projects entries mix every project's data,
# so any project's mutation must invalidate them.
ANALYTICS_EPOCH_ALL = "__all__"

# A bump runs right after a commit, on the request (or task) that made the
# mutation. Redis being slow or unreachable must not stall that path for the
# client's full 5 s socket timeout per INCR: a bump is ONE pipelined round
# trip bounded by this deadline, then it gives up (and counts the failure).
ANALYTICS_EPOCH_BUMP_TIMEOUT_SECONDS = 0.25


def analytics_epoch_key(project_id: "str | uuid.UUID | None") -> str:
    """Redis key of the epoch counter for ``project_id`` (None = all projects)."""
    scope = str(project_id) if project_id else ANALYTICS_EPOCH_ALL
    return f"{ANALYTICS_EPOCH_PREFIX}{scope}"


def _build_cache_key(
    namespace: str, project_id: str | None, *, epoch: int, **kwargs: Any
) -> str:
    """Build a deterministic cache key from namespace + epoch + params."""
    parts = [f"analytics:{namespace}", project_id or "all", f"e{epoch}"]
    for k, v in sorted(kwargs.items()):
        parts.append(f"{k}={v}")
    return ":".join(parts)


async def get_analytics_epoch(project_id: "str | uuid.UUID | None") -> int | None:
    """The current analytics epoch for a project, or for all projects.

    0 when the counter does not exist yet; None when it cannot be read, which
    callers pass straight to ``cache_get``/``cache_set`` to bypass the cache.
    """
    try:
        from app.db.redis_client import get_redis

        raw = await get_redis().get(analytics_epoch_key(project_id))
    except Exception as exc:
        logger.warning("analytics_epoch_read_failed", error=str(exc))
        return None
    if raw is None:
        return 0
    epoch = _as_epoch(raw)
    if epoch is None:
        # A counter we cannot interpret is an epoch we do not know: bypass
        # the cache for this read, and repair the counter so the NEXT read
        # caches again (a corrupt counter must not disable caching forever).
        logger.warning("analytics_epoch_corrupt", project_id=str(project_id))
        await _repair_epoch(analytics_epoch_key(project_id))
    return epoch


def _as_epoch(raw: Any) -> int | None:
    """A stored counter as an epoch, or None if it is not a usable one.

    Non-integer (garbage), negative and saturated values are unknown. INCR
    only ever produces 1, 2, 3, … from a missing key, so a negative value was
    written by something else and says nothing about which entries are
    current; a counter at the 64-bit maximum can no longer be INCRed, so it
    cannot invalidate anything cached under it.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if 0 <= value < _EPOCH_MAX else None


_EPOCH_MAX = 2**63 - 1  # Redis INCR fails ("would overflow") at this value


def _fresh_epoch() -> int:
    """A value no counter has held before: wall-clock microseconds."""
    return time.time_ns() // 1_000


async def _repair_epoch(key: str) -> None:
    """Reset a corrupt counter to a fresh value. Never raises.

    ``SET`` without a TTL, like INCR (a TTL would make it evictable under
    ``volatile-lru``). The value is wall-clock microseconds (~1.7e15), far
    above anything INCR reaches, so it re-validates no entry written before
    the corruption; and while the counter was corrupt every reader bypassed
    the cache, so no entry exists under a value derived from the bad one.

    Races, all benign: two repairers both SET — each value is fresh, last
    writer wins. A repair can overwrite a bump that landed between another
    repair and itself; the overwrite still CHANGES the value (a later clock
    reading), which is all a bump has to achieve — entries cached under the
    overwritten value are no longer current. The one unsafe case is two
    repairs writing the identical microsecond on hosts whose clocks disagree,
    in the window of a concurrent query; that is accepted for a counter that
    is only ever corrupted by hand.
    """
    try:
        from app.db.redis_client import get_redis

        await asyncio.wait_for(
            get_redis().set(key, _fresh_epoch()),
            timeout=ANALYTICS_EPOCH_BUMP_TIMEOUT_SECONDS,
        )
        logger.warning("analytics_epoch_repaired", key=key)
    except Exception as exc:
        logger.warning("analytics_epoch_repair_failed", key=key, error=str(exc))


def _count_bump_failure(n: int = 1) -> None:
    try:
        from app.core.metrics import analytics_epoch_bump_failures_total

        analytics_epoch_bump_failures_total.inc(n)
    except Exception:  # telemetry must never break the mutation path
        pass


async def bump_analytics_epoch(project_id: "str | uuid.UUID | None") -> None:
    """Invalidate every analytics cache entry of a project. Call AFTER commit.

    One pipelined round trip: INCR of the project's counter and of the
    all-projects counter. Never raises — it runs after the commit, so the data
    change has already happened and an exception here would only turn a
    success into a 500.
    """
    await bump_analytics_epochs([project_id])


async def bump_analytics_epochs(project_ids: Iterable["str | uuid.UUID | None"]) -> None:
    """Bump each distinct project once, plus the all-projects counter once.

    ONE non-transactional pipeline (a single round trip however many
    projects), bounded by ``ANALYTICS_EPOCH_BUMP_TIMEOUT_SECONDS``. The
    pipeline does not stop at a failed command, so the ``__all__`` INCR runs
    even when a project's INCR fails (e.g. on a corrupt counter). A counter
    that cannot be incremented, or that INCR takes negative, is repaired.
    Every failure is logged and counted in
    ``analytics_epoch_bump_failures_total``; nothing is raised.
    """
    keys: list[str] = []
    for project_id in project_ids:
        if not project_id:
            logger.warning("analytics_epoch_bump_without_project")
            continue
        key = analytics_epoch_key(project_id)
        if key not in keys:
            keys.append(key)
    if not keys:
        return
    keys.append(analytics_epoch_key(None))
    try:
        from app.db.redis_client import get_redis

        pipe = get_redis().pipeline(transaction=False)
        for key in keys:
            pipe.incr(key)
        results = await asyncio.wait_for(
            pipe.execute(raise_on_error=False),
            timeout=ANALYTICS_EPOCH_BUMP_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        # Timeout / unreachable: nothing is known to have been bumped.
        _count_bump_failure(len(keys))
        logger.warning(
            "analytics_epoch_bump_failed", keys=keys, error=repr(exc) or type(exc).__name__
        )
        return
    for key, result in zip(keys, results):
        if isinstance(result, Exception) or _as_epoch(result) is None:
            _count_bump_failure()
            logger.warning("analytics_epoch_bump_failed", key=key, error=str(result))
            # A corrupt (non-integer / overflowed / negative) counter: readers
            # already bypass it; give it a fresh value so caching resumes.
            await _repair_epoch(key)


async def cache_get(
    namespace: str, project_id: str | None, *, epoch: int | None, **kwargs: Any
) -> Any | None:
    """Fetch a cached result. Returns None on miss, unknown epoch, or Redis failure."""
    if epoch is None:
        return None
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        key = _build_cache_key(namespace, project_id, epoch=epoch, **kwargs)
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
    *,
    epoch: int | None,
    **kwargs: Any,
) -> None:
    """Store a value in the cache. Non-blocking — failures are silently ignored.

    ``epoch`` must be the value read BEFORE the query that produced ``value``.
    """
    if epoch is None:
        return
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        key = _build_cache_key(namespace, project_id, epoch=epoch, **kwargs)
        await redis.set(key, json.dumps(value, default=str), ex=ttl)
    except Exception:
        pass


async def invalidate_analytics_cache(project_id: str | None = None) -> None:
    """Invalidate all analytics cache entries for a project (or all projects).

    Called after a new test run completes to ensure fresh dashboard data.
    Bumps the project's epoch first (the O(1) invalidation every reader keys
    on), then SCAN+DELETEs the entries themselves to free the memory. The
    epoch counters sit outside ``analytics:*``, so the SCAN cannot touch them.
    """
    if project_id:
        await bump_analytics_epoch(project_id)
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        if not project_id:
            # Deleting every entry is the invalidation here; the all-projects
            # counter is bumped too so no reader re-validates a value it read
            # just before the delete.
            await redis.incr(analytics_epoch_key(None))
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
