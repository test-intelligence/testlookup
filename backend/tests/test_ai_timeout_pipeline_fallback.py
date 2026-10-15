"""AIC-02 (G-B half): the root-cause stage obeys the timeout the pipeline froze.

Before W04 the per-test ``asyncio.wait_for`` read ``settings.AI_TIMEOUT_SECONDS``
(the env value, 300 s) whatever Settings > AI said, and a timeout degraded
through the progressive fallback with its reason only on the analysis dict
(``fallback_reason="timeout_*"``), not in ``_routing``. So ``_audit.fallback_from``
stayed null, the ``analysis_engine_fallback`` decision never fired and the
stage's ``fallback_count`` read 0 for a stage that had fallen back on every test.

Here the stage runs over a REAL ``BudgetedLLM`` whose inner fake model answers
after 10 virtual seconds (``tests/virtual_clock``: the loop's clock, never the
wall clock), and the pipeline state carries the frozen timeout:

* 5 s frozen: every test times out at virtual t=5 (not 10, not 300), degrades
  through the progressive fallback, and ``_audit`` (persisted verbatim as
  ``AIAnalysis.routing_metadata``) says ``fallback_from="llm"`` /
  ``fallback_reason="llm_timeout: LLM timed out after 5 s"``; the decision log
  carries that rationale; ``fallback_count == n``;
* 300 s frozen: the same calls complete at t=10 and nothing falls back;
* an explicit project override (3 s) under the 5 s global wins.

The breaker / cost reservation / cluster slot gates are no-ops (they need
Redis and have their own tests); the timed call path is the real one.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agents.analysis_agent import AnalysisAgent
from app.core.config import settings
from app.services import llm_circuit_breaker
from app.services import llm_cost_reservation as cost
from app.services.llm_factory import BudgetedLLM
from app.services.pipeline_ai_scope import build_ai_config_snapshot
from tests.virtual_clock import VirtualClockPolicy

RCA = "agent.root_cause_analysis.v1"


@pytest.fixture
def event_loop_policy():
    return VirtualClockPolicy()


class _SlowModel(FakeListChatModel):
    """Answers after ``delay`` virtual seconds, on the loop's clock (no thread pool)."""

    delay: float = 10.0
    calls: int = 0

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        await asyncio.sleep(self.delay)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])


@pytest.fixture
def gates(monkeypatch):
    async def _none(*_a, **_k):
        return None

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", _none)
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(_none))
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(_none))
    monkeypatch.setattr(cost, "reserve", _none)
    monkeypatch.setattr(cost, "settle", _none)


def _llm() -> tuple[BudgetedLLM, _SlowModel]:
    inner = _SlowModel(responses=["done"])
    return BudgetedLLM(inner, provider="openrouter", model="slow"), inner


@pytest.fixture
def stage(monkeypatch, gates):
    """An AnalysisAgent whose ReAct triage and fast classifier both call the
    slow real wrapper; DB/broadcast side effects recorded, nothing else faked."""
    llm, inner = _llm()
    fast_llm, fast_inner = _llm()
    loop_times: dict[str, list[float]] = {"triage_done": [], "fast_done": []}

    async def _triage(**kwargs):
        await llm.ainvoke(f"investigate {kwargs['test_case_id']}")
        loop_times["triage_done"].append(asyncio.get_running_loop().time())
        return {
            "root_cause_summary": "The checkout service rounds the tax twice on every order.",
            "failure_category": "PRODUCT_BUG",
            "confidence_score": 90,
            "evidence_references": [{"source": "stacktrace"}],
            "tools_used": ["fetch_stacktrace"],
            "_routing": {"mode_used": "llm", "mode_resolved": "llm", "fallback_from": None},
        }

    class _FastClassifier:
        @staticmethod
        async def classify(**_kwargs):
            await fast_llm.ainvoke("classify")
            loop_times["fast_done"].append(asyncio.get_running_loop().time())
            return None

    import app.services.training.classifier as fast_module

    monkeypatch.setattr("app.agents.analysis_agent.run_triage_agent", _triage)
    monkeypatch.setattr(fast_module, "FastClassifier", _FastClassifier)
    monkeypatch.setattr("app.agents.analysis_agent.store_artifact", AsyncMock(return_value=None))

    agent = AnalysisAgent()
    agent.mark_stage_running = AsyncMock()  # type: ignore[method-assign]
    agent.mark_stage_done = AsyncMock()  # type: ignore[method-assign]
    agent.broadcast_progress = AsyncMock()  # type: ignore[method-assign]
    agent.log_decision = AsyncMock()  # type: ignore[method-assign]
    agent._fetch_human_corrections = AsyncMock(return_value={})  # type: ignore[method-assign]
    agent._resolve_adaptive_concurrency = AsyncMock(  # type: ignore[method-assign]
        return_value={"concurrency": 3, "rationale": "test"}
    )
    agent._batch_upsert_analyses = AsyncMock()  # type: ignore[method-assign]
    agent._record_latency_feedback = MagicMock()  # type: ignore[method-assign]
    return agent, inner, fast_inner, loop_times


def _meta(ids: list[str]) -> dict[str, dict[str, Any]]:
    # "xyzzy" matches no heuristic pattern, so tier 2 declines and tier 3 answers.
    return {
        tc: {"test_name": f"test_{tc}", "severity": "NORMAL", "error_message": "xyzzy happened"}
        for tc in ids
    }


def _state(ids: list[str], *, timeout: int, overrides: dict[str, int] | None = None) -> dict:
    snapshot = build_ai_config_snapshot({"timeout_seconds": timeout, "timeout_seconds_source": "ai_config"})
    if overrides:
        snapshot["agent_timeout_overrides"] = dict(overrides)
    return {
        "pipeline_run_id": "pipeline-aic02",
        "project_id": "symbolic-project",  # symbolic: the legacy (non-tiered) LLM path
        "failed_test_ids": ids,
        "analysis_mode_requested": "llm",
        "analysis_mode_resolved": "llm",
        "analysis_mode_resolution": {"provider": "openrouter", "model": "slow"},
        "_cost_budget_prechecked": True,
        "_cost_budget_action": "ALLOW",
        "ai_config_snapshot": snapshot,
        "confidence_threshold": 80,
        "llm_timeout_seconds": snapshot["llm_timeout_seconds"],
    }


def _fallback_decisions(agent) -> list[dict]:
    return [
        call.kwargs
        for call in agent.log_decision.await_args_list
        if call.kwargs.get("decision_point") == "analysis_engine_fallback"
    ]


@pytest.mark.asyncio
async def test_a_five_second_freeze_times_out_at_five_and_is_recorded_as_a_fallback(stage):
    agent, inner, _fast_inner, times = stage
    ids = ["tc-1", "tc-2", "tc-3"]
    agent._fetch_test_metadata = AsyncMock(return_value=_meta(ids))  # type: ignore[method-assign]
    loop = asyncio.get_running_loop()

    result = await agent.run(_state(ids, timeout=5))

    # Every provider call was started and none answered: the 5 s bound fired
    # before the model's 10 s, then tier 1 got min(30, 5 // 2) = 2 s.
    assert inner.calls == 3
    assert times["triage_done"] == []
    assert loop.time() == pytest.approx(7.0)

    expected = "llm_timeout: LLM timed out after 5 s"
    for tc in ids:
        analysis = result["analyses"][tc]
        audit = analysis["_audit"]  # persisted verbatim as AIAnalysis.routing_metadata
        assert audit["fallback_from"] == "llm"
        assert audit["fallback_reason"] == expected
        assert audit["analysis_mode"] == "rules"   # tiers 2-3 are deterministic
        assert analysis["fallback_tier"] == 3
        assert analysis["fallback_reason"] == "timeout_generic"  # the dict's own reason stays
        assert "timed out after 5s" in analysis["root_cause_summary"]

    decisions = _fallback_decisions(agent)
    assert len(decisions) == 3
    assert {d["rationale"] for d in decisions} == {expected}
    assert {d["chosen"] for d in decisions} == {"rules"}
    assert {tuple(d["alternatives"]) for d in decisions} == {("llm",)}

    done = agent.mark_stage_done.await_args.kwargs
    assert done["result_data"]["fallback_count"] == 3
    assert done["fallback_reason"] == "3/3 tests fell back from requested engine"
    # The persisted payload is the one the stage handed to the upsert.
    persisted = agent._batch_upsert_analyses.await_args.args[0]
    assert {persisted[tc]["_audit"]["fallback_reason"] for tc in ids} == {expected}


@pytest.mark.asyncio
async def test_a_three_hundred_second_freeze_lets_the_same_calls_complete(stage):
    agent, inner, _fast, times = stage
    ids = ["tc-1", "tc-2"]
    agent._fetch_test_metadata = AsyncMock(return_value=_meta(ids))  # type: ignore[method-assign]

    result = await agent.run(_state(ids, timeout=300))

    assert inner.calls == 2
    assert times["triage_done"] == [pytest.approx(10.0)] * 2
    for tc in ids:
        audit = result["analyses"][tc]["_audit"]
        assert audit["fallback_from"] is None
        assert audit["analysis_mode"] == "llm"
    assert _fallback_decisions(agent) == []
    assert agent.mark_stage_done.await_args.kwargs["result_data"]["fallback_count"] == 0


@pytest.mark.asyncio
async def test_an_explicit_project_override_tightens_the_frozen_global(stage):
    """Global 5 s, the project's root-cause agent row says 3 s: 3 s wins."""
    agent, _inner, _fast, _times = stage
    ids = ["tc-1"]
    agent._fetch_test_metadata = AsyncMock(return_value=_meta(ids))  # type: ignore[method-assign]
    loop = asyncio.get_running_loop()

    result = await agent.run(_state(ids, timeout=5, overrides={RCA: 3}))

    audit = result["analyses"]["tc-1"]["_audit"]
    assert audit["fallback_reason"] == "llm_timeout: LLM timed out after 3 s"
    # 3 s, then tier 1 = min(30, 3 // 2) = 1 s.
    assert loop.time() == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_a_tier_one_answer_is_still_recorded_as_an_llm_engine(monkeypatch, stage):
    """Tier 1 is an LLM call: ``mode_used="llm"``; the fallback is still recorded."""
    agent, _inner, _fast, _times = stage
    ids = ["tc-1"]
    agent._fetch_test_metadata = AsyncMock(return_value=_meta(ids))  # type: ignore[method-assign]

    class _QuickClassifier:
        @staticmethod
        async def classify(**_kwargs):
            return {
                "root_cause_summary": "The checkout service rounds the tax twice on every order.",
                "failure_category": "PRODUCT_BUG",
                "confidence_score": 75,
                "evidence_references": [],
                "tools_used": [],
            }

    import app.services.training.classifier as fast_module

    monkeypatch.setattr(fast_module, "FastClassifier", _QuickClassifier)

    result = await agent.run(_state(ids, timeout=5))

    analysis = result["analyses"]["tc-1"]
    assert analysis["fallback_tier"] == 1
    assert analysis["_audit"]["analysis_mode"] == "llm"
    assert analysis["_audit"]["fallback_from"] == "llm"
    assert analysis["_audit"]["fallback_reason"] == "llm_timeout: LLM timed out after 5 s"
    assert [d["chosen"] for d in _fallback_decisions(agent)] == ["llm"]


@pytest.mark.asyncio
async def test_the_progressive_fallback_tier_one_budget_follows_the_timeout(stage):
    """``min(30, timeout // 2)``, at least 1 s; the generic message names the timeout."""
    agent, _inner, fast_inner, times = stage
    loop = asyncio.get_running_loop()

    started = loop.time()
    result = await agent._build_progressive_fallback("tc-1", {"error_message": "xyzzy"}, timeout=9)
    assert fast_inner.calls == 1
    assert loop.time() - started == pytest.approx(4.0)
    assert result["fallback_tier"] == 3
    assert "timed out after 9s" in result["root_cause_summary"]
    assert times["fast_done"] == []
