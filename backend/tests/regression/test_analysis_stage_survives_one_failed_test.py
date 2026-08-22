"""Regression guard: one failed per-test task must not kill the analysis stage.

The defect
----------
``AnalysisAgent.run`` fans out per-test analyses with
``asyncio.gather(..., return_exceptions=True)``, so a task that raises comes
back in ``results_list`` as the exception object rather than a dict. The
aggregation loop handled that correctly for two lines -- it recorded the error
and synthesised a placeholder analysis -- and then did this::

    if isinstance(analyses.get(tc_id), dict):     # always True by now
        ...
        if result.get("timed_out"):               # `result` IS the exception

The guard interrogated ``analyses[tc_id]`` -- the dict the exception branch had
just written, so always a dict -- while the calls beneath it read ``result``,
which on that branch is the ``BaseException``. ``result.get`` raised
``AttributeError``, which escaped ``run()`` entirely. One test whose task
failed therefore took the whole ``root_cause_analysis`` stage down with it and
discarded every sibling analysis that had already succeeded.

The trigger is ordinary, not exotic. ``_analyse_one`` awaits ``log_decision``
(a Mongo write) and ``store_artifact`` outside its own ``try/except``, so a
Mongo or object-store blip mid-run is enough to raise out of the task.

Why it survived
---------------
``test_analysis_agent.py::TestAnalysisAgentRun::test_run_handles_all_exceptions``
makes ``run_triage_agent`` raise -- but ``_analyse_one`` catches that internally
and returns an error *dict*, so the exception never reached the branch that
crashed. The test named for the behaviour never exercised it.

What is guarded
---------------
* a raising per-test task degrades to one recorded error while every sibling
  analysis survives, named and intact;
* the per-test counters come from the STORED analysis, so an errored test
  counts as low-confidence but never as a timeout or a retry, and the
  ``errors`` list (tasks that raised) stays distinct from ``error_count``
  (raised + timed out), which is what drives ``stage_quality``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("asyncpg")  # skip module if the DB driver is absent

from app.agents.analysis_agent import AnalysisAgent  # noqa: E402


def _build_agent(outcomes: dict[str, object]) -> AnalysisAgent:
    """An AnalysisAgent whose per-test step yields ``outcomes`` verbatim.

    A value that is a ``BaseException`` is raised by the task; anything else is
    returned as the analysis dict. Only the stage plumbing is mocked -- the
    ``asyncio.gather`` fan-out and the aggregation loop under test both run for
    real, which is the whole point of this guard.
    """
    agent = AnalysisAgent()
    agent.mark_stage_running = AsyncMock()  # type: ignore[method-assign]
    agent.mark_stage_done = AsyncMock()  # type: ignore[method-assign]
    agent.broadcast_progress = AsyncMock()  # type: ignore[method-assign]
    agent.log_decision = AsyncMock()  # type: ignore[method-assign]
    agent._fetch_test_metadata = AsyncMock(  # type: ignore[method-assign]
        return_value={
            tc_id: {"test_name": f"name::{tc_id}", "suite_name": "checkout-suite"}
            for tc_id in outcomes
        }
    )
    agent._fetch_human_corrections = AsyncMock(return_value={})  # type: ignore[method-assign]
    agent._batch_upsert_analyses = AsyncMock()  # type: ignore[method-assign]
    agent._resolve_adaptive_concurrency = AsyncMock(  # type: ignore[method-assign]
        return_value={"concurrency": 2, "rationale": "pinned for the test"}
    )

    async def _fake_analyse(_semaphore, tc_id, _meta, _state):
        outcome = outcomes[tc_id]
        if isinstance(outcome, BaseException):
            raise outcome
        return dict(outcome)  # type: ignore[arg-type]

    agent._analyse_with_retry = _fake_analyse  # type: ignore[method-assign]
    return agent


def _state(tc_ids) -> dict:
    # A non-UUID project id keeps the LLM cost-budget check hermetic: it
    # returns its no-op decision without reaching Postgres.
    return {
        "pipeline_run_id": "pipe-regression",
        "project_id": "proj-regression",
        "failed_test_ids": list(tc_ids),
    }


@pytest.mark.asyncio
async def test_one_raising_analysis_does_not_fail_the_stage():
    """The stage completes and the siblings of a failed task survive."""
    outcomes = {
        "tc-before": {"confidence_score": 91, "failure_category": "PRODUCT_BUG"},
        "tc-boom": RuntimeError("pipeline event log write failed"),
        "tc-after": {"confidence_score": 88, "failure_category": "INFRASTRUCTURE"},
    }
    agent = _build_agent(outcomes)

    # Before the fix this raised AttributeError: 'RuntimeError' object has no
    # attribute 'get' -- and took every sibling analysis with it.
    result = await agent.run(_state(outcomes))

    assert "root_cause_analysis" in result["completed_stages"]
    assert set(result["analyses"]) == {"tc-before", "tc-boom", "tc-after"}

    # The siblings are intact, and carry the identity the stage attaches.
    assert result["analyses"]["tc-before"]["failure_category"] == "PRODUCT_BUG"
    assert result["analyses"]["tc-after"]["failure_category"] == "INFRASTRUCTURE"
    assert result["analyses"]["tc-after"]["test_name"] == "name::tc-after"
    assert result["analyses"]["tc-after"]["suite_name"] == "checkout-suite"

    # The failure is recorded rather than swallowed -- and the placeholder is
    # a dict, so nothing downstream has to special-case it.
    failed = result["analyses"]["tc-boom"]
    assert failed["confidence_score"] == 0
    assert "pipeline event log write failed" in failed["error"]
    assert len(result["errors"]) == 1
    assert "tc-boom" in result["errors"][0]
    assert result["stage_errors"]["root_cause_analysis"] == result["errors"]


@pytest.mark.asyncio
async def test_counters_come_from_the_stored_analysis_not_the_raised_object():
    """An errored test is low-confidence; it is never a timeout or a retry."""
    outcomes = {
        "tc-ok": {"confidence_score": 90},
        "tc-timeout": {"confidence_score": 0, "timed_out": True},
        "tc-retried": {"confidence_score": 85, "retry_count": 1},
        "tc-boom": RuntimeError("object store unreachable"),
    }
    agent = _build_agent(outcomes)

    result = await agent.run(_state(outcomes))

    assert len(result["analyses"]) == 4

    result_data = agent.mark_stage_done.call_args.kwargs["result_data"]
    assert result_data["analysed"] == 4
    assert result_data["timed_out"] == 1        # the timeout only, not the raise
    assert result_data["retried"] == 1          # the retry only, not the raise
    assert result_data["low_confidence"] == 2   # the timeout AND the raise

    # ``errors`` counts tasks that RAISED; ``error_ratio`` also counts
    # timeouts, and it is the ratio that decides stage quality.
    assert result_data["errors"] == 1
    assert result_data["error_ratio"] == 0.5
    assert result_data["stage_quality"] == "degraded"
    assert result["stage_quality"] == "degraded"
    assert result["low_confidence_count"] == 2
