from __future__ import annotations

import uuid
from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import TestCaseComment, TestCaseReview, TestCaseVersion, User
from app.models.schemas import (
    ManagedTestCaseCreate,
    ManagedTestCaseListResponse,
    ManagedTestCaseResponse,
    ManagedTestCaseUpdate,
    ReviewActionRequest,
    TestCaseCommentCreate,
    TestCaseCommentResponse,
    TestCaseReviewResponse,
    TestCaseVersionResponse,
)
from app.routers.test_management_shared import row
from app.services.test_management_service import (
    add_test_case_comment,
    apply_review_action,
    create_managed_test_case,
    deprecate_managed_test_case,
    get_test_case_or_404,
    list_managed_test_cases,
    request_test_case_review,
    update_managed_test_case,
)

router = APIRouter()


@router.get("/cases", response_model=ManagedTestCaseListResponse)
async def list_test_cases(
    project_id: Optional[uuid.UUID] = None,
    status: str | None = None,
    test_type: str | None = None,
    priority: str | None = None,
    feature_area: str | None = None,
    ai_generated: bool | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    items, total, pages = await list_managed_test_cases(
        db,
        project_id=project_id,
        page=page,
        size=size,
        status=status,
        test_type=test_type,
        priority=priority,
        feature_area=feature_area,
        ai_generated=ai_generated,
        search=search,
    )
    return {"items": [row(item, ManagedTestCaseResponse) for item in items], "total": total, "page": page, "size": size, "pages": pages}


@router.post("/cases", response_model=ManagedTestCaseResponse, status_code=status.HTTP_201_CREATED)
async def create_test_case(
    payload: ManagedTestCaseCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return row(await create_managed_test_case(db, payload, current_user), ManagedTestCaseResponse)


@router.get("/cases/{case_id}", response_model=ManagedTestCaseResponse)
async def get_test_case(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return row(await get_test_case_or_404(db, case_id), ManagedTestCaseResponse)


@router.patch("/cases/{case_id}", response_model=ManagedTestCaseResponse)
async def update_test_case(
    case_id: uuid.UUID,
    payload: ManagedTestCaseUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return row(await update_managed_test_case(db, case_id, payload, current_user), ManagedTestCaseResponse)


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deprecate_test_case(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    await deprecate_managed_test_case(db, case_id, current_user)


@router.get("/cases/{case_id}/history", response_model=list[TestCaseVersionResponse])
async def get_test_case_history(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(select(TestCaseVersion).where(TestCaseVersion.test_case_id == case_id).order_by(TestCaseVersion.version.desc()))
    return [row(version, TestCaseVersionResponse) for version in result.scalars().all()]


@router.post("/cases/{case_id}/request-review", response_model=TestCaseReviewResponse)
async def request_review(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return row(await request_test_case_review(db, case_id, current_user), TestCaseReviewResponse)


@router.post("/cases/{case_id}/review-action", response_model=ManagedTestCaseResponse)
async def review_action(
    case_id: uuid.UUID,
    payload: ReviewActionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return row(await apply_review_action(db, case_id, payload, current_user), ManagedTestCaseResponse)


@router.get("/cases/{case_id}/reviews", response_model=list[TestCaseReviewResponse])
async def get_reviews(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(select(TestCaseReview).where(TestCaseReview.test_case_id == case_id).order_by(TestCaseReview.created_at.desc()))
    return [row(review, TestCaseReviewResponse) for review in result.scalars().all()]


@router.get("/cases/{case_id}/comments", response_model=list[TestCaseCommentResponse])
async def list_comments(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(select(TestCaseComment).where(TestCaseComment.test_case_id == case_id).order_by(TestCaseComment.created_at.asc()))
    return [row(comment, TestCaseCommentResponse) for comment in result.scalars().all()]


@router.post("/cases/{case_id}/comments", response_model=TestCaseCommentResponse, status_code=status.HTTP_201_CREATED)
async def add_comment(
    case_id: uuid.UUID,
    payload: TestCaseCommentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return row(await add_test_case_comment(db, case_id, payload, current_user), TestCaseCommentResponse)
