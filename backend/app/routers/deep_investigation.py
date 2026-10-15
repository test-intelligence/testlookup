"""
Deep Investigation Router.
Triggers the deep analysis pipeline and exposes cluster/finding results.
"""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_accessible_project_ids,
    require_role,
    require_run_access,
    resolve_project_scope,
)
from app.db.postgres import get_db
from app.models.postgres import (
    Defect,
    DeepFinding,
    FailureCluster,
    Project,
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
    PendingDefectItem,
    PendingDefectList,
)
from app.services.action_policy import ActionStatus, approve_action, reject_action
from app.services.activity.service import ActorRef, record as record_activity
from app.services.cluster_ranking_service import rank_clusters
from app.services.deep_investigation_gate import require_deep_investigation
from app.services.defect_jira_service import file_issue_for_defect
from app.services.defect_promotion_service import get_defect_candidate, promote_cluster
from app.services.defect_triage_service import defect_label, reject_pending_defect
from app.worker.tasks import run_agent_pipeline
from app.services.cluster_snapshot import is_current_snapshot

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
    # AI-F4: who wrote the row — "pipeline" (real deep-pipeline output via
    # agents/deep_persistence.py), "seed" (demo data), or "unknown" (legacy
    # rows written before origin tagging). Lets consumers distinguish real
    # analysis from seeded fiction.
    origin: str = "unknown"
    # AI-F4: calibration basis of confidence_score when known —
    # "empirical" | "heuristic_estimate" | None.
    confidence_basis: Optional[str] = None


@router.post("/{run_id}", response_model=TriggerDeepResponse)
async def trigger_deep_investigation(
    run_id: uuid.UUID,
    body: TriggerDeepRequest,
    # QA_ENGINEER, as the page's button and ``POST /analyze`` require: each
    # call queues an LLM pipeline, and a VIEWER member could loop it.
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
    _: User = Depends(require_run_access()),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger the deep investigation pipeline for a completed test run.
    Uses workflow_type="deep" which adds failure clustering, flaky sentinel,
    test health analysis, and release risk on top of the standard 5-stage pipeline.
    """
    # AIC-01 / AIC-04: Settings > AI (over env, both directions) decides, through
    # the request session only. It used to read the env flag and the in-process
    # mode, which fell back to env after 30 s, so a stored "off" or "rules" did
    # nothing here.
    await require_deep_investigation(db)

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
                "requested_by": str(current_user.id),
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
    db: AsyncSession = Depends(get_db),
):
    """Return semantic failure clusters for a test run."""
    result = await db.execute(
        select(FailureCluster)
        .where(FailureCluster.test_run_id == run_id, is_current_snapshot())
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
    db: AsyncSession = Depends(get_db),
):
    """Return deep investigation findings per failure cluster for a test run."""
    result = await db.execute(
        select(DeepFinding)
        .where(DeepFinding.test_run_id == run_id)
        .limit(200)  # Scalability: prevent unbounded results on large runs
    )
    findings = result.scalars().all()

    return [_to_finding_response(f) for f in findings]


def _to_finding_response(f: DeepFinding) -> DeepFindingResponse:
    """Map a DeepFinding row to its response, surfacing the origin tag
    (pipeline vs seed vs legacy-unknown) from the log_evidence blob."""
    log_evidence = f.log_evidence if isinstance(f.log_evidence, dict) else {}
    return DeepFindingResponse(
        cluster_id=f.cluster_id,
        root_cause=f.root_cause,
        failure_category=f.failure_category,
        confidence_score=f.confidence_score,
        causal_chain=f.causal_chain,
        evidence=f.evidence,
        affected_services=f.affected_services,
        contract_violations=f.contract_violations,
        recommended_actions=f.recommended_actions,
        origin=str(log_evidence.get("origin") or "unknown"),
        confidence_basis=log_evidence.get("confidence_basis"),
    )


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
    # QA_ENGINEER, like ``POST /analytics/defects``: a VIEWER member created
    # OPEN defects on the release, which the gate and the KPIs count
    # (E2E 2026-10-10).
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """Promote a failure cluster to a defect record (optionally with Jira ticket)."""
    # Resolve project_id from run
    result = await db.execute(select(TestRun).where(TestRun.id == run_id))
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
            actor=current_user,
        )
        # BL-03: Mark intelligence snapshot stale after defect promotion.
        # mark_stale also only flushes, so a single commit below covers the
        # defect row, any Jira ticket link updates, the staleness flag and
        # the defect.promoted ledger row (when the promotion is effective).
        try:
            from app.services.intelligence_snapshot_service import mark_stale
            await mark_stale(db, run_id)
        except Exception:
            pass  # Non-blocking
        await db.commit()
        # VIZ-212: a new OPEN defect changes the cached dashboard's counts.
        from app.services.cache_service import bump_analytics_epoch

        await bump_analytics_epoch(run.project_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    # DEF-07: after the commit (the request's connection is back in the pool),
    # never inside it. A failing webhook does not undo the promotion.
    try:
        from app.services.defect_promoted_webhook import emit_defect_promoted

        await emit_defect_promoted(result_dict["defect_id"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("defect.promoted webhook failed for %s: %s", result_dict.get("defect_id"), exc)
    return DefectPromotionResponse(**result_dict)


@router.get("/{run_id}/clusters/ranked")
async def get_ranked_clusters(
    run_id: uuid.UUID,
    current_user: User = Depends(require_run_access()),
    db: AsyncSession = Depends(get_db),
):
    """Return failure clusters ranked by impact score for triage prioritization."""
    result = await db.execute(
        select(FailureCluster)
        .where(FailureCluster.test_run_id == run_id, is_current_snapshot())
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
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    db: AsyncSession = Depends(get_db),
):
    """
    Approve or reject a defect that is pending review (QA Lead+ only).

    Approve (DEF-06): the approval is committed FIRST, so a slow or failing
    Jira never undoes it. Then, when a Jira project key was given at
    promotion, the ticket is filed ON this defect (or linked to the open issue
    already filed for the same failure) and committed; offline it says so.
    ``defect.promoted`` (ledger + webhook) covers cluster promotions only, the
    webhook after the last commit.

    Reject (D-11): the defect is also closed as ``INVALID`` with the reason in
    the activity ledger, so a rejected suggestion stops blocking the gate.
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

    # ``require_role(QA_LEAD)`` gates by ROLE, never by project membership, and
    # ``approve_action``/``reject_action`` update by id alone -- so a QA lead of
    # one project could flip another project's pending defect to APPROVED or
    # REJECTED with their own name recorded as the approver, and use the
    # 200-vs-404 split as an existence oracle for defect ids.
    #
    # The sibling ``list_pending_defects`` below already documents this exact
    # class and was fixed for reads; the endpoint that WRITES those rows was
    # not. ``defects.project_id`` is indexed (``ix_defects_project_id``).
    target = await db.get(Defect, defect_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Defect not found or not in pending_review status.",
        )
    await resolve_project_scope(db, current_user, str(target.project_id))
    # Read before any commit / rollback: a rollback expires the loaded row.
    project_id = target.project_id

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
        # The bulk UPDATE left the identity-map copy stale.
        await db.refresh(target)
        pe = dict(target.policy_evaluation or {})
        jira_project_key = (
            str(pe.get("jira_project_key") or pe.get("requested_project_key") or "").strip() or None
        )
        if target.cluster_id:
            await record_activity(
                db,
                project_id=project_id,
                event_type="defect.promoted",
                actor=ActorRef.from_user(current_user),
                entity_id=target.id,
                entity_label=defect_label(target),
                entity_type="defect",
                release_id=target.release_id,
                context={"cluster_id": target.cluster_id, "severity": target.severity},
            )
        # Commit 1: the approval stands whatever Jira does next.
        await db.commit()

        filing: dict = {"status": "not_requested", "reason": None, "error": None}
        if jira_project_key:
            try:
                filing = await file_issue_for_defect(
                    db, target, current_user, jira_project_key=jira_project_key,
                )
                if filing.get("status") == "filed":
                    await record_activity(
                        db,
                        project_id=project_id,
                        event_type="defect.ticket_created",
                        actor=ActorRef.from_user(current_user),
                        entity_id=target.id,
                        entity_label=defect_label(target),
                        entity_type="defect",
                        release_id=target.release_id,
                        context={"ticket_key": filing.get("jira_key")},
                    )
                # Commit 2: the ticket link (or the record that filing failed).
                await db.commit()
            except Exception as exc:  # noqa: BLE001 — commit 1 already holds the approval
                await db.rollback()
                logger.warning("Jira filing after approving defect %s failed: %s", defect_id, exc)
                filing = {"status": "failed", "reason": None, "error": type(exc).__name__}
        from app.services.cache_service import bump_analytics_epoch

        await bump_analytics_epoch(project_id)
        try:
            from app.services.defect_promoted_webhook import emit_defect_promoted

            await emit_defect_promoted(defect_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("defect.promoted webhook failed for %s: %s", defect_id, exc)

        # The row as committed (after a rollback, re-read from the database).
        approved = await db.get(Defect, defect_id)
        status_word = getattr(approved, "approval_status", None) or ActionStatus.APPROVED.value
        jira_key = getattr(approved, "jira_ticket_id", None)
        jira_url = getattr(approved, "jira_ticket_url", None)
        return DefectApprovalResponse(
            defect_id=str(defect_id),
            approval_status=str(getattr(status_word, "value", status_word)),
            message=_approval_message(filing),
            jira_ticket={"key": jira_key, "url": jira_url} if jira_key else None,
            jira_url=jira_url,
            jira_status=filing.get("status"),
            jira_reason=filing.get("reason") if filing.get("status") == "not_filed" else None,
            jira_error=filing.get("error") if filing.get("status") in ("failed", "unknown") else None,
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
        await db.refresh(target)
        await reject_pending_defect(
            db, defect=target, actor=current_user, reason=(body.reason or "").strip(),
        )
        await db.commit()
        from app.services.cache_service import bump_analytics_epoch

        await bump_analytics_epoch(project_id)
        return DefectApprovalResponse(
            defect_id=str(defect_id),
            approval_status=ActionStatus.REJECTED,
            message=f"Defect rejected: {body.reason}",
        )


_NOT_FILED_LABELS = {
    "offline_mode": "offline mode",
    "disabled": "Jira integration disabled",
    "not_configured": "Jira not configured",
}


def _approval_message(filing: dict) -> str:
    """One sentence for API callers; the UI writes its own toast from ``jira_status``."""
    status_word = filing.get("status")
    if status_word == "filed":
        return f"Defect approved; Jira {filing.get('jira_key')} filed."
    if status_word == "linked":
        return f"Defect approved; linked to the open Jira issue {filing.get('jira_key')}."
    if status_word == "not_filed":
        why = _NOT_FILED_LABELS.get(str(filing.get("reason")), str(filing.get("reason")))
        return f"Defect approved; Jira not filed ({why})."
    if status_word in ("failed", "unknown"):
        return f"Defect approved; Jira filing failed: {filing.get('error')}"
    return "Defect approved successfully."


@router.get("/defects/pending-review", response_model=PendingDefectList)
async def list_pending_defects(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    project_id: Optional[str] = Query(None, description="Only this project's queue (must be accessible)"),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    db: AsyncSession = Depends(get_db),
):
    """List defects awaiting approval in the caller's projects (QA Lead+, paginated).

    ``require_role(QA_LEAD)`` gates by ROLE, not by project membership, and this
    query previously had no project filter at all — so a QA lead of one project
    received the titles, components and owner teams of pending defects belonging
    to every OTHER project on the deployment.

    ``get_accessible_project_ids()`` returning ``None`` means ADMIN, who is
    legitimately unscoped; the defect was that non-admins were unscoped too.
    ``defects.project_id`` is indexed (``ix_defects_project_id``), so the schema
    already anticipated this filter.

    DEF-06: ``{items, total, page, page_size}``. ``total`` is a COUNT over the
    same scope, not the page length, so the Inbox badge never reads
    ``min(n, page_size)``. ``project_id`` pins one project (400 malformed, 403
    not accessible); soft-deleted projects' defects never wait for anyone.
    """
    accessible = await get_accessible_project_ids(db, current_user)

    stmt = select(Defect).where(Defect.approval_status == ActionStatus.PENDING_REVIEW)
    if accessible is not None:
        # Non-admin: restrict to projects the caller actually belongs to. An
        # empty set must yield NO rows, not every row.
        stmt = stmt.where(Defect.project_id.in_(accessible))
    if project_id:
        pinned, _ = await resolve_project_scope(db, current_user, project_id)
        stmt = stmt.where(Defect.project_id == pinned)
    # Live projects only: a soft-deleted project's queue is nobody's work.
    stmt = stmt.where(Defect.project_id.in_(select(Project.id).where(Project.is_active.is_(True))))

    total = int(
        await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    )
    result = await db.execute(
        stmt.order_by(Defect.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    defects = result.scalars().all()
    return PendingDefectList(
        items=[_pending_item(d) for d in defects],
        total=total,
        page=page,
        page_size=page_size,
    )


def _pending_item(d: Defect) -> PendingDefectItem:
    pe = d.policy_evaluation if isinstance(d.policy_evaluation, dict) else {}
    key = pe.get("jira_project_key") or pe.get("requested_project_key")
    run_id = pe.get("source_run_id") or pe.get("requested_run_id")
    reasons = pe.get("policy_reasons") or []
    return PendingDefectItem(
        defect_id=str(d.id),
        project_id=str(d.project_id),
        title=d.title,
        severity=d.severity,
        component=d.component,
        owner_team=d.owner_team,
        created_at=d.created_at.isoformat() if d.created_at else None,
        policy_reasons=[str(r) for r in reasons] if isinstance(reasons, list) else [],
        jira_project_key=str(key) if key else None,
        run_id=str(run_id) if run_id else None,
        cluster_id=d.cluster_id,
        ai_confidence_score=d.ai_confidence_score,
    )
