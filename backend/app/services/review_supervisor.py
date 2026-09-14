"""Deterministic supervisor policy for generic reviewer verdicts."""
from __future__ import annotations

from typing import Any

from app.models.agent_contracts import (
    ReviewerSupervisorDecisionV1,
    ReviewVerdictV1,
)
from app.services.step_llm_budget import StepLLMBudget


def supervise_review(
    value: ReviewVerdictV1 | dict[str, Any],
    *,
    retry_count: int,
    budget: StepLLMBudget,
) -> tuple[ReviewVerdictV1, ReviewerSupervisorDecisionV1]:
    """Validate fail-closed and produce a route without writing run status."""
    from app.agents.reviewer_agent import validate_review_verdict

    verdict = validate_review_verdict(value)
    if verdict.verdict == "retry":
        if retry_count < 1 and budget.consume("review_retry"):
            return verdict, ReviewerSupervisorDecisionV1(
                route="retry",
                reviewed_steps=verdict.reviewed_steps,
                tier_override="llm",
                requires_human_review=True,
                retry_count=1,
            )
        verdict = verdict.model_copy(
            update={"verdict": "reject", "requires_human_review": True}
        )
    if verdict.verdict == "reject":
        return verdict, ReviewerSupervisorDecisionV1(
            route="finalize",
            reviewed_steps=verdict.reviewed_steps,
            status="failed",
            error_code="validation_failed",
            requires_human_review=True,
            retry_count=min(max(0, retry_count), 1),
        )
    return verdict, ReviewerSupervisorDecisionV1(
        route="continue",
        reviewed_steps=verdict.reviewed_steps,
        requires_human_review=(
            verdict.verdict == "pass_with_flags" or verdict.requires_human_review
        ),
        retry_count=min(max(0, retry_count), 1),
    )


__all__ = ["supervise_review"]
