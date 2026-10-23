"""Third-party integration endpoints (Jira, etc.)."""
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_active_user, require_role, resolve_project_scope
from app.db.postgres import get_db, release_read_connection
from app.models.postgres import AIAnalysis, Defect, TestCase, TestRun, User, UserRole
from app.models.schemas import JiraIssueRequest, JiraIssueResponse
from app.services.action_policy import (
    ActionStatus,
    check_jira_ticket_creation_policy,
)
from app.services.defect_jira_service import resolve_jira_config
from app.services.defect_status import ACTIVE_STATUSES
from app.services.jira_client import JiraUnavailable, create_jira_issue

router = APIRouter(prefix="/api/v1/integrations", tags=["Integrations"])


@router.post(
    "/jira",
    response_model=JiraIssueResponse,
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def create_jira_defect(
    request: JiraIssueRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Create a Jira Bug ticket from AI triage analysis output."""
    # Fetch test case and AI analysis
    tc_result = await db.execute(
        select(TestCase).where(TestCase.id == request.test_case_id)
    )
    tc = tc_result.scalar_one_or_none()
    if not tc:
        raise HTTPException(status_code=404, detail="Test case not found")

    # ``TestCase`` is fetched by primary key and carries no project of its own;
    # the owner is ``TestCase -> TestRun.project_id``. Behind only
    # ``require_role(QA_ENGINEER)`` this filed another tenant's failure text and
    # AI analysis into a Jira project of the caller's choosing -- the same
    # copy-across-the-boundary shape as ``/agents/defect-command``.
    owning_project_id = (
        await db.execute(
            select(TestRun.project_id).where(TestRun.id == tc.test_run_id)
        )
    ).scalar_one_or_none()
    if owning_project_id is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    await resolve_project_scope(db, current_user, str(owning_project_id))

    ai_result = await db.execute(
        select(AIAnalysis).where(AIAnalysis.test_case_id == tc.id)
    )
    ai_analysis = ai_result.scalar_one_or_none()

    # ``TestCase`` has no ``project_id`` (the comment above): reading
    # ``tc.project_id`` raised AttributeError, a 500 before the pending defect
    # the approval queue lists could be staged (W06 §0.34).
    policy_result = await check_jira_ticket_creation_policy(
        project_id=str(owning_project_id),
        confidence_score=(
            ai_analysis.confidence_score if ai_analysis else None
        ),
        failure_category=(
            str(ai_analysis.failure_category) if ai_analysis and ai_analysis.failure_category else None
        ),
        source="integrations_jira_endpoint",
    )
    if policy_result["initial_status"] == ActionStatus.PENDING_REVIEW:
        defect = await _stage_pending_jira_defect(
            db=db,
            tc=tc,
            project_id=owning_project_id,
            ai_analysis=ai_analysis,
            request=request,
            policy_result=policy_result,
        )
        await db.commit()
        # VIZ-212: a new OPEN defect changes the cached dashboard's counts.
        from app.services.cache_service import bump_analytics_epoch

        await bump_analytics_epoch(owning_project_id)
        return JiraIssueResponse(
            approval_status=ActionStatus.PENDING_REVIEW.value,
            requires_approval=True,
            policy_reasons=policy_result["policy_reasons"],
            defect_id=defect.id,
            mutating_action="jira_ticket_creation",
        )

    stack_trace = tc.error_message or "Stack trace not available"
    dashboard_link = f"{settings.public_base_url}/runs/{request.run_id}/tests/{tc.id}"
    test_name = tc.test_name

    # W10 INT-01 (D-7): the Jira saved in Settings > Integrations, read on
    # this request's session; the connection is then handed back (nothing is
    # pending on this branch), so no pooled connection waits on Jira.
    jira_cfg = await resolve_jira_config(db)
    await release_read_connection(db)

    try:
        result = await create_jira_issue(
            project_key=request.project_key,
            test_name=test_name,
            run_id=str(request.run_id),
            ai_summary=request.ai_summary,
            recommended_action=request.recommended_action,
            stack_trace=stack_trace,
            dashboard_link=dashboard_link,
            cfg=jira_cfg,
        )
        return JiraIssueResponse(**result)
    except JiraUnavailable as e:
        # Offline mode, a disabled / unconfigured integration or a refused
        # host: nothing was sent, and the sentence says which.
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Jira API error: {str(e)}")


async def _stage_pending_jira_defect(
    *,
    db: AsyncSession,
    tc: TestCase,
    project_id: uuid.UUID,
    ai_analysis: AIAnalysis | None,
    request: JiraIssueRequest,
    policy_result: dict,
) -> Defect:
    """Persist a local approval record before any direct Jira mutation.

    ``project_id`` is the test case's owning project (``TestRun.project_id``),
    resolved and authorised by the caller: ``TestCase`` carries none.
    """
    # The newest live (OPEN or IN_PROGRESS) defect of this test. Two legacy
    # live rows must not raise MultipleResultsFound (the OPEN-only unique
    # index does not cover IN_PROGRESS).
    existing_result = await db.execute(
        select(Defect)
        .where(
            Defect.test_case_id == tc.id,
            Defect.resolution_status.in_(ACTIVE_STATUSES),
        )
        .order_by(Defect.created_at.desc())
        .limit(1)
    )
    defect = existing_result.scalars().first()
    policy_snapshot = _json_safe({
        **policy_result,
        "requested_project_key": request.project_key,
        "requested_run_id": str(request.run_id),
        "requested_summary_sha256": _hash_text(request.ai_summary),
        "requested_action_sha256": _hash_text(request.recommended_action),
    })

    if defect:
        defect.approval_status = ActionStatus.PENDING_REVIEW.value
        defect.policy_evaluation = policy_snapshot
        if not defect.title:
            defect.title = tc.test_name[:255]
        return defect

    defect = Defect(
        test_case_id=tc.id,
        project_id=project_id,
        title=tc.test_name[:255],
        description=request.ai_summary,
        ai_confidence_score=ai_analysis.confidence_score if ai_analysis else None,
        failure_category=ai_analysis.failure_category if ai_analysis else None,
        resolution_status="OPEN",
        approval_status=ActionStatus.PENDING_REVIEW.value,
        policy_evaluation=policy_snapshot,
        promotion_source="manual_jira_request",
    )
    db.add(defect)
    await db.flush()
    return defect


def _hash_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value
