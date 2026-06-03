from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, resolve_project_scope
from app.db.postgres import get_db
from app.models.postgres import User
from app.models.schemas import (
    AICoverageAnalysisRequest,
    AICoverageAnalysisResponse,
    AIGenerateTestCasesRequest,
    AIGenerateTestCasesResponse,
    AITaskEnqueueResponse,
    AITaskStatusResponse,
    AIReviewTestCaseResponse,
)
from app.services.test_management_ai_service import (
    analyze_case_coverage,
    enqueue_ai_case_generation,
    generate_ai_cases,
    get_ai_task_status as get_ai_task_status_result,
    review_test_case_with_ai,
)

router = APIRouter()


@router.post("/cases/ai-generate", response_model=AIGenerateTestCasesResponse)
async def ai_generate_cases(
    payload: AIGenerateTestCasesRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # Tenant guard — caller must be a member of the target project before we
    # persist AI-generated cases into it (matches the /plans/ai-create pattern).
    await resolve_project_scope(db, current_user, str(payload.project_id))
    result = await generate_ai_cases(db, payload, current_user)
    await db.commit()
    return result


@router.post("/cases/ai-generate/async", response_model=AITaskEnqueueResponse)
async def ai_generate_cases_async(
    payload: AIGenerateTestCasesRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # Verify access before queueing — the worker runs without request context.
    await resolve_project_scope(db, current_user, str(payload.project_id))
    return await enqueue_ai_case_generation(payload, current_user)


@router.get("/cases/ai-task/{task_id}", response_model=AITaskStatusResponse)
async def get_ai_task_status(
    task_id: str,
    current_user: User = Depends(get_current_active_user),
):
    return get_ai_task_status_result(task_id)


@router.post("/cases/{case_id}/ai-review", response_model=AIReviewTestCaseResponse)
async def ai_review_case(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = await review_test_case_with_ai(db, case_id, current_user)
    await db.commit()
    return result


@router.post("/cases/ai-coverage", response_model=AICoverageAnalysisResponse)
async def ai_coverage_analysis(
    payload: AICoverageAnalysisRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # Tenant guard — coverage analysis reads the project's existing cases and
    # sends their titles/objectives to the LLM; block cross-project access.
    await resolve_project_scope(db, current_user, str(payload.project_id))
    return await analyze_case_coverage(db, payload)
