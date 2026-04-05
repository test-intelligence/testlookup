"""Third-party integration endpoints (Jira, etc.)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.db.postgres import get_db
from app.models.postgres import AIAnalysis, TestCase, UserRole
from app.models.schemas import JiraIssueRequest, JiraIssueResponse
from app.services.jira_client import create_jira_issue

router = APIRouter(prefix="/api/v1/integrations", tags=["Integrations"])


@router.post(
    "/jira",
    response_model=JiraIssueResponse,
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def create_jira_defect(
    request: JiraIssueRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a Jira Bug ticket from AI triage analysis output."""
    # Fetch test case and AI analysis
    tc_result = await db.execute(
        select(TestCase).where(TestCase.id == request.test_case_id)
    )
    tc = tc_result.scalar_one_or_none()
    if not tc:
        raise HTTPException(status_code=404, detail="Test case not found")

    ai_result = await db.execute(
        select(AIAnalysis).where(AIAnalysis.test_case_id == tc.id)
    )
    # ai_analysis fetched for potential future use (logging, enrichment)
    ai_result.scalar_one_or_none()

    stack_trace = tc.error_message or "Stack trace not available"
    dashboard_link = f"http://localhost:3000/runs/{request.run_id}/tests/{tc.id}"

    try:
        result = await create_jira_issue(
            project_key=request.project_key,
            test_name=tc.test_name,
            run_id=str(request.run_id),
            ai_summary=request.ai_summary,
            recommended_action=request.recommended_action,
            stack_trace=stack_trace,
            dashboard_link=dashboard_link,
        )
        return JiraIssueResponse(**result)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Jira API error: {str(e)}")
