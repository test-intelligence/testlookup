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


class _Ledger(list):
    """reserve/settle events, plus whether each settle was sent to the meter."""

    def __init__(self):
        super().__init__()
        self.meter: list[bool] = []


@pytest.fixture
def ledger(monkeypatch):
    """Record reserve/settle without a store."""
    events = _Ledger()

    async def fake_reserve(provider, model, *, input_tokens, max_output_tokens):
        events.append(("reserve", provider, model, input_tokens, max_output_tokens))
        return Reservation(key="k", project_id=PROJECT, estimated_usd=0.5, ttl_seconds=60)

    async def fake_settle(reservation, actual, *, record_meter=False, **tokens):
        events.append(("settle", actual))
        events.meter.append(record_meter)

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


def _wrapped(outer: BaseException, cause: BaseException) -> BaseException:
    """What an SDK raises: its own error ``from`` the transport's."""
    outer.__cause__ = cause
    return outer


def _billable_failures() -> dict[str, BaseException]:
    import asyncio

    import httpx

    return {
        "provider 5xx": RuntimeError("provider 500"),
        "read timeout": httpx.ReadTimeout("read timed out"),
        "cancelled mid-call": asyncio.CancelledError(),
        "SDK error from a read error": _wrapped(RuntimeError("APIConnectionError"), httpx.ReadError("reset")),
        "timeout after a retried connect (context only)": _connect_then_read_timeout(),
    }


def _connect_then_read_timeout() -> BaseException:
    import httpx

    try:
        try:
            raise httpx.ConnectError("first attempt refused")
        except httpx.ConnectError:
            raise httpx.ReadTimeout("second attempt timed out")
    except httpx.ReadTimeout as exc:
        assert isinstance(exc.__context__, httpx.ConnectError) and exc.__cause__ is None
        return exc


def _no_request_failures() -> dict[str, BaseException]:
    import httpx

    from app.services.llm_egress import OffBoxTargetError

    return {
        "connect error": httpx.ConnectError("refused"),
        "connect timeout": httpx.ConnectTimeout("connect timed out"),
        "socket refused": ConnectionRefusedError("refused"),
        "offline pin": OffBoxTargetError("8.8.8.8 is off-box"),
        "SDK error from a connect error": _wrapped(RuntimeError("APIConnectionError"), httpx.ConnectError("x")),
        "cap refused": CostCapExceeded("over"),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(_billable_failures()))
async def test_a_call_that_may_have_reached_the_provider_keeps_its_whole_reservation(ledger, case):
    """QA-B45-A1: the provider may have done (and billed) the work."""
    inner = _Inner(fail=_billable_failures()[case])
    with cost_budget_scope(PROJECT):
        with pytest.raises(BaseException):
            await _llm(inner).ainvoke("prompt")
    assert inner.calls == 1
    assert ledger[-1] == ("settle", 0.5)
    assert ledger.meter[-1] is True  # and the durable meter records it


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(_no_request_failures()))
async def test_a_failure_that_proves_no_request_releases_the_reservation(ledger, case):
    inner = _Inner(fail=_no_request_failures()[case])
    with cost_budget_scope(PROJECT):
        with pytest.raises(BaseException):
            await _llm(inner).ainvoke("prompt")
    assert ledger[-1] == ("settle", 0.0)
    assert ledger.meter[-1] is False


@pytest.mark.asyncio
async def test_no_slot_in_time_charges_nothing(ledger, monkeypatch):
    import contextlib

    @contextlib.asynccontextmanager
    async def full(provider):
        raise llm_cluster_semaphore.LLMSlotTimeout("full")
        yield  # pragma: no cover

    monkeypatch.setattr(llm_cluster_semaphore, "cluster_llm_slot", full)
    inner = _Inner()
    with cost_budget_scope(PROJECT):
        with pytest.raises(llm_cluster_semaphore.LLMSlotTimeout):
            await _llm(inner).ainvoke("prompt")
    assert inner.calls == 0
    assert ledger[-1] == ("settle", 0.0)


@pytest.mark.asyncio
async def test_cancelled_while_waiting_for_a_slot_charges_nothing(ledger, monkeypatch):
    import asyncio
    import contextlib

    @contextlib.asynccontextmanager
    async def waiting(provider):
        await asyncio.sleep(3600)
        yield True  # pragma: no cover

    monkeypatch.setattr(llm_cluster_semaphore, "cluster_llm_slot", waiting)
    inner = _Inner()

    async def call():
        with cost_budget_scope(PROJECT):
            await _llm(inner).ainvoke("prompt")

    task = asyncio.create_task(call())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert inner.calls == 0
    assert ledger[-1] == ("settle", 0.0)


@pytest.mark.asyncio
async def test_a_success_outside_a_stage_is_metered_and_one_inside_a_stage_is_not(ledger):
    """A stage meters the calls it observed; anything else is metered at settle."""
    from app.services.pipeline_budget_service import (
        reset_pipeline_budget_context,
        set_pipeline_budget_context,
    )

    with cost_budget_scope(PROJECT):
        await _llm(_Inner()).ainvoke("prompt")
        token = set_pipeline_budget_context(stage_name="summary")
        try:
            await _llm(_Inner()).ainvoke("prompt")
        finally:
            reset_pipeline_budget_context(token)
    assert ledger.meter == [True, False]


@pytest.mark.asyncio
async def test_settle_writes_a_kept_charge_to_the_durable_meter(monkeypatch):
    import app.services.llm_cost_budget as budget

    recorded: list[tuple] = []

    async def fake_record(project_id, **kwargs):
        recorded.append((project_id, kwargs))

    class Redis:
        async def eval(self, *args):
            return "0"

    monkeypatch.setattr(budget, "record_usage", fake_record)
    monkeypatch.setattr(cost, "_redis", lambda: Redis())
    kept = Reservation(key="k", project_id=PROJECT, estimated_usd=0.5, ttl_seconds=60)
    await cost.settle(kept, 0.5, record_meter=True, input_tokens=7, output_tokens=3)
    released = Reservation(key="k", project_id=PROJECT, estimated_usd=0.5, ttl_seconds=60)
    await cost.settle(released, 0.0, record_meter=True)
    unmetered = Reservation(key="k", project_id=PROJECT, estimated_usd=0.5, ttl_seconds=60)
    await cost.settle(unmetered, 0.4, record_meter=False)
    assert recorded == [
        (PROJECT, {"cost_usd": 0.5, "input_tokens": 7, "output_tokens": 3, "llm_calls": 1}),
    ]


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
