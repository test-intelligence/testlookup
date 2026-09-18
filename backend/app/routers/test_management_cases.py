from __future__ import annotations

import uuid
from typing import Optional
from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import (
    ManagedTestCase,
    TestCaseComment,
    TestCaseLifecycleState,
    TestCaseReview,
    TestCaseVersion,
    User,
)
from app.models.schemas import (
    AllowedTransitionResponse,
    ManagedTestCaseCreate,
    ManagedTestCaseListResponse,
    ManagedTestCaseResponse,
    ManagedTestCaseUpdate,
    ReviewActionRequest,
    TestCaseCommentCreate,
    TestCaseCommentResponse,
    TestCaseReviewResponse,
    TestCaseDeprecateRequest,
    EvidenceGapListResponse,
    TestCaseTransitionRequest,
    TestCaseVersionResponse,
)
from app.routers.test_management_shared import (
    require_case_access,
    require_case_access_for_review_target,
    row,
)
from app.services.test_management_service import (
    add_test_case_comment,
    apply_review_action,
    create_managed_test_case,
    deprecate_managed_test_case,
    get_test_case_or_404,
    list_combined_test_case_identities,
    list_automation_test_cases,
    list_managed_test_cases,
    request_test_case_review,
    update_managed_test_case,
)
from app.services.test_case_lifecycle_service import (
    lifecycle_actions_for,
    require_lifecycle_v2_enabled,
    transition,
    transition_availability_for,
)
from app.services.test_management_metrics_service import emit_staged_test_management_metrics
from app.services.test_suite_service import list_test_case_evidence_gaps

router = APIRouter()


async def _case_response(
    db: AsyncSession,
    test_case: ManagedTestCase,
    current_user: User,
) -> ManagedTestCaseResponse:
    response = row(test_case, ManagedTestCaseResponse)
    response.allowed_actions = await lifecycle_actions_for(db, test_case, current_user)
    return response


@router.get("/cases", response_model=ManagedTestCaseListResponse)
async def list_test_cases(
    project_id: Optional[uuid.UUID] = None,
    status: TestCaseLifecycleState | None = None,
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
    include_archived: bool = Query(
        False,
        description="Include archived authored cases when no exact status filter is set.",
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
    # F-042: this check ran ONLY in the ``not project_id`` branch, so naming a
    # project skipped it entirely — the guard fired only where there was
    # nothing to guard. Third recurrence of the class (F-033 digests, F-040
    # chat). ``resolve_project_scope`` 403s a non-admin naming a project they
    # do not belong to; the empty return below preserves today's behaviour for
    # a non-admin who names no project at all.
    from app.core.deps import resolve_project_scope  # noqa: PLC0415

    scoped_project_id, allowed = await resolve_project_scope(
        db, current_user, str(project_id) if project_id else None
    )
    if scoped_project_id is None and allowed is not None:
        return {"items": [], "total": 0, "page": page, "size": size, "pages": 0}

    # Fetch the FULL managed-cases set when merging with automation so we
    # can dedupe by fingerprint correctly and paginate the merged result
    # at the end. When ``include_automation`` is off we keep the existing
    # SQL-paginated path (cheap, no merge needed).
    if (
        not include_automation
        or status is not None
        or priority is not None
        or feature_area is not None
        or ai_generated is not None
    ):
        items, total, pages = await list_managed_test_cases(
            db,
            project_id=project_id,
            page=page,
            size=size,
            status=status,
            include_archived=include_archived,
            test_type=test_type,
            priority=priority,
            feature_area=feature_area,
            ai_generated=ai_generated,
            search=search,
            suite_name=suite_name,
        )
        return {
            "items": [await _case_response(db, item, current_user) for item in items],
            "total": total,
            "page": page,
            "size": size,
            "pages": pages,
        }

    identities, total, pages = await list_combined_test_case_identities(
        db,
        project_id=project_id,
        include_archived=include_archived,
        test_type=test_type,
        search=search,
        suite_name=suite_name,
        page=page,
        size=size,
    )
    managed_ids = {entity_id for source, entity_id in identities if source == "managed"}
    automation_ids = {
        entity_id for source, entity_id in identities if source == "automation"
    }
    managed_rows = []
    if managed_ids:
        managed_rows = list(
            (
                await db.execute(
                    select(ManagedTestCase).where(ManagedTestCase.id.in_(managed_ids))
                )
            ).scalars().all()
        )
    automation_rows = await list_automation_test_cases(
        db,
        project_id=project_id,
        case_ids=automation_ids,
    )
    by_identity = {
        **{
            ("managed", item.id): await _case_response(db, item, current_user)
            for item in managed_rows
        },
        **{("automation", item["id"]): item for item in automation_rows},
    }
    page_items = [by_identity[identity] for identity in identities if identity in by_identity]

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
    await emit_staged_test_management_metrics(db)
    await db.refresh(test_case)
    return await _case_response(db, test_case, current_user)


@router.get("/cases/evidence-gaps", response_model=EvidenceGapListResponse)
async def get_test_case_evidence_gaps(
    kind: str = Query(..., pattern="^(never_executed|automation_vanished)$"),
    project_id: Optional[uuid.UUID] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # This is a query-param scope, so the path-based guard is intentionally
    # not used. ``resolve_project_scope`` is the authority for both named
    # projects and the caller's all-project view.
    from app.core.deps import resolve_project_scope

    scoped_project_id, accessible = await resolve_project_scope(
        db, current_user, str(project_id) if project_id else None
    )
    if project_id is not None:
        project_ids: Optional[list[uuid.UUID]] = [project_id]
    elif accessible is None:
        project_ids = None
    else:
        project_ids = list(accessible)
    if scoped_project_id is None and accessible is not None and not project_ids:
        return {"items": [], "total": 0}
    items, total = await list_test_case_evidence_gaps(
        db, project_ids, kind=kind, page=page, size=size
    )
    return {"items": items, "total": total}


@router.get("/cases/{case_id}", response_model=ManagedTestCaseResponse)
async def get_test_case(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _case=Depends(require_case_access),
):
    return await _case_response(
        db, await get_test_case_or_404(db, case_id), current_user
    )


@router.patch("/cases/{case_id}", response_model=ManagedTestCaseResponse)
async def update_test_case(
    case_id: uuid.UUID,
    payload: ManagedTestCaseUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _case=Depends(require_case_access),
):
    test_case = await update_managed_test_case(db, case_id, payload, current_user)
    await db.commit()
    await emit_staged_test_management_metrics(db)
    await db.refresh(test_case)
    return await _case_response(db, test_case, current_user)


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deprecate_test_case(
    case_id: uuid.UUID,
    payload: Optional[TestCaseDeprecateRequest] = Body(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _case=Depends(require_case_access),
):
    await deprecate_managed_test_case(
        db, case_id, current_user, reason=payload.reason if payload else None
    )
    await db.commit()
    await emit_staged_test_management_metrics(db)


@router.get("/cases/{case_id}/history", response_model=list[TestCaseVersionResponse])
async def get_test_case_history(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _case=Depends(require_case_access),
):
    result = await db.execute(select(TestCaseVersion).where(TestCaseVersion.test_case_id == case_id).order_by(TestCaseVersion.version.desc()).limit(100))
    return [row(version, TestCaseVersionResponse) for version in result.scalars().all()]


@router.post("/cases/{case_id}/request-review", response_model=TestCaseReviewResponse)
async def request_review(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _case=Depends(require_case_access_for_review_target),
):
    review = await request_test_case_review(db, case_id, current_user)
    await db.commit()
    await emit_staged_test_management_metrics(db)
    await db.refresh(review)
    return row(review, TestCaseReviewResponse)


@router.post("/cases/{case_id}/transition", response_model=ManagedTestCaseResponse)
async def transition_test_case(
    case_id: uuid.UUID,
    payload: TestCaseTransitionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _case: ManagedTestCase = Depends(require_case_access),
):
    await require_lifecycle_v2_enabled(db, _case, current_user)
    result = await transition(
        db,
        case_id,
        payload.action,
        current_user,
        reason=payload.reason,
        notes=payload.notes,
        expected_version=payload.expected_version,
    )
    await db.commit()
    await emit_staged_test_management_metrics(db)
    await db.refresh(result.case)
    return await _case_response(db, result.case, current_user)


@router.get(
    "/cases/{case_id}/allowed-transitions",
    response_model=list[AllowedTransitionResponse],
)
async def get_allowed_transitions(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _case: ManagedTestCase = Depends(require_case_access),
):
    await require_lifecycle_v2_enabled(db, _case, current_user)
    return transition_availability_for(_case, current_user)


@router.post("/cases/{case_id}/review-action", response_model=ManagedTestCaseResponse)
async def review_action(
    case_id: uuid.UUID,
    payload: ReviewActionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _case=Depends(require_case_access),
):
    test_case = await apply_review_action(db, case_id, payload, current_user)
    await db.commit()
    await emit_staged_test_management_metrics(db)
    await db.refresh(test_case)
    return await _case_response(db, test_case, current_user)


@router.get("/cases/{case_id}/reviews", response_model=list[TestCaseReviewResponse])
async def get_reviews(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _case=Depends(require_case_access),
):
    result = await db.execute(select(TestCaseReview).where(TestCaseReview.test_case_id == case_id).order_by(TestCaseReview.created_at.desc()).limit(50))
    return [row(review, TestCaseReviewResponse) for review in result.scalars().all()]


@router.get("/cases/{case_id}/comments", response_model=list[TestCaseCommentResponse])
async def list_comments(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _case=Depends(require_case_access),
):
    result = await db.execute(select(TestCaseComment).where(TestCaseComment.test_case_id == case_id).order_by(TestCaseComment.created_at.asc()).limit(200))
    return [row(comment, TestCaseCommentResponse) for comment in result.scalars().all()]


@router.post("/cases/{case_id}/comments", response_model=TestCaseCommentResponse, status_code=status.HTTP_201_CREATED)
async def add_comment(
    case_id: uuid.UUID,
    payload: TestCaseCommentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _case=Depends(require_case_access),
):
    comment = await add_test_case_comment(db, case_id, payload, current_user)
    await db.commit()
    await db.refresh(comment)
    return row(comment, TestCaseCommentResponse)
