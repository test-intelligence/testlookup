"""The review envelope on every AI report response (architecture E8.3, section 8.2).

A report response has always said what the AI concluded. Since E8.1 there is also
a durable answer to "has a person looked at this?", and a client reading the
report has to see it where it reads the report, not on a separate endpoint it
may never call. So every report-bearing response carries:

* ``requires_human_review`` -- true for anything AI-generated;
* ``review`` -- ``{state, message, review_id, reviewed_at}``;
* ``ai_disclaimer`` and ``ai_disclaimer_version``;
* two headers, exposed through CORS so a browser can read them:
  ``X-TestLookup-AI-Generated: true|false`` and ``X-TestLookup-Review-State``.

What it deliberately does NOT carry: who reviewed. API and export payloads say
*that* a report was reviewed and when; the reviewer's name stays in-app
(section 8.2). ``ReviewRequest.reviewed_by`` is never read here.

Two cases that could lie, and how they are resolved
---------------------------------------------------
* **AI content with no review request** -- a report produced before E8.1, or a
  run whose request failed to stage. It is reported ``pending_review`` with no
  ``review_id``. Fail closed: absence of a review is not a review, and a client
  that gates on ``accepted`` must not be handed something that reads as settled.
* **A deterministic fallback** -- the summary built from PostgreSQL when the AI
  pipeline has not run. No AI was involved, so the state is ``not_applicable``
  and ``X-TestLookup-AI-Generated`` is ``false``. Labelling it AI-generated would
  send people to review something no model wrote.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select

from app.models.postgres import ReviewRequest
from app.services.review_request_service import AI_DISCLAIMER, AI_DISCLAIMER_VERSION

__all__ = [
    "EXPOSED_HEADERS",
    "HEADER_AI_GENERATED",
    "HEADER_REVIEW_STATE",
    "NOT_APPLICABLE",
    "ReviewEnvelope",
    "envelope_from_review",
    "not_ai_generated",
    "review_envelope_for_run",
    "review_envelope_for_pipeline",
]

HEADER_AI_GENERATED = "X-TestLookup-AI-Generated"
HEADER_REVIEW_STATE = "X-TestLookup-Review-State"
#: Added to CORS ``expose_headers`` in ``app.bootstrap``; a browser cannot read a
#: custom response header the server does not expose.
EXPOSED_HEADERS: tuple[str, ...] = (HEADER_AI_GENERATED, HEADER_REVIEW_STATE)

NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class ReviewEnvelope:
    ai_generated: bool
    state: str
    message: str
    review_id: Optional[str] = None
    reviewed_at: Optional[str] = None

    def block(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "message": self.message,
            "review_id": self.review_id,
            "reviewed_at": self.reviewed_at,
        }

    def fields(self) -> dict[str, Any]:
        """The keys a report response gains."""
        return {
            "requires_human_review": self.ai_generated,
            "review": self.block(),
            "ai_disclaimer": AI_DISCLAIMER if self.ai_generated else None,
            "ai_disclaimer_version": AI_DISCLAIMER_VERSION if self.ai_generated else None,
        }

    def apply_headers(self, response: Any) -> None:
        if response is None:
            return
        response.headers[HEADER_AI_GENERATED] = "true" if self.ai_generated else "false"
        response.headers[HEADER_REVIEW_STATE] = self.state


def not_ai_generated() -> ReviewEnvelope:
    return ReviewEnvelope(
        ai_generated=False,
        state=NOT_APPLICABLE,
        message="Not AI-generated: built deterministically from recorded test results.",
    )


def _unreviewed() -> ReviewEnvelope:
    return ReviewEnvelope(
        ai_generated=True,
        state="pending_review",
        message=(
            "AI-generated. No human review has been recorded for this report; "
            "treat it as a draft."
        ),
    )


def envelope_from_review(review: Any) -> ReviewEnvelope:
    """Project a ``ReviewRequest`` into the envelope. Reads no reviewer identity."""
    state = str(review.state)
    review_id = str(review.id)
    messages = {
        "pending_review": (
            f"AI-generated. Human review required before use. Review at /api/v1/reviews/{review_id}."
        ),
        "accepted": "AI-generated. Reviewed and accepted by a human reviewer.",
        "rejected": "AI-generated. A human reviewer rejected this report; do not rely on it.",
        "superseded": "AI-generated. A newer report replaces this one.",
    }
    reviewed_at = getattr(review, "reviewed_at", None)
    return ReviewEnvelope(
        ai_generated=True,
        state=state,
        message=messages.get(state, messages["pending_review"]),
        review_id=review_id,
        reviewed_at=reviewed_at.isoformat() if reviewed_at is not None else None,
    )


async def review_envelope_for_run(
    db: Any,
    test_run_id: Any,
    *,
    workflow_type: Optional[str] = None,
    ai_generated: bool = True,
) -> ReviewEnvelope:
    """The envelope for a report about ``test_run_id``.

    Uses the newest live (not superseded) report review for the run, optionally
    for one workflow type. See the module docstring for the no-review and
    deterministic cases.
    """
    if not ai_generated:
        return not_ai_generated()
    try:
        run_uuid = test_run_id if isinstance(test_run_id, uuid.UUID) else uuid.UUID(str(test_run_id))
    except (TypeError, ValueError):
        return _unreviewed()
    if db is None:
        return _unreviewed()
    stmt = select(ReviewRequest).where(
        ReviewRequest.test_run_id == run_uuid,
        ReviewRequest.kind == "report",
        ReviewRequest.state != "superseded",
    )
    if workflow_type:
        stmt = stmt.where(ReviewRequest.workflow_type == workflow_type)
    row: Optional[ReviewRequest] = (
        await db.execute(stmt.order_by(ReviewRequest.created_at.desc()).limit(1))
    ).scalars().first()
    if row is None:
        return _unreviewed()
    return envelope_from_review(row)


async def review_envelope_for_pipeline(
    db: Any,
    pipeline_run_id: Any,
    *,
    ai_generated: bool = True,
) -> ReviewEnvelope:
    """The live review envelope for one exact pipeline subject.

    Investigator excerpts need this narrower lookup because one test run may
    have several investigations. Selecting by test_run_id would let the newest
    investigation's review state authorize every older narrative.
    """
    if not ai_generated:
        return not_ai_generated()
    try:
        pipeline_uuid = (
            pipeline_run_id
            if isinstance(pipeline_run_id, uuid.UUID)
            else uuid.UUID(str(pipeline_run_id))
        )
    except (TypeError, ValueError):
        return _unreviewed()
    if db is None:
        return _unreviewed()
    row: Optional[ReviewRequest] = (
        await db.execute(
            select(ReviewRequest)
            .where(
                ReviewRequest.pipeline_run_id == pipeline_uuid,
                ReviewRequest.kind == "report",
                ReviewRequest.state != "superseded",
            )
            .order_by(ReviewRequest.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    return envelope_from_review(row) if row is not None else _unreviewed()
