"""
Release Council Service — deterministic context assembly for release decisions.

Gathers cluster criticality, regression classifications, baseline diff, and open
defects grouped by component into a single input_snapshot.  The snapshot is
persisted with the ReleaseDecision for full traceability: every release
recommendation can be reproduced from its recorded inputs.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from app.core.metrics import release_overrides_total

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    Defect,
    FailureCluster,
    ReleaseDecision,
    TestRun,
)
from app.models.schemas import (
    BaselineDiff,
    ClusterInsightResponse,
    DimensionScore,
    OverrideAuditEntry,
    ReleaseCouncilResponse,
    RuleEvaluationResponse,
)
from app.services.criticality_service import (
    SCORE_MODEL_VERSION,
    score_cluster,
)

from app.models.constants import DIMENSION_METADATA

logger = logging.getLogger("services.release_council")


def _build_dimension_scores(scores_dict: Optional[dict]) -> list[DimensionScore]:
    if not scores_dict:
        return []
    result = []
    for key, (label, weight) in DIMENSION_METADATA.items():
        score = float(scores_dict.get(key, 0))
        result.append(DimensionScore(
            name=key,
            label=label,
            score=round(score, 1),
            weight=weight,
            contribution=round(score * weight, 2),
        ))
    return result


async def assemble_input_snapshot(
    run_id: uuid.UUID,
    db: AsyncSession,
) -> dict[str, Any]:
    """
    Build a deterministic input_snapshot capturing every data source the
    release decision depends on.

    Returns a JSON-serializable dict persisted alongside the ReleaseDecision.
    """
    snapshot: dict[str, Any] = {
        "assembled_at": datetime.now(timezone.utc).isoformat(),
        "score_model_version": SCORE_MODEL_VERSION,
    }

    # ── Test run context ─────────────────────────────────────────────────────
    run_result = await db.execute(select(TestRun).where(TestRun.id == run_id))
    run = run_result.scalar_one_or_none()
    if run:
        snapshot["test_run"] = {
            "id": str(run.id),
            "build_number": run.build_number,
            "pass_rate": run.pass_rate,
            "total_tests": run.total_tests,
            "passed_tests": run.passed_tests,
            "failed_tests": run.failed_tests,
            "branch": run.branch,
        }
    else:
        snapshot["test_run"] = None

    # ── Failure clusters ─────────────────────────────────────────────────────
    cluster_result = await db.execute(
        select(FailureCluster)
        .where(FailureCluster.test_run_id == run_id)
        .order_by(FailureCluster.size.desc())
    )
    clusters = cluster_result.scalars().all()
    snapshot["clusters"] = [
        {
            "cluster_id": c.cluster_id,
            "label": c.label,
            "size": c.size,
            "cohesion_score": c.cohesion_score,
            "regression_classification": c.regression_classification,
            "member_count": len(c.member_test_ids or []),
        }
        for c in clusters
    ]

    # ── Open defects by component ────────────────────────────────────────────
    project_id = run.project_id if run else None
    if project_id:
        defect_result = await db.execute(
            select(
                Defect.component,
                sa_func.count(Defect.id).label("count"),
            )
            .where(Defect.project_id == project_id)
            .where(Defect.resolution_status == "OPEN")
            .group_by(Defect.component)
        )
        defect_rows = defect_result.all()
        snapshot["open_defects_by_component"] = [
            {"component": row.component or "unassigned", "count": row.count}
            for row in defect_rows
        ]
    else:
        snapshot["open_defects_by_component"] = []

    return snapshot


async def get_release_council(
    run_id: uuid.UUID,
    db: AsyncSession,
    baseline_diff: Optional[BaselineDiff] = None,
) -> Optional[ReleaseCouncilResponse]:
    """
    Retrieve the release decision with full council context:
    dimension scores, linked cluster insights, baseline diff, open defects.
    """
    result = await db.execute(
        select(ReleaseDecision).where(ReleaseDecision.test_run_id == run_id)
    )
    decision = result.scalar_one_or_none()
    if not decision:
        return None

    run_result = await db.execute(select(TestRun).where(TestRun.id == run_id))
    run = run_result.scalar_one_or_none()

    # Build dimension scores
    dim_scores = _build_dimension_scores(decision.dimension_scores)

    # Build cluster insights
    cluster_result = await db.execute(
        select(FailureCluster)
        .where(FailureCluster.test_run_id == run_id)
        .order_by(FailureCluster.size.desc())
    )
    clusters = cluster_result.scalars().all()
    total_analyses = sum(c.size for c in clusters) or 1

    cluster_insights = []
    for c in clusters:
        c_dim = score_cluster(
            cluster={},
            all_dim_scores=decision.dimension_scores or {},
            total_analyses=total_analyses,
            member_count=c.size,
        )
        c_dim_scores = _build_dimension_scores(c_dim) if c_dim else []
        cluster_insights.append(ClusterInsightResponse(
            id=str(c.id),
            cluster_id=c.cluster_id,
            label=c.label,
            size=c.size,
            representative_error=c.representative_error,
            member_test_ids=[str(m) for m in (c.member_test_ids or [])],
            cohesion_score=c.cohesion_score,
            criticality_level=c.regression_classification,
            dimension_scores=c_dim_scores,
        ))

    # Open defects by component
    project_id = run.project_id if run else None
    open_defects_by_component: list[dict] = []
    if project_id:
        defect_result = await db.execute(
            select(
                Defect.component,
                sa_func.count(Defect.id).label("count"),
            )
            .where(Defect.project_id == project_id)
            .where(Defect.resolution_status == "OPEN")
            .group_by(Defect.component)
        )
        open_defects_by_component = [
            {"component": row.component or "unassigned", "count": row.count}
            for row in defect_result.all()
        ]

    # Override audit trail
    raw_audit = decision.override_audit or []
    override_audit = []
    for entry in raw_audit:
        try:
            override_audit.append(OverrideAuditEntry(**entry))
        except Exception:
            continue

    # ── Policy context (ENT-02) ────────────────────────────────────────────
    policy_id = str(decision.policy_id) if decision.policy_id else None
    policy_version = None
    policy_level = None
    rule_evaluations: list[RuleEvaluationResponse] = []

    policy_eval = decision.policy_evaluation
    if policy_eval:
        policy_version = policy_eval.get("policy_version")
        policy_level = policy_eval.get("policy_level")
        for rev in policy_eval.get("rule_evaluations", []):
            try:
                rule_evaluations.append(RuleEvaluationResponse(
                    rule_id=rev.get("rule_id", ""),
                    rule_name=rev.get("rule_name", ""),
                    rule_type=rev.get("rule_type", ""),
                    passed=rev.get("passed", True),
                    action=rev.get("action", "INFO"),
                    message=rev.get("message", ""),
                    actual_value=rev.get("actual_value"),
                    threshold_value=rev.get("threshold_value"),
                ))
            except Exception:
                continue

    return ReleaseCouncilResponse(
        run_id=str(run_id),
        recommendation=decision.recommendation,
        risk_score=decision.risk_score,
        composite_risk=decision.composite_risk,
        dimension_scores=dim_scores,
        blocking_issues=decision.blocking_issues or [],
        conditions_for_go=decision.conditions_for_go or [],
        reasoning=decision.reasoning,
        score_model_version=decision.score_model_version,
        input_snapshot=decision.input_snapshot,
        cluster_insights=cluster_insights,
        baseline_diff=baseline_diff,
        open_defects_by_component=open_defects_by_component,
        human_override=decision.human_override,
        overridden_by=str(decision.overridden_by) if decision.overridden_by else None,
        original_recommendation=decision.original_recommendation,
        original_risk_score=decision.original_risk_score,
        override_audit=override_audit,
        pass_rate=run.pass_rate if run else None,
        build_number=run.build_number if run else None,
        policy_id=policy_id,
        policy_version=policy_version,
        policy_level=policy_level,
        rule_evaluations=rule_evaluations,
    )


async def apply_override(
    run_id: uuid.UUID,
    override_recommendation: str,
    reason: str,
    actor: Any,
    db: AsyncSession,
) -> Optional[ReleaseCouncilResponse]:
    """
    Override the release decision and persist an auditable before/after record.
    """
    result = await db.execute(
        select(ReleaseDecision).where(ReleaseDecision.test_run_id == run_id)
    )
    decision = result.scalar_one_or_none()
    if not decision:
        return None

    # ── Policy override constraints (ENT-02) ─────────────────────────────
    from app.services.policy_evaluator_service import check_override_constraints, resolve_effective_policy

    # Resolve project_id from the test run
    run_result = await db.execute(select(TestRun.project_id).where(TestRun.id == run_id))
    project_id = run_result.scalar_one_or_none()

    policy, policy_level = await resolve_effective_policy(project_id, db)
    allowed, constraint_error = check_override_constraints(
        policy, decision.recommendation, override_recommendation, reason,
    )
    if not allowed:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=constraint_error,
        )

    # Record original values on first override
    if decision.original_recommendation is None:
        decision.original_recommendation = decision.recommendation
        decision.original_risk_score = decision.risk_score

    # Build audit entry
    audit_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor_id": str(actor.id) if actor else None,
        "actor_name": getattr(actor, "username", None) or getattr(actor, "full_name", None),
        "before_recommendation": decision.recommendation,
        "before_risk_score": decision.risk_score,
        "after_recommendation": override_recommendation,
        "reason": reason,
        "policy_id": str(policy.id) if policy else None,
        "policy_version": policy.version if policy else None,
    }

    # Append to audit trail
    existing_audit = list(decision.override_audit or [])
    existing_audit.append(audit_entry)
    decision.override_audit = existing_audit

    # Apply override. Transaction is owned by the handler — ``apply_override``
    # only stages the mutation so the override row, any follow-up
    # ``mark_stale`` on the intelligence snapshot, and the metrics
    # counter can all land atomically under one commit.
    before_rec = decision.recommendation
    decision.recommendation = override_recommendation
    decision.human_override = reason
    decision.overridden_by = actor.id if actor else None

    await db.flush()
    await db.refresh(decision)

    # Track metric
    release_overrides_total.labels(
        from_recommendation=before_rec,
        to_recommendation=override_recommendation,
    ).inc()

    logger.info(
        "Release decision overridden for run %s by %s: %s → %s",
        run_id,
        getattr(actor, "username", "unknown"),
        audit_entry["before_recommendation"],
        override_recommendation,
    )

    return await get_release_council(run_id, db)
