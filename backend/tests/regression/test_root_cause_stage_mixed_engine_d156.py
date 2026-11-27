"""D-156: the Root Cause stage says "mixed" when its tests ran on more than one engine.

The defect
----------
``AnalysisAgent.run`` summarised the stage with
``dominant_mode = max(mode_counts, key=mode_counts.get)`` and stored that one
engine as the stage's ``analysis_mode``. Three rules answers and one model
answer therefore read "rules" on the Deep Investigation page's Model routing
card; the full split sat in ``result_data.mode_distribution`` and was never
shown.

What is guarded
---------------
* more than one recorded engine -> ``analysis_mode == "mixed"``, with the split
  kept in ``result_data.mode_distribution``;
* one engine keeps today's value (that engine), and a test whose engine was
  not recorded ("unknown") is not a second engine;
* the decision trail exposes each stage's ``mode_distribution`` so the page
  can print the split.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("asyncpg")  # skip module if the DB driver is absent

from app.agents.analysis_agent import AnalysisAgent  # noqa: E402
from app.services.decision_trail_service import _summarize_stage  # noqa: E402


def _build_agent(outcomes: dict[str, dict]) -> AnalysisAgent:
    """An AnalysisAgent whose per-test step returns ``outcomes`` verbatim."""
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
        return dict(outcomes[tc_id])

    agent._analyse_with_retry = _fake_analyse  # type: ignore[method-assign]
    return agent


def _state(tc_ids) -> dict:
    # A non-UUID project id keeps the LLM cost-budget check hermetic.
    return {
        "pipeline_run_id": "pipe-d156",
        "project_id": "proj-d156",
        "failed_test_ids": list(tc_ids),
    }


def _analysis(mode: str | None, **audit) -> dict:
    out: dict = {"confidence_score": 80, "failure_category": "PRODUCT_BUG"}
    if mode is not None:
        out["_audit"] = {"analysis_mode": mode, **audit}
    return out


@pytest.mark.asyncio
async def test_one_model_and_three_rules_answers_store_mixed():
    outcomes = {
        "tc-1": _analysis("llm"),
        "tc-2": _analysis("rules", fallback_from="llm"),
        "tc-3": _analysis("rules", fallback_from="llm"),
        "tc-4": _analysis("rules", fallback_from="llm"),
    }
    agent = _build_agent(outcomes)

    await agent.run(_state(outcomes))

    kwargs = agent.mark_stage_done.call_args.kwargs
    assert kwargs["analysis_mode"] == "mixed"
    assert kwargs["result_data"]["mode_distribution"] == {"llm": 1, "rules": 3}
    assert kwargs["fallback_reason"] == "3/4 tests fell back from requested engine"


@pytest.mark.asyncio
async def test_one_engine_keeps_that_engine():
    outcomes = {"tc-1": _analysis("rules"), "tc-2": _analysis("rules")}
    agent = _build_agent(outcomes)

    await agent.run(_state(outcomes))

    kwargs = agent.mark_stage_done.call_args.kwargs
    assert kwargs["analysis_mode"] == "rules"
    assert kwargs["result_data"]["mode_distribution"] == {"rules": 2}


@pytest.mark.asyncio
async def test_an_unrecorded_engine_is_not_a_second_engine():
    outcomes = {
        "tc-1": _analysis("llm"),
        "tc-2": _analysis("llm"),
        "tc-3": _analysis(None),
    }
    agent = _build_agent(outcomes)

    await agent.run(_state(outcomes))

    kwargs = agent.mark_stage_done.call_args.kwargs
    assert kwargs["analysis_mode"] == "llm"
    assert kwargs["result_data"]["mode_distribution"] == {"llm": 2, "unknown": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("non_engine", ["hint", "human_corrected"])
async def test_a_hint_or_a_human_correction_is_not_a_second_engine(non_engine):
    """Review F6: a rules-only run with a hint or a replayed correction is not "mixed"."""
    outcomes = {
        "tc-1": _analysis("rules"),
        "tc-2": _analysis("rules"),
        "tc-3": _analysis(non_engine),
    }
    agent = _build_agent(outcomes)

    await agent.run(_state(outcomes))

    kwargs = agent.mark_stage_done.call_args.kwargs
    assert kwargs["analysis_mode"] == "rules"
    assert kwargs["result_data"]["mode_distribution"] == {"rules": 2, non_engine: 1}


@pytest.mark.asyncio
async def test_model_and_rules_with_a_correction_is_still_mixed():
    outcomes = {
        "tc-1": _analysis("llm"),
        "tc-2": _analysis("rules"),
        "tc-3": _analysis("human_corrected"),
    }
    agent = _build_agent(outcomes)

    await agent.run(_state(outcomes))

    assert agent.mark_stage_done.call_args.kwargs["analysis_mode"] == "mixed"


def _stage(**overrides) -> SimpleNamespace:
    base = dict(
        stage_name="root_cause_analysis", status="completed", started_at=None,
        completed_at=None, analysis_mode="mixed", fallback_used=True,
        fallback_reason="3/4 tests fell back from requested engine",
        route_rationale=None, error_category=None, skipped_reason=None,
        execution_path=None, confidence_score=None, evidence_count=None,
        input_tokens=None, output_tokens=None, cost_usd=None, decision_log=None,
        result_data={"analysed": 4, "mode_distribution": {"llm": 1, "rules": 3}},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_the_decision_trail_exposes_each_stages_split():
    summary = _summarize_stage(_stage())  # type: ignore[arg-type]
    assert summary["analysis_mode"] == "mixed"
    assert summary["mode_distribution"] == {"llm": 1, "rules": 3}


def test_a_stage_without_a_split_exposes_none():
    assert _summarize_stage(_stage(result_data=None))["mode_distribution"] is None  # type: ignore[arg-type]
    junk = _stage(result_data={"mode_distribution": "junk"})
    assert _summarize_stage(junk)["mode_distribution"] is None  # type: ignore[arg-type]
