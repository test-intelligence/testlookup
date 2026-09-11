"""Re-audit M13 (unit): the cost reservation sits at the invocation boundary
and fails closed. The atomicity itself is proved against a real Redis in
tests/integration/test_llm_cost_cap_and_slots_redis_postgres.py.
"""
from __future__ import annotations

import uuid

import pytest

from app.services import llm_cluster_semaphore, llm_cost_reservation as cost
from app.services.llm_cost_reservation import CostCapExceeded, Reservation, cost_budget_scope
from app.services.llm_factory import BudgetedLLM

PROJECT = str(uuid.uuid4())


class _Result:
    def __init__(self, input_tokens=1000, output_tokens=500):
        self.usage_metadata = {"input_tokens": input_tokens, "output_tokens": output_tokens}


class _Inner:
    max_tokens = 2000

    def __init__(self, fail: BaseException | None = None):
        self.calls = 0
        self.fail = fail

    async def ainvoke(self, *args, **kwargs):
        self.calls += 1
        if self.fail:
            raise self.fail
        return _Result()

    def invoke(self, *args, **kwargs):
        self.calls += 1
        return _Result()


@pytest.fixture(autouse=True)
def no_cluster_bound(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)


@pytest.fixture
def ledger(monkeypatch):
    """Record reserve/settle without a store."""
    events: list[tuple] = []

    async def fake_reserve(provider, model, *, input_tokens, max_output_tokens):
        events.append(("reserve", provider, model, input_tokens, max_output_tokens))
        return Reservation(key="k", project_id=PROJECT, estimated_usd=0.5, ttl_seconds=60)

    async def fake_settle(reservation, actual):
        events.append(("settle", actual))

    monkeypatch.setattr(cost, "reserve", fake_reserve)
    monkeypatch.setattr(cost, "settle", fake_settle)
    return events


def _llm(inner, provider="openai", model="gpt-4o-mini"):
    return BudgetedLLM(inner, provider=provider, model=model)


# ── reserve(): what it holds, and when it refuses ───────────────────────────


@pytest.mark.asyncio
async def test_no_scope_reserves_nothing_and_touches_no_store(monkeypatch):
    async def boom():  # pragma: no cover - must not be reached
        raise AssertionError("store consulted without a project scope")

    monkeypatch.setattr(cost, "_cap_context", lambda pid: boom())
    assert await cost.reserve("openai", "gpt-4o-mini", input_tokens=100, max_output_tokens=100) is None


@pytest.mark.asyncio
async def test_a_self_hosted_model_reserves_nothing(monkeypatch):
    monkeypatch.setattr(cost, "_cap_context", lambda pid: (_ for _ in ()).throw(AssertionError("consulted")))
    with cost_budget_scope(PROJECT):
        assert await cost.reserve("ollama", "qwen2.5:7b", input_tokens=10_000, max_output_tokens=4000) is None


@pytest.mark.asyncio
async def test_an_unreachable_budget_store_refuses_the_call(monkeypatch):
    async def down(pid):
        raise ConnectionError("postgres down")

    monkeypatch.setattr(cost, "_cap_context", down)
    with cost_budget_scope(PROJECT):
        with pytest.raises(CostCapExceeded, match="budget store unavailable"):
            await cost.reserve("openai", "gpt-4o-mini", input_tokens=1000, max_output_tokens=1000)


@pytest.mark.asyncio
async def test_an_unreachable_reservation_store_refuses_the_call(monkeypatch):
    async def capped(pid):
        return 10.0, 1.0

    class DownRedis:
        async def eval(self, *args):
            raise ConnectionError("redis down")

    monkeypatch.setattr(cost, "_cap_context", capped)
    monkeypatch.setattr(cost, "_redis", lambda: DownRedis())
    with cost_budget_scope(PROJECT):
        with pytest.raises(CostCapExceeded, match="reservation store unavailable"):
            await cost.reserve("openai", "gpt-4o-mini", input_tokens=1000, max_output_tokens=1000)


@pytest.mark.asyncio
async def test_a_flag_store_outage_does_not_open_the_cap(monkeypatch):
    """The meter's own flag check reads an outage as "off"; this one must not."""
    import app.services.feature_flags as flags

    async def flag_down(key, **kwargs):
        raise ConnectionError("flag store down")

    monkeypatch.setattr(flags, "is_enabled", flag_down)
    with cost_budget_scope(PROJECT):
        with pytest.raises(CostCapExceeded):
            await cost.reserve("openai", "gpt-4o-mini", input_tokens=1000, max_output_tokens=1000)


@pytest.mark.asyncio
async def test_no_cap_configured_reserves_nothing(monkeypatch):
    async def uncapped(pid):
        return None

    monkeypatch.setattr(cost, "_cap_context", uncapped)
    with cost_budget_scope(PROJECT):
        assert await cost.reserve("openai", "gpt-4o-mini", input_tokens=1000, max_output_tokens=1000) is None


def test_the_estimate_is_the_worst_case_for_the_client_ceiling():
    per_call = cost.price("openai", "gpt-4o-mini", 1_000_000, 1_000_000)
    assert per_call == pytest.approx(0.15 + 0.60)
    assert cost.output_ceiling(_Inner(), 99) == 2000
    assert cost.output_ceiling(object(), 99) == 99


# ── BudgetedLLM: the reservation brackets the provider call ─────────────────


@pytest.mark.asyncio
async def test_a_refused_reservation_means_the_provider_is_never_called(monkeypatch):
    async def refuse(*args, **kwargs):
        raise CostCapExceeded("over")

    monkeypatch.setattr(cost, "reserve", refuse)
    inner = _Inner()
    with cost_budget_scope(PROJECT):
        with pytest.raises(CostCapExceeded):
            await _llm(inner).ainvoke("prompt")
    assert inner.calls == 0


@pytest.mark.asyncio
async def test_a_successful_call_settles_its_actual_cost(ledger):
    inner = _Inner()
    with cost_budget_scope(PROJECT):
        await _llm(inner).ainvoke("prompt")
    assert ledger[0][0] == "reserve" and ledger[0][4] == 2000
    assert ledger[1] == ("settle", pytest.approx(cost.price("openai", "gpt-4o-mini", 1000, 500)))


@pytest.mark.asyncio
async def test_a_failed_call_releases_its_reservation(ledger):
    inner = _Inner(fail=RuntimeError("provider 500"))
    with cost_budget_scope(PROJECT):
        with pytest.raises(RuntimeError):
            await _llm(inner).ainvoke("prompt")
    assert ledger[-1] == ("settle", 0.0)


@pytest.mark.asyncio
async def test_unreported_usage_keeps_the_worst_case(ledger):
    class NoUsage(_Inner):
        async def ainvoke(self, *args, **kwargs):
            self.calls += 1
            return "text without usage metadata"

    with cost_budget_scope(PROJECT):
        await _llm(NoUsage()).ainvoke("prompt")
    assert ledger[-1] == ("settle", 0.5)


def test_a_sync_priced_call_under_a_scope_is_refused():
    inner = _Inner()
    with cost_budget_scope(PROJECT):
        with pytest.raises(CostCapExceeded, match="use ainvoke"):
            _llm(inner).invoke("prompt")
    assert inner.calls == 0
    # Outside a scope, and for a self-hosted model, sync invoke still works.
    _llm(inner).invoke("prompt")
    with cost_budget_scope(PROJECT):
        _llm(inner, provider="ollama", model="qwen2.5:7b").invoke("prompt")
    assert inner.calls == 2


# ── the cluster slot brackets the call too (M12 wiring) ─────────────────────


@pytest.mark.asyncio
async def test_every_call_holds_a_cluster_slot_while_it_runs(monkeypatch):
    import contextlib

    held: list[str] = []
    seen_inside: list[bool] = []

    @contextlib.asynccontextmanager
    async def fake_slot(provider):
        held.append(provider)
        yield True
        held.remove(provider)

    class Watching(_Inner):
        async def ainvoke(self, *args, **kwargs):
            seen_inside.append(held == ["vllm"])
            return _Result()

    monkeypatch.setattr(llm_cluster_semaphore, "cluster_llm_slot", fake_slot)
    await _llm(Watching(), provider="vllm", model="m").ainvoke("prompt")
    assert seen_inside == [True]
    assert held == []


@pytest.mark.asyncio
async def test_an_unreachable_coordinator_degrades_to_the_local_bound():
    class DownRedis:
        async def eval(self, *args):
            raise ConnectionError("redis down")

    semaphore = llm_cluster_semaphore.ClusterSemaphore("t", 1, lease_seconds=5, redis=DownRedis())
    async with semaphore.slot(timeout=1) as held:
        assert held is False
