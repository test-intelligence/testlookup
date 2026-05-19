"""Per-TestCase human review router (migration 0081).

Surfaces three endpoints under ``/api/v1/test-cases/{id}/review``:

  * GET    — current review state (or 404 if no transition has happened
             yet; UI falls back to the implicit ``pending_review`` tag).
  * PUT    — upsert the review row with a new state. The reviewer must
             have project access; ``defect_filed`` requires a ``defect_link``.
  * DELETE — clear the review (revert to implicit ``pending_review``).
             Useful when an over-eager reviewer wants to re-open the case.

Tenant isolation: the route resolves the TestCase's project_id via TestRun,
then enforces project access via ``require_project_access``-equivalent
logic inline. Any authenticated user with access to the project can record
a review — there's no role gate beyond that, since reviewing is part of
the QA_ENGINEER day-to-day.
"""
from __future__ import annotations

import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_accessible_project_ids,
    get_current_active_user,
    get_db,
)
from app.models.postgres import User
from app.models.schemas import (
    TestExecutionReviewRead,
    TestExecutionReviewUpdate,
)
from app.services import test_execution_review_service as svc

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/test-cases", tags=["Test Execution Reviews"])


async def _assert_project_access(
    db: AsyncSession,
    user: User,
    project_id: uuid.UUID,
) -> None:
    """Reject when the caller can't see this test case's project. ADMINs
    (accessible=None) bypass."""
    accessible = await get_accessible_project_ids(db, user)
    if accessible is None:
        return
    if project_id not in accessible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this project",
        )


@router.get(
    "/{test_case_id}/review",
    response_model=Optional[TestExecutionReviewRead],
)
async def get_test_case_review(
    test_case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Current review state for a test case, or ``null`` if no transition
    has been recorded yet (the implicit ``pending_review`` initial state).

    Returns 200 with ``null`` rather than 404 for the "no review yet"
    case so test-run detail pages don't pepper the browser network
    tab with red error rows on every page load. The frontend service
    treats ``null`` and 404 identically — but 200/null keeps the tab
    clean and removes a UI-test false positive. (Bug 2026-05-19.)
    """
    project_id = await svc.get_test_case_project(db, test_case_id)
    if project_id is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    await _assert_project_access(db, current_user, project_id)

    review = await svc.get_review(db, test_case_id)
    if review is None:
        return None
    return await svc.hydrate_response(db, review)


@router.put("/{test_case_id}/review", response_model=TestExecutionReviewRead)
async def upsert_test_case_review(
    test_case_id: uuid.UUID,
    payload: TestExecutionReviewUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Transition the review state. The router resolves the project via
    TestRun, enforces tenant access, then delegates to the service for
    state-machine validation + write."""
    project_id = await svc.get_test_case_project(db, test_case_id)
    if project_id is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    await _assert_project_access(db, current_user, project_id)

    review = await svc.upsert_review(
        db=db,
        test_case_id=test_case_id,
        project_id=project_id,
        state=payload.state,
        reviewer_user_id=current_user.id,
        defect_link=payload.defect_link,
        note=payload.note,
    )
    return await svc.hydrate_response(db, review)


@router.delete("/{test_case_id}/review", status_code=204)
async def clear_test_case_review(
    test_case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Drop the review row so the case reverts to ``pending_review``."""
    project_id = await svc.get_test_case_project(db, test_case_id)
    if project_id is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    await _assert_project_access(db, current_user, project_id)

    review = await svc.get_review(db, test_case_id)
    if review is not None:
        await db.delete(review)
    return None
