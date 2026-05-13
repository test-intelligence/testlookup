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
    list_automation_test_cases,
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
    suite_name: str | None = None,
    include_automation: bool = Query(
        False,
        description=(
            "When true, merge synthesised rows derived from per-run "
            "test_cases into the response so the Test Management page can "
            "surface automation-ingested tests alongside authored ones. "
            "Deduped by test_fingerprint — any fingerprint already linked "
            "to a managed_test_cases row is skipped."
        ),
    ),
    page: int = Query(1, ge=1),
    # Cap raised from 100 → 200 because the Test Management page fetches
    # a full health-roll snapshot via ``useTestCases({ size: 200 })`` to
    # compute per-suite aggregates without pagination round-trips. Values
    # above 200 still 422.
    size: int = Query(25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not project_id:
        from app.core.deps import get_accessible_project_ids
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            return {"items": [], "total": 0, "page": page, "size": size, "pages": 0}

    # Fetch the FULL managed-cases set when merging with automation so we
    # can dedupe by fingerprint correctly and paginate the merged result
    # at the end. When ``include_automation`` is off we keep the existing
    # SQL-paginated path (cheap, no merge needed).
    if not include_automation:
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
            suite_name=suite_name,
        )
        return {
            "items": [row(item, ManagedTestCaseResponse) for item in items],
            "total": total,
            "page": page,
            "size": size,
            "pages": pages,
        }

    # ── Merge path ──────────────────────────────────────────────────────
    # 1. Pull the full filtered managed-cases set (cap at 1000 — anything
    #    larger means the project should be using server-side search).
    managed_all, _managed_total, _ = await list_managed_test_cases(
        db,
        project_id=project_id,
        page=1,
        size=1000,
        status=status,
        test_type=test_type,
        priority=priority,
        feature_area=feature_area,
        ai_generated=ai_generated,
        search=search,
        suite_name=suite_name,
    )
    managed_dicts = [row(item, ManagedTestCaseResponse).model_dump() for item in managed_all]

    # 2. Pull the automation-ingested set, skipping any fingerprint that
    #    already shows up in managed rows so we don't double-count.
    managed_fps = {m.get("test_fingerprint") for m in managed_dicts if m.get("test_fingerprint")}
    if project_id is not None:
        automation_dicts = await list_automation_test_cases(
            db,
            project_id=project_id,
            search=search,
            suite_name=suite_name,
            exclude_fingerprints=managed_fps,
        )
    else:
        automation_dicts = []

    # 3. Sort merged set by recency (last_executed_at then created_at) so
    #    the freshest signal is on top regardless of source.
    def _recency_key(d: dict):
        return d.get("last_executed_at") or d.get("created_at") or ""

    merged = sorted([*managed_dicts, *automation_dicts], key=_recency_key, reverse=True)
    total = len(merged)
    start = (page - 1) * size
    page_items = merged[start:start + size]
    pages = max(1, -(-total // size)) if total > 0 else 0

    return {
        "items": page_items,
        "total": total,
        "page": page,
        "size": size,
        "pages": pages,
    }


@router.post("/cases", response_model=ManagedTestCaseResponse, status_code=status.HTTP_201_CREATED)
async def create_test_case(
    payload: ManagedTestCaseCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    test_case = await create_managed_test_case(db, payload, current_user)
    await db.commit()
    await db.refresh(test_case)
    return row(test_case, ManagedTestCaseResponse)


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
    test_case = await update_managed_test_case(db, case_id, payload, current_user)
    await db.commit()
    await db.refresh(test_case)
    return row(test_case, ManagedTestCaseResponse)


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deprecate_test_case(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    await deprecate_managed_test_case(db, case_id, current_user)
    await db.commit()


@router.get("/cases/{case_id}/history", response_model=list[TestCaseVersionResponse])
async def get_test_case_history(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(select(TestCaseVersion).where(TestCaseVersion.test_case_id == case_id).order_by(TestCaseVersion.version.desc()).limit(100))
    return [row(version, TestCaseVersionResponse) for version in result.scalars().all()]


@router.post("/cases/{case_id}/request-review", response_model=TestCaseReviewResponse)
async def request_review(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    review = await request_test_case_review(db, case_id, current_user)
    await db.commit()
    await db.refresh(review)
    return row(review, TestCaseReviewResponse)


@router.post("/cases/{case_id}/review-action", response_model=ManagedTestCaseResponse)
async def review_action(
    case_id: uuid.UUID,
    payload: ReviewActionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    test_case = await apply_review_action(db, case_id, payload, current_user)
    await db.commit()
    await db.refresh(test_case)
    return row(test_case, ManagedTestCaseResponse)


@router.get("/cases/{case_id}/reviews", response_model=list[TestCaseReviewResponse])
async def get_reviews(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(select(TestCaseReview).where(TestCaseReview.test_case_id == case_id).order_by(TestCaseReview.created_at.desc()).limit(50))
    return [row(review, TestCaseReviewResponse) for review in result.scalars().all()]


@router.get("/cases/{case_id}/comments", response_model=list[TestCaseCommentResponse])
async def list_comments(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(select(TestCaseComment).where(TestCaseComment.test_case_id == case_id).order_by(TestCaseComment.created_at.asc()).limit(200))
    return [row(comment, TestCaseCommentResponse) for comment in result.scalars().all()]


@router.post("/cases/{case_id}/comments", response_model=TestCaseCommentResponse, status_code=status.HTTP_201_CREATED)
async def add_comment(
    case_id: uuid.UUID,
    payload: TestCaseCommentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    comment = await add_test_case_comment(db, case_id, payload, current_user)
    await db.commit()
    await db.refresh(comment)
    return row(comment, TestCaseCommentResponse)
