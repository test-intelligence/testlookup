"""
Run Intelligence Service.

Aggregates all AI pipeline outputs for a test run into a single structured
payload.  The router (run_intelligence.py) is a thin HTTP wrapper around this
service — all DB/Mongo queries and business logic live here.
"""
import logging
import uuid
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.mongo import Collections
from app.models.enums import CriticalityLevel
from app.models.postgres import (
    AgentPipelineRun,
    AgentStageResult,
    AIAnalysis,
    FailureCluster,
    Project,
    ReleaseDecision,
    TestCase,
    TestRun,
)
from app.models.schemas import (
    ClusterInsightResponse,
    DefectCandidate,
    DimensionScore,
    SummaryModes,
)
from app.services.criticality_service import score_cluster
from app.services.run_diff_service import get_baseline_diff

logger = logging.getLogger("services.run_intelligence")

# ── Dimension metadata ────────────────────────────────────────────────────────

_DIMENSION_META: dict[str, tuple[str, float]] = {
    "user_impact":       ("User Impact",        0.25),
    "env_sensitivity":   ("Env Sensitivity",    0.10),
    "reproducibility":   ("Reproducibility",    0.15),
    "regression_likely": ("Regression Likely",  0.20),
    "hist_recurrence":   ("Hist. Recurrence",   0.10),
    "blast_radius":      ("Blast Radius",       0.15),
    "diagnosis_conf":    ("Diagnosis Confidence", 0.05),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _criticality_from_cluster_size(size: int) -> str:
    if size >= 20:
        return CriticalityLevel.CRITICAL
    if size >= 10:
        return CriticalityLevel.HIGH
    if size >= 4:
        return CriticalityLevel.MEDIUM
    return CriticalityLevel.LOW


def _build_dimension_scores(scores_dict: Optional[dict]) -> list[DimensionScore]:
    if not scores_dict:
        return []
    result = []
    for key, (label, weight) in _DIMENSION_META.items():
        score = float(scores_dict.get(key, 0))
        result.append(DimensionScore(
            name=key,
            label=label,
            score=round(score, 1),
            weight=weight,
            contribution=round(score * weight, 2),
        ))
    return result


def _stringify_model_value(value: Any, default: str = "UNKNOWN") -> str:
    raw_value = getattr(value, "value", value)
    return str(raw_value or default)



# ── Main service function ─────────────────────────────────────────────────────

async def get_run_intelligence(
    run_id: uuid.UUID,
    db: AsyncSession,
    mongo_db: Any,
    include: set[str] | None = None,
) -> dict:
    """
    Aggregate all AI pipeline outputs for a test run.

    Returns a dict matching the RunIntelligenceResponse shape.
    Raises ValueError if the run is not found (caller should map to 404).
    """
    include = include or set()

    # ── 1. Fetch the test run ─────────────────────────────────────────────────
    run_result = await db.execute(select(TestRun).where(TestRun.id == run_id))
    run = run_result.scalar_one_or_none()
    if not run:
        raise ValueError(f"TestRun {run_id} not found")

    run_summary = {
        "id": str(run.id),
        "build_number": run.build_number,
        "branch": run.branch,
        "status": _stringify_model_value(run.status),
        "total_tests": run.total_tests,
        "passed_tests": run.passed_tests,
        "failed_tests": run.failed_tests,
        "skipped_tests": run.skipped_tests,
        "pass_rate": run.pass_rate,
        "duration_ms": run.duration_ms,
        "start_time": run.start_time.isoformat() if run.start_time else None,
        "end_time": run.end_time.isoformat() if run.end_time else None,
        "ocp_namespace": run.ocp_namespace,
    }

    # Partial-failure accumulator — sections that fail don't crash the whole response
    _partial_errors: list[str] = []

    # ── 2. Fetch 4-layer structured summary from MongoDB ──────────────────────
    summary_doc = None
    try:
        summary_doc = await mongo_db[Collections.RUN_SUMMARIES].find_one({"test_run_id": str(run_id)})
    except Exception as exc:
        logger.warning("Failed to fetch summary from MongoDB: %s", exc)
        _partial_errors.append("summary_unavailable")
    structured_summary: Optional[dict] = None
    fallback_used = False
    generated_at = None

    if summary_doc:
        summary_doc.pop("_id", None)
        structured_summary = {
            "executive_summary":   summary_doc.get("executive_summary") or summary_doc.get("layer1_executive_summary"),
            "layer1_executive":    summary_doc.get("layer1_executive_summary"),
            "layer2_incident":     summary_doc.get("layer2_incident_view"),
            "layer3_evidence":     summary_doc.get("layer3_evidence_pack"),
            "layer4_action_plan":  summary_doc.get("layer4_action_plan"),
            "generated_at":        summary_doc.get("generated_at"),
            "schema_version":      summary_doc.get("schema_version", 1),
        }
        fallback_used = bool(summary_doc.get("fallback_used", False))
        generated_at = summary_doc.get("generated_at")

    # Summary modes metadata
    summary_modes = SummaryModes(
        available=["executive", "developer", "manager"],
        default="executive",
    ).model_dump()

    # ── 3. Fetch failure clusters (raw) ──────────────────────────────────────
    clusters_result = await db.execute(
        select(FailureCluster)
        .where(FailureCluster.test_run_id == run_id)
        .order_by(FailureCluster.size.desc())
        .limit(20)
    )
    clusters_raw = clusters_result.scalars().all()

    # ── 4. Fetch AI analyses ─────────────────────────────────────────────────
    analyses_result = await db.execute(
        select(AIAnalysis)
        .join(TestCase, AIAnalysis.test_case_id == TestCase.id)
        .where(TestCase.test_run_id == run_id)
        .limit(100)
    )
    analyses = analyses_result.scalars().all()

    category_breakdown: dict[str, int] = {}
    confidence_scores: list[int] = []
    flaky_count = 0
    top_analyses = []

    for a in analyses:
        cat = _stringify_model_value(a.failure_category)
        category_breakdown[cat] = category_breakdown.get(cat, 0) + 1
        if a.confidence_score:
            confidence_scores.append(a.confidence_score)
        if a.is_flaky:
            flaky_count += 1
        top_analyses.append({
            "test_case_id": str(a.test_case_id),
            "failure_category": cat,
            "root_cause_summary": a.root_cause_summary,
            "confidence_score": a.confidence_score,
            "is_flaky": a.is_flaky,
            "requires_human_review": a.requires_human_review,
            "recommended_actions": a.recommended_actions or [],
            "evidence_references": a.evidence_references or [],
            "role_actions": a.role_actions or {},
        })

    avg_confidence = round(sum(confidence_scores) / len(confidence_scores), 1) if confidence_scores else 0

    # Affected suites from failed test cases
    suites_result = await db.execute(
        select(TestCase.suite_name, func.count(TestCase.id).label("count"))
        .where(TestCase.test_run_id == run_id, TestCase.status.in_(["FAILED", "BROKEN"]))
        .group_by(TestCase.suite_name)
        .order_by(func.count(TestCase.id).desc())
        .limit(10)
    )
    affected_suites = [
        {"suite": row.suite_name or "Unknown", "failed_count": row.count}
        for row in suites_result.all()
    ]

    # ── 5. Fetch release decision + dimension scores ──────────────────────────
    release_result = await db.execute(
        select(ReleaseDecision).where(ReleaseDecision.test_run_id == run_id)
    )
    release_rec = release_result.scalar_one_or_none()
    release_decision: Optional[dict] = None
    dimension_scores: list[dict] = []
    dim_scores_raw: dict[str, float] = {}

    if release_rec:
        release_decision = {
            "recommendation": release_rec.recommendation,
            "risk_score": release_rec.risk_score,
            "composite_risk": release_rec.composite_risk,
            "blocking_issues": release_rec.blocking_issues or [],
            "conditions_for_go": release_rec.conditions_for_go or [],
            "reasoning": release_rec.reasoning,
        }
        dim_scores_raw = release_rec.dimension_scores or {}
        dimension_scores = [
            s.model_dump()
            for s in _build_dimension_scores(dim_scores_raw)
        ]

    # ── 3b. Build failure clusters with per-cluster dimension scores ──────────
    total_analyses = len(analyses)
    failure_clusters = []
    for c in clusters_raw:
        member_count = len(c.member_test_ids or [])
        cluster_dim_scores = score_cluster(
            cluster={"member_test_ids": c.member_test_ids or []},
            all_dim_scores=dim_scores_raw,
            total_analyses=total_analyses,
            member_count=member_count,
        )
        cluster_score_objects = [
            DimensionScore(
                name=key,
                label=label,
                score=round(cluster_dim_scores.get(key, 0.0), 1),
                weight=weight,
                contribution=round(cluster_dim_scores.get(key, 0.0) * weight, 2),
            )
            for key, (label, weight) in _DIMENSION_META.items()
            if key in cluster_dim_scores
        ]
        failure_clusters.append(
            ClusterInsightResponse(
                id=str(c.id),
                cluster_id=c.cluster_id,
                label=c.label,
                size=c.size,
                representative_error=c.representative_error,
                member_test_ids=c.member_test_ids or [],
                cohesion_score=c.cohesion_score,
                criticality_level=_criticality_from_cluster_size(c.size),
                dimension_scores=cluster_score_objects,
            ).model_dump()
        )

    # ── 6. Baseline diff — via run_diff_service (deterministic) ─────────────
    what_changed = None
    try:
        env_sensitivity = dim_scores_raw.get("env_sensitivity") if dim_scores_raw else None
        what_changed = await get_baseline_diff(
            run=run,
            db=db,
            flaky_count=flaky_count,
            env_sensitivity=env_sensitivity,
        )
    except Exception as exc:
        logger.warning("Failed to compute baseline diff for run %s: %s", run_id, exc)
        _partial_errors.append("baseline_diff_unavailable")

    # ── 7. Defect candidates — from persisted candidates or real promotion service ─
    defect_candidates = await _load_or_build_defect_candidates(
        run_id=run_id,
        failure_clusters=failure_clusters,
        top_analyses=top_analyses,
        db=db,
    )

    # ── 8. Pipeline stages ───────────────────────────────────────────────────
    pipeline_result = await db.execute(
        select(AgentPipelineRun)
        .where(AgentPipelineRun.test_run_id == run_id)
        .order_by(AgentPipelineRun.created_at.desc())
        .limit(1)
    )
    pipeline_run = pipeline_result.scalar_one_or_none()
    pipeline_stages: list[dict] = []
    pipeline_exec_meta: Optional[dict] = None

    if pipeline_run:
        pipeline_exec_meta = pipeline_run.execution_metadata
        stages_result = await db.execute(
            select(AgentStageResult)
            .where(AgentStageResult.pipeline_run_id == pipeline_run.id)
            .order_by(AgentStageResult.started_at)
        )
        pipeline_stages = [
            {
                "stage_name": s.stage_name,
                "status": s.status,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                "skipped_reason": s.skipped_reason,
                "execution_path": s.execution_path,
                "fallback_used": s.fallback_used,
            }
            for s in stages_result.scalars().all()
        ]

    # Determine all_green flag from stage metadata
    all_green = (run.failed_tests or 0) == 0 and (run.broken_tests or 0) == 0
    if pipeline_exec_meta:
        skipped = pipeline_exec_meta.get("skipped_stages", [])
        all_green = all_green or "root_cause_analysis" in skipped

    # ── 9. Role-aware action hints ───────────────────────────────────────────
    from app.services.role_actions_service import generate_role_actions, merge_role_actions

    # Determine top category
    top_category = (
        max(category_breakdown, key=lambda category: category_breakdown[category])
        if category_breakdown
        else "UNKNOWN"
    )

    # Fetch project's component_owner_map
    project_owner_map = None
    try:
        project_result = await db.execute(select(Project).where(Project.id == run.project_id))
        project_obj = project_result.scalar_one_or_none()
        if project_obj:
            project_owner_map = project_obj.component_owner_map
    except Exception:
        pass

    summary_role_actions: dict[str, str] = {}
    if structured_summary and structured_summary.get("layer4_action_plan"):
        hints = structured_summary["layer4_action_plan"].get("owner_hints", {})
        summary_role_actions = {k: hints.get(k, "") for k in ("qa", "developer", "sre", "release_manager")}

    role_actions = merge_role_actions(
        generate_role_actions(
            failure_category=top_category,
            component_owner_map=project_owner_map,
        ),
        summary_role_actions,
    )

    # ── 10. Enriched Provenance (Epic 4) ─────────────────────────────────────
    from app.services.evidence_service import build_enriched_provenance  # noqa: PLC0415

    actual_fallback = fallback_used or bool(pipeline_exec_meta and pipeline_exec_meta.get("fallback_used"))
    provenance = build_enriched_provenance(
        pipeline_meta=pipeline_exec_meta,
        analyses=top_analyses,
        clusters=failure_clusters,
        has_baseline=what_changed is not None,
        has_release_decision=release_decision is not None,
        fallback_used=actual_fallback,
        generated_at=generated_at,
    )

    return {
        "run": run_summary,
        "structured_summary": structured_summary,
        "failure_clusters": failure_clusters,
        "category_breakdown": category_breakdown,
        "affected_suites": affected_suites,
        "top_analyses": top_analyses[:20],
        "avg_confidence": avg_confidence,
        "release_decision": release_decision,
        "role_actions": role_actions,
        "pipeline_stages": pipeline_stages,
        "intelligence_available": structured_summary is not None or bool(failure_clusters),
        "all_green": all_green,
        "dimension_scores": dimension_scores,
        "what_changed_since_last_good_run": what_changed,
        "defect_candidates": defect_candidates,
        "summary_modes": summary_modes,
        "provenance": provenance,
        "partial_errors": _partial_errors if _partial_errors else None,
    }



async def _load_or_build_defect_candidates(
    run_id: uuid.UUID,
    failure_clusters: list[dict],
    top_analyses: list[dict],
    db: AsyncSession,
) -> list[dict]:
    """
    Load persisted defect candidates for this run, or build them from the
    real defect_promotion_service. Falls back to a lightweight heuristic
    if the full service is unavailable.

    Returns candidates enriched with lifecycle status (pending/promoted/duplicate/dismissed).
    """
    from app.models.postgres import DefectCandidate as DefectCandidateModel

    if not failure_clusters:
        return []

    # ── 1. Check for already-persisted candidates ────────────────────────────
    try:
        persisted_result = await db.execute(
            select(DefectCandidateModel)
            .where(DefectCandidateModel.run_id == run_id)
            .order_by(DefectCandidateModel.composite_score.desc().nulls_last())
            .limit(10)
        )
        persisted = persisted_result.scalars().all()

        if persisted:
            return [
                {
                    "cluster_id": c.cluster_id,
                    "label": c.title or c.cluster_id,
                    "severity_hint": c.severity or "HIGH",
                    "failure_category": c.failure_category or "UNKNOWN",
                    "confidence": int(c.composite_score * 100) if c.composite_score else 0,
                    "recommended_actions": [],
                    "status": c.status,
                    "duplicate_detected": c.is_duplicate,
                    "duplicate_defect_id": str(c.duplicate_of) if c.duplicate_of else None,
                    "promoted_defect_id": str(c.promoted_defect_id) if c.promoted_defect_id else None,
                    "composite_score": c.composite_score,
                    "evidence_bundle": c.evidence_bundle or {},
                }
                for c in persisted
            ]
    except Exception as exc:
        logger.warning("Failed to load persisted defect candidates: %s", exc)

    # ── 2. Try the real promotion service for top clusters ───────────────────
    try:
        from app.services.defect_promotion_service import get_defect_candidate

        candidates = []
        for cluster in failure_clusters[:5]:
            cluster_id = cluster.get("cluster_id", "")
            if not cluster_id:
                continue
            try:
                candidate = await get_defect_candidate(str(run_id), cluster_id, db)
                # Persist the candidate for future reads
                try:
                    db.add(DefectCandidateModel(
                        run_id=run_id,
                        cluster_id=cluster_id,
                        title=candidate.get("title", "")[:500],
                        description=candidate.get("description", ""),
                        severity=candidate.get("severity", "HIGH"),
                        component=candidate.get("component"),
                        owner_team=candidate.get("owner_team"),
                        is_duplicate=candidate.get("duplicate_detected", False),
                        duplicate_of=uuid.UUID(candidate["duplicate_defect_id"]) if candidate.get("duplicate_defect_id") else None,
                        evidence_bundle=candidate.get("evidence_bundle"),
                        criticality_scores=candidate.get("criticality_scores"),
                        composite_score=candidate.get("composite_score", 0.0),
                        failure_category=candidate.get("failure_category"),
                        member_count=candidate.get("member_count", 0),
                        status="pending",
                    ))
                except Exception:
                    pass  # Persist failure is non-blocking

                candidates.append({
                    "cluster_id": cluster_id,
                    "label": candidate.get("title", cluster.get("label", "")),
                    "severity_hint": candidate.get("severity", "HIGH"),
                    "failure_category": candidate.get("failure_category", "UNKNOWN"),
                    "confidence": int((candidate.get("composite_score") or 0) * 100),
                    "recommended_actions": [],
                    "status": "pending",
                    "duplicate_detected": candidate.get("duplicate_detected", False),
                    "duplicate_defect_id": candidate.get("duplicate_defect_id"),
                    "promoted_defect_id": None,
                    "composite_score": candidate.get("composite_score"),
                    "evidence_bundle": candidate.get("evidence_bundle", {}),
                })
            except Exception as exc:
                logger.debug("Skipping candidate for cluster %s: %s", cluster_id, exc)

        if candidates:
            try:
                await db.commit()
            except Exception:
                await db.rollback()
            return candidates
    except Exception as exc:
        logger.warning("Full defect candidate generation failed, using fallback: %s", exc)

    # ── 3. Fallback: lightweight heuristic (same as old stub) ────────────────
    return _build_fallback_candidates(failure_clusters, top_analyses)


def _build_fallback_candidates(
    failure_clusters: list[dict],
    top_analyses: list[dict],
) -> list[dict]:
    """Lightweight heuristic fallback when the promotion service is unavailable."""
    candidates = []
    for cluster in failure_clusters[:5]:
        criticality = cluster.get("criticality_level", "MEDIUM")
        severity_hint = "CRITICAL" if criticality in ("CRITICAL", "HIGH") else "MAJOR"

        failure_category = "UNKNOWN"
        best_confidence = 0
        for a in top_analyses:
            if a.get("test_case_id") in (cluster.get("member_test_ids") or []):
                score = a.get("confidence_score") or 0
                if score > best_confidence:
                    best_confidence = score
                    failure_category = a.get("failure_category", "UNKNOWN")

        candidates.append(DefectCandidate(
            cluster_id=cluster.get("cluster_id", ""),
            label=cluster.get("label", ""),
            severity_hint=severity_hint,
            failure_category=failure_category,
            confidence=best_confidence,
            recommended_actions=[],
        ).model_dump())

    return candidates


async def get_run_mode_summary(
    run_id: uuid.UUID,
    mode: str,
    db: AsyncSession,
    mongo_db: Any,
) -> dict:
    """
    Return a mode-specific summary view (developer | manager | executive).

    Developer mode: emphasis on layer3_evidence (stack traces, log anomalies, data sources),
                    layer4 fix recommendations, and analysis confidence.
    Manager mode:   emphasis on layer1 executive summary, criticality, release impact,
                    and layer4 immediate mitigation.
    Executive:      short layer1 summary + release recommendation.

    For developer/manager modes: checks mode_variants cache in MongoDB.
    If not cached, assembles context from stored layers and renders on-demand, then caches.

    Falls back to a deterministic summary from PostgreSQL when MongoDB has no document.
    """
    valid_modes = {"developer", "manager", "executive"}
    if mode not in valid_modes:
        mode = "executive"

    # Try MongoDB first
    summary_doc = await mongo_db[Collections.RUN_SUMMARIES].find_one(
        {"test_run_id": str(run_id)}
    )

    if summary_doc:
        summary_doc.pop("_id", None)
        layer1 = summary_doc.get("layer1_executive_summary") or summary_doc.get(
            "executive_summary"
        )
        layer2 = summary_doc.get("layer2_incident_view")
        layer3 = summary_doc.get("layer3_evidence_pack")
        layer4 = summary_doc.get("layer4_action_plan")
        citations = summary_doc.get("citations", [])
        provenance = summary_doc.get("provenance")

        # For developer/manager modes, check mode_variants cache then generate on-demand
        if mode in ("developer", "manager"):
            cached_variant = (summary_doc.get("mode_variants") or {}).get(mode)
            if not cached_variant:
                cached_variant = await _generate_and_cache_mode_variant(
                    run_id=run_id,
                    mode=mode,
                    summary_doc=summary_doc,
                    mongo_db=mongo_db,
                    db=db,
                )

            if cached_variant:
                # Build similar_failures from layer3 if present
                similar_failures_raw = []
                if isinstance(layer3, dict):
                    similar_failures_raw = [
                        {"test_name": s}
                        for s in (layer3.get("similar_historical_failures") or [])
                        if s
                    ]
                variant_citations = cached_variant.get("citations", [])
                return {
                    "test_run_id": str(run_id),
                    "mode": mode,
                    "executive_summary": cached_variant.get("headline")
                    or cached_variant.get("executive_summary")
                    or layer1,
                    "markdown_report": _variant_to_markdown(cached_variant, mode),
                    "layer1_executive": layer1,
                    "layer2_incident": layer2,
                    "layer3_evidence": layer3,
                    "layer4_action_plan": layer4,
                    "fallback_used": bool(cached_variant.get("fallback_used")),
                    "generated_at": summary_doc.get("generated_at"),
                    "citations": variant_citations,
                    "similar_failures": similar_failures_raw,
                    "provenance": provenance,
                }

        if mode == "developer":
            markdown = _render_developer_markdown(layer1, layer2, layer3, layer4)
        elif mode == "manager":
            markdown = _render_manager_markdown(layer1, layer2, layer4)
        else:
            markdown = _render_executive_markdown(layer1, layer2)

        # Extract similar_failures from layer3
        similar_failures_list: list[dict] = []
        if isinstance(layer3, dict):
            similar_failures_list = [
                {"test_name": s}
                for s in (layer3.get("similar_historical_failures") or [])
                if s
            ]

        return {
            "test_run_id": str(run_id),
            "mode": mode,
            "executive_summary": layer1,
            "markdown_report": markdown,
            "layer1_executive": layer1,
            "layer2_incident": layer2,
            "layer3_evidence": layer3 if mode == "developer" else None,
            "layer4_action_plan": layer4,
            "fallback_used": bool(summary_doc.get("fallback_used", False)),
            "generated_at": summary_doc.get("generated_at"),
            "citations": citations,
            "similar_failures": similar_failures_list,
            "provenance": provenance,
        }

    # Fallback: build deterministic summary from PostgreSQL data
    from app.core.metrics import summary_fallback_total  # noqa: PLC0415
    from app.services.run_summary_service import build_fallback_summary  # noqa: PLC0415

    summary_fallback_total.labels(mode=mode).inc()

    fallback = await build_fallback_summary(db, str(run_id))
    if not fallback:
        raise ValueError(f"TestRun {run_id} not found")

    return {
        "test_run_id": str(run_id),
        "mode": mode,
        "executive_summary": fallback.executive_summary,
        "markdown_report": fallback.markdown_report,
        "layer1_executive": fallback.executive_summary,
        "layer2_incident": None,
        "layer3_evidence": None,
        "layer4_action_plan": None,
        "fallback_used": True,
        "generated_at": fallback.generated_at,
        "citations": [],
        "similar_failures": [],
        "provenance": None,
    }


async def _generate_and_cache_mode_variant(
    run_id: uuid.UUID,
    mode: str,
    summary_doc: dict,
    mongo_db: Any,
    db: Optional[AsyncSession] = None,
) -> dict | None:
    """
    Generate a mode-specific summary variant using REAL run data from PostgreSQL,
    enriched with MongoDB layers for evidence and context.

    ROI-03: Replaces the old lossy reconstruction that used placeholder values
    (pass_rate=0, branch="unknown") with actual database facts.
    """
    from app.services.summary_assembler import assemble_context  # noqa: PLC0415
    from app.services.summary_renderer import (  # noqa: PLC0415
        render_developer_summary,
        render_manager_summary,
    )

    layer2 = summary_doc.get("layer2_incident_view") or {}
    layer3 = summary_doc.get("layer3_evidence_pack") or {}
    layer4 = summary_doc.get("layer4_action_plan") or {}

    # ── Fetch REAL run data from PostgreSQL ───────────────────────────────────
    run_data = {
        "build_number": summary_doc.get("build_number", "unknown"),
        "branch": "unknown",
        "total_tests": 0,
        "failed_tests": 0,
        "pass_rate": 0.0,
    }

    if db is not None:
        try:
            run_result = await db.execute(select(TestRun).where(TestRun.id == run_id))
            run = run_result.scalar_one_or_none()
            if run:
                run_data = {
                    "build_number": run.build_number or "unknown",
                    "branch": run.branch or "unknown",
                    "total_tests": run.total_tests or 0,
                    "failed_tests": run.failed_tests or 0,
                    "pass_rate": run.pass_rate or 0.0,
                }
        except Exception as exc:
            logger.warning("Failed to fetch run data for mode variant: %s", exc)

    # ── Fetch REAL analyses from PostgreSQL ───────────────────────────────────
    analyses_data: dict = {}
    if db is not None:
        try:
            analyses_result = await db.execute(
                select(AIAnalysis)
                .join(TestCase, AIAnalysis.test_case_id == TestCase.id)
                .where(TestCase.test_run_id == run_id)
                .limit(20)
            )
            for a in analyses_result.scalars().all():
                cat = _stringify_model_value(a.failure_category)
                analyses_data[str(a.test_case_id)] = {
                    "failure_category": cat,
                    "root_cause_summary": a.root_cause_summary or "",
                    "confidence_score": a.confidence_score or 50,
                    "is_flaky": bool(a.is_flaky),
                    "recommended_actions": a.recommended_actions or [],
                    "evidence_references": a.evidence_references or [],
                }
        except Exception as exc:
            logger.debug("Failed to fetch analyses for mode variant: %s", exc)

    # Fallback: extract from MongoDB layers if DB fetch produced nothing
    if not analyses_data:
        if isinstance(layer2, dict) and layer2.get("likely_cause"):
            analyses_data["_doc_cause"] = {
                "failure_category": layer2.get("criticality", "UNKNOWN"),
                "root_cause_summary": layer2.get("likely_cause", ""),
                "confidence_score": 70,
                "is_flaky": False,
                "recommended_actions": [],
                "evidence_references": [],
            }
        if isinstance(layer3, dict):
            for i, trace in enumerate((layer3.get("top_stack_traces") or [])[:3]):
                analyses_data[f"_trace_{i}"] = {
                    "failure_category": "UNKNOWN",
                    "root_cause_summary": "",
                    "confidence_score": 50,
                    "is_flaky": False,
                    "recommended_actions": [],
                    "evidence_references": [{"source": "stack_trace", "excerpt": str(trace)[:300]}],
                }

    # ── Fetch REAL release decision ──────────────────────────────────────────
    release_inputs = None
    if db is not None:
        try:
            rel_result = await db.execute(
                select(ReleaseDecision).where(ReleaseDecision.test_run_id == run_id)
            )
            rel = rel_result.scalar_one_or_none()
            if rel:
                release_inputs = {
                    "recommendation": rel.recommendation,
                    "risk_score": rel.risk_score,
                    "blocking_issues": rel.blocking_issues or [],
                }
        except Exception:
            pass

    # Fallback from layer4
    if release_inputs is None and isinstance(layer4, dict):
        owner_hints = layer4.get("owner_hints") or {}
        rm_hint = owner_hints.get("release_manager", "")
        if rm_hint:
            release_inputs = {"recommendation": rm_hint.split("—")[0].strip()}

    # ── Fetch REAL clusters ──────────────────────────────────────────────────
    clusters_data = None
    if db is not None:
        try:
            clusters_result = await db.execute(
                select(FailureCluster)
                .where(FailureCluster.test_run_id == run_id)
                .order_by(FailureCluster.size.desc())
                .limit(10)
            )
            raw_clusters = clusters_result.scalars().all()
            if raw_clusters:
                clusters_data = [
                    {"cluster_id": c.cluster_id, "label": c.label, "size": c.size,
                     "representative_error": c.representative_error}
                    for c in raw_clusters
                ]
        except Exception:
            pass

    # Similar failures from layer3
    similar_failures = [
        {"test_name": s}
        for s in (layer3.get("similar_historical_failures") or [])
        if s
    ]

    assembled = assemble_context(
        run_data=run_data,
        anomalies=[],
        analyses=analyses_data,
        clusters=clusters_data,
        release_decision=release_inputs,
        similar_failures=similar_failures,
    )

    try:
        if mode == "developer":
            variant = await render_developer_summary(assembled)
        else:
            variant = await render_manager_summary(assembled)
    except Exception as exc:
        logger.warning("On-demand mode variant generation failed: %s", exc)
        return None

    # Cache in MongoDB
    try:
        await mongo_db[Collections.RUN_SUMMARIES].update_one(
            {"test_run_id": str(run_id)},
            {"$set": {f"mode_variants.{mode}": variant}},
        )
    except Exception as exc:
        logger.warning("Failed to cache mode variant: %s", exc)

    return variant


def _variant_to_markdown(variant: dict, mode: str) -> str:
    """Convert a mode variant dict to a markdown string."""
    sections: list[str] = []
    if mode == "developer":
        if variant.get("headline"):
            sections.append(f"## Summary\n{variant['headline']}")
        if variant.get("root_cause_analysis"):
            sections.append(f"## Root Cause\n{variant['root_cause_analysis']}")
        if variant.get("fix_recommendations"):
            recs = "\n".join(f"- {r}" for r in variant["fix_recommendations"])
            sections.append(f"## Fix Recommendations\n{recs}")
        if variant.get("validation_steps"):
            steps = "\n".join(f"- {s}" for s in variant["validation_steps"])
            sections.append(f"## Validation Steps\n{steps}")
        if variant.get("similar_historical_context"):
            sections.append(f"## Historical Context\n{variant['similar_historical_context']}")
    elif mode == "manager":
        if variant.get("executive_summary"):
            sections.append(f"## Executive Summary\n{variant['executive_summary']}")
        if variant.get("release_recommendation"):
            sections.append(
                f"## Release Recommendation\n{variant['release_recommendation']}"
            )
        if variant.get("key_risks"):
            risks = "\n".join(f"- {r}" for r in variant["key_risks"])
            sections.append(f"## Key Risks\n{risks}")
        if variant.get("recommended_decisions"):
            decisions = "\n".join(f"- {d}" for d in variant["recommended_decisions"])
            sections.append(f"## Recommended Decisions\n{decisions}")
    return "\n\n".join(s for s in sections if s).strip()


def _render_developer_markdown(layer1: Any, layer2: Any, layer3: Any, layer4: Any) -> str:
    sections: list[str] = []
    if layer1:
        sections.append(f"## Summary\n{layer1}")
    if layer2:
        sections.append(
            f"## What Failed\n"
            f"**Likely cause:** {layer2.get('likely_cause', '—')}\n"
            f"**Scope:** {layer2.get('scope', '—')}\n"
            f"**Criticality:** {layer2.get('criticality', '—')}"
        )
    if layer3:
        traces = "\n".join(f"```\n{t}\n```" for t in (layer3.get("top_stack_traces") or []))
        anomalies = "\n".join(f"- {a}" for a in (layer3.get("log_anomalies") or []))
        sources = ", ".join(layer3.get("data_sources_used") or [])
        sections.append(
            f"## Evidence Pack\n"
            f"**Data sources:** {sources or '—'}\n\n"
            + (f"**Stack traces:**\n{traces}\n\n" if traces else "")
            + (f"**Log anomalies:**\n{anomalies}" if anomalies else "")
        )
    if layer4:
        recs = "\n".join(f"- {r}" for r in (layer4.get("fix_recommendations") or []))
        steps = "\n".join(f"- {s}" for s in (layer4.get("validation_steps") or []))
        sections.append(
            f"## Fix Recommendations\n{recs or '—'}\n\n"
            f"## Validation Steps\n{steps or '—'}"
        )
    return "\n\n".join(s for s in sections if s).strip()


def _render_manager_markdown(layer1: Any, layer2: Any, layer4: Any) -> str:
    sections: list[str] = []
    if layer1:
        sections.append(f"## Executive Summary\n{layer1}")
    if layer2:
        sections.append(
            f"## Impact\n"
            f"**Criticality:** {layer2.get('criticality', '—')}\n"
            f"**Release impact:** {layer2.get('release_impact', '—')}\n"
            f"**Scope:** {layer2.get('scope', '—')}"
        )
    if layer4:
        mitigation = layer4.get("immediate_mitigation", "")
        rollback = layer4.get("rollback_guidance", "")
        sections.append(
            "## Recommended Actions\n"
            + (f"**Immediate:** {mitigation}\n\n" if mitigation else "")
            + (f"**Rollback guidance:** {rollback}" if rollback else "")
        )
    return "\n\n".join(s for s in sections if s).strip()


def _render_executive_markdown(layer1: Any, layer2: Any) -> str:
    sections: list[str] = []
    if layer1:
        sections.append(f"## Summary\n{layer1}")
    if layer2:
        sections.append(
            f"**Criticality:** {layer2.get('criticality', '—')} | "
            f"**Release impact:** {layer2.get('release_impact', '—')}"
        )
    return "\n\n".join(s for s in sections if s).strip()
