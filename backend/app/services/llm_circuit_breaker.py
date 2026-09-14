"""Endpoint-scoped circuit breaker for LLM provider calls (architecture E5.4).

The old streaming breaker uses one Redis key for every model.  One unavailable
Ollama instance can therefore suppress a healthy cloud endpoint.  This breaker
keys state by ``provider + normalized base_url`` and sits at the actual
``BudgetedLLM`` invocation boundary, where the endpoint is known.

Only provider-availability failures count.  An open circuit refuses before a
cost reservation or concurrency slot is taken, letting the agent's existing
deterministic fallback run immediately.  Redis is observability/control-plane
state rather than the model itself, so a Redis error is logged and the model
call is allowed.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.db.redis_client import get_redis

logger = logging.getLogger(__name__)

FAILURE_THRESHOLD = 5
RECOVERY_TIMEOUT_S = 120
HALF_OPEN_PROBE_S = 30
STATE_TTL_S = RECOVERY_TIMEOUT_S * 4
STORE_TIMEOUT_S = 0.05

_STATE_CLOSED = "CLOSED"
_STATE_OPEN = "OPEN"
_STATE_HALF_OPEN = "HALF_OPEN"
_KEY_PREFIX = "testlookup:circuit:llm:v2"


@dataclass(frozen=True)
class CircuitScope:
    provider: str
    base_url: str
    scope_id: str

    @property
    def state_key(self) -> str:
        return f"{_KEY_PREFIX}:{self.scope_id}"

    @property
    def probe_key(self) -> str:
        return f"{self.state_key}:probe"

    @property
    def trip_key(self) -> str:
        return f"{self.state_key}:trip"


class CircuitBreakerOpen(RuntimeError):
    """The selected provider endpoint is temporarily unavailable."""

    def __init__(self, scope: CircuitScope, retry_after_seconds: int):
        self.provider = scope.provider
        self.scope_id = scope.scope_id
        self.retry_after_seconds = max(0, int(retry_after_seconds))
        super().__init__(
            f"LLM circuit is open for provider={scope.provider} "
            f"endpoint={scope.scope_id}; retry in {self.retry_after_seconds}s"
        )


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


async def _store(awaitable: Any) -> Any:
    """Bound breaker Redis I/O so control state cannot delay inference."""
    return await asyncio.wait_for(awaitable, timeout=STORE_TIMEOUT_S)


def _normalize_base_url(base_url: str | None) -> str:
    raw = str(base_url or "default").strip()
    if raw == "default":
        return raw
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw.rstrip("/").lower()
    if not parsed.scheme or not parsed.hostname:
        return raw.rstrip("/").lower()
    host = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError:
        return raw.rstrip("/").lower()
    if port is not None:
        host = f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), host, path, "", ""))


def scope_for(provider: str, base_url: str | None) -> CircuitScope:
    normalized_provider = str(provider or "unknown").strip().lower()
    normalized_url = _normalize_base_url(base_url)
    digest = hashlib.sha256(
        f"{normalized_provider}\n{normalized_url}".encode("utf-8")
    ).hexdigest()[:16]
    return CircuitScope(normalized_provider, normalized_url, digest)


def is_model_unavailable(exc: BaseException) -> bool:
    """Return whether ``exc`` represents provider/model unavailability."""
    if isinstance(exc, CircuitBreakerOpen):
        return False
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    try:
        import httpx

        if isinstance(exc, httpx.RequestError):
            return True
    except ImportError:  # pragma: no cover - httpx is a backend dependency
        pass

    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if isinstance(status, int) and (status in {404, 408, 429} or status >= 500):
        return True

    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "connection refused",
            "connection timed out",
            "connect timeout",
            "service unavailable",
            "model not found",
            "model is not available",
        )
    )


def _state_metric(scope: CircuitScope, state: str) -> None:
    try:
        from app.core.metrics import llm_circuit_breaker_state

        value = {_STATE_CLOSED: 0.0, _STATE_HALF_OPEN: 0.5, _STATE_OPEN: 1.0}.get(
            state, 0.0
        )
        llm_circuit_breaker_state.labels(
            provider=scope.provider, endpoint=scope.scope_id
        ).set(value)
    except Exception:  # noqa: BLE001 - telemetry never breaks inference
        pass


class LLMCircuitBreaker:
    """Redis-backed breaker isolated to one provider endpoint."""

    _known_open_until: dict[str, float] = {}

    @classmethod
    async def _field(cls, redis: Any, scope: CircuitScope, field: str) -> str:
        return _text(await _store(redis.hget(scope.state_key, field)))

    @classmethod
    async def is_available(cls, provider: str, base_url: str | None) -> bool:
        scope = scope_for(provider, base_url)
        try:
            redis = get_redis()
            state = (await cls._field(redis, scope, "state")) or _STATE_CLOSED
            if state == _STATE_CLOSED:
                cls._known_open_until.pop(scope.scope_id, None)
                _state_metric(scope, state)
                return True
            if state == _STATE_HALF_OPEN:
                _state_metric(scope, state)
                return False
            if state != _STATE_OPEN:
                logger.warning(
                    "Unknown LLM circuit state %s for provider=%s endpoint=%s; allowing call",
                    state,
                    scope.provider,
                    scope.scope_id,
                )
                return True

            opened_at = float(await cls._field(redis, scope, "opened_at") or 0)
            open_until = opened_at + RECOVERY_TIMEOUT_S
            remaining = max(0, math.ceil(open_until - time.time()))
            if remaining > 0:
                cls._known_open_until[scope.scope_id] = open_until
                await _store(redis.expire(scope.state_key, STATE_TTL_S))
                _state_metric(scope, state)
                return False

            won_probe = await _store(
                redis.set(
                    scope.probe_key,
                    "1",
                    ex=HALF_OPEN_PROBE_S,
                    nx=True,
                )
            )
            if not won_probe:
                _state_metric(scope, _STATE_HALF_OPEN)
                return False
            await _store(
                redis.hset(
                    scope.state_key, mapping={"state": _STATE_HALF_OPEN}
                )
            )
            await _store(redis.expire(scope.state_key, STATE_TTL_S))
            cls._known_open_until.pop(scope.scope_id, None)
            _state_metric(scope, _STATE_HALF_OPEN)
            logger.info(
                "LLM circuit HALF_OPEN provider=%s endpoint=%s",
                scope.provider,
                scope.scope_id,
            )
            return True
        except Exception as exc:  # noqa: BLE001 - breaker storage fails open
            logger.warning(
                "LLM circuit availability check failed; allowing provider call: %s",
                type(exc).__name__,
            )
            return True

    @classmethod
    def is_known_open(cls, provider: str, base_url: str | None) -> bool:
        """Best-effort sync fast path for the otherwise-unused sync invoke API."""
        scope = scope_for(provider, base_url)
        until = cls._known_open_until.get(scope.scope_id, 0.0)
        if until <= time.time():
            cls._known_open_until.pop(scope.scope_id, None)
            return False
        return True

    @classmethod
    def cached_retry_after_seconds(cls, provider: str, base_url: str | None) -> int:
        scope = scope_for(provider, base_url)
        return max(0, int(cls._known_open_until.get(scope.scope_id, 0.0) - time.time()))

    @classmethod
    async def record_success(cls, provider: str, base_url: str | None) -> None:
        scope = scope_for(provider, base_url)
        try:
            redis = get_redis()
            await _store(
                redis.delete(scope.state_key, scope.probe_key, scope.trip_key)
            )
            cls._known_open_until.pop(scope.scope_id, None)
            _state_metric(scope, _STATE_CLOSED)
        except Exception as exc:  # noqa: BLE001 - telemetry never masks success
            logger.warning(
                "LLM circuit success recording failed: %s", type(exc).__name__
            )

    @classmethod
    async def record_failure(
        cls,
        provider: str,
        base_url: str | None,
        exc: BaseException,
    ) -> bool:
        """Record one unavailable call; return whether the circuit is open."""
        if not is_model_unavailable(exc):
            return False
        scope = scope_for(provider, base_url)
        try:
            redis = get_redis()
            state = (await cls._field(redis, scope, "state")) or _STATE_CLOSED
            if state == _STATE_OPEN:
                await _store(redis.expire(scope.state_key, STATE_TTL_S))
                cls._known_open_until[scope.scope_id] = time.time() + RECOVERY_TIMEOUT_S
                _state_metric(scope, _STATE_OPEN)
                return True

            failure_count = int(
                await _store(
                    redis.hincrby(
                        scope.state_key, "failure_count", 1
                    )
                )
            )
            await _store(redis.expire(scope.state_key, STATE_TTL_S))
            if state != _STATE_HALF_OPEN and failure_count < FAILURE_THRESHOLD:
                _state_metric(scope, _STATE_CLOSED)
                return False

            now = time.time()
            await _store(
                redis.hset(
                    scope.state_key,
                    mapping={
                        "state": _STATE_OPEN,
                        "opened_at": str(now),
                        "failure_count": str(failure_count),
                    },
                )
            )
            await _store(redis.expire(scope.state_key, STATE_TTL_S))
            await _store(redis.delete(scope.probe_key))
            cls._known_open_until[scope.scope_id] = now + RECOVERY_TIMEOUT_S
            first_trip = await _store(
                redis.set(scope.trip_key, "1", ex=STATE_TTL_S, nx=True)
            )
            if first_trip:
                try:
                    from app.core.metrics import llm_circuit_breaker_trips_total

                    llm_circuit_breaker_trips_total.inc()
                except Exception:  # noqa: BLE001 - telemetry never breaks inference
                    pass
            _state_metric(scope, _STATE_OPEN)
            logger.warning(
                "LLM circuit OPEN provider=%s endpoint=%s failures=%d",
                scope.provider,
                scope.scope_id,
                failure_count,
            )
            return True
        except Exception as storage_exc:  # noqa: BLE001 - preserve provider error
            logger.warning(
                "LLM circuit failure recording failed: %s",
                type(storage_exc).__name__,
            )
            return False

    @classmethod
    async def retry_after_seconds(cls, provider: str, base_url: str | None) -> int:
        scope = scope_for(provider, base_url)
        try:
            redis = get_redis()
            state = (await cls._field(redis, scope, "state")) or _STATE_CLOSED
            if state != _STATE_OPEN:
                return 0
            opened_at = float(await cls._field(redis, scope, "opened_at") or 0)
            return max(
                0,
                math.ceil(opened_at + RECOVERY_TIMEOUT_S - time.time()),
            )
        except Exception:  # noqa: BLE001 - status is advisory
            return 0

    @classmethod
    async def get_status(cls, provider: str, base_url: str | None) -> dict[str, Any]:
        scope = scope_for(provider, base_url)
        try:
            redis = get_redis()
            state = (await cls._field(redis, scope, "state")) or _STATE_CLOSED
            failures = int(await cls._field(redis, scope, "failure_count") or 0)
            _state_metric(scope, state)
            return {
                "state": state,
                "provider": scope.provider,
                "endpoint": scope.scope_id,
                "failure_count": failures,
                "failure_threshold": FAILURE_THRESHOLD,
                "retry_after_seconds": await cls.retry_after_seconds(provider, base_url),
            }
        except Exception as exc:  # noqa: BLE001 - health reporting is advisory
            return {
                "state": "UNKNOWN",
                "provider": scope.provider,
                "endpoint": scope.scope_id,
                "failure_count": 0,
                "failure_threshold": FAILURE_THRESHOLD,
                "retry_after_seconds": 0,
                "error": type(exc).__name__,
            }


async def require_available(provider: str, base_url: str | None) -> None:
    """Raise before provider admission when the endpoint circuit is open."""
    if await LLMCircuitBreaker.is_available(provider, base_url):
        return
    scope = scope_for(provider, base_url)
    retry_after = await LLMCircuitBreaker.retry_after_seconds(provider, base_url)
    raise CircuitBreakerOpen(scope, retry_after)


__all__ = [
    "CircuitBreakerOpen",
    "CircuitScope",
    "FAILURE_THRESHOLD",
    "HALF_OPEN_PROBE_S",
    "LLMCircuitBreaker",
    "RECOVERY_TIMEOUT_S",
    "is_model_unavailable",
    "require_available",
    "scope_for",
]
