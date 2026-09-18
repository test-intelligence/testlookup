from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.agent_capability_registry import CAPABILITY_REGISTRY, DEFAULT_TIERS
from app.services.agent_config_resolver import ResolvedAgentConfig, ResolvedEndpoint
from app.services.agent_config_service import default_config
from app.services.analysis_router import classify_root_cause_tiered
from app.agents.analysis_agent import AnalysisAgent
from app.services.step_llm_budget import StepLLMBudget


def _endpoint(model: str) -> ResolvedEndpoint:
    return ResolvedEndpoint(
        provider="ollama",
        model=model,
        temperature=0.2,
        max_tokens=1024,
        source="project",
    )


def _resolved(*, tier: str = "auto") -> ResolvedAgentConfig:
    agent_id = CAPABILITY_REGISTRY["root_cause_analysis"].capability_id
    config = default_config(agent_id)
    config.model.tier = tier
    return ResolvedAgentConfig(
        agent_id=agent_id,
        source="default",
        config_version=0,
        config=config,
        endpoints={"slm": _endpoint("small"), "llm": _endpoint("large")},
        offline_mode=True,
        offline_mode_source="env",
        offline_mode_env_pinned=True,
    )


def _classification(confidence: int = 95) -> dict:
    return {
        "failure_category": "PRODUCT_BUG",
        "confidence_score": confidence,
        "root_cause_summary": "small model explanation",
        "recommended_actions": ["inspect code"],
        "evidence_references": [],
        "classified_by": "fast_classifier",
    }


def test_tiered_result_does_not_enter_the_legacy_retry_loop():
    result = {
        "confidence_score": 10,
        "root_cause_summary": "under-evidenced result",
        "_routing": {"execution_path": "tiered_root_cause"},
    }

    assert AnalysisAgent()._should_retry_analysis(
        result,
        {"error_message": "boom"},
    ) is False


@pytest.fixture
def tier_stubs(monkeypatch):
    from app.services.training.classifier import FastClassifier
    from app.services.model_registry import ModelRegistry
    import app.services.tier_comparison_service as tier_comparison

    classify = AsyncMock(return_value=(_classification(), "classified"))
    monkeypatch.setattr(FastClassifier, "classify_with_outcome", classify)
    monkeypatch.setattr(ModelRegistry, "get_active_model", AsyncMock(return_value=None))
    monkeypatch.setattr(tier_comparison, "enqueue_shadow_pair", lambda **_kwargs: None)
    return classify


@pytest.mark.asyncio
async def test_default_root_cause_uses_slm_without_llm_for_accepted_single_artifact(
    monkeypatch, tier_stubs
):
    import app.services.agent as agent_service

    react = AsyncMock()
    monkeypatch.setattr(agent_service, "run_triage_agent", react)

    result = await classify_root_cause_tiered(
        {"test_case_id": "tc-1", "test_name": "checkout", "error_message": "boom"},
        None,
        None,
        resolved=_resolved(),
        budget_remaining_usd=1.0,
        step_llm_calls_remaining=2,
    )

    assert DEFAULT_TIERS["root_cause_analysis"] == "slm"
    assert tier_stubs.await_args.kwargs["endpoint"].model == "small"
    assert tier_stubs.await_args.kwargs["accept_low_confidence"] is True
    react.assert_not_awaited()
    assert result["_routing"]["classification_tier"] == "slm"
    assert result["_routing"]["explanation_tier"] == "slm"
    assert result["_routing"]["escalations"] == 0


@pytest.mark.asyncio
async def test_reviewer_retry_override_runs_classifier_on_llm_without_reescalating(
    monkeypatch, tier_stubs
):
    import app.services.agent as agent_service

    tier_stubs.return_value = (_classification(30), "low_confidence")
    react = AsyncMock()
    monkeypatch.setattr(agent_service, "run_triage_agent", react)
    budget = StepLLMBudget(limit=2, used=1, reasons=["review_retry"])

    result = await classify_root_cause_tiered(
        {"test_case_id": "tc-retry", "test_name": "checkout", "error_message": "boom"},
        None,
        None,
        resolved=_resolved(tier="slm"),
        budget_remaining_usd=1.0,
        step_llm_calls_remaining=2,
        step_budget=budget,
        tier_override="llm",
    )

    assert tier_stubs.await_args.kwargs["endpoint"].model == "large"
    react.assert_not_awaited()
    assert result["_routing"]["classification_tier"] == "llm"
    assert result["_routing"]["tier_used"] == "llm"
    assert result["_routing"]["escalations"] == 0
    assert budget.as_state()["reasons"] == ["review_retry"]


@pytest.mark.asyncio
async def test_low_confidence_uses_llm_explanation_but_preserves_slm_verdict(
    monkeypatch, tier_stubs
):
    from app.core.metrics import model_escalations_total
    import app.services.agent as agent_service

    tier_stubs.return_value = (_classification(55), "low_confidence")
    react = AsyncMock(
        return_value={
            "failure_category": "INFRASTRUCTURE",
            "confidence_score": 99,
            "root_cause_summary": "large model explanation",
            "recommended_actions": ["inspect deployment"],
            "evidence_references": [{"type": "logs", "id": "log-1"}],
            "tools_used": ["query_logs"],
        }
    )
    monkeypatch.setattr(agent_service, "run_triage_agent", react)
    metric = model_escalations_total.labels(
        agent="root_cause_analysis", **{"from": "slm", "to": "llm"}
    )
    before = metric._value.get()

    result = await classify_root_cause_tiered(
        {"test_case_id": "tc-2", "test_name": "checkout", "error_message": "boom"},
        None,
        None,
        resolved=_resolved(),
        budget_remaining_usd=1.0,
        step_llm_calls_remaining=2,
    )

    assert result["failure_category"] == "PRODUCT_BUG"
    assert result["confidence_score"] == 55
    assert result["root_cause_summary"] == "large model explanation"
    assert result["_routing"]["escalation_trigger"] == "low_confidence"
    assert result["_routing"]["explanation_tier"] == "llm"
    assert result["_routing"]["escalations"] == 1
    assert metric._value.get() == before + 1
    assert react.await_args.kwargs["endpoint"].model == "large"
    assert react.await_args.kwargs["skip_fast_classifier"] is True
    assert react.await_args.kwargs["skip_cache"] is True


@pytest.mark.asyncio
async def test_multi_artifact_evidence_escalates_even_with_high_confidence(
    monkeypatch, tier_stubs
):
    import app.services.agent as agent_service

    react = AsyncMock(return_value={"root_cause_summary": "correlated explanation"})
    monkeypatch.setattr(agent_service, "run_triage_agent", react)

    result = await classify_root_cause_tiered(
        {
            "test_case_id": "tc-3",
            "test_name": "checkout",
            "error_message": "boom",
            "stack_trace": "trace",
        },
        None,
        None,
        resolved=_resolved(),
        budget_remaining_usd=1.0,
        step_llm_calls_remaining=2,
    )

    react.assert_awaited_once()
    assert result["_routing"]["escalation_trigger"] == "multi_artifact_evidence"
    assert result["_routing"]["artifact_types"] == ["error_message", "stack_trace"]


@pytest.mark.asyncio
async def test_explicit_slm_pin_refuses_explanation_escalation(
    monkeypatch, tier_stubs
):
    import app.services.agent as agent_service

    tier_stubs.return_value = (_classification(30), "low_confidence")
    react = AsyncMock()
    monkeypatch.setattr(agent_service, "run_triage_agent", react)

    result = await classify_root_cause_tiered(
        {"test_case_id": "tc-4", "test_name": "checkout", "error_message": "boom"},
        None,
        None,
        resolved=_resolved(tier="slm"),
        budget_remaining_usd=1.0,
        step_llm_calls_remaining=1,
    )

    react.assert_not_awaited()
    assert result["_routing"]["tier_used"] == "deterministic"
    assert result["_routing"]["fallback_used"] is True
    assert result["_routing"]["model_tier_reason"] == "tier_pinned"


@pytest.mark.asyncio
async def test_initial_slm_call_consumes_the_last_step_call_budget(
    monkeypatch, tier_stubs
):
    import app.services.agent as agent_service

    tier_stubs.return_value = (_classification(30), "low_confidence")
    react = AsyncMock()
    monkeypatch.setattr(agent_service, "run_triage_agent", react)

    result = await classify_root_cause_tiered(
        {"test_case_id": "tc-5", "test_name": "checkout", "error_message": "boom"},
        None,
        None,
        resolved=_resolved(),
        budget_remaining_usd=1.0,
        step_llm_calls_remaining=1,
    )

    react.assert_not_awaited()
    assert result["_routing"]["model_tier_reason"] == "step_llm_budget"
    assert result["_routing"]["fallback_used"] is True


@pytest.mark.asyncio
async def test_escalation_rechecks_cost_after_the_slm_call(monkeypatch, tier_stubs):
    import app.services.agent as agent_service

    tier_stubs.return_value = (_classification(30), "low_confidence")
    react = AsyncMock()
    monkeypatch.setattr(agent_service, "run_triage_agent", react)

    result = await classify_root_cause_tiered(
        {"test_case_id": "tc-6", "test_name": "checkout", "error_message": "boom"},
        None,
        None,
        resolved=_resolved(),
        budget_remaining_usd=CAPABILITY_REGISTRY[
            "root_cause_analysis"
        ].expected_cost_usd,
        step_llm_calls_remaining=2,
    )

    react.assert_not_awaited()
    assert result["_routing"]["model_tier_reason"] == "budget"
    assert result["_routing"]["fallback_used"] is True


@pytest.mark.asyncio
async def test_fast_classifier_builds_the_client_from_the_resolved_slm_endpoint(
    monkeypatch,
):
    import app.services.training.classifier as classifier_module
    from app.services.training.classifier import FastClassifier

    class Response:
        content = '{"category":"FLAKY","confidence":10,"reasoning":"intermittent"}'

    class LLM:
        async def ainvoke(self, _messages):
            return Response()

    get_llm = AsyncMock(return_value=LLM())
    monkeypatch.setattr(classifier_module, "get_llm", get_llm)
    monkeypatch.setattr(
        classifier_module.ModelRegistry,
        "get_active_model",
        AsyncMock(side_effect=AssertionError("endpoint must bypass global model lookup")),
    )

    result, outcome = await FastClassifier.classify_with_outcome(
        "checkout",
        "boom",
        endpoint=_endpoint("small"),
        accept_low_confidence=True,
    )

    assert outcome == "low_confidence"
    assert result is not None and result["confidence_score"] == 10
    selected = get_llm.await_args.kwargs["endpoint"]
    assert selected.model == "small"
    assert selected.temperature == 0.0


@pytest.mark.asyncio
async def test_react_explanation_uses_endpoint_and_bypasses_classifier_and_caches(
    monkeypatch,
):
    import app.services.agent as agent_service
    from app.services.training.classifier import FastClassifier

    sentinel = RuntimeError("stop after endpoint selection")
    get_llm = AsyncMock(side_effect=sentinel)
    cache_lookup = AsyncMock(side_effect=AssertionError("cache lookup must be bypassed"))
    classifier = AsyncMock(side_effect=AssertionError("classifier must be bypassed"))
    monkeypatch.setattr(agent_service, "get_llm", get_llm)
    monkeypatch.setattr(agent_service, "_check_analysis_cache", cache_lookup)
    monkeypatch.setattr(FastClassifier, "classify_with_outcome", classifier)

    with pytest.raises(RuntimeError, match="stop after endpoint selection"):
        await agent_service.run_triage_agent(
            test_case_id="tc-7",
            test_name="checkout",
            error_message="boom",
            project_id="00000000-0000-0000-0000-000000000001",
            endpoint=_endpoint("large"),
            skip_fast_classifier=True,
            skip_cache=True,
        )

    cache_lookup.assert_not_awaited()
    classifier.assert_not_awaited()
    assert get_llm.await_args.kwargs["endpoint"].model == "large"
