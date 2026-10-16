"""FAIL-10: a project classification hint beats every engine; a human correction beats the hint.

Precedence is correction > hint > engines (rules, ML, LLM). These tests drive
the REAL ``BudgetedLLM`` wrapper around a counting fake provider (never a
mocked ``get_llm`` handing back a raw model): when a hint matches, the
provider is never called.

* ``classify_root_cause_tiered`` (the pipeline's tiered LLM path and the
  router's other callers): the fast classifier's ``get_llm`` returns the real
  wrapper; a matching hint answers with no provider call, no hint calls it.
* ``AnalysisAgent`` end to end over ``run()`` (a UUID project, so the tiered
  path, whose fast classifier calls the real wrapper; only the tier-policy
  read is stubbed): a matching hint short-circuits it, logs
  ``apply_project_hint`` with the decision-trail line, and the persisted
  ``_audit`` (``AIAnalysis.routing_metadata``) carries the ``hint_*`` keys; a
  human correction of the same test wins over the hint; a disabled hint and a
  non-matching test reach the model as before.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.core.config import settings
from app.services import llm_circuit_breaker
from app.services import llm_cost_reservation as cost
from app.services.llm_factory import BudgetedLLM

HINT = {
    "id": "hint-1", "project_id": "p", "match_field": "error_message", "pattern": "ECONNREFUSED",
    "category": "INFRASTRUCTURE", "note": None, "enabled": True,
    "created_by_name": "Priya Nair", "created_at": "2026-10-01T00:00:00+00:00",
}
RATIONALE = 'Matched project hint (set by Priya Nair): error message contains "ECONNREFUSED" → Infrastructure'


class _CountingModel(FakeListChatModel):
    """The fake provider: counts every call that reaches it."""

    calls: int = 0

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        content = '{"category": "PRODUCT_BUG", "confidence": 95, "reasoning": "The model read the refund state machine."}'
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])


def _wrapped() -> tuple[BudgetedLLM, _CountingModel]:
    inner = _CountingModel(responses=["unused"])
    return BudgetedLLM(inner, provider="openrouter", model="scripted"), inner


@pytest.fixture
def gates(monkeypatch):
    """The wrapper's stores (breaker, cost reservation, cluster slots) are
    no-ops; the call path through the wrapper is the real one."""

    async def _none(*_a, **_k):
        return None

    def _refuse(*_a, **_k):
        raise RuntimeError("no external store in this test")

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", _none)
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(_none))
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(_none))
    monkeypatch.setattr(cost, "reserve", _none)
    monkeypatch.setattr(cost, "settle", _none)
    monkeypatch.setattr("app.db.redis_client.get_redis", _refuse)


# ── the tiered path (classify_root_cause_tiered) ────────────────────────────


@pytest.fixture
def tiered(monkeypatch, gates):
    import app.services.tier_comparison_service as tier_comparison
    import app.services.training.classifier as classifier_module
    from app.services.model_registry import ModelRegistry

    llm, inner = _wrapped()

    async def _get_llm(*_a, **_k):
        return llm

    monkeypatch.setattr(classifier_module, "get_llm", _get_llm)
    monkeypatch.setattr(ModelRegistry, "get_active_model", AsyncMock(return_value=None))
    monkeypatch.setattr(tier_comparison, "enqueue_shadow_pair", lambda **_k: None)
    return inner


def _resolved():
    from app.services.agent_capability_registry import CAPABILITY_REGISTRY
    from app.services.agent_config_resolver import ResolvedAgentConfig, ResolvedEndpoint
    from app.services.agent_config_service import default_config

    def endpoint(model: str) -> ResolvedEndpoint:
        return ResolvedEndpoint(provider="ollama", model=model, temperature=0.2, max_tokens=1024, source="project")

    agent_id = CAPABILITY_REGISTRY["root_cause_analysis"].capability_id
    return ResolvedAgentConfig(
        agent_id=agent_id, source="default", config_version=0, config=default_config(agent_id),
        endpoints={"slm": endpoint("small"), "llm": endpoint("large")},
        offline_mode=True, offline_mode_source="env", offline_mode_env_pinned=True,
    )


async def _tiered(run_context):
    from app.services.analysis_router import classify_root_cause_tiered

    return await classify_root_cause_tiered(
        {"test_case_id": "tc-1", "test_name": "test_pays", "error_message": "connect ECONNREFUSED sandbox"},
        None,
        run_context,
        resolved=_resolved(),
        budget_remaining_usd=1.0,
        step_llm_calls_remaining=2,
    )


@pytest.mark.asyncio
async def test_a_matching_hint_beats_the_llm_and_the_provider_is_never_called(tiered):
    result = await _tiered({"classification_hints": [dict(HINT)]})
    assert tiered.calls == 0
    assert result["failure_category"] == "INFRASTRUCTURE"
    assert result["_routing"]["mode_used"] == "hint"
    assert result["_routing"]["hint_pattern"] == "ECONNREFUSED"


@pytest.mark.asyncio
async def test_without_a_hint_the_same_test_reaches_the_provider(tiered):
    """Not vacuous: the identical call with no (or a disabled) hint calls the model."""
    await _tiered({"classification_hints": [dict(HINT, enabled=False)]})
    assert tiered.calls == 1
    await _tiered(None)
    assert tiered.calls == 2


# ── the pipeline (AnalysisAgent.run) ────────────────────────────────────────


@pytest.fixture
def stage(monkeypatch, tiered):
    """The pipeline's tiered LLM path (a UUID project): ``classify_root_cause_tiered``
    whose fast classifier calls the real wrapper (``tiered``); the tier policy
    read is the only stubbed lookup."""
    from app.agents.analysis_agent import AnalysisAgent

    inner = tiered

    async def _no_react(**_kwargs):
        raise AssertionError("the ReAct path is not the one under test")

    monkeypatch.setattr("app.agents.analysis_agent.run_triage_agent", _no_react)
    monkeypatch.setattr("app.agents.analysis_agent.store_artifact", AsyncMock(return_value=None))
    agent = AnalysisAgent()
    agent._routing_inputs = AsyncMock(return_value=(_resolved(), 1.0, 2))  # type: ignore[method-assign]
    agent.mark_stage_running = AsyncMock()  # type: ignore[method-assign]
    agent.mark_stage_done = AsyncMock()  # type: ignore[method-assign]
    agent.broadcast_progress = AsyncMock()  # type: ignore[method-assign]
    agent.log_decision = AsyncMock()  # type: ignore[method-assign]
    agent._fetch_human_corrections = AsyncMock(return_value={})  # type: ignore[method-assign]
    agent._fetch_classification_hints = AsyncMock(return_value=[dict(HINT)])  # type: ignore[method-assign]
    agent._resolve_adaptive_concurrency = AsyncMock(  # type: ignore[method-assign]
        return_value={"concurrency": 2, "rationale": "test"}
    )
    agent._batch_upsert_analyses = AsyncMock()  # type: ignore[method-assign]
    agent._record_latency_feedback = MagicMock()  # type: ignore[method-assign]
    agent._fetch_test_metadata = AsyncMock(return_value={  # type: ignore[method-assign]
        "tc-hinted": {"test_name": "test_pays", "severity": "NORMAL", "test_fingerprint": "fp-hinted",
                      "error_message": "requests: connect econnrefused payments-sandbox:443"},
        "tc-plain": {"test_name": "test_refund", "severity": "NORMAL", "test_fingerprint": "fp-plain",
                     "error_message": "AssertionError: refund status pending"},
    })
    return agent, inner


def _state() -> dict[str, Any]:
    from app.services.pipeline_ai_scope import build_ai_config_snapshot

    snapshot = build_ai_config_snapshot({"timeout_seconds": 300, "timeout_seconds_source": "ai_config"})
    return {
        "pipeline_run_id": "pipeline-fail10",
        "project_id": "11111111-2222-4333-8444-555555555555",
        "failed_test_ids": ["tc-hinted", "tc-plain"],
        "analysis_mode_requested": "llm",
        "analysis_mode_resolved": "llm",
        "analysis_mode_resolution": {"provider": "openrouter", "model": "scripted"},
        "_cost_budget_prechecked": True,
        "_cost_budget_action": "ALLOW",
        "ai_config_snapshot": snapshot,
        "confidence_threshold": 80,
        "llm_timeout_seconds": snapshot["llm_timeout_seconds"],
    }


def _decisions(agent, point: str) -> list[dict]:
    return [c.kwargs for c in agent.log_decision.await_args_list if c.kwargs.get("decision_point") == point]


@pytest.mark.asyncio
async def test_the_pipeline_applies_the_hint_before_the_model(stage):
    agent, inner = stage
    result = await agent.run(_state())

    hinted = result["analyses"]["tc-hinted"]
    assert hinted["failure_category"] == "INFRASTRUCTURE"
    assert hinted["classified_by"] == "project_hint"
    # A cited verdict: the confidence policy keeps the hint's 90 and the gate passes.
    assert hinted["confidence_score"] == 90 and hinted["requires_human_review"] is False
    audit = hinted["_audit"]  # persisted verbatim as AIAnalysis.routing_metadata
    assert audit["analysis_mode"] == "hint"
    assert audit["confidence_basis"] == "project_hint"
    assert (audit["hint_id"], audit["hint_match_field"], audit["hint_pattern"], audit["hint_set_by"]) == (
        "hint-1", "error_message", "ECONNREFUSED", "Priya Nair",
    )
    # Only the plain test reached the model.
    assert inner.calls == 1
    assert result["analyses"]["tc-plain"]["failure_category"] == "PRODUCT_BUG"
    assert "hint_id" not in result["analyses"]["tc-plain"]["_audit"]
    [decision] = _decisions(agent, "apply_project_hint")
    assert decision["chosen"] == "INFRASTRUCTURE"
    assert decision["rationale"] == RATIONALE
    assert decision["test_case_id"] == "tc-hinted"
    assert decision["context"] == {"hint_id": "hint-1"}
    agent._fetch_classification_hints.assert_awaited_once_with(_state()["project_id"])


@pytest.mark.asyncio
async def test_a_human_correction_of_the_same_test_beats_the_hint(stage):
    agent, inner = stage
    agent._fetch_human_corrections = AsyncMock(return_value={  # type: ignore[method-assign]
        "fp-hinted": {"corrected_category": "TEST_DATA", "corrected_root_cause": "fixture expired",
                      "feedback_id": "fb-1"},
    })
    result = await agent.run(_state())

    hinted = result["analyses"]["tc-hinted"]
    assert hinted["failure_category"] == "TEST_DATA"
    assert hinted["classified_by"] == "human_correction"
    assert "hint_id" not in hinted["_audit"]
    assert _decisions(agent, "apply_project_hint") == []
    assert [d["test_case_id"] for d in _decisions(agent, "apply_human_correction")] == ["tc-hinted"]
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_a_disabled_hint_lets_the_model_answer(stage):
    agent, inner = stage
    agent._fetch_classification_hints = AsyncMock(return_value=[dict(HINT, enabled=False)])  # type: ignore[method-assign]
    result = await agent.run(_state())

    assert inner.calls == 2
    assert result["analyses"]["tc-hinted"]["failure_category"] == "PRODUCT_BUG"
    assert _decisions(agent, "apply_project_hint") == []


@pytest.mark.asyncio
async def test_no_real_project_loads_no_hints_and_opens_no_session(monkeypatch):
    from app.agents.analysis_agent import AnalysisAgent

    def _no_session(*_a, **_k):
        raise AssertionError("a session was opened for a project id that is not a project")

    monkeypatch.setattr("app.agents.analysis_agent.AsyncSessionLocal", _no_session)
    agent = AnalysisAgent()
    assert await agent._fetch_classification_hints(None) == []
    assert await agent._fetch_classification_hints("symbolic-project") == []
