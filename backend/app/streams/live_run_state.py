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

# start()'s decision and write, atomically (re-audit H6, batch 5).
# KEYS[1] state hash, KEYS[2] active set.
# ARGV[1] '1' = reset a COMPLETED run, ARGV[2] TTL, ARGV[3] run id,
# ARGV[4..] the new state's field/value pairs.
# Returns 'kept' (an existing run left alone), 'replaced' or 'created'.
_START_LUA = r"""
local existed = redis.call('EXISTS', KEYS[1]) == 1
if existed then
  if ARGV[1] ~= '1' or redis.call('HGET', KEYS[1], 'status') ~= 'completed' then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
    redis.call('SADD', KEYS[2], ARGV[3])
    return 'kept'
  end
  redis.call('DEL', KEYS[1])
end
redis.call('HSET', KEYS[1], unpack(ARGV, 4))
redis.call('EXPIRE', KEYS[1], ARGV[2])
redis.call('SADD', KEYS[2], ARGV[3])
if existed then return 'replaced' end
return 'created'
"""


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
        reset: bool = False,
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

        # A source Redis Stream entry may be reclaimed after its side effects
        # succeeded but before XACK. Do not reset counters when that same
        # run_start is delivered again.
        #
        # ``reset`` is for the producer's own run_start (POST /ws/events), where
        # the run id is the caller's: a caller that reuses the id of a run that
        # has COMPLETED is starting a new run, which must not begin from the
        # last run's totals, or stay "completed" (code review of re-audit H6).
        # A run still in progress is left alone: a retried run_start, or a
        # parallel shard opening the same id, is the same run, and resetting it
        # wiped its counts (QA and code review of the H6 fix). The consumer
        # never resets: its call can arrive after the producer's -- even after
        # the run completed -- and must change nothing.
        #
        # The decision and the write are ONE Lua script (re-audit H6, batch 5).
        # Done as EXISTS, HGET, then a replace, two run_starts for a completed
        # run could both see "completed": the first replaced the state, a result
        # was counted, and the second replaced it again and wiped that result.
        # The create path had the same gap: two creators could both see no key,
        # and the later HSET zeroed what the earlier one's run had counted.
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
        pairs: list[Any] = []
        for field, value in mapping.items():
            pairs.extend((field, str(value)))
        outcome = await redis.eval(  # type: ignore[misc]
            _START_LUA,
            2,
            key,
            LIVE_ACTIVE_SET,
            "1" if reset else "0",
            str(_TTL),
            run_id,
            *pairs,
        )
        if isinstance(outcome, bytes):
            outcome = outcome.decode()
        if outcome == "kept":
            return
        logger.info("Live run started: %s build=%s launch=%s suite=%s",
                    run_id, build_number, launch_name or "-", suite_name or "-")

    @classmethod
    async def record_test_event(
        cls,
        run_id: str,
        status: str,
        test_name: str,
        total_tests: int = 0,
        *,
        return_state: bool = True,
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
        if not return_state:
            # The producer discards the state; reading the whole hash back cost
            # a round trip per event (code review of re-audit H6).
            return None
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
