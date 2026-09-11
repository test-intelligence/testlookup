"""Suite owners + human-in-the-loop AI analysis reviews (migration 0076).

All endpoints are mounted under the ``/api/v1/test-management`` prefix via
``test_management.py``. They live in their own module to keep the existing
test-management sub-routers narrow.

Authorization summary:

  * ``GET`` endpoints: any authenticated user with project access.
  * ``PUT /suite-owners/{suite_name}``: ``QA_LEAD`` or higher (assigning
    suite ownership is a project-wide configuration change).
  * ``PUT /suite-reviews/...``: any authenticated user; the recorded
    ``reviewer_user_id`` is whoever submits the review, regardless of
    whether they're the resolved owner. The UI surfaces non-owner reviews
    for transparency rather than blocking them.
"""
from __future__ import annotations

import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_accessible_project_ids,
    get_current_active_user,
    require_role,
)
from app.db.postgres import get_db
from app.models.postgres import TestRun, User, UserRole
from app.models.schemas import (
    SuiteOwnerResponse,
    SuiteOwnerUpdate,
    SuiteReviewResponse,
    SuiteReviewUpdate,
)
from app.services import suite_review_service as svc
from sqlalchemy import select

logger = structlog.get_logger(__name__)

router = APIRouter()


async def _enforce_project_access(
    db: AsyncSession, user: User, project_id: uuid.UUID
) -> None:
    accessible = await get_accessible_project_ids(db, user)
    if accessible is None:
        return
    if project_id not in accessible:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


# ── Suite owners ──────────────────────────────────────────────────────────


@router.get("/suite-owners", response_model=list[SuiteOwnerResponse])
async def list_suite_owners(
    project_id: uuid.UUID = Query(...),
    suite_name: Optional[str] = Query(
        None, description="If set, returns the single suite owner."
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Resolve owners for a project's suites.

    With ``suite_name`` set, returns a single-item list (or an empty list
    when neither an explicit owner nor a project manager is configured —
    callers should not rely on a 404 for the empty case so the UI can show
    a neutral "Unassigned" badge).
    """
    await _enforce_project_access(db, current_user, project_id)

    if suite_name:
        user, is_fallback = await svc.resolve_suite_owner(db, project_id, suite_name)
        if user is None:
            return [
                SuiteOwnerResponse(
                    project_id=project_id,
                    suite_name=suite_name,
                    owner_user_id=None,
                    is_fallback=False,
                )
            ]
        return [
            SuiteOwnerResponse(
                project_id=project_id,
                suite_name=suite_name,
                owner_user_id=user.id,
                owner_email=user.email,
                owner_full_name=user.full_name,
                is_fallback=is_fallback,
            )
        ]

    # No suite_name: return every explicit owner row (the aggregated
    # /suites endpoint is the canonical source for the suite list itself;
    # the UI enriches its rows with this map and falls back to project
    # manager for suites missing here).
    from app.models.postgres import TestSuiteOwner

    rows = (
        await db.execute(
            select(TestSuiteOwner.suite_name).where(
                TestSuiteOwner.project_id == project_id
            )
        )
    ).all()
    suite_names = [r[0] for r in rows]
    resolved = await svc.list_suite_owners(db, project_id, suite_names)
    return [
        SuiteOwnerResponse(
            project_id=project_id,
            suite_name=name,
            owner_user_id=info["owner_user_id"],
            owner_email=info["owner_email"],
            owner_full_name=info["owner_full_name"],
            is_fallback=info["is_fallback"],
        )
        for name, info in resolved.items()
    ]


@router.put(
    "/suite-owners/{suite_name}",
    response_model=SuiteOwnerResponse,
    dependencies=[Depends(require_role(UserRole.QA_LEAD, allow_project_key=True))],
)
async def set_suite_owner(
    suite_name: str,
    payload: SuiteOwnerUpdate,
    project_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Assign or clear (``owner_user_id=null``) the suite's explicit owner."""
    await _enforce_project_access(db, current_user, project_id)
    await svc.set_suite_owner(db, project_id, suite_name, payload.owner_user_id)
    user, is_fallback = await svc.resolve_suite_owner(db, project_id, suite_name)
    return SuiteOwnerResponse(
        project_id=project_id,
        suite_name=suite_name,
        owner_user_id=user.id if user else None,
        owner_email=user.email if user else None,
        owner_full_name=user.full_name if user else None,
        is_fallback=is_fallback,
    )


# ── Suite reviews ─────────────────────────────────────────────────────────


def _review_to_response(
    review, reviewer_email_map: dict[uuid.UUID, str]
) -> SuiteReviewResponse:
    return SuiteReviewResponse(
        id=review.id,
        project_id=review.project_id,
        suite_name=review.suite_name,
        test_run_id=review.test_run_id,
        state=review.state,
        note=review.note,
        reviewer_user_id=review.reviewer_user_id,
        reviewer_email=reviewer_email_map.get(review.reviewer_user_id)
        if review.reviewer_user_id
        else None,
        reviewed_at=review.reviewed_at,
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


@router.get("/suite-reviews", response_model=list[SuiteReviewResponse])
async def list_suite_reviews(
    project_id: uuid.UUID = Query(...),
    suite_name: Optional[str] = Query(None),
    state: Optional[str] = Query(
        None, pattern="^(pending|confirmed|acknowledged|review_later)$"
    ),
    test_run_id: Optional[uuid.UUID] = Query(None),
    reviewer_user_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    await _enforce_project_access(db, current_user, project_id)
    reviews = await svc.list_reviews(
        db,
        project_id,
        suite_name=suite_name,
        state=state,
        test_run_id=test_run_id,
        reviewer_user_id=reviewer_user_id,
        limit=limit,
    )
    email_map = await svc.enrich_reviewers(db, reviews)
    return [_review_to_response(r, email_map) for r in reviews]


@router.get(
    "/suite-reviews/by-run/{test_run_id}", response_model=list[SuiteReviewResponse]
)
async def list_reviews_for_run(
    test_run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """All suite reviews for a single run (used by the run-detail page)."""
    run = (
        await db.execute(select(TestRun).where(TestRun.id == test_run_id))
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Test run not found")
    await _enforce_project_access(db, current_user, run.project_id)

    reviews = await svc.list_reviews(db, run.project_id, test_run_id=test_run_id)
    email_map = await svc.enrich_reviewers(db, reviews)
    return [_review_to_response(r, email_map) for r in reviews]


@router.put(
    "/suite-reviews/by-run/{test_run_id}/{suite_name}",
    response_model=SuiteReviewResponse,
)
async def upsert_review_for_run_suite(
    test_run_id: uuid.UUID,
    suite_name: str,
    payload: SuiteReviewUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Set the review state for (run, suite). Creates the row on first call.

    Non-gating: the AI pipeline already wrote ``AIAnalysis`` rows for this
    run independently. This call records the human verdict on top.
    """
    run = (
        await db.execute(select(TestRun).where(TestRun.id == test_run_id))
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Test run not found")
    await _enforce_project_access(db, current_user, run.project_id)

    review = await svc.get_or_create_review(
        db, run.project_id, suite_name, test_run_id
    )
    review = await svc.update_review(
        db, review.id, payload.state, payload.note, current_user.id
    )
    email_map = await svc.enrich_reviewers(db, [review])
    return _review_to_response(review, email_map)
