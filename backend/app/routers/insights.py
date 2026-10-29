"""Cross-run insights (W23a FAIL-11): list and dismiss.

Both routes sit under ``/api/v1/projects/{project_id}`` behind
``require_project_access()``: a project-bound API key reaches only its own
project's insights. Dismissing also needs the instance role ``QA_ENGINEER``
(``require_role``; below QA_LEAD the role guard does not refuse a
project-bound key, so a key of THIS project may dismiss, as it may pin a
timeline note; nothing here opts in to more).

``{insight_id}`` is loaded ``WHERE id AND project_id FOR UPDATE``
(``insights_service.dismiss_insight``), so another project's insight and an
unknown id answer the same 404. Refusals are ``{"detail": {"code",
"message"}}`` with the ``insight_vocab.INSIGHT_ERRORS`` sentences.

The list computes and reads (a GET never writes). The insights themselves
are written only by the hourly beat (``tasks.detect_cross_run_insights``);
the dismissal is the one write here: the service flushes, this router stages
the ``insight.dismissed`` ledger row on the same session (outcome mode) and
commits once. An already dismissed insight is answered as it is, with no
write and no second ledger row.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_project_access, require_role
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.models.schemas import InsightItem, InsightListResponse
from app.services import insights_service as insights
from app.services.activity.service import ActorRef, record as record_activity
from app.services.insight_vocab import INSIGHT_LIST_DEFAULT, INSIGHT_LIST_MAX, INSIGHT_RANGE_DEFAULT

router = APIRouter(prefix="/api/v1/projects/{project_id}/insights", tags=["Insights"])


def _refusal(exc: insights.InsightError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail())


@router.get("", response_model=InsightListResponse)
async def list_project_insights(
    project_id: uuid.UUID,
    range_key: str = Query(
        INSIGHT_RANGE_DEFAULT, alias="range",
        description="How far back an insight must have been confirmed: 24h or 7d.",
    ),
    include_dismissed: bool = Query(False, description="Also list the insights someone dismissed."),
    limit: int = Query(INSIGHT_LIST_DEFAULT, ge=1, le=INSIGHT_LIST_MAX, description="Rows to return; `total` counts all."),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_access()),
):
    """The project's insights confirmed in the range (open first, then by
    severity, newest first), the counts beside them, and the four detectors'
    last evaluations -- a detector that could not measure says why."""
    try:
        return await insights.list_insights(
            db, project_id, range_key=range_key, include_dismissed=include_dismissed,
            limit=limit, now=datetime.now(timezone.utc),
        )
    except insights.InsightError as exc:
        raise _refusal(exc) from None


@router.post("/{insight_id}/dismiss", response_model=InsightItem)
async def dismiss_project_insight(
    project_id: uuid.UUID,
    insight_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
    _: User = Depends(require_project_access()),
):
    """Hide an insight for everyone in the project for the rest of its
    episode (QA engineer or above). There is no un-dismiss; "Show dismissed"
    keeps it readable."""
    try:
        row, changed = await insights.dismiss_insight(
            db, project_id, insight_id, user=current_user, now=datetime.now(timezone.utc),
        )
    except insights.InsightError as exc:
        raise _refusal(exc) from None
    if changed:
        await record_activity(
            db,
            project_id=project_id,
            event_type="insight.dismissed",
            actor=ActorRef.from_user(current_user),
            entity_id=str(row.id),
            entity_type="insight",
            entity_label=row.subject_label,
            context={"kind": row.kind},
        )
        await db.commit()
        # A commit expires the row: reload before serialising (MissingGreenlet).
        await db.refresh(row)
    return insights.insight_item(row)
