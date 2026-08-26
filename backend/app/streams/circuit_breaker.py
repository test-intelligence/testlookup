"""
LLM Circuit Breaker — Redis-backed, shared across all worker processes.

Prevents retry storms when the LLM provider (Ollama / OpenAI) is down.

State machine:
  CLOSED    → Normal operation. All requests pass through.
  OPEN      → Provider is failing. Requests rejected immediately.
             After RECOVERY_TIMEOUT seconds, transitions to HALF_OPEN.
  HALF_OPEN → Test mode. First request allowed through.
             Success → CLOSED.  Failure → OPEN again.

Redis keys:
  testlookup:circuit:llm        Hash: {state, failure_count, opened_at}
  testlookup:circuit:llm:window Sorted set of failure timestamps (sliding window)
"""
import logging
import time
import uuid
from typing import Any, Callable, Coroutine

from app.db.redis_client import get_redis
from app.streams import CIRCUIT_KEY

logger = logging.getLogger("streams.circuit_breaker")

# ── Thresholds ────────────────────────────────────────────────────────────────
FAILURE_THRESHOLD  = 5      # number of failures in the window before OPEN
FAILURE_WINDOW_S   = 60     # sliding window length in seconds
RECOVERY_TIMEOUT_S = 120    # seconds to wait before trying HALF_OPEN
HALF_OPEN_PROBE_S  = 30     # HALF_OPEN state remains for this long before giving up

_WINDOW_KEY = f"{CIRCUIT_KEY}:window"
_STATE_CLOSED    = "CLOSED"
_STATE_OPEN      = "OPEN"
_STATE_HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpen(Exception):
    """Raised when the circuit is open and requests are being rejected."""


class LLMCircuitBreaker:
    """
    Redis-backed circuit breaker for LLM provider calls.
    All methods are class methods — no instance state needed.
    """

    @classmethod
    async def is_available(cls) -> bool:
        """
        Return True if the circuit is CLOSED or HALF_OPEN (allow request).
        Return False if OPEN (reject request — caller should retry later).
        """
        redis = get_redis()
        state = await cls._get_state()
        if state == _STATE_CLOSED:
            return True
        if state == _STATE_OPEN:
            # Check if recovery timeout has elapsed → transition to HALF_OPEN
            opened_at = float(await cls._get_field("opened_at") or 0)
            if time.time() - opened_at >= RECOVERY_TIMEOUT_S:
                await cls._set_state(_STATE_HALF_OPEN)
                logger.info("Circuit breaker → HALF_OPEN (probing LLM provider)")
                return True
            # Still OPEN. Refresh the state-hash TTL so a sustained reject
            # storm can't let the key lapse back to CLOSED before recovery
            # elapses (reads don't refresh TTL on their own). The TTL is kept
            # only as a self-heal net for a circuit that goes fully idle.
            await redis.expire(CIRCUIT_KEY, RECOVERY_TIMEOUT_S * 4)
            return False
        # HALF_OPEN: allow the probe through. Refresh TTL too so the probe
        # window can't expire to CLOSED before a call resolves it.
        await redis.expire(CIRCUIT_KEY, RECOVERY_TIMEOUT_S * 4)
        return True

    @classmethod
    async def retry_after_seconds(cls) -> int:
        """Return estimated seconds until the circuit will next allow a request."""
        state = await cls._get_state()
        if state != _STATE_OPEN:
            return 0
        opened_at = float(await cls._get_field("opened_at") or 0)
        remaining = int(RECOVERY_TIMEOUT_S - (time.time() - opened_at))
        return max(remaining, 0)

    @classmethod
    async def record_success(cls) -> None:
        """Record a successful LLM call. Closes the circuit if HALF_OPEN."""
        state = await cls._get_state()
        if state == _STATE_HALF_OPEN:
            await cls._reset()
            logger.info("Circuit breaker → CLOSED (LLM provider recovered)")
        # In CLOSED state, clear any stale failures
        elif state == _STATE_CLOSED:
            await cls._prune_window()

    @classmethod
    async def record_failure(cls) -> None:
        """Record a failed LLM call. Opens the circuit if threshold exceeded."""
        redis = get_redis()
        now = time.time()

        # Add failure to sliding window. The member must be unique — keying it
        # solely on the float timestamp collapsed two failures in the same tick
        # (or across processes producing identical floats) into one sorted-set
        # member, under-counting failures and delaying the OPEN transition.
        await redis.zadd(_WINDOW_KEY, {f"{now}:{uuid.uuid4().hex}": now})
        await redis.expire(_WINDOW_KEY, FAILURE_WINDOW_S * 2)

        # Prune failures outside the window
        await cls._prune_window()

        # Count recent failures
        failure_count = await redis.zcard(_WINDOW_KEY)

        state = await cls._get_state()
        if state == _STATE_HALF_OPEN or failure_count >= FAILURE_THRESHOLD:
            # Count the TRANSITION, not the call: record_failure() can run
            # again while the circuit is already OPEN, and counting those
            # would report a breaker that tripped once as tripping
            # repeatedly. TestLookupLLMCircuitBreakerOpen alerts on any
            # increase, and nothing incremented this counter — verified
            # live, 0 samples — so the alert could never fire.
            was_open = state == _STATE_OPEN
            await cls._open()
            if not was_open:
                try:
                    from app.core.metrics import llm_circuit_breaker_trips_total

                    llm_circuit_breaker_trips_total.inc()
                except Exception:  # noqa: BLE001 — metrics never break the path
                    pass
            logger.warning(
                "Circuit breaker → OPEN (failures=%d in %ds)",
                failure_count, FAILURE_WINDOW_S,
            )

    @classmethod
    async def get_status(cls) -> dict:
        """Return current circuit breaker status (for health endpoint)."""
        redis = get_redis()
        state = await cls._get_state()
        failure_count = await redis.zcard(_WINDOW_KEY)
        opened_at = float(await cls._get_field("opened_at") or 0)
        return {
            "state": state,
            "failure_count_in_window": failure_count,
            "failure_threshold": FAILURE_THRESHOLD,
            "opened_at": opened_at if opened_at else None,
            "retry_after_seconds": await cls.retry_after_seconds(),
        }

    # ── Convenience wrapper ───────────────────────────────────────────────────

    @classmethod
    async def call(
        cls,
        coro_factory: Callable[[], Coroutine[Any, Any, Any]],
    ) -> Any:
        """
        Execute `coro_factory()` guarded by the circuit breaker.

        Usage:
            result = await LLMCircuitBreaker.call(lambda: llm.ainvoke(messages))
        """
        if not await cls.is_available():
            retry_after = await cls.retry_after_seconds()
            raise CircuitBreakerOpen(
                f"LLM circuit breaker is OPEN — retry in {retry_after}s"
            )
        try:
            result = await coro_factory()
            await cls.record_success()
            return result
        except CircuitBreakerOpen:
            raise
        except Exception as exc:
            # Never let a circuit-breaker bookkeeping error (e.g. Redis down)
            # mask the real provider exception the caller needs to see.
            try:
                await cls.record_failure()
            except Exception as cb_err:
                logger.warning("circuit breaker record_failure failed: %s", cb_err)
            raise exc

    # ── Internal helpers ──────────────────────────────────────────────────────

    @classmethod
    async def _get_state(cls) -> str:
        state = await cls._get_field("state")
        return state or _STATE_CLOSED

    @classmethod
    async def _get_field(cls, field: str) -> Any:
        redis = get_redis()
        return await redis.hget(CIRCUIT_KEY, field)  # type: ignore[misc]

    @classmethod
    async def _set_state(cls, state: str) -> None:
        redis = get_redis()
        await redis.hset(CIRCUIT_KEY, "state", state)  # type: ignore[misc]
        await redis.expire(CIRCUIT_KEY, RECOVERY_TIMEOUT_S * 4)

    @classmethod
    async def _open(cls) -> None:
        redis = get_redis()
        await redis.hset(CIRCUIT_KEY, mapping={  # type: ignore[misc]
            "state": _STATE_OPEN,
            "opened_at": str(time.time()),
        })
        await redis.expire(CIRCUIT_KEY, RECOVERY_TIMEOUT_S * 4)

    @classmethod
    async def _reset(cls) -> None:
        redis = get_redis()
        await redis.delete(CIRCUIT_KEY, _WINDOW_KEY)

    @classmethod
    async def _prune_window(cls) -> None:
        redis = get_redis()
        cutoff = time.time() - FAILURE_WINDOW_S
        await redis.zremrangebyscore(_WINDOW_KEY, "-inf", cutoff)
