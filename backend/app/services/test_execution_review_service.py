"""Service layer for per-TestCase human review transitions (migration 0081).

Pattern mirrors ``suite_review_service`` but at the per-execution
(``test_cases.id``) grain rather than per-(run, suite). Used when the AI
analysis pipeline flags a failure as ``requires_human_review`` and the
reviewer needs to record their verdict.

State machine (validated here and mirrored by DB CHECK constraints):

    pending_review (implicit initial state — no row written until first transition)
        ├── reviewed         → human confirmed the AI verdict
        ├── defect_filed     → ticket created (``defect_link`` captured)
        ├── false_positive   → flake / test bug; downstream un-tags
        └── reproducible     → failure confirmed locally, awaiting fix

Any state can transition to any other (no strict ordering). The reviewer's
identity and timestamp are stamped on every write.

Tenant isolation: callers must verify the requester has project access
BEFORE calling these helpers. The service trusts its inputs — keeping the
auth check at the router layer matches every other service in the codebase.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    TEST_EXECUTION_REVIEW_STATES,
    TestCase,
    TestExecutionReview,
    User,
)

logger = structlog.get_logger(__name__)

# The reviewer can't transition INTO "pending_review" — that's the implicit
# initial state, represented by the absence of a row. Block the value at the
# service layer so a typo in the UI doesn't produce a row stuck on the
# initial state.
_TRANSITIONABLE_STATES = frozenset(TEST_EXECUTION_REVIEW_STATES) - {"pending_review"}


async def get_review(
    db: AsyncSession,
    test_case_id: uuid.UUID,
) -> Optional[TestExecutionReview]:
    """Return the existing review row, or ``None`` if no transition has
    happened yet (the implicit ``pending_review`` state)."""
    result = await db.execute(
        select(TestExecutionReview).where(
            TestExecutionReview.test_case_id == test_case_id,
        )
    )
    return result.scalar_one_or_none()


async def upsert_review(
    db: AsyncSession,
    test_case_id: uuid.UUID,
    project_id: uuid.UUID,
    state: str,
    reviewer_user_id: uuid.UUID,
    defect_link: Optional[str] = None,
    note: Optional[str] = None,
) -> TestExecutionReview:
    """Create or update the review row for ``test_case_id``.

    Validates the state against the application-layer state machine and
    rejects ``pending_review`` (the implicit initial state — can't be
    transitioned INTO). The reviewer identity + timestamp are stamped on
    every write so the audit picture stays complete.

    ``defect_link`` is required when ``state == 'defect_filed'`` so the
    triage queue can route from the failure straight to the tracker.
    """
    if state not in _TRANSITIONABLE_STATES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid review state '{state}'. Allowed: "
                f"{sorted(_TRANSITIONABLE_STATES)}"
            ),
        )
    if state == "defect_filed" and not (defect_link and defect_link.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="defect_link is required when state is 'defect_filed'.",
        )

    now = datetime.now(timezone.utc)
    existing = await get_review(db, test_case_id)
    if existing is None:
        row = TestExecutionReview(
            test_case_id=test_case_id,
            project_id=project_id,
            state=state,
            reviewed_by_user_id=reviewer_user_id,
            defect_link=(defect_link or None),
            note=(note or None),
            transitioned_at=now,
        )
        db.add(row)
        await db.flush()
        logger.info(
            "test_execution_review_created",
            test_case_id=str(test_case_id),
            state=state,
            reviewer_user_id=str(reviewer_user_id),
        )
        return row

    # Update in-place. Preserve fields that weren't included in the payload
    # so a state-only PATCH doesn't clobber a previously-captured defect link.
    existing.state = state
    existing.reviewed_by_user_id = reviewer_user_id
    existing.transitioned_at = now
    if defect_link is not None:
        existing.defect_link = defect_link or None
    if note is not None:
        existing.note = note or None
    await db.flush()
    logger.info(
        "test_execution_review_transitioned",
        test_case_id=str(test_case_id),
        state=state,
        reviewer_user_id=str(reviewer_user_id),
    )
    return existing


async def hydrate_response(
    db: AsyncSession,
    review: TestExecutionReview,
) -> dict:
    """Build the read-shape with the reviewer's username + full_name resolved
    in one extra query. The Pydantic response model is built from this dict."""
    reviewer = None
    if review.reviewed_by_user_id:
        reviewer = (
            await db.execute(
                select(User).where(User.id == review.reviewed_by_user_id)
            )
        ).scalar_one_or_none()
    return {
        "id": review.id,
        "test_case_id": review.test_case_id,
        "project_id": review.project_id,
        "state": review.state,
        "reviewed_by_user_id": review.reviewed_by_user_id,
        "reviewed_by_username": reviewer.username if reviewer else None,
        "reviewed_by_full_name": reviewer.full_name if reviewer else None,
        "defect_link": review.defect_link,
        "note": review.note,
        "transitioned_at": review.transitioned_at,
        "created_at": review.created_at,
        "updated_at": review.updated_at,
    }


async def get_test_case_project(
    db: AsyncSession,
    test_case_id: uuid.UUID,
) -> Optional[uuid.UUID]:
    """Resolve a TestCase's project_id via TestRun. Returns ``None`` when the
    test case doesn't exist — the router maps that to 404."""
    from app.models.postgres import TestRun
    result = await db.execute(
        select(TestRun.project_id)
        .join(TestCase, TestCase.test_run_id == TestRun.id)
        .where(TestCase.id == test_case_id)
    )
    row = result.first()
    return row[0] if row else None
