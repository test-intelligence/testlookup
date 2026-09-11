"""b45 round-2 reviewer item 7: a successful call inside a pipeline stage
reaches the durable Postgres meter, not only the Redis counter.

BudgetedLLM leaves a successful call inside a stage for the stage to meter
(``record_meter = not metered_by_stage``). The stage meters in
``BaseAgent.mark_stage_done`` -- but only with a ``project_id``, and most
agents (anomaly, release risk, summary, regression watchman, ...) never pass
one. Their calls were reserved in Redis and never written to Postgres: an
under-count the ``max(redis, pg)`` seed hid until Redis was lost.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.agents import base
from app.agents.regression_watchman import RegressionWatchman
from app.services.llm_cost_reservation import cost_budget_scope
from app.services.pipeline_budget_service import (
    reset_pipeline_budget_context,
    set_pipeline_budget_context,
)


class _Stop(Exception):
    """The stage's DB bookkeeping is not under test."""


@pytest.fixture
def metered(monkeypatch):
    recorded: list[tuple] = []

    async def record_usage(project_id, **kwargs):
        recorded.append((project_id, kwargs))

    def no_db():
        raise _Stop()

    async def estimate(self, input_tokens, output_tokens):
        from types import SimpleNamespace

        return SimpleNamespace(cost_usd=(input_tokens + output_tokens) / 1_000_000, source="priced")

    monkeypatch.setattr("app.services.llm_cost_budget.record_usage", record_usage)
    monkeypatch.setattr(base, "emit_event", AsyncMock())
    monkeypatch.setattr(base, "AsyncSessionLocal", no_db)
    monkeypatch.setattr(base.BaseAgent, "_estimate_stage_cost", estimate)
    return recorded


async def _stage_done(agent, **kwargs) -> None:
    token = set_pipeline_budget_context(
        stage_name=agent.stage_name,
        observed_llm_calls=1, observed_input_tokens=3000, observed_output_tokens=1000,
    )
    try:
        with pytest.raises(_Stop):
            await agent.mark_stage_done(str(uuid.uuid4()), {"ok": True}, **kwargs)
    finally:
        reset_pipeline_budget_context(token)


@pytest.mark.asyncio
async def test_a_stage_that_passes_no_project_meters_to_the_scopes_project(metered):
    """RegressionWatchman.mark_stage_done passes no project_id (a real call site)."""
    project = str(uuid.uuid4())
    with cost_budget_scope(project):
        await _stage_done(RegressionWatchman())
    assert len(metered) == 1
    project_id, usage = metered[0]
    assert project_id == project
    assert usage["cost_usd"] == pytest.approx(0.004)
    assert usage["llm_calls"] == 1
    assert (usage["input_tokens"], usage["output_tokens"]) == (3000, 1000)


@pytest.mark.asyncio
async def test_an_explicit_project_wins_over_the_scope(metered):
    explicit, scope = str(uuid.uuid4()), str(uuid.uuid4())
    with cost_budget_scope(scope):
        await _stage_done(RegressionWatchman(), project_id=explicit)
    assert [p for p, _ in metered] == [explicit]


@pytest.mark.asyncio
async def test_no_project_anywhere_meters_nothing(metered):
    await _stage_done(RegressionWatchman())
    assert metered == []


def test_every_llm_stage_that_passes_no_project_runs_under_the_graph_scope():
    """The fallback is only as good as the scope: pin that the agents which
    make priced calls without passing project_id are graph stages, which run
    inside cost_budget_scope (test_llm_cost_scope_entry_points)."""
    from app.agents import workflow

    for name in ("_anomaly", "_summary", "_regression_watchman", "_release_risk"):
        assert isinstance(getattr(workflow, name), base.BaseAgent), name
