"""E5.4 endpoint-scoped LLM circuit-breaker regression tests."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock

import pytest

from app.services import llm_circuit_breaker as breaker
from app.services.llm_factory import BudgetedLLM


class _FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = defaultdict(dict)
        self.values: dict[str, str] = {}

    async def hget(self, key, field):
        return self.hashes[key].get(field)

    async def hset(self, key, mapping=None, **kwargs):
        self.hashes[key].update(
            {str(k): str(v) for k, v in (mapping or kwargs).items()}
        )
        return 1

    async def hincrby(self, key, field, amount):
        value = int(self.hashes[key].get(field, "0")) + int(amount)
        self.hashes[key][field] = str(value)
        return value

    async def set(self, key, value, *, ex=None, nx=False):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = str(value)
        return True

    async def expire(self, key, ttl):
        del key, ttl
        return True

    async def delete(self, *keys):
        for key in keys:
            self.hashes.pop(key, None)
            self.values.pop(key, None)
        return len(keys)


class _RacingRedis(_FakeRedis):
    """Hold two OPEN-state readers so the probe lease faces a real race."""

    def __init__(self) -> None:
        super().__init__()
        self.race_open_reads = False
        self.open_readers = 0
        self.both_read_open = asyncio.Event()

    async def hget(self, key, field):
        value = await super().hget(key, field)
        if self.race_open_reads and field == "state" and value == "OPEN":
            self.open_readers += 1
            if self.open_readers == 2:
                self.both_read_open.set()
            await self.both_read_open.wait()
        return value


class _Inner:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, *args, **kwargs):
        del args, kwargs
        self.calls += 1
        return "provider result"


class _FailingInner:
    async def ainvoke(self, *args, **kwargs):
        del args, kwargs
        raise TimeoutError("provider timed out")


class _Slot:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback
        return False


@pytest.fixture(autouse=True)
def _clear_process_cache():
    breaker.LLMCircuitBreaker._known_open_until.clear()
    yield
    breaker.LLMCircuitBreaker._known_open_until.clear()


def test_scope_is_provider_plus_normalized_base_url_without_credentials_or_query():
    first = breaker.scope_for(
        " OLLAMA ", "HTTP://user:secret@Model-A:11434/v1/?token=secret"
    )
    same = breaker.scope_for("ollama", "http://model-a:11434/v1")
    other_provider = breaker.scope_for("vllm", "http://model-a:11434/v1")
    other_endpoint = breaker.scope_for("ollama", "http://model-b:11434/v1")

    assert first == same
    assert first.scope_id != other_provider.scope_id
    assert first.scope_id != other_endpoint.scope_id
    assert "secret" not in first.state_key
    assert "model-a" not in first.state_key


@pytest.mark.asyncio
async def test_failures_open_only_the_matching_provider_endpoint(monkeypatch):
    assert breaker.FAILURE_THRESHOLD == 5
    redis = _FakeRedis()
    monkeypatch.setattr(breaker, "get_redis", lambda: redis)

    for _ in range(breaker.FAILURE_THRESHOLD):
        await breaker.LLMCircuitBreaker.record_failure(
            "ollama", "http://model-a:11434", TimeoutError("timed out")
        )

    assert not await breaker.LLMCircuitBreaker.is_available(
        "ollama", "http://model-a:11434"
    )
    assert await breaker.LLMCircuitBreaker.is_available(
        "ollama", "http://model-b:11434"
    )
    assert await breaker.LLMCircuitBreaker.is_available(
        "openai", "http://model-a:11434"
    )


@pytest.mark.asyncio
async def test_success_breaks_the_consecutive_failure_sequence(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr(breaker, "get_redis", lambda: redis)

    for _ in range(breaker.FAILURE_THRESHOLD - 1):
        await breaker.LLMCircuitBreaker.record_failure(
            "ollama", None, ConnectionRefusedError("down")
        )
    await breaker.LLMCircuitBreaker.record_success("ollama", None)
    for _ in range(breaker.FAILURE_THRESHOLD - 1):
        await breaker.LLMCircuitBreaker.record_failure(
            "ollama", None, ConnectionRefusedError("down")
        )

    assert await breaker.LLMCircuitBreaker.is_available("ollama", None)


@pytest.mark.asyncio
async def test_recovery_allows_exactly_one_half_open_probe(monkeypatch):
    redis = _RacingRedis()
    now = [1_000.0]
    monkeypatch.setattr(breaker, "get_redis", lambda: redis)
    monkeypatch.setattr(breaker.time, "time", lambda: now[0])
    for _ in range(breaker.FAILURE_THRESHOLD):
        await breaker.LLMCircuitBreaker.record_failure(
            "ollama", None, TimeoutError("down")
        )

    now[0] += breaker.RECOVERY_TIMEOUT_S + 1
    redis.race_open_reads = True

    admitted = await asyncio.gather(
        breaker.LLMCircuitBreaker.is_available("ollama", None),
        breaker.LLMCircuitBreaker.is_available("ollama", None),
    )
    assert sorted(admitted) == [False, True]


@pytest.mark.asyncio
async def test_non_availability_errors_do_not_trip_the_breaker(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr(breaker, "get_redis", lambda: redis)

    for _ in range(breaker.FAILURE_THRESHOLD * 2):
        opened = await breaker.LLMCircuitBreaker.record_failure(
            "openai", None, ValueError("invalid response schema")
        )
        assert opened is False

    assert await breaker.LLMCircuitBreaker.is_available("openai", None)


@pytest.mark.asyncio
async def test_redis_failure_does_not_hide_a_healthy_provider(monkeypatch):
    def _broken_redis():
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(breaker, "get_redis", _broken_redis)

    assert await breaker.LLMCircuitBreaker.is_available("ollama", None)


@pytest.mark.asyncio
async def test_budgeted_llm_refuses_open_circuit_before_provider_call(monkeypatch):
    scope = breaker.scope_for("ollama", "http://model-a:11434")
    unavailable = AsyncMock(
        side_effect=breaker.CircuitBreakerOpen(scope, retry_after_seconds=90)
    )
    monkeypatch.setattr(breaker, "require_available", unavailable)
    inner = _Inner()
    llm = BudgetedLLM(
        inner,  # type: ignore[arg-type]
        provider="ollama",
        model="small",
        base_url="http://model-a:11434",
    )

    with pytest.raises(breaker.CircuitBreakerOpen):
        await llm.ainvoke("prompt")

    unavailable.assert_awaited_once_with("ollama", "http://model-a:11434")
    assert inner.calls == 0


@pytest.mark.asyncio
async def test_budgeted_llm_records_success_and_unavailability_by_endpoint(
    monkeypatch,
):
    from app.services import llm_cluster_semaphore, llm_cost_reservation

    available = AsyncMock(return_value=None)
    success = AsyncMock()
    failure = AsyncMock(return_value=False)
    monkeypatch.setattr(breaker, "require_available", available)
    monkeypatch.setattr(breaker.LLMCircuitBreaker, "record_success", success)
    monkeypatch.setattr(breaker.LLMCircuitBreaker, "record_failure", failure)
    monkeypatch.setattr(llm_cost_reservation, "reserve", AsyncMock(return_value=None))
    monkeypatch.setattr(llm_cost_reservation, "settle", AsyncMock())
    monkeypatch.setattr(
        llm_cluster_semaphore, "cluster_llm_slot", lambda provider: _Slot()
    )
    endpoint = "http://model-a:11434"

    healthy = BudgetedLLM(
        _Inner(),  # type: ignore[arg-type]
        provider="ollama",
        model="small",
        base_url=endpoint,
    )
    assert await healthy.ainvoke("prompt") == "provider result"
    success.assert_awaited_once_with("ollama", endpoint)

    unavailable = BudgetedLLM(
        _FailingInner(),  # type: ignore[arg-type]
        provider="ollama",
        model="small",
        base_url=endpoint,
    )
    with pytest.raises(TimeoutError):
        await unavailable.ainvoke("prompt")
    failure.assert_awaited_once()
    assert failure.await_args.args[:2] == ("ollama", endpoint)
    assert isinstance(failure.await_args.args[2], TimeoutError)


@pytest.mark.asyncio
async def test_open_circuit_raises_a_stable_fallback_reason(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr(breaker, "get_redis", lambda: redis)
    for _ in range(breaker.FAILURE_THRESHOLD):
        await breaker.LLMCircuitBreaker.record_failure(
            "ollama", None, TimeoutError("down")
        )

    with pytest.raises(breaker.CircuitBreakerOpen) as exc_info:
        await breaker.require_available("ollama", None)

    assert exc_info.value.provider == "ollama"
    assert exc_info.value.scope_id == breaker.scope_for("ollama", None).scope_id
    assert "circuit is open" in str(exc_info.value)
