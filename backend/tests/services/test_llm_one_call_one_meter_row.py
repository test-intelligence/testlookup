"""R-B45-R3-2 / R-B45-R3-3: one LLM call is written to the Postgres meter once.

BudgetedLLM meters a call itself at settle when it failed after sending (the
investigator's own ``wait_for`` timeout) or succeeded with no reported usage.
The investigator agents then priced an ESTIMATE of the same call and
``mark_stage_done`` metered that too: two meter rows, two ``llm_calls``, for
one call. ``project_llm_usage`` seeds the cap's committed spend, so the next
calls were refused early.

Driven through the real ``InfraHypothesisAgent._weigh_with_llm`` and
``SynthesisAgent`` stage end, inside a real stage budget context and cost
scope, with a real BudgetedLLM; reserve/settle and the meter are recorded.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents import base
from app.services import llm_cost_reservation as cost
from app.services.llm_cost_reservation import Reservation, cost_budget_scope
from app.services.llm_factory import BudgetedLLM
from app.services.pipeline_budget_service import (
    reset_pipeline_budget_context,
    set_pipeline_budget_context,
)

PROJECT = str(uuid.uuid4())


class _Stop(Exception):
    """The stage's DB bookkeeping is not under test."""


@pytest.fixture
def world(monkeypatch):
    from app.core.config import settings

    meter: list[dict] = []   # every Postgres meter write, from either path
    settles: list[tuple] = []

    async def fake_reserve(provider, model, *, input_tokens, max_output_tokens):
        return Reservation(key="k", project_id=PROJECT, estimated_usd=0.5, ttl_seconds=60)

    async def fake_settle(reservation, actual, *, record_meter=False, input_tokens=0, output_tokens=0):
        settles.append((actual, record_meter))
        if record_meter and actual > 0:  # what the real settle does
            meter.append({"source": "settle", "cost_usd": actual, "llm_calls": 1})

    async def record_usage(project_id, **usage):
        meter.append({"source": "stage", **usage})

    async def estimate(self, input_tokens, output_tokens):
        return SimpleNamespace(cost_usd=(input_tokens + output_tokens) / 10_000, source="priced")

    def no_db():
        raise _Stop()

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(cost, "reserve", fake_reserve)
    monkeypatch.setattr(cost, "settle", fake_settle)
    monkeypatch.setattr("app.services.llm_cost_budget.record_usage", record_usage)
    monkeypatch.setattr(base, "emit_event", AsyncMock())
    monkeypatch.setattr(base, "AsyncSessionLocal", no_db)
    monkeypatch.setattr(base.BaseAgent, "_estimate_stage_cost", estimate)
    return SimpleNamespace(meter=meter, settles=settles)


class _Hangs:
    max_tokens = 100

    async def ainvoke(self, *args, **kwargs):
        await asyncio.sleep(3600)


class _NoUsage:
    max_tokens = 100

    async def ainvoke(self, *args, **kwargs):
        return SimpleNamespace(
            content='{"status": "validated", "confidence": 0.8, "summary": "s"}',
            usage_metadata=None, response_metadata={},
        )


class _Reported:
    max_tokens = 100

    async def ainvoke(self, *args, **kwargs):
        return SimpleNamespace(
            content='{"status": "validated", "confidence": 0.8, "summary": "s"}',
            usage_metadata={"input_tokens": 300, "output_tokens": 50}, response_metadata={},
        )


def _patch_investigator(monkeypatch, inner):
    from app.agents.investigator import hypotheses as hyp
    import app.services.llm_factory as factory

    async def reserve_investigation_budget(*args, **kwargs):
        return SimpleNamespace(allowed=True, reserved_tokens=2000, stop_reason=None)

    async def settle_investigation_budget(*args, **kwargs):
        return True

    async def not_cancelled(investigation_id):
        return False

    async def get_llm(*args, **kwargs):
        return BudgetedLLM(inner, provider="openai", model="gpt-4o-mini")

    monkeypatch.setattr(hyp, "reserve_investigation_budget", reserve_investigation_budget)
    monkeypatch.setattr(hyp, "settle_investigation_budget", settle_investigation_budget)
    monkeypatch.setattr(hyp, "is_cancel_requested", not_cancelled)
    monkeypatch.setattr(hyp, "_LLM_CALL_TIMEOUT_S", 0.05)
    monkeypatch.setattr(factory, "get_llm", get_llm)
    return hyp


_DET = {
    "status": "inconclusive", "confidence": 0.4, "summary": "det",
    "signals": {"x": 1},
    "evidence": [{"kind": "log", "label": "l", "detail": "d"}],
}


async def _hypothesis_stage(hyp) -> tuple:
    agent = hyp.InfraHypothesisAgent()
    state = {
        "investigation_id": str(uuid.uuid4()), "project_id": PROJECT,
        "deadline_ts": time.monotonic() + 120, "pipeline_run_id": str(uuid.uuid4()),
    }
    token = set_pipeline_budget_context(stage_name=agent.stage_name)
    try:
        with cost_budget_scope(PROJECT):
            weighed, usage, _ = await agent._weigh_with_llm(_DET, state)
            # what run() does next: price the usage and end the stage
            estimate = await agent._estimate_stage_cost(usage.input_tokens, usage.output_tokens)
            with pytest.raises(_Stop):
                await agent.mark_stage_done(
                    state["pipeline_run_id"], {"hypothesis": agent.hypothesis_id},
                    input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                    llm_calls_count=1, cost_usd=estimate.cost_usd if estimate else 0.0,
                    project_id=PROJECT,
                )
    finally:
        reset_pipeline_budget_context(token)
    return weighed, usage


@pytest.mark.asyncio
async def test_a_hypothesis_call_that_times_out_after_sending_is_metered_once(world, monkeypatch):
    hyp = _patch_investigator(monkeypatch, _Hangs())
    weighed, usage = await _hypothesis_stage(hyp)
    assert weighed is None and usage.source == "estimated"  # the agent still estimates for its own ledger
    assert world.settles == [(0.5, True)]
    assert world.meter == [{"source": "settle", "cost_usd": 0.5, "llm_calls": 1}]


@pytest.mark.asyncio
async def test_a_hypothesis_call_with_no_reported_usage_is_metered_once(world, monkeypatch):
    hyp = _patch_investigator(monkeypatch, _NoUsage())
    weighed, usage = await _hypothesis_stage(hyp)
    assert weighed is not None and usage.total_tokens > 0  # fallback-estimated tokens
    assert world.meter == [{"source": "settle", "cost_usd": 0.5, "llm_calls": 1}]
    # and the call is counted once (R-B45-R3-3)
    assert sum(row["llm_calls"] for row in world.meter) == 1


@pytest.mark.asyncio
async def test_a_hypothesis_call_with_reported_usage_is_metered_once_by_the_stage(world, monkeypatch):
    hyp = _patch_investigator(monkeypatch, _Reported())
    await _hypothesis_stage(hyp)
    assert world.settles == [(pytest.approx(cost.price("openai", "gpt-4o-mini", 300, 50)), False)]
    assert [row["source"] for row in world.meter] == ["stage"]
    assert world.meter[0]["llm_calls"] == 1


@pytest.mark.asyncio
async def test_a_stage_with_one_settled_and_one_reported_call_meters_only_the_reported_one(world):
    """The subtraction keeps what the stage alone saw."""
    from app.agents.regression_watchman import RegressionWatchman

    token = set_pipeline_budget_context(stage_name="regression_watchman")
    try:
        with cost_budget_scope(PROJECT):
            await BudgetedLLM(_NoUsage(), provider="openai", model="gpt-4o-mini").ainvoke("a")
            await BudgetedLLM(_Reported(), provider="openai", model="gpt-4o-mini").ainvoke("b")
            with pytest.raises(_Stop):
                await RegressionWatchman().mark_stage_done(str(uuid.uuid4()), {"ok": True})
    finally:
        reset_pipeline_budget_context(token)
    stage = [row for row in world.meter if row["source"] == "stage"]
    assert len(stage) == 1
    assert stage[0]["llm_calls"] == 1                      # 2 observed - 1 settled
    assert (stage[0]["input_tokens"], stage[0]["output_tokens"]) == (300, 50)
    assert stage[0]["cost_usd"] == pytest.approx(350 / 10_000)
    assert sum(row["llm_calls"] for row in world.meter) == 2
