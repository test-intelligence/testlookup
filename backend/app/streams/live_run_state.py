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
    async def start(cls, run_id: str, project_id: str, build_number: str, total_tests: int = 0) -> None:
        """Register a new live run. Idempotent — safe to call if run already exists."""
        redis = get_redis()
        key = _STATE_KEY(run_id)

        now = datetime.now(timezone.utc).isoformat()
        await redis.hset(key, mapping={  # type: ignore[misc]
            "run_id":       run_id,
            "project_id":   project_id,
            "build_number": build_number,
            "total":        total_tests,
            "passed":       0,
            "failed":       0,
            "skipped":      0,
            "broken":       0,
            "current_test": "",
            "started_at":   now,
            "last_event_at": now,
            "status":       "running",
        })
        await redis.expire(key, _TTL)
        await redis.sadd(LIVE_ACTIVE_SET, run_id)  # type: ignore[misc]
        logger.info("Live run started: %s build=%s", run_id, build_number)

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

        # Atomic counter increment
        status_upper = status.upper()
        field_map: dict[str, str] = {
            "PASSED": "passed",
            "FAILED": "failed",
            "SKIPPED": "skipped",
            "BROKEN": "broken",
        }
        counter_field = field_map.get(status_upper)
        if counter_field:
            await redis.hincrby(key, counter_field, 1)  # type: ignore[misc]

        # Update metadata fields
        now = datetime.now(timezone.utc).isoformat()
        updates: dict[str, str | int] = {"last_event_at": now}
        if test_name:
            updates["current_test"] = test_name
        if total_tests > 0:
            # Use the max seen so we don't regress (events can arrive slightly out of order)
            current_total = int(await redis.hget(key, "total") or 0)  # type: ignore[misc]
            if total_tests > current_total:
                updates["total"] = total_tests
        if updates:
            await redis.hset(key, mapping=updates)  # type: ignore[misc]

        # Refresh TTL on activity
        await redis.expire(key, _TTL)
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

        await redis.hset(key, mapping={  # type: ignore[misc]
            "status":       "completed",
            "last_event_at": datetime.now(timezone.utc).isoformat(),
        })
        # Shorten TTL to 1 hour post-completion
        await redis.expire(key, 3600)
        await redis.srem(LIVE_ACTIVE_SET, run_id)  # type: ignore[misc]

        state = await cls.get(run_id)
        logger.info(
            "Live run completed: %s pass_rate=%.1f%%",
            run_id, state.get("pass_rate", 0) if state else 0,
        )
        return state

    @classmethod
    def should_warn(cls, state: dict) -> bool:
        """Return True if early-warning threshold is crossed."""
        passed = int(state.get("passed", 0) or 0)
        failed = int(state.get("failed", 0) or 0)
        broken = int(state.get("broken", 0) or 0)
        completed = passed + failed + broken
        pass_rate = float(state.get("pass_rate", 100.0) or 100.0)
        return completed >= _WARN_THRESHOLD and pass_rate < _WARN_PASS_RATE

    @staticmethod
    def _deserialise(raw: dict[str, Any]) -> dict[str, Any]:
        """Convert Redis string values back to typed Python values."""
        int_fields = {"total", "passed", "failed", "skipped", "broken"}
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
        return result
