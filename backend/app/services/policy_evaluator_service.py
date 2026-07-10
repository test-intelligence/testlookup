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
    # ── Kind-aware gating trail (US-9.3) ─────────────────────────────────
    # All three stay at their defaults unless the policy's kind_rules block
    # is enabled — the identical-when-disabled pin depends on that.
    kind_breakdown: dict | None = None      # {"product": n, "test_code": n, ...}
    kind_rule_applied: bool = False         # True when a NO_GO was downgraded
    kind_counterfactual: str | None = None  # human-readable "would have been…"

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


def _extract_kind_rules(policy: ReleaseGatePolicy | None) -> dict | None:
    """Return the policy's ``kind_rules`` block when it is enabled, else None.

    STRICTLY OPT-IN (US-9.3): absent block, ``enabled: false``, or a
    malformed non-dict value all resolve to None, and None means the verdict
    computation is byte-identical to the pre-feature behaviour.
    """
    if not (policy and policy.rules):
        return None
    kind_rules = policy.rules.get("kind_rules")
    if not isinstance(kind_rules, dict) or not kind_rules.get("enabled", False):
        return None
    return kind_rules


# Kinds a policy may budget away. ``product`` is deliberately NOT here —
# product failures can never be excluded — and ``unknown`` failures are
# conservatively counted as product below.
_EXCLUDABLE_KINDS: tuple[str, ...] = ("infrastructure", "test_code")


def _apply_kind_rules(
    kind_rules: dict,
    context: dict,
    recommendation: str,
) -> tuple[str, list[RuleEvaluation], dict | None, bool, str | None]:
    """Apply opt-in failure-kind weighting (US-9.3) to the base recommendation.

    Semantics (all pinned by tests/test_kind_gate_policy.py):
      * Failures are bucketed by the derived kind triad. ``unknown`` is folded
        into ``product`` — an unclassified failure must never soften the gate.
      * A kind with a configured budget whose count is <= max_failures is
        excluded from the NO_GO trigger; exceeding the budget restores full
        counting for that kind. Kinds without a budget always count.
      * If, after exclusions, zero blocking failures remain AND at least one
        failure was excluded, a base NO_GO is downgraded — at most to
        CONDITIONAL_GO, never to GO (hard rule; the schema Literal and this
        function both enforce it).
      * GO / CONDITIONAL_GO base verdicts are never touched.
      * Missing/absent kind counts in ``context`` (older snapshots, simulator
        against pre-feature decisions) → no downgrade, recorded in the trail.

    Returns ``(recommendation, rule_evaluations, kind_breakdown,
    applied, counterfactual)``. The evaluations always report the breakdown —
    excluded failures are reported, never hidden.
    """
    raw_counts = context.get("failure_kind_counts")
    if not isinstance(raw_counts, dict):
        return (
            recommendation,
            [RuleEvaluation(
                rule_id="kind_rules",
                rule_name="Failure-kind weighting",
                rule_type="kind_budget",
                passed=True,
                action="INFO",
                message=(
                    "Failure-kind weighting is enabled but no kind breakdown was "
                    "available for this run — all failures counted in full."
                ),
            )],
            None,
            False,
            None,
        )

    counts = {k: int(raw_counts.get(k, 0) or 0) for k in ("product", "test_code", "infrastructure", "unknown")}
    # Conservative fold: unknown-kind failures always count as product.
    product_effective = counts["product"] + counts["unknown"]
    total_failures = sum(counts.values())

    evals: list[RuleEvaluation] = []
    blocking = product_effective
    excluded_parts: list[str] = []
    excluded_total = 0

    for kind in _EXCLUDABLE_KINDS:
        budget = kind_rules.get(kind)
        count = counts[kind]
        if not isinstance(budget, dict):
            # No budget configured for this kind → counts in full.
            blocking += count
            continue
        max_failures = int(budget.get("max_failures", 0) or 0)
        within = count <= max_failures
        if within:
            excluded_total += count
            if count:
                excluded_parts.append(f"{count} {kind} failure(s) <= budget {max_failures}")
        else:
            blocking += count
        evals.append(RuleEvaluation(
            rule_id=f"kind_budget_{kind}",
            rule_name=f"Failure-kind budget ({kind})",
            rule_type="kind_budget",
            passed=within,
            action="INFO",  # budgets never escalate — they can only soften
            message=(
                f"{kind} failures: {count} (budget: {max_failures}) — "
                "excluded from NO_GO trigger but still reported"
                if within else
                f"{kind} failures: {count} exceed budget {max_failures} — counted in full"
            ),
            actual_value=count,
            threshold_value=max_failures,
        ))

    applied = False
    counterfactual: str | None = None
    if (
        recommendation == "NO_GO"
        and total_failures > 0
        and excluded_total > 0
        and blocking == 0
    ):
        # Hard rule: at most CONDITIONAL_GO — never GO.
        recommendation = "CONDITIONAL_GO"
        applied = True
        counterfactual = (
            "Would have been NO_GO; downgraded to CONDITIONAL_GO because "
            + " and ".join(excluded_parts)
            + f" (product failures: {product_effective}"
            + (f", of which {counts['unknown']} unknown-kind counted as product" if counts["unknown"] else "")
            + ")"
        )
        summary_message = counterfactual
    elif recommendation == "NO_GO" and product_effective > 0:
        summary_message = (
            f"No downgrade: {product_effective} product failure(s) always count"
            + (f" (includes {counts['unknown']} unknown-kind counted as product)" if counts["unknown"] else "")
        )
    else:
        summary_message = "Failure-kind weighting evaluated — no downgrade applied"

    breakdown = dict(counts)
    evals.append(RuleEvaluation(
        rule_id="kind_rules",
        rule_name="Failure-kind weighting",
        rule_type="kind_budget",
        passed=True,
        action="INFO",
        message=summary_message,
        actual_value=blocking,
        threshold_value=None,
    ))
    return recommendation, evals, breakdown, applied, counterfactual


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

    # ── Kind-aware weighting (US-9.3, strictly opt-in) ────────────────────
    # Applied to the BASE recommendation only, before rule escalation, so an
    # explicit failing BLOCK rule below still forces NO_GO regardless of any
    # kind-budget downgrade. Disabled/absent kind_rules → this whole branch
    # is a no-op and the verdict path is identical to the pre-feature code.
    kind_breakdown: dict | None = None
    kind_rule_applied = False
    kind_counterfactual: str | None = None
    kind_evals: list[RuleEvaluation] = []
    kind_rules = _extract_kind_rules(policy)
    if kind_rules is not None:
        (
            recommendation,
            kind_evals,
            kind_breakdown,
            kind_rule_applied,
            kind_counterfactual,
        ) = _apply_kind_rules(kind_rules, context, recommendation)

    # Evaluate each rule
    rule_evals: list[RuleEvaluation] = []
    for rule in rules:
        rule_type = rule.get("type", "")
        evaluator = _RULE_EVALUATORS.get(rule_type)
        if evaluator:
            evaluation = evaluator(rule, context, dim_scores)
            rule_evals.append(evaluation)
    rule_evals.extend(kind_evals)

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

    # A failing BLOCK rule outranks the kind-budget downgrade (US-9.3): if it
    # restored NO_GO, correct the trail so it doesn't claim a downgrade that
    # didn't survive.
    if kind_rule_applied and recommendation == "NO_GO":
        kind_rule_applied = False
        kind_counterfactual = (
            "Failure-kind budgets were satisfied, but a failing BLOCK rule "
            "keeps the verdict at NO_GO — kind weighting never bypasses "
            "explicit policy rules."
        )

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
        kind_breakdown=kind_breakdown,
        kind_rule_applied=kind_rule_applied,
        kind_counterfactual=kind_counterfactual,
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
