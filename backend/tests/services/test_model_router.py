from __future__ import annotations

import pytest

from app.services.agent_capability_registry import (
    CAPABILITY_REGISTRY,
    CLASSIFY_CAPABILITIES,
    DEFAULT_TIERS,
    ESCALATION_TRIGGERS,
    SYNC_ELIGIBLE,
)
from app.services.agent_config_resolver import ResolvedAgentConfig, ResolvedEndpoint
from app.services.agent_config_service import default_config
from app.services.model_router import ModelChoice, choose_model, decide_escalation, provenance


def _endpoint(model: str) -> ResolvedEndpoint:
    return ResolvedEndpoint(
        provider="ollama",
        model=model,
        temperature=0.0,
        max_tokens=1024,
        source="project",
    )


def _resolved(stage: str, *, tier: str = "auto") -> ResolvedAgentConfig:
    capability = CAPABILITY_REGISTRY[stage]
    config = default_config(capability.capability_id)
    config.model.tier = tier
    return ResolvedAgentConfig(
        agent_id=capability.capability_id,
        source="default",
        config_version=0,
        config=config,
        endpoints={"slm": _endpoint("small"), "llm": _endpoint("large")},
        offline_mode=True,
        offline_mode_source="env",
        offline_mode_env_pinned=True,
    )


def test_routing_maps_are_total_and_non_deterministic_defaults_are_budgeted():
    stages = set(CAPABILITY_REGISTRY)
    assert set(DEFAULT_TIERS) == stages
    assert set(ESCALATION_TRIGGERS) == stages
    assert CLASSIFY_CAPABILITIES <= stages
    assert SYNC_ELIGIBLE <= stages
    assert set(DEFAULT_TIERS.values()) <= {"deterministic", "slm", "llm"}
    for stage, tier in DEFAULT_TIERS.items():
        if tier != "deterministic":
            assert CAPABILITY_REGISTRY[stage].expected_cost_usd > 0, stage
    assert all(DEFAULT_TIERS[stage] == "deterministic" for stage in SYNC_ELIGIBLE)


def test_zero_cost_defaults_are_limited_to_truly_deterministic_capabilities():
    assert CAPABILITY_REGISTRY["flaky_sentinel"].expected_cost_usd == 0
    assert CAPABILITY_REGISTRY["test_health"].expected_cost_usd == 0
    assert DEFAULT_TIERS["flaky_sentinel"] == "deterministic"
    assert DEFAULT_TIERS["test_health"] == "deterministic"
    assert DEFAULT_TIERS["release_risk"] == "deterministic"
    assert CAPABILITY_REGISTRY["release_risk"].expected_cost_usd > 0


def test_auto_uses_the_registered_default_without_promoting_summary_before_e9_3():
    choice = choose_model("summary", _resolved("summary"), budget_remaining_usd=1.0)

    assert choice.tier == "llm"
    assert choice.endpoint is not None and choice.endpoint.model == "large"
    assert choice.reason == "tier"


def test_router_refuses_unbudgeted_and_over_budget_model_calls():
    unbudgeted = choose_model(
        "ingestion",
        _resolved("ingestion", tier="slm"),
        budget_remaining_usd=1.0,
    )
    over_budget = choose_model(
        "summary",
        _resolved("summary"),
        budget_remaining_usd=0.001,
    )

    assert (unbudgeted.tier, unbudgeted.reason) == (
        "deterministic",
        "capability_unbudgeted",
    )
    assert unbudgeted.fallback == CAPABILITY_REGISTRY["ingestion"].fallback
    assert (over_budget.tier, over_budget.reason) == ("deterministic", "budget")


def test_promoted_classifier_replaces_only_a_classification_slm_model():
    classified = choose_model(
        "root_cause_analysis",
        _resolved("root_cause_analysis", tier="slm"),
        budget_remaining_usd=1.0,
        promoted_classifier="failure-kind-v7",
    )
    summary = choose_model(
        "summary",
        _resolved("summary", tier="slm"),
        budget_remaining_usd=1.0,
        promoted_classifier="failure-kind-v7",
    )

    assert classified.reason == "classifier"
    assert classified.endpoint is not None
    assert classified.endpoint.model == "failure-kind-v7"
    assert classified.endpoint.source == "classifier"
    assert summary.endpoint is not None and summary.endpoint.model == "small"


def test_low_confidence_escalation_rechecks_all_budgets_and_uses_llm():
    resolved = _resolved("root_cause_analysis")
    initial = ModelChoice("slm", resolved.endpoints["slm"], "tier")

    decision = decide_escalation(
        "root_cause_analysis",
        initial,
        resolved,
        trigger="low_confidence",
        confidence=40,
        escalations=0,
        step_llm_calls_remaining=2,
        budget_remaining_usd=1.0,
    )

    assert decision.action == "escalate"
    assert decision.choice.tier == "llm"
    assert decision.choice.endpoint is resolved.endpoints["llm"]
    assert decision.escalations == 1
    assert decision.step_llm_calls_remaining == 1
    assert decision.stage_quality == "normal"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"budget_remaining_usd": 0.001}, "budget"),
        ({"step_llm_calls_remaining": 0}, "step_llm_budget"),
        ({"escalations": 1}, "escalation_limit"),
    ],
)
def test_failed_escalation_constraints_take_the_declared_fallback(changes, reason):
    resolved = _resolved("root_cause_analysis")
    initial = ModelChoice("slm", resolved.endpoints["slm"], "tier")
    kwargs = {
        "trigger": "low_confidence",
        "confidence": 40,
        "escalations": 0,
        "step_llm_calls_remaining": 1,
        "budget_remaining_usd": 1.0,
    }
    kwargs.update(changes)

    decision = decide_escalation("root_cause_analysis", initial, resolved, **kwargs)

    assert decision.action == "fallback"
    assert decision.choice.tier == "deterministic"
    assert decision.choice.fallback == CAPABILITY_REGISTRY["root_cause_analysis"].fallback
    assert decision.reason == reason
    assert decision.stage_quality == "degraded"


def test_explicit_slm_pin_cannot_be_loosened_by_escalation():
    resolved = _resolved("root_cause_analysis", tier="slm")
    initial = choose_model(
        "root_cause_analysis", resolved, budget_remaining_usd=1.0
    )

    decision = decide_escalation(
        "root_cause_analysis",
        initial,
        resolved,
        trigger="low_confidence",
        confidence=10,
        escalations=0,
        step_llm_calls_remaining=1,
        budget_remaining_usd=1.0,
    )

    assert decision.action == "fallback"
    assert decision.reason == "tier_pinned"


def test_missing_llm_endpoint_and_disabled_validation_escalation_fall_back():
    root = _resolved("root_cause_analysis")
    root.endpoints["llm"] = None
    initial = ModelChoice("slm", root.endpoints["slm"], "tier")
    unavailable = decide_escalation(
        "root_cause_analysis",
        initial,
        root,
        trigger="low_confidence",
        confidence=None,
        escalations=0,
        step_llm_calls_remaining=1,
        budget_remaining_usd=1.0,
    )

    summary = _resolved("summary")
    summary.config.model.escalation.on_validation_failure = False
    disabled = decide_escalation(
        "summary",
        ModelChoice("slm", summary.endpoints["slm"], "tier"),
        summary,
        trigger="validation_failure",
        confidence=None,
        escalations=0,
        step_llm_calls_remaining=1,
        budget_remaining_usd=1.0,
    )

    assert unavailable.action == "fallback"
    assert unavailable.reason == "llm_unavailable"
    assert disabled.action == "fallback"
    assert disabled.reason == "project_disabled"


def test_irrelevant_trigger_accepts_and_provenance_has_the_pinned_shape():
    resolved = _resolved("summary", tier="slm")
    initial = choose_model("summary", resolved, budget_remaining_usd=1.0)
    decision = decide_escalation(
        "summary",
        initial,
        resolved,
        trigger="contradictions",
        confidence=None,
        escalations=0,
        step_llm_calls_remaining=1,
        budget_remaining_usd=1.0,
    )

    assert decision.action == "accept"
    assert provenance("slm", decision.choice, 0, False) == {
        "tier_requested": "slm",
        "tier_used": "slm",
        "escalations": 0,
        "fallback_used": False,
    }
