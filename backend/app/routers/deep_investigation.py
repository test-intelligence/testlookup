"""
Deep Investigation Router.
Triggers the deep analysis pipeline and exposes cluster/finding results.
"""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, require_role, require_run_access
from app.db.postgres import AsyncSessionLocal, get_db
from app.models.postgres import (
    Defect,
    DeepFinding,
    FailureCluster,
    TestRun,
    User,
    UserRole,
)
from app.models.schemas import (
    DefectApprovalRequest,
    DefectApprovalResponse,
    DefectCandidateResponse,
    DefectPromotionRequest,
    DefectPromotionResponse,
)
from app.core.config import settings
from app.services.action_policy import ActionStatus, approve_action, reject_action
from app.services.analysis_router import get_analysis_mode
from app.services.cluster_ranking_service import rank_clusters
from app.services.defect_promotion_service import get_defect_candidate, promote_cluster
from app.worker.tasks import run_agent_pipeline

logger = logging.getLogger("routers.deep_investigation")

router = APIRouter(prefix="/api/v1/deep-investigate", tags=["Deep Investigation"])


class TriggerDeepRequest(BaseModel):
    mode: str = "deep"  # "deep" | "offline"


class TriggerDeepResponse(BaseModel):
    task_id: Optional[str] = None           # WF-2: Celery task ID (for queue tracking)
    pipeline_run_id: Optional[str] = None   # WF-2: Deprecated — use task_id + pipeline-status endpoint
    message: str
    run_id: str


class ClusterResponse(BaseModel):
    cluster_id: str
    label: str
    representative_error: Optional[str]
    member_test_ids: list[str]
    size: int
    cohesion_score: Optional[float] = None


class DeepFindingResponse(BaseModel):
    cluster_id: str
    root_cause: Optional[str]
    failure_category: Optional[str]
    confidence_score: Optional[int]
    causal_chain: Optional[list]
    evidence: Optional[list]
    affected_services: Optional[list]
    contract_violations: Optional[list]
    recommended_actions: Optional[list]


@router.post("/{run_id}", response_model=TriggerDeepResponse)
async def trigger_deep_investigation(
    run_id: uuid.UUID,
    body: TriggerDeepRequest,
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_run_access()),
):
    """
    Trigger the deep investigation pipeline for a completed test run.
    Uses workflow_type="deep" which adds failure clustering, flaky sentinel,
    test health analysis, and release risk on top of the standard 5-stage pipeline.
    """
    if not settings.DEEP_INVESTIGATION_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Deep investigation is disabled. Enable it in Settings > AI Configuration.",
        )
    current_mode = get_analysis_mode()
    if current_mode == "rules":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Deep investigation requires LLM or Auto mode. Current mode: rules.",
        )

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(TestRun).where(TestRun.id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test run not found")

    workflow_type = body.mode if body.mode in ("deep", "offline") else "deep"

    try:
        task = run_agent_pipeline.apply_async(
            kwargs={
                "test_run_id": str(run_id),
                "project_id": str(run.project_id),
                "build_number": run.build_number,
                "workflow_type": workflow_type,
            },
            queue="ai_analysis",
        )
        return TriggerDeepResponse(
            task_id=task.id,
            pipeline_run_id=task.id,  # Deprecated: kept for backward compat
            message=f"Deep investigation pipeline queued (mode={workflow_type}). Poll /pipeline-status for real execution state.",
            run_id=str(run_id),
        )
    except Exception as exc:
        logger.error("Failed to queue deep pipeline for run %s: %s", run_id, exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.get("/{run_id}/clusters", response_model=list[ClusterResponse])
async def get_failure_clusters(
    run_id: uuid.UUID,
    current_user: User = Depends(require_run_access()),
):
    """Return semantic failure clusters for a test run."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(FailureCluster)
            .where(FailureCluster.test_run_id == run_id)
            .order_by(FailureCluster.size.desc())
            .limit(200)  # Scalability: prevent unbounded results on large runs
        )
        clusters = result.scalars().all()

    return [
        ClusterResponse(
            cluster_id=c.cluster_id,
            label=c.label,
            representative_error=c.representative_error,
            member_test_ids=c.member_test_ids or [],
            size=c.size,
            cohesion_score=c.cohesion_score,
        )
        for c in clusters
    ]


@router.get("/{run_id}/findings", response_model=list[DeepFindingResponse])
async def get_deep_findings(
    run_id: uuid.UUID,
    current_user: User = Depends(require_run_access()),
):
    """Return deep investigation findings per failure cluster for a test run."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(DeepFinding)
            .where(DeepFinding.test_run_id == run_id)
            .limit(200)  # Scalability: prevent unbounded results on large runs
        )
        findings = result.scalars().all()

    return [
        DeepFindingResponse(
            cluster_id=f.cluster_id,
            root_cause=f.root_cause,
            failure_category=f.failure_category,
            confidence_score=f.confidence_score,
            causal_chain=f.causal_chain,
            evidence=f.evidence,
            affected_services=f.affected_services,
            contract_violations=f.contract_violations,
            recommended_actions=f.recommended_actions,
        )
        for f in findings
    ]


@router.get(
    "/{run_id}/clusters/{cluster_id}/defect-candidate",
    response_model=DefectCandidateResponse,
)
async def get_cluster_defect_candidate(
    run_id: uuid.UUID,
    cluster_id: str,
    current_user: User = Depends(require_run_access()),
    db: AsyncSession = Depends(get_db),
):
    """Get a pre-assembled defect candidate for a failure cluster."""
    try:
        candidate = await get_defect_candidate(str(run_id), cluster_id, db)
        return DefectCandidateResponse(**candidate)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/{run_id}/clusters/{cluster_id}/promote",
    response_model=DefectPromotionResponse,
)
async def promote_cluster_to_defect(
    run_id: uuid.UUID,
    cluster_id: str,
    body: DefectPromotionRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """Promote a failure cluster to a defect record (optionally with Jira ticket)."""
    # Resolve project_id from run
    async with AsyncSessionLocal() as _db:
        result = await _db.execute(select(TestRun).where(TestRun.id == run_id))
        run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Test run not found"
        )
    try:
        result_dict = await promote_cluster(
            run_id=str(run_id),
            cluster_id=cluster_id,
            project_id=str(run.project_id),
            request=body.model_dump(),
            db=db,
        )
        # BL-03: Mark intelligence snapshot stale after defect promotion.
        # mark_stale also only flushes, so a single commit below covers the
        # defect row, any Jira ticket link updates, and the staleness flag.
        try:
            from app.services.intelligence_snapshot_service import mark_stale
            await mark_stale(db, run_id)
        except Exception:
            pass  # Non-blocking
        await db.commit()
        return DefectPromotionResponse(**result_dict)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{run_id}/clusters/ranked")
async def get_ranked_clusters(
    run_id: uuid.UUID,
    current_user: User = Depends(require_run_access()),
    db: AsyncSession = Depends(get_db),
):
    """Return failure clusters ranked by impact score for triage prioritization."""
    result = await db.execute(
        select(FailureCluster)
        .where(FailureCluster.test_run_id == run_id)
        .order_by(FailureCluster.size.desc())
        .limit(20)
    )
    clusters = result.scalars().all()
    if not clusters:
        return {"items": [], "total": 0}

    cluster_dicts = [
        {
            "cluster_id": c.cluster_id,
            "label": c.label,
            "size": c.size,
            "representative_error": c.representative_error,
            "member_test_ids": c.member_test_ids or [],
            "cohesion_score": c.cohesion_score,
            "criticality_level": "HIGH" if c.size >= 10 else "MEDIUM" if c.size >= 4 else "LOW",
            "regression_classification": c.regression_classification,
        }
        for c in clusters
    ]

    ranked = rank_clusters(cluster_dicts)
    return {"items": ranked, "total": len(ranked)}


@router.get("/{run_id}/clusters/{cluster_id}/duplicate-check")
async def check_cluster_duplicate(
    run_id: uuid.UUID,
    cluster_id: str,
    current_user: User = Depends(require_run_access()),
    db: AsyncSession = Depends(get_db),
):
    """
    Check if a cluster likely duplicates an existing open defect.
    Returns duplicate info without creating anything.
    P3-9: Business logic extracted to cluster_service.
    """
    from app.services.cluster_service import check_duplicate

    try:
        return await check_duplicate(str(run_id), cluster_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ── Phase 4: Defect approval workflow ────────────────────────────────────────


@router.post(
    "/defects/{defect_id}/review",
    response_model=DefectApprovalResponse,
)
async def review_defect(
    defect_id: uuid.UUID,
    body: DefectApprovalRequest,
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """
    Approve or reject a defect that is pending review (QA Lead+ only).

    When approved with a Jira project key, the Jira ticket is created.
    """
    if body.action not in ("approve", "reject"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="action must be 'approve' or 'reject'",
        )

    if body.action == "reject" and not (body.reason and body.reason.strip()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Rejection reason is required.",
        )

    if body.action == "approve":
        success = await approve_action(
            db,
            model_class=Defect,
            record_id=defect_id,
            approver_id=current_user.id,
            approver_name=current_user.username,
        )
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Defect not found or not in pending_review status.",
            )

        # After approval, execute pending Jira ticket creation if applicable
        defect = await db.get(Defect, defect_id)
        jira_ticket = None
        jira_url = None
        if defect and defect.policy_evaluation:
            # Check if a Jira project key was specified at promotion time
            policy_eval = defect.policy_evaluation or {}
            if any("Jira" in r for r in policy_eval.get("policy_reasons", [])):
                logger.info("Approved defect %s — Jira creation deferred to manual step", defect_id)

        await db.commit()
        return DefectApprovalResponse(
            defect_id=str(defect_id),
            approval_status=ActionStatus.APPROVED,
            message="Defect approved successfully.",
            jira_ticket=jira_ticket,
            jira_url=jira_url,
        )
    else:
        success = await reject_action(
            db,
            model_class=Defect,
            record_id=defect_id,
            rejector_id=current_user.id,
            rejector_name=current_user.username,
            reason=body.reason or "",
        )
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Defect not found or not in pending_review status.",
            )
        await db.commit()
        return DefectApprovalResponse(
            defect_id=str(defect_id),
            approval_status=ActionStatus.REJECTED,
            message=f"Defect rejected: {body.reason}",
        )


@router.get("/defects/pending-review")
async def list_pending_defects(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """List all defects awaiting approval (QA Lead+ only, paginated)."""
    result = await db.execute(
        select(Defect)
        .where(Defect.approval_status == ActionStatus.PENDING_REVIEW)
        .order_by(Defect.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    defects = result.scalars().all()
    return [
        {
            "defect_id": str(d.id),
            "title": d.title,
            "severity": d.severity,
            "component": d.component,
            "owner_team": d.owner_team,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "policy_reasons": (d.policy_evaluation or {}).get("policy_reasons", []),
        }
        for d in defects
    ]
