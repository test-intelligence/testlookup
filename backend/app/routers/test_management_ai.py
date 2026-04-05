from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user
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
    return await generate_ai_cases(db, payload, current_user)


@router.post("/cases/ai-generate/async", response_model=AITaskEnqueueResponse)
async def ai_generate_cases_async(
    payload: AIGenerateTestCasesRequest,
    current_user: User = Depends(get_current_active_user),
):
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
    return await review_test_case_with_ai(db, case_id, current_user)


@router.post("/cases/ai-coverage", response_model=AICoverageAnalysisResponse)
async def ai_coverage_analysis(
    payload: AICoverageAnalysisRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return await analyze_case_coverage(db, payload)
