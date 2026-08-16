"""
Redis Hash-backed live run state.

Replaces the in-memory _active_runs dict in live_monitor.py.

Benefits:
  - Survives backend restarts (state persists in Redis)
  - Shared across multiple FastAPI worker processes
  - Atomic counter increments (HINCRBY — no race conditions)
  - Automatic expiry (24h TTL prevents orphaned state)
  - O(1) reads and writes via Redis Hash operations
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.db.redis_client import get_redis
from app.streams import LIVE_ACTIVE_SET, LIVE_STATE_KEY

logger = logging.getLogger("streams.live_state")

# Key builders
def _STATE_KEY(run_id: str) -> str:
    return LIVE_STATE_KEY.format(run_id=run_id)
_TTL = 86_400          # 24 hours — cleans up abandoned runs automatically
_WARN_THRESHOLD = 10   # need at least this many completed tests before warning
_WARN_PASS_RATE  = 50.0


class RedisLiveRunState:
    """
    Manages live test run state in Redis.

    Each run is stored as a Redis Hash at key `testlookup:live:state:{run_id}`.
    All counter updates use HINCRBY for atomic, race-free increments.
    """

    @classmethod
    async def start(
        cls,
        run_id: str,
        project_id: str,
        build_number: str,
        total_tests: int = 0,
        *,
        launch_name: Optional[str] = None,
        suite_name: Optional[str] = None,
    ) -> None:
        """Register a new live run. Idempotent — safe to call if run already exists.

        ``launch_name`` is the human-readable label (analogous to ReportPortal's
        ``rp.launch``). When provided it lands in the Redis state hash so the
        Live Execution UI can show it during the run, before the session is
        finalised and persisted to Postgres.

        ``suite_name`` is the run-level suite identifier (testlookup.suite,
        with testlookup.launch as the documented SDK fallback). Stored on the
        live-state hash so the /live UI can surface a suite column even while
        the session is still active.
        """
        redis = get_redis()
        key = _STATE_KEY(run_id)

        now = datetime.now(timezone.utc).isoformat()
        mapping: dict = {
            "run_id":       run_id,
            "project_id":   project_id,
            "build_number": build_number,
            "total":        total_tests,
            "passed":       0,
            "failed":       0,
            "skipped":      0,
            "broken":       0,
            "unknown":      0,
            "current_test": "",
            "started_at":   now,
            "last_event_at": now,
            "status":       "running",
        }
        if launch_name:
            mapping["launch_name"] = launch_name
        if suite_name:
            mapping["suite_name"] = suite_name
        await redis.hset(key, mapping=mapping)  # type: ignore[misc]
        await redis.expire(key, _TTL)
        await redis.sadd(LIVE_ACTIVE_SET, run_id)  # type: ignore[misc]
        logger.info("Live run started: %s build=%s launch=%s suite=%s",
                    run_id, build_number, launch_name or "-", suite_name or "-")

    @classmethod
    async def record_test_event(
        cls,
        run_id: str,
        status: str,
        test_name: str,
        total_tests: int = 0,
    ) -> Optional[dict]:
        """
        Atomically increment the counter for the given status and update metadata.
        Returns the updated state dict, or None if run is not registered.
        """
        redis = get_redis()
        key = _STATE_KEY(run_id)

        if not await redis.exists(key):
            logger.warning("Received event for unknown run %s — ignoring", run_id)
            return None

        status_upper = status.upper()
        field_map: dict[str, str] = {
            "PASSED": "passed",
            "FAILED": "failed",
            "SKIPPED": "skipped",
            "BROKEN": "broken",
            "UNKNOWN": "unknown",
        }
        # Anything outside the vocabulary buckets as ``unknown`` rather than
        # incrementing nothing. Previously an unrecognised status (a client
        # sending "FAIL" instead of "FAILED", say) left every counter untouched
        # while ``_event_to_row`` still persisted the TestCase as UNKNOWN — so
        # the run's aggregates disagreed with its own test_cases rows, and a
        # genuinely failing test was erased from a run that then graded PASSED
        # at 100%. Counting it keeps the aggregates honest; run_status.py
        # decides what an uninterpretable result means for the grade.
        counter_field = field_map.get(status_upper, "unknown")

        # Build the metadata update.
        now = datetime.now(timezone.utc).isoformat()
        updates: dict[str, str | int] = {"last_event_at": now}
        if test_name:
            updates["current_test"] = test_name
        if total_tests > 0:
            # Use the max seen so we don't regress (events can arrive slightly out of order)
            current_total = int(await redis.hget(key, "total") or 0)  # type: ignore[misc]
            if total_tests > current_total:
                updates["total"] = total_tests

        # Commit the counter increment, metadata, and TTL refresh as ONE
        # pipeline. Done as separate awaits, a crash between the HSET and the
        # EXPIRE could leave the hash without a TTL (orphaned forever), and the
        # counter could land without its metadata. The pipeline makes them a
        # single round trip that applies together.
        pipe = redis.pipeline(transaction=True)
        if counter_field:
            pipe.hincrby(key, counter_field, 1)
        pipe.hset(key, mapping=updates)
        pipe.expire(key, _TTL)
        await pipe.execute()
        return await cls.get(run_id)

    @classmethod
    async def get(cls, run_id: str) -> Optional[dict]:
        """Return the current state as a plain dict, or None if not found."""
        redis = get_redis()
        raw = await redis.hgetall(_STATE_KEY(run_id))  # type: ignore[misc]
        if not raw:
            return None
        return cls._deserialise(raw)

    @classmethod
    async def get_all_active(cls) -> list[dict]:
        """Return state dicts for all currently active runs."""
        redis = get_redis()
        run_ids = await redis.smembers(LIVE_ACTIVE_SET)  # type: ignore[misc]
        states = []
        dead = []
        for run_id in run_ids:
            state = await cls.get(run_id)
            if state:
                states.append(state)
            else:
                dead.append(run_id)
        # Prune expired run IDs from the active set
        if dead:
            await redis.srem(LIVE_ACTIVE_SET, *dead)  # type: ignore[misc]
        return states

    @classmethod
    async def complete(cls, run_id: str) -> Optional[dict]:
        """
        Mark a run as complete. Returns the final state dict.
        Removes from the active set but keeps state in Redis for 1 hour
        (for late-arriving summary/analysis results to reference).
        """
        redis = get_redis()
        key = _STATE_KEY(run_id)
        if not await redis.exists(key):
            return None

        # Flip status, shorten TTL to 1h post-completion, and drop from the
        # active set as ONE pipeline. Done separately, a crash between the HSET
        # and the SREM left a completed run lingering in LIVE_ACTIVE_SET (so
        # get_all_active reported it as active until its key expired).
        pipe = redis.pipeline(transaction=True)
        pipe.hset(key, mapping={
            "status":       "completed",
            "last_event_at": datetime.now(timezone.utc).isoformat(),
        })
        pipe.expire(key, 3600)
        pipe.srem(LIVE_ACTIVE_SET, run_id)
        await pipe.execute()

        state = await cls.get(run_id)
        logger.info(
            "Live run completed: %s pass_rate=%.1f%%",
            run_id, state.get("pass_rate", 0) if state else 0,
        )
        return state

    @classmethod
    def should_warn(cls, state: dict) -> bool:
        """Return True if early-warning threshold is crossed.

        Computes pass_rate from the counter fields rather than trusting a
        ``pass_rate`` key — that key only exists on ``get()``/``_deserialise``
        results, so a caller passing a raw Redis hash would otherwise always
        see the 100.0 default and never warn.
        """
        passed = int(state.get("passed", 0) or 0)
        failed = int(state.get("failed", 0) or 0)
        broken = int(state.get("broken", 0) or 0)
        completed = passed + failed + broken
        if completed < _WARN_THRESHOLD:
            return False
        pass_rate = (passed / completed * 100) if completed else 100.0
        return pass_rate < _WARN_PASS_RATE

    @staticmethod
    def _deserialise(raw: dict[str, Any]) -> dict[str, Any]:
        """Convert Redis string values back to typed Python values."""
        int_fields = {
            "total", "passed", "failed", "skipped", "broken", "unknown",
            "events_received",
        }
        result: dict[str, Any] = {}
        for k, v in raw.items():
            if k in int_fields:
                result[k] = int(v or 0)
            else:
                result[k] = v
        # Computed pass_rate
        completed = result.get("passed", 0) + result.get("failed", 0) + result.get("broken", 0)
        result["pass_rate"] = (
            round((result.get("passed", 0) / completed * 100), 2) if completed else 0.0
        )
        # Ensure total >= completed count.
        # When no pre-announced total was given (total=0 at start), total stays 0
        # in Redis but the UI needs it to reflect tests seen so far.
        # When a pre-announced total IS given (e.g. 100), max() keeps it unchanged
        # until completed count surpasses it (shouldn't happen, but safe).
        # ``unknown`` is included so an uninterpretable result cannot be
        # silently dropped from the run's total the way it was before.
        all_completed = (result.get("passed", 0) + result.get("failed", 0)
                         + result.get("skipped", 0) + result.get("broken", 0)
                         + result.get("unknown", 0))
        result["total"] = max(result.get("total", 0), all_completed)
        return result
