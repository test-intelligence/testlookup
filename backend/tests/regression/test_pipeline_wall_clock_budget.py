"""Regression guard: an oversized run degrades instead of being retried whole.

The defect
----------
The agent pipeline had no wall-clock budget of its own. It runs as a Celery
task under a 1740s soft / 1800s hard limit, and the analysis stage alone can
outlast that: with a local provider it runs 3-wide at up to
``AI_TIMEOUT_SECONDS`` (300s) per test, so roughly 18 slow failures consume the
whole task.

What happened then was *not* a clean kill. ``SoftTimeLimitExceeded`` is an
ordinary ``Exception``, so ``run_agent_pipeline``'s handler caught it and
called ``self.retry`` -- re-running the entire pipeline, twice, before the DLQ.
One oversized run could therefore occupy ~87 minutes of ``ai_analysis``
capacity while other runs queued behind it, and the report that eventually
published said nothing about the two attempts that came before it.

What is guarded
---------------
* a stage whose deadline has passed is skipped rather than started, and the
  skip is recorded in ``stage_errors`` -- which is what
  ``build_decision_intelligence`` folds into
  ``missing_or_failed_specialists``, so the published report degrades itself
  and names the stage;
* terminal synthesis is exempt: ``decision_report`` and its critic still run
  past the deadline, because they are deterministic and they are what turns a
  truncated run into a report that describes its own gaps instead of silence;
* the analysis stage stops taking on NEW work at the deadline and still stores
  a zero-confidence record for every failed test it never reached, so the gap
  is visible rather than silently absent from the analysis set;
* a disabled budget (``0``) changes nothing.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("asyncpg")  # skip module if the DB driver is absent

from app.agents import workflow as wf  # noqa: E402
from app.agents.analysis_agent import AnalysisAgent  # noqa: E402
from app.models.enums import ExecutionPath  # noqa: E402

PAST = 1.0  # a monotonic instant that is always already behind us


@pytest.fixture
def quiet_workflow(monkeypatch):
    """Silence the workflow module's persistence and event side effects."""
    skips: list[dict] = []

    async def _record_skip(pipeline_run_id, stage_name, skipped_reason,
                           execution_path, stop_reason=None):
        skips.append({
            "stage_name": stage_name,
            "skipped_reason": skipped_reason,
            "execution_path": execution_path,
            "stop_reason": stop_reason,
        })

    monkeypatch.setattr(wf, "_write_stage_skipped", _record_skip)
    monkeypatch.setattr(wf, "emit_event", AsyncMock())
    monkeypatch.setattr(wf, "_checkpoint_stage", AsyncMock())
    monkeypatch.setattr(wf, "_planner_stage_selection", lambda _s, _n: (True, "selected"))
    return skips


# ── The between-stage guard ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stage_past_the_deadline_is_skipped_and_disclosed(quiet_workflow):
    ran: list[str] = []

    async def _node(_state):
        ran.append("summary")
        return {"completed_stages": ["summary"]}

    wrapped = wf._make_checkpointed_node(_node, "summary")
    delta = await wrapped({"pipeline_run_id": "pipe-1", "pipeline_deadline_ts": PAST})

    assert ran == [], "the stage must not start once the budget is gone"
    assert delta["skipped_stages"] == ["summary"]
    assert delta["execution_path"] == ExecutionPath.DEADLINE_SKIP
    assert delta["stage_quality"] == "degraded"

    # The disclosure hook: stage_errors is what reaches
    # missing_or_failed_specialists, and therefore the degraded report.
    assert "summary" in delta["stage_errors"]
    assert "budget" in delta["stage_errors"]["summary"][0].lower()

    assert quiet_workflow[0]["stop_reason"] == "pipeline_deadline_exceeded"
    assert quiet_workflow[0]["execution_path"] is ExecutionPath.DEADLINE_SKIP


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["decision_report", "decision_report_critic"])
async def test_terminal_synthesis_still_runs_past_the_deadline(quiet_workflow, stage):
    """Skipping these would trade a truncated report for no report at all."""
    ran: list[str] = []

    async def _node(_state):
        ran.append(stage)
        return {"completed_stages": [stage]}

    wrapped = wf._make_checkpointed_node(_node, stage)
    delta = await wrapped({"pipeline_run_id": "pipe-2", "pipeline_deadline_ts": PAST})

    assert ran == [stage]
    assert delta["completed_stages"] == [stage]
    assert "skipped_stages" not in delta
    assert quiet_workflow == []


@pytest.mark.asyncio
async def test_disabled_budget_never_skips(quiet_workflow):
    ran: list[str] = []

    async def _node(_state):
        ran.append("summary")
        return {"completed_stages": ["summary"]}

    wrapped = wf._make_checkpointed_node(_node, "summary")
    delta = await wrapped({"pipeline_run_id": "pipe-3", "pipeline_deadline_ts": 0.0})

    assert ran == ["summary"]
    assert delta["completed_stages"] == ["summary"]
    assert quiet_workflow == []


def test_deadline_is_computed_from_the_configured_budget(monkeypatch):
    monkeypatch.setattr(wf.settings, "AI_PIPELINE_DEADLINE_SECONDS", 0)
    assert wf._pipeline_deadline() == 0.0, "0 must mean unbounded, not 'already expired'"

    monkeypatch.setattr(wf.settings, "AI_PIPELINE_DEADLINE_SECONDS", 900)
    assert wf._pipeline_deadline() > 0


# ── The per-test guard inside the analysis stage ─────────────────────────────


def _analysis_agent(tc_ids: list[str]) -> AnalysisAgent:
    agent = AnalysisAgent()
    agent.mark_stage_running = AsyncMock()  # type: ignore[method-assign]
    agent.mark_stage_done = AsyncMock()  # type: ignore[method-assign]
    agent.broadcast_progress = AsyncMock()  # type: ignore[method-assign]
    agent.log_decision = AsyncMock()  # type: ignore[method-assign]
    agent._fetch_test_metadata = AsyncMock(  # type: ignore[method-assign]
        return_value={tc: {"test_name": f"name::{tc}"} for tc in tc_ids}
    )
    agent._fetch_human_corrections = AsyncMock(return_value={})  # type: ignore[method-assign]
    agent._batch_upsert_analyses = AsyncMock()  # type: ignore[method-assign]
    agent._resolve_adaptive_concurrency = AsyncMock(  # type: ignore[method-assign]
        return_value={"concurrency": 1, "rationale": "pinned for the test"}
    )

    async def _analyse(_semaphore, tc_id, _meta, _state):
        return {"confidence_score": 90, "failure_category": "PRODUCT_BUG"}

    agent._analyse_with_retry = _analyse  # type: ignore[method-assign]
    return agent


@pytest.mark.asyncio
async def test_analysis_stops_at_the_deadline_and_records_what_it_missed():
    tc_ids = ["tc-1", "tc-2", "tc-3", "tc-4"]
    agent = _analysis_agent(tc_ids)

    result = await agent.run({
        "pipeline_run_id": "pipe-4",
        "project_id": "proj-regression",   # non-UUID keeps the cost cap hermetic
        "failed_test_ids": tc_ids,
        "pipeline_deadline_ts": PAST,      # already gone before the first batch
    })

    # Every failed test still has a record -- none are silently dropped.
    assert set(result["analyses"]) == set(tc_ids)
    for tc_id in tc_ids:
        assert result["analyses"][tc_id]["budget_exhausted"] is True
        assert result["analyses"][tc_id]["confidence_score"] == 0

    # And the stage says so, in the channel the decision report reads.
    assert result["stage_quality"] == "degraded"
    assert any("not" in err and "analysed" in err for err in result["stage_errors"]["root_cause_analysis"])

    result_data = agent.mark_stage_done.call_args.kwargs["result_data"]
    assert result_data["budget_skipped"] == 4


@pytest.mark.asyncio
async def test_analysis_with_budget_remaining_analyses_everything():
    import time as _time

    tc_ids = ["tc-1", "tc-2", "tc-3"]
    agent = _analysis_agent(tc_ids)

    result = await agent.run({
        "pipeline_run_id": "pipe-5",
        "project_id": "proj-regression",
        "failed_test_ids": tc_ids,
        "pipeline_deadline_ts": _time.monotonic() + 300,
    })

    assert set(result["analyses"]) == set(tc_ids)
    for tc_id in tc_ids:
        assert "budget_exhausted" not in result["analyses"][tc_id]
        assert result["analyses"][tc_id]["failure_category"] == "PRODUCT_BUG"
    assert result["stage_quality"] == "normal"
    assert agent.mark_stage_done.call_args.kwargs["result_data"]["budget_skipped"] == 0
