"""
Policy Evaluator Service — resolve, evaluate, and explain release gate policies.

Precedence: project-specific policy → system default (project_id IS NULL) → hardcoded defaults.
Every evaluation produces a structured result with per-rule pass/fail and explanation.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import ReleaseGatePolicy
from app.services.criticality_service import (
    _GO_THRESHOLD,
    _NO_GO_THRESHOLD,
    _weights,
    compute_composite,
    score_to_recommendation,
)

logger = logging.getLogger("services.policy_evaluator")


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class RuleEvaluation:
    rule_id: str
    rule_name: str
    rule_type: str
    passed: bool
    action: str  # BLOCK | WARN | INFO
    message: str
    actual_value: float | int | None = None
    threshold_value: float | int | None = None


@dataclass
class PolicyEvaluationResult:
    policy_id: str | None
    policy_version: int | None
    policy_level: str  # "project" | "system" | "hardcoded"
    overall_result: str  # "PASS" | "BLOCK" | "WARN"
    recommendation: str  # GO | CONDITIONAL_GO | NO_GO
    effective_composite: float
    rule_evaluations: list[RuleEvaluation] = field(default_factory=list)
    effective_thresholds: dict = field(default_factory=dict)
    effective_weights: dict = field(default_factory=dict)
    evaluated_at: str = ""

    def to_dict(self) -> dict:
        """Convert to a JSON-serializable dict for persistence."""
        return asdict(self)


# ── Policy resolution ────────────────────────────────────────────────────────


async def resolve_effective_policy(
    project_id: uuid.UUID | str | None,
    db: AsyncSession,
) -> tuple[ReleaseGatePolicy | None, str]:
    """
    Return (policy, level) where level is "project" | "system" | "hardcoded".

    Precedence:
    1. Active policy for the specific project → "project"
    2. Active system default (project_id IS NULL) → "system"
    3. None → "hardcoded" (use config.py defaults)
    """
    # NB: ``is_active`` has no DB-level single-active guarantee (the only
    # unique constraint is (project_id, version), and publish_policy enforces
    # "one active per scope" in application code). A concurrent publish race
    # can therefore leave two active rows for a scope. Order by version desc so
    # resolution is deterministic regardless — the latest published version
    # wins, matching the /history endpoints' ordering — instead of an arbitrary
    # row from an unordered LIMIT 1.
    if project_id:
        pid = uuid.UUID(str(project_id)) if not isinstance(project_id, uuid.UUID) else project_id
        result = await db.execute(
            select(ReleaseGatePolicy)
            .where(
                ReleaseGatePolicy.project_id == pid,
                ReleaseGatePolicy.is_active == True,  # noqa: E712
            )
            .order_by(ReleaseGatePolicy.version.desc())
            .limit(1)
        )
        policy = result.scalar_one_or_none()
        if policy:
            return policy, "project"

    # Fall back to system default
    result = await db.execute(
        select(ReleaseGatePolicy)
        .where(
            ReleaseGatePolicy.project_id.is_(None),
            ReleaseGatePolicy.is_active == True,  # noqa: E712
        )
        .order_by(ReleaseGatePolicy.version.desc())
        .limit(1)
    )
    policy = result.scalar_one_or_none()
    if policy:
        return policy, "system"

    return None, "hardcoded"


# ── Policy evaluation ────────────────────────────────────────────────────────


def _extract_thresholds(policy: ReleaseGatePolicy | None) -> dict:
    """Extract thresholds from a policy document, or return hardcoded defaults."""
    if policy and policy.rules:
        doc = policy.rules
        thresholds = doc.get("thresholds", {})
        return {
            "go_threshold": float(thresholds.get("go_threshold", _GO_THRESHOLD)),
            "no_go_threshold": float(thresholds.get("no_go_threshold", _NO_GO_THRESHOLD)),
            "pass_rate_minimum": float(thresholds.get("pass_rate_minimum", 90.0)),
            "pass_rate_hard_floor_factor": float(thresholds.get("pass_rate_hard_floor_factor", 0.7)),
        }
    from app.core.config import settings
    return {
        "go_threshold": _GO_THRESHOLD,
        "no_go_threshold": _NO_GO_THRESHOLD,
        "pass_rate_minimum": settings.RELEASE_PASS_RATE_THRESHOLD,
        "pass_rate_hard_floor_factor": 0.7,
    }


def _extract_weights(policy: ReleaseGatePolicy | None) -> dict[str, float]:
    """Extract dimension weights from a policy document, or return config defaults."""
    if policy and policy.rules:
        doc = policy.rules
        dim_weights = doc.get("dimension_weights", {})
        if dim_weights:
            default = _weights()
            return {k: float(dim_weights.get(k, default.get(k, 0.0))) for k in default}
    return _weights()


def _extract_rules(policy: ReleaseGatePolicy | None) -> list[dict]:
    """Extract enabled rules from a policy document."""
    if policy and policy.rules:
        doc = policy.rules
        return [r for r in doc.get("rules", []) if r.get("enabled", True)]
    return []


async def evaluate_policy(
    project_id: uuid.UUID | str | None,
    dim_scores: dict[str, float],
    pass_rate: float,
    context: dict,
    db: AsyncSession,
    policy_override: ReleaseGatePolicy | None = None,
) -> PolicyEvaluationResult:
    """
    Evaluate the effective policy against scoring data.

    If policy_override is provided, use that instead of resolving from DB (simulator mode).
    """
    policy: ReleaseGatePolicy | None
    if policy_override:
        policy = policy_override
        level = "simulated"
    else:
        policy, level = await resolve_effective_policy(project_id, db)

    thresholds = _extract_thresholds(policy)
    weights = _extract_weights(policy)
    rules = _extract_rules(policy)

    # Recompute composite with policy weights
    effective_composite = compute_composite(dim_scores, weights=weights)

    # Base recommendation from thresholds
    recommendation = score_to_recommendation(
        composite=effective_composite,
        pass_rate=pass_rate,
        threshold=thresholds["pass_rate_minimum"],
        go_threshold=thresholds["go_threshold"],
        no_go_threshold=thresholds["no_go_threshold"],
        hard_floor_factor=thresholds["pass_rate_hard_floor_factor"],
    )

    # Evaluate each rule
    rule_evals: list[RuleEvaluation] = []
    for rule in rules:
        rule_type = rule.get("type", "")
        evaluator = _RULE_EVALUATORS.get(rule_type)
        if evaluator:
            evaluation = evaluator(rule, context, dim_scores)
            rule_evals.append(evaluation)

    # Escalate recommendation based on rule results
    overall_result = "PASS"
    for ev in rule_evals:
        if not ev.passed:
            if ev.action == "BLOCK":
                overall_result = "BLOCK"
                recommendation = "NO_GO"
            elif ev.action == "WARN" and overall_result != "BLOCK":
                overall_result = "WARN"
                if recommendation == "GO":
                    recommendation = "CONDITIONAL_GO"

    return PolicyEvaluationResult(
        policy_id=str(policy.id) if policy else None,
        policy_version=policy.version if policy else None,
        policy_level=level,
        overall_result=overall_result,
        recommendation=recommendation,
        effective_composite=effective_composite,
        rule_evaluations=rule_evals,
        effective_thresholds=thresholds,
        effective_weights=weights,
        evaluated_at=datetime.now(timezone.utc).isoformat(),
    )


# ── Rule evaluators ──────────────────────────────────────────────────────────


def _eval_flaky_recurrence(rule: dict, context: dict, dim_scores: dict) -> RuleEvaluation:
    """Evaluate flaky test count against a maximum threshold."""
    params = rule.get("params", {})
    max_flaky = int(params.get("max_flaky_tests", 10))
    action = params.get("action", "BLOCK")
    actual = int(context.get("flaky_count", 0))
    passed = actual <= max_flaky
    return RuleEvaluation(
        rule_id=rule["id"],
        rule_name=rule["name"],
        rule_type="flaky_recurrence",
        passed=passed,
        action=action,
        message=f"Flaky tests: {actual} (limit: {max_flaky})" if passed
        else f"Flaky test count {actual} exceeds limit of {max_flaky}",
        actual_value=actual,
        threshold_value=max_flaky,
    )


def _eval_open_defect_limit(rule: dict, context: dict, dim_scores: dict) -> RuleEvaluation:
    """Evaluate open defect count against a maximum, optionally filtered by severity."""
    params = rule.get("params", {})
    max_defects = int(params.get("max_open_defects", 5))
    action = params.get("action", "BLOCK")
    actual = int(context.get("open_defects", 0))
    passed = actual <= max_defects
    return RuleEvaluation(
        rule_id=rule["id"],
        rule_name=rule["name"],
        rule_type="open_defect_limit",
        passed=passed,
        action=action,
        message=f"Open defects: {actual} (limit: {max_defects})" if passed
        else f"Open defect count {actual} exceeds limit of {max_defects}",
        actual_value=actual,
        threshold_value=max_defects,
    )


def _eval_dimension_ceiling(rule: dict, context: dict, dim_scores: dict) -> RuleEvaluation:
    """Evaluate whether a specific risk dimension exceeds a ceiling score."""
    params = rule.get("params", {})
    dimension = params.get("dimension", "")
    max_score = float(params.get("max_score", 100))
    action = params.get("action", "BLOCK")
    actual = float(dim_scores.get(dimension, 0))
    passed = actual <= max_score
    return RuleEvaluation(
        rule_id=rule["id"],
        rule_name=rule["name"],
        rule_type="dimension_ceiling",
        passed=passed,
        action=action,
        message=f"{dimension}: {actual:.1f} (ceiling: {max_score})" if passed
        else f"{dimension} score {actual:.1f} exceeds ceiling of {max_score}",
        actual_value=actual,
        threshold_value=max_score,
    )


# Registry of rule evaluators (override_rules handled separately at override time)
_RULE_EVALUATORS: dict = {
    "flaky_recurrence": _eval_flaky_recurrence,
    "open_defect_limit": _eval_open_defect_limit,
    "dimension_ceiling": _eval_dimension_ceiling,
}


# ── Override constraint checking ─────────────────────────────────────────────


def check_override_constraints(
    policy: ReleaseGatePolicy | None,
    current_recommendation: str,
    override_recommendation: str,
    reason: str,
) -> tuple[bool, str]:
    """
    Check if an override is allowed by the active policy's override_rules.

    Returns (allowed, error_message). If allowed is True, error_message is empty.
    """
    if not policy or not policy.rules:
        return True, ""

    rules = policy.rules.get("rules", [])
    for rule in rules:
        if rule.get("type") == "override_rules" and rule.get("enabled", True):
            params = rule.get("params", {})

            # Check GO from NO_GO restriction
            if not params.get("allow_override_to_go_from_no_go", True):
                if current_recommendation == "NO_GO" and override_recommendation == "GO":
                    return False, "Policy does not allow overriding NO_GO to GO directly"

            # Check reason minimum length
            min_len = int(params.get("require_reason_min_length", 0))
            if min_len > 0 and len(reason.strip()) < min_len:
                return False, f"Override reason must be at least {min_len} characters (got {len(reason.strip())})"

    return True, ""
