"""Human review gate API (architecture E8.2, section 8).

Every AI-generated report is a proposal until a human accepts it. Finalize
creates the review request (E8.1); this is where a person settles it.

* ``GET  /api/v1/projects/{project_id}/reviews`` -- the project's review queue.
* ``GET  /api/v1/reviews/{review_id}``
* ``POST /api/v1/reviews/{review_id}/accept`` -- the run becomes ``passed``.
* ``POST /api/v1/reviews/{review_id}/reject`` -- needs a ``reason_code``; the
  run becomes ``failed`` with ``review_rejected: <reason_code>`` (section 7.2).

Accept and reject are refused to API keys and synthetic accounts, enforce
separation of duties, and are recorded in the access-audit log, the activity
feed and the pipeline event log. The MCP server must never expose them
(section 8.3; parity test in E8.6).

Reviewer identity is deliberately absent from these responses: API and export
payloads carry ``reviewed`` and a timestamp, not a name (section 8.2).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    _enforce_api_key_project_binding,
    get_current_active_user,
    require_project_access,
    require_role,
)
from app.db.postgres import get_db
from app.models.postgres import AgentPipelineRun, ProjectMember, ReviewRequest, User, UserRole
from app.services import review_request_service
from app.services.access_audit_service import log_access_change
from app.services.activity.service import ActorRef, record as record_activity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Reviews"])

ReviewState = Literal["pending_review", "accepted", "rejected", "superseded"]
ReasonCode = Literal[
    "wrong_category", "unsupported_claim", "missing_evidence",
    "contradiction", "stale_data", "other",
]


class ReviewResponse(BaseModel):
    """A review request as clients see it. No reviewer identity (section 8.2)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    kind: str
    subject_type: str
    subject_id: str
    pipeline_run_id: Optional[uuid.UUID] = None
    test_run_id: Optional[uuid.UUID] = None
    workflow_type: Optional[str] = None
    state: ReviewState
    reviewed: bool
    reviewed_at: Optional[datetime] = None
    reason_code: Optional[str] = None
    notes: Optional[str] = None
    evidence_bundle_sha256: Optional[str] = None
    superseded_by: Optional[uuid.UUID] = None
    created_at: datetime
    requires_human_review: bool = True
    ai_disclaimer: str
    ai_disclaimer_version: str


class AcceptReviewRequest(BaseModel):
    notes: Optional[str] = Field(default=None, max_length=4000)


class RejectReviewRequest(BaseModel):
    reason_code: ReasonCode
    notes: Optional[str] = Field(default=None, max_length=4000)


def _to_response(review: ReviewRequest) -> ReviewResponse:
    return ReviewResponse(
        id=review.id,
        project_id=review.project_id,
        kind=review.kind,
        subject_type=review.subject_type,
        subject_id=review.subject_id,
        pipeline_run_id=review.pipeline_run_id,
        test_run_id=review.test_run_id,
        workflow_type=review.workflow_type,
        state=review.state,  # type: ignore[arg-type]
        reviewed=review.reviewed_at is not None,
        reviewed_at=review.reviewed_at,
        reason_code=review.reason_code,
        notes=review.notes,
        evidence_bundle_sha256=review.evidence_bundle_sha256,
        superseded_by=review.superseded_by,
        created_at=review.created_at,
        ai_disclaimer=review_request_service.AI_DISCLAIMER,
        ai_disclaimer_version=review.ai_disclaimer_version,
    )


def require_review_access():
    """Resolve ``{review_id}`` to its project and check the caller may see it.

    Modelled on ``require_investigation_access``. A non-member gets **404, not
    403**: a UUID-only route must not confirm that someone else's review exists
    (architecture section 3). A project-bound API key reaches only its own
    project; ADMIN bypasses membership once the row is confirmed to exist.
    """

    async def _check(
        request: Request,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        raw = request.path_params.get("review_id")
        if not raw:
            return current_user
        try:
            review_uuid = uuid.UUID(str(raw))
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid review ID")
        project_id = (
            await db.execute(select(ReviewRequest.project_id).where(ReviewRequest.id == review_uuid))
        ).scalar_one_or_none()
        if project_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
        _enforce_api_key_project_binding(current_user, project_id)
        role_value = getattr(current_user.role, "value", current_user.role)
        if str(role_value) == UserRole.ADMIN.value:
            return current_user
        member = (
            await db.execute(
                select(ProjectMember.id).where(
                    ProjectMember.user_id == current_user.id,
                    ProjectMember.project_id == project_id,
                )
            )
        ).scalar_one_or_none()
        if member is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
        return current_user

    return _check


async def _load_for_update(db: AsyncSession, review_id: uuid.UUID) -> ReviewRequest:
    # Finalization owns the pipeline row before it stages or supersedes a
    # review. Settlement must take the same pipeline -> review lock order or a
    # concurrent evidence refresh can deadlock and leave stale authority.
    review = (
        await db.execute(select(ReviewRequest).where(ReviewRequest.id == review_id))
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
    if review.pipeline_run_id is not None:
        await db.execute(
            select(AgentPipelineRun.id)
            .where(AgentPipelineRun.id == review.pipeline_run_id)
            .with_for_update()
        )
    review = (
        await db.execute(
            select(ReviewRequest)
            .where(ReviewRequest.id == review_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
    return review


async def _settle_and_audit(
    db: AsyncSession,
    review: ReviewRequest,
    reviewer: User,
    decision: str,
    reason_code: Optional[str],
    notes: Optional[str],
) -> None:
    try:
        await review_request_service.settle_review(
            db, review=review, reviewer=reviewer, decision=decision,
            reason_code=reason_code, notes=notes,
        )
    except review_request_service.ReviewDecisionRefused as exc:
        raise HTTPException(
            status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
        ) from exc
    # Notes never reach the audit trail: they are free text, redacted, and
    # kept out of every export (section 8.1).
    await log_access_change(
        db,
        action=f"ai_review.{decision}",
        actor=reviewer,
        project_id=review.project_id,
        before_value={"state": "pending_review"},
        after_value={
            "state": decision,
            "review_id": str(review.id),
            "subject_type": review.subject_type,
            "subject_id": review.subject_id,
            "reason_code": review.reason_code,
        },
    )


def _entity(review: ReviewRequest) -> tuple[Any, str]:
    entity_id = review.test_run_id or review.pipeline_run_id or review.id
    return entity_id, f"run {str(entity_id)[:8]}"


async def _emit_review_event(review: ReviewRequest, decision: str) -> None:
    """Best-effort pipeline event after the commit (section 8.3)."""
    if review.pipeline_run_id is None:
        return
    try:
        from app.services.pipeline_event_log import emit_event  # noqa: PLC0415

        await emit_event(
            str(review.pipeline_run_id),
            f"review_{decision}",
            detail={"review_id": str(review.id), "reason_code": review.reason_code},
        )
    except Exception as exc:  # noqa: BLE001 -- the decision is already committed
        logger.warning("review event not logged (%s)", type(exc).__name__)


@router.get("/projects/{project_id}/reviews", response_model=list[ReviewResponse])
async def list_reviews(
    project_id: uuid.UUID,
    state: Optional[ReviewState] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_access()),
):
    """The project's review queue, newest first. ``?state=pending_review`` for the open queue."""
    stmt = select(ReviewRequest).where(ReviewRequest.project_id == project_id)
    # Never offer a review whose subject is gone. ``pipeline_run_id`` is an FK
    # with ``ON DELETE SET NULL``; ``subject_id`` is a plain varchar with no FK,
    # so a deleted pipeline run leaves the review row behind pointing at an id
    # that resolves to nothing. Settling one cannot change any run, and
    # ``settle_review`` now refuses it outright — so surfacing it here only
    # offers the user an action that is guaranteed to fail (BUG-012).
    stmt = stmt.where(
        ~(
            (ReviewRequest.subject_type == "pipeline_run")
            & (ReviewRequest.pipeline_run_id.is_(None))
        )
    )
    if state:
        stmt = stmt.where(ReviewRequest.state == state)
    rows = (
        await db.execute(stmt.order_by(ReviewRequest.created_at.desc()).limit(limit))
    ).scalars().all()
    return [_to_response(row) for row in rows]


@router.get("/reviews/{review_id}", response_model=ReviewResponse)
async def get_review(
    review_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_review_access()),
):
    review = (
        await db.execute(select(ReviewRequest).where(ReviewRequest.id == review_id))
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
    return _to_response(review)


@router.post("/reviews/{review_id}/accept", response_model=ReviewResponse)
async def accept_review(
    review_id: uuid.UUID,
    body: Optional[AcceptReviewRequest] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_review_access()),
):
    """Accept an AI report. Its run becomes ``passed``."""
    review = await _load_for_update(db, review_id)
    await _settle_and_audit(db, review, current_user, "accepted", None, body.notes if body else None)
    entity_id, entity_label = _entity(review)
    await record_activity(
        db,
        project_id=review.project_id,
        event_type="review.accepted",
        actor=ActorRef.from_user(current_user),
        entity_id=entity_id,
        entity_label=entity_label,
        context={"review_id": str(review.id), "pipeline_run_id": str(review.pipeline_run_id or "")},
    )
    await db.commit()
    await _emit_review_event(review, "accepted")
    return _to_response(review)


@router.post("/reviews/{review_id}/reject", response_model=ReviewResponse)
async def reject_review(
    review_id: uuid.UUID,
    body: RejectReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_review_access()),
):
    """Reject an AI report with a reason code. Its run becomes ``failed``."""
    review = await _load_for_update(db, review_id)
    await _settle_and_audit(db, review, current_user, "rejected", body.reason_code, body.notes)
    entity_id, entity_label = _entity(review)
    await record_activity(
        db,
        project_id=review.project_id,
        event_type="review.rejected",
        actor=ActorRef.from_user(current_user),
        entity_id=entity_id,
        entity_label=entity_label,
        context={
            "review_id": str(review.id),
            "pipeline_run_id": str(review.pipeline_run_id or ""),
            "reason_code": review.reason_code,
        },
    )
    await db.commit()
    await _emit_review_event(review, "rejected")
    return _to_response(review)
