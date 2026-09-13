"""Pure per-capability model-tier routing (architecture E5.1, section 5.3).

The router chooses an already-resolved endpoint. It does not create clients,
read secrets, reserve cost, or mutate workflow state. Callers can therefore
record the decision before invoking a model, while ``BudgetedLLM`` remains the
hard atomic cost authority at the invocation boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

from app.services.agent_capability_registry import (
    CLASSIFY_CAPABILITIES,
    DEFAULT_TIERS,
    ESCALATION_TRIGGERS,
    get_capability,
)
from app.services.agent_config_resolver import ResolvedAgentConfig, ResolvedEndpoint

ModelTier = Literal["deterministic", "slm", "llm"]
EscalationTrigger = Literal[
    "validation_failure",
    "low_confidence",
    "not_enough_evidence",
    "contradictions",
]


@dataclass(frozen=True)
class ModelChoice:
    tier: ModelTier
    endpoint: ResolvedEndpoint | None
    reason: str
    fallback: str | None = None


@dataclass(frozen=True)
class EscalationDecision:
    action: Literal["accept", "escalate", "fallback"]
    choice: ModelChoice
    escalations: int
    step_llm_calls_remaining: int
    stage_quality: Literal["normal", "degraded"]
    reason: str


def _can_afford(remaining: float | None, estimate: float) -> bool:
    return remaining is None or remaining + 1e-9 >= estimate


def _fallback(stage: str, reason: str) -> ModelChoice:
    return ModelChoice(
        tier="deterministic",
        endpoint=None,
        reason=reason,
        fallback=get_capability(stage).fallback,
    )


def choose_model(
    stage: str,
    resolved: ResolvedAgentConfig,
    *,
    budget_remaining_usd: float | None,
    promoted_classifier: str | None = None,
) -> ModelChoice:
    """Choose the cheapest configured tier allowed by today's defaults.

    ``None`` budget means the caller has no pipeline cost ledger. A finite
    value is checked before any non-deterministic endpoint is returned. A
    zero-cost capability can never reach a model even if a stored config asks
    for one; that would bypass the pipeline reservation made by BaseAgent.
    """
    capability = get_capability(stage)
    configured = resolved.config.model.tier
    tier = cast(ModelTier, DEFAULT_TIERS[stage] if configured == "auto" else configured)
    if tier == "deterministic":
        return ModelChoice(tier="deterministic", endpoint=None, reason="tier")
    if capability.expected_cost_usd <= 0:
        return _fallback(stage, "capability_unbudgeted")
    if not _can_afford(budget_remaining_usd, capability.expected_cost_usd):
        return _fallback(stage, "budget")
    endpoint = resolved.endpoints.get(tier)
    if endpoint is None:
        return _fallback(stage, "llm_unavailable")
    classifier = (promoted_classifier or "").strip()
    if tier == "slm" and classifier and stage in CLASSIFY_CAPABILITIES:
        endpoint = endpoint.model_copy(update={"model": classifier, "source": "classifier"})
        return ModelChoice(tier="slm", endpoint=endpoint, reason="classifier")
    return ModelChoice(tier=tier, endpoint=endpoint, reason="tier")


def _trigger_applies(
    stage: str,
    resolved: ResolvedAgentConfig,
    trigger: EscalationTrigger | None,
    confidence: int | None,
) -> tuple[bool, str]:
    if trigger is None and confidence is not None:
        trigger = "low_confidence"
    if trigger not in ESCALATION_TRIGGERS[stage]:
        return False, "trigger_not_configured"
    escalation = resolved.config.model.escalation
    if trigger == "validation_failure" and not escalation.on_validation_failure:
        return True, "project_disabled"
    if trigger == "low_confidence" and (
        confidence is not None and confidence >= escalation.on_confidence_below
    ):
        return False, "confidence_accepted"
    return True, str(trigger)


def decide_escalation(
    stage: str,
    choice: ModelChoice,
    resolved: ResolvedAgentConfig,
    *,
    trigger: EscalationTrigger | None,
    confidence: int | None,
    escalations: int,
    step_llm_calls_remaining: int,
    budget_remaining_usd: float | None,
) -> EscalationDecision:
    """Accept, upgrade SLM to LLM, or take the deterministic fallback."""
    capability = get_capability(stage)
    applies, trigger_reason = _trigger_applies(stage, resolved, trigger, confidence)
    if not applies:
        return EscalationDecision(
            action="accept",
            choice=choice,
            escalations=escalations,
            step_llm_calls_remaining=step_llm_calls_remaining,
            stage_quality="normal",
            reason=trigger_reason,
        )

    escalation = resolved.config.model.escalation
    refusal = trigger_reason if trigger_reason == "project_disabled" else None
    if refusal is None and choice.tier != "slm":
        refusal = "tier"
    if refusal is None and resolved.config.model.tier != "auto":
        refusal = "tier_pinned"
    if refusal is None and escalations >= escalation.max_escalations:
        refusal = "escalation_limit"
    if refusal is None and step_llm_calls_remaining < 1:
        refusal = "step_llm_budget"
    endpoint = resolved.endpoints.get("llm")
    if refusal is None and endpoint is None:
        refusal = "llm_unavailable"
    if refusal is None and not _can_afford(
        budget_remaining_usd, capability.expected_cost_usd
    ):
        refusal = "budget"

    if refusal is not None:
        return EscalationDecision(
            action="fallback",
            choice=_fallback(stage, refusal),
            escalations=escalations,
            step_llm_calls_remaining=step_llm_calls_remaining,
            stage_quality="degraded",
            reason=refusal,
        )
    assert endpoint is not None
    escalated = ModelChoice(tier="llm", endpoint=endpoint, reason="escalation")
    return EscalationDecision(
        action="escalate",
        choice=escalated,
        escalations=escalations + 1,
        step_llm_calls_remaining=step_llm_calls_remaining - 1,
        stage_quality="normal",
        reason=trigger_reason,
    )


def provenance(
    requested: str,
    final: ModelChoice,
    escalations: int,
    fallback_used: bool,
) -> dict[str, Any]:
    return {
        "tier_requested": requested,
        "tier_used": final.tier,
        "escalations": max(0, int(escalations)),
        "fallback_used": bool(fallback_used),
    }


__all__ = [
    "EscalationDecision",
    "EscalationTrigger",
    "ModelChoice",
    "ModelTier",
    "choose_model",
    "decide_escalation",
    "provenance",
]
