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
    HARD_FLOOR_FACTOR as _HARD_FLOOR_FACTOR,
    SCORE_MODEL_VERSION,
    compute_composite,
    compute_dimension_scores,
    score_cluster,
    score_to_recommendation,
)
from app.core.config import settings

from app.models.constants import DIMENSION_METADATA

logger = logging.getLogger("services.release_council")


# Worst → best so worse_of returns the leftmost in a sorted pair.
#
# Two vocabularies converge here: the recommendation vocabulary produced by
# ``criticality_service.score_to_recommendation`` (and persisted on
# ``ReleaseDecision.recommendation``) is ``CONDITIONAL_GO``, while
# ``metrics_service.classify_with_policy`` emits ``CONDITIONAL`` for the band
# verdict. Both must rank identically — otherwise an unrecognised
# ``CONDITIONAL_GO`` falls through ``_worse_verdict``'s "unknown → return the
# other" branch and a green band SOFTENS the composite to GO (fail-OPEN). Rank
# both spellings the same.
_VERDICT_RANK = {"NO_GO": 0, "CONDITIONAL": 1, "CONDITIONAL_GO": 1, "GO": 2}


def _normalize_verdict(verdict: str) -> str:
    """Map the band vocabulary (``CONDITIONAL``) onto the canonical
    recommendation vocabulary (``CONDITIONAL_GO``) so the value returned to the
    API/UI/override endpoint is always one of GO / CONDITIONAL_GO / NO_GO."""
    return "CONDITIONAL_GO" if verdict == "CONDITIONAL" else verdict


def _worse_verdict(a: str, b: str) -> str:
    """Return whichever recommendation is stricter (NO_GO > CONDITIONAL > GO).

    Unknown verdicts fall through to the input value — never softens.
    """
    if a not in _VERDICT_RANK:
        return b
    if b not in _VERDICT_RANK:
        return a
    return a if _VERDICT_RANK[a] <= _VERDICT_RANK[b] else b


async def _apply_band_floor(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    recommendation: str,
    pass_rate: float,
) -> tuple[str, Optional[str], list[str]]:
    """Layer the project's ``PolicyPassRateBands`` over the composite verdict.

    "Fail-closed" semantics: if the band-derived verdict is *stricter* than
    the composite, the recommendation is downgraded. The composite is never
    softened — bands can only block, never unblock. This keeps /overview and
    /release-gate in lockstep: when /overview shows red (band=red), the gate
    cannot say GO even if the composite was below the NO_GO threshold.

    The band classifier mirrors what ``metrics_service.classify_with_policy``
    uses for /overview, so both pages produce the same colour for the same
    project + run. Returns ``(final_recommendation, band, downgrades)``;
    ``band`` is ``None`` and ``downgrades`` is empty when no policy is active.

    The open-CRITICAL defect count for the ``max_p0_defects`` hard cap is
    resolved here via the shared ``count_open_critical_defects`` helper rather
    than trusting a caller-supplied number — the synth and deep call sites
    previously passed an all-severities total and a dead ``severity == "P0"``
    zero respectively, so the cap either over-counted or never fired.
    """
    if project_id is None:
        return recommendation, None, []

    # Local import to keep release_council_service free of metrics-service
    # coupling at module level — _resolve_policy_for_project also depends on
    # ReleaseGatePolicy, which release_council already touches via the
    # policy_id / policy_version fields. Both services live downstream of
    # the same data, so this is safe.
    from app.services.metrics_service import (
        _resolve_policy_for_project,
        classify_with_policy,
        count_open_critical_defects,
    )

    policy_doc = await _resolve_policy_for_project(db, str(project_id))
    if policy_doc is None:
        return recommendation, None, []

    bands = policy_doc.get("pass_rate_bands") or {}
    caps = policy_doc.get("hard_caps") or {}
    # Count open CRITICAL defects for the P0 hard cap. flaky / new_failures
    # stats aren't handy on the synth path, so pass 0 — those caps join the
    # picture when a deep run lands. The band still reflects pass-rate + open
    # CRITICAL defects, which is what the user asked the gate to honour.
    active_p0 = await count_open_critical_defects(db, project_id)
    classified = classify_with_policy(
        pass_rate=pass_rate,
        active_defects_p0=active_p0,
        flaky_count=0,
        new_failures_24h=0,
        bands=bands,
        hard_caps=caps,
    )
    # Normalise the band verdict onto the recommendation vocabulary so
    # ``_worse_verdict`` compares like-with-like and the returned value stays
    # canonical (CONDITIONAL_GO, not the band's CONDITIONAL).
    band_verdict = _normalize_verdict(classified["verdict"])
    final = _worse_verdict(recommendation, band_verdict)
    return final, classified["band"], classified["downgrades"]


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


async def _synthesize_release_council(
    run_id: uuid.UUID,
    db: AsyncSession,
) -> Optional[ReleaseCouncilResponse]:
    """Deterministic quick-look release decision derived from a run's
    aggregates when no ``ReleaseDecision`` row has been persisted yet.

    Why this exists: the ReleaseRiskAgent only runs as part of the
    *deep* pipeline. Runs ingested via the live-stream SDK queue
    ``run_agent_pipeline`` with ``workflow_type="offline"``, so they
    never get a ReleaseDecision row. Without this helper, every such
    run produced a 404 on /api/v1/release-readiness/{id} and the
    /release-gate page showed an "empty state — go trigger deep
    investigation" prompt for routine successful runs.

    What this is NOT:
      * A replacement for the deep pipeline. We don't have failure
        clusters, defect-by-component, override audit, or LLM
        narrative here — those still require deep investigation.
      * A persisted row. The synthesis runs at read time only; the
        next deep run still writes the real ReleaseDecision and
        supersedes this view.
      * A policy evaluator. We fall back to the hardcoded thresholds
        in ``settings`` so the synth never blocks on policy table
        gaps. A real deep run picks up the project-scoped policy.

    Returns ``None`` (caller maps to 404) only when the run itself
    doesn't exist — that's a genuine "not found" the user should see.
    """
    run = (await db.execute(
        select(TestRun).where(TestRun.id == run_id)
    )).scalar_one_or_none()
    if run is None:
        return None

    # Count open defects scoped to the run's project — same input the
    # real agent uses; cheap one-query lookup.
    open_defects = 0
    if run.project_id is not None:
        open_defects = int(
            (await db.execute(
                select(sa_func.count(Defect.id))
                .where(Defect.project_id == run.project_id)
                .where(Defect.resolution_status == "OPEN")
            )).scalar_one() or 0
        )

    pass_rate = float(run.pass_rate or 0.0)
    threshold = float(settings.RELEASE_PASS_RATE_THRESHOLD)

    # With no per-test analyses available (those are produced by the
    # offline pipeline's analysis stage, which has already run by the
    # time this endpoint is called — but the synth deliberately stays
    # cheap and deterministic), every analysis-driven dimension scores
    # 0. The pass-rate-driven dimensions still produce meaningful
    # numbers, and the hard floor below catches the failing-run case
    # even when ``composite`` is small.
    dim_scores = compute_dimension_scores(
        analyses={},
        anomalies=[],
        pass_rate=pass_rate,
        is_regression=False,
        regression_tests=[],
        failure_clusters=[],
        open_defects=open_defects,
    )
    composite = compute_composite(dim_scores)
    if pass_rate < threshold * _HARD_FLOOR_FACTOR:
        # Mirror the agent's hard floor: a run that drops below 70% of
        # the configured pass-rate threshold is NO_GO regardless of
        # the composite score. Bump composite so the recommendation
        # function reaches NO_GO via the standard mapping.
        composite = max(composite, 60.0)
    recommendation = score_to_recommendation(composite, pass_rate, threshold)

    # Layer the active ReleaseGatePolicy's pass-rate bands over the composite
    # so /release-gate honours the same colours /overview renders. Fail-closed:
    # the band can downgrade the verdict but never soften it.
    recommendation, band, band_downgrades = await _apply_band_floor(
        db, run.project_id, recommendation, pass_rate,
    )

    reasoning = (
        "Quick-look decision derived from this run's aggregates "
        f"(pass rate {pass_rate:.1f}%, {open_defects} open defects). "
        "Run Deep Investigation for richer insights — failure "
        "clusters, defect breakdown, and AI narrative."
    )

    return ReleaseCouncilResponse(
        run_id=str(run_id),
        recommendation=recommendation,
        release_readiness_band=band,
        band_downgrades=band_downgrades,
        risk_score=int(round(composite)),
        composite_risk=composite,
        dimension_scores=_build_dimension_scores(dim_scores),
        blocking_issues=[],
        conditions_for_go=[],
        reasoning=reasoning,
        score_model_version=SCORE_MODEL_VERSION,
        input_snapshot={
            "synthesized": True,
            "pass_rate": pass_rate,
            "open_defects": open_defects,
            # The CONFIGURED target (RELEASE_PASS_RATE_THRESHOLD). Reporting it
            # alone was actively misleading: a run at 83% sat next to
            # "threshold: 90.0" and a GO verdict, which reads as a contradiction.
            "threshold": threshold,
            # ...because `threshold` is NOT the GO cutoff. Pass rate only forces
            # a verdict when it falls under `hard_floor_factor` (0.7) of the
            # threshold; above that the composite risk score decides. Publish the
            # number actually applied so the verdict is explicable from its own
            # snapshot instead of appearing to contradict it.
            "no_go_floor_pct": round(threshold * _HARD_FLOOR_FACTOR, 2),
            "hard_floor_factor": _HARD_FLOOR_FACTOR,
            "verdict_driver": (
                "pass_rate_floor"
                if pass_rate < threshold * _HARD_FLOOR_FACTOR
                # Above the floor but under the configured bar -> CONDITIONAL_GO.
                # Named distinctly so the reader can tell "your own threshold
                # held this back" from "the risk model held this back".
                else "pass_rate_below_threshold"
                if pass_rate < threshold
                else "composite_risk"
            ),
        },
        cluster_insights=[],
        baseline_diff=None,
        open_defects_by_component=[],
        human_override=None,
        override_audit=[],
        pass_rate=pass_rate,
        build_number=run.build_number,
        policy_level="hardcoded",
        rule_evaluations=[],
        synthesized=True,
    )


async def get_release_council(
    run_id: uuid.UUID,
    db: AsyncSession,
    baseline_diff: Optional[BaselineDiff] = None,
) -> Optional[ReleaseCouncilResponse]:
    """
    Retrieve the release decision with full council context:
    dimension scores, linked cluster insights, baseline diff, open defects.

    When no persisted ``ReleaseDecision`` row exists (typically because
    deep investigation hasn't run yet), this function synthesises a
    deterministic quick-look decision from the run's aggregates so the
    /release-gate page can render something useful instead of a 404.
    The synthesised response carries ``synthesized=True`` so the UI can
    explain that richer insights (clusters, defect breakdown, override
    audit) need a deep investigation run.
    """
    result = await db.execute(
        select(ReleaseDecision).where(ReleaseDecision.test_run_id == run_id)
    )
    decision = result.scalar_one_or_none()
    if not decision:
        # Caller may still get None if the run itself doesn't exist —
        # the synth path needs the TestRun row to compute anything.
        return await _synthesize_release_council(run_id, db)

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

    # Layer the active ReleaseGatePolicy's pass-rate bands over the persisted
    # recommendation, same fail-closed semantic as the synthesised path. A
    # human override is left untouched — override is a deliberate human
    # decision that should not be silently downgraded by automated bands.
    final_recommendation = decision.recommendation
    band: Optional[str] = None
    band_downgrades: list[str] = []
    if decision.human_override is None:
        # The open-CRITICAL defect count for the P0 hard cap is resolved
        # inside _apply_band_floor via the shared helper — the old inline
        # ``severity == "P0"`` count matched nothing, so the cap never fired.
        final_recommendation, band, band_downgrades = await _apply_band_floor(
            db, project_id, decision.recommendation, run.pass_rate if run else 0.0,
        )

    return ReleaseCouncilResponse(
        run_id=str(run_id),
        recommendation=final_recommendation,
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
        # Preserve the originally-persisted recommendation alongside any
        # band-driven downgrade so the UI can show "GO → CONDITIONAL (band)".
        original_recommendation=decision.original_recommendation or (
            decision.recommendation if final_recommendation != decision.recommendation else None
        ),
        original_risk_score=decision.original_risk_score,
        override_audit=override_audit,
        pass_rate=run.pass_rate if run else None,
        build_number=run.build_number if run else None,
        policy_id=policy_id,
        policy_version=policy_version,
        policy_level=policy_level,
        release_readiness_band=band,
        band_downgrades=band_downgrades,
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
        select(ReleaseDecision)
        .where(ReleaseDecision.test_run_id == run_id)
        # The JSON audit list is read, appended, and written back. Serialize
        # concurrent overrides so a stale reader cannot overwrite another QA
        # lead's decision and silently erase its immutable audit entry.
        .with_for_update()
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
