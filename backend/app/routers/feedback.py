"""
AI feedback endpoints — capture human signals for continuous fine-tuning.

POST /api/v1/feedback/{analysis_id}      — rate an AI analysis result
PUT  /api/v1/feedback/{analysis_id}      — update a previously submitted rating
GET  /api/v1/feedback/stats              — feedback summary for dashboard
POST /api/v1/training/export             — manually trigger training data export
POST /api/v1/training/promote            — manually promote a fine-tuned model
GET  /api/v1/training/status             — model registry + pending example counts

GET  /api/v1/projects/{project_id}/analyses/lookup — latest analysis_id for a
     test fingerprint (US-2.4; lives on ``lookup_router`` so the
     ``require_project_access`` guard applies to the {project_id} scope)
"""
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, require_project_access, require_role
from app.core.config import settings
from app.db.postgres import get_db
from app.models.postgres import (
    FeedbackRating,
    FailureCategory,
    User,
    UserRole
)
from app.services import feedback_service

router = APIRouter(prefix="/api/v1", tags=["Feedback & Training"])
# US-2.4 — fingerprint → analysis_id bridge for the Failure Analysis page's
# "correct classification" dialog. Separate router because the project-scoped
# path must carry the ``require_project_access`` guard (authorization ratchet).
lookup_router = APIRouter(prefix="/api/v1/projects", tags=["Feedback & Training"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    rating: FeedbackRating
    corrected_category: Optional[FailureCategory] = None
    corrected_root_cause: Optional[str] = None
    comment: Optional[str] = None


class PromoteModelRequest(BaseModel):
    track: str           # "classifier" | "reasoning" | "embedding"
    model_name: str
    eval_accuracy: Optional[float] = None
    baseline_accuracy: Optional[float] = None


class AnalysisLookupResponse(BaseModel):
    """US-2.4: latest AI analysis for a (project, fingerprint) pair.

    All fields are ``None`` when the test has never been analysed — the UI
    renders a "no AI analysis recorded yet" empty state instead of a 404
    (which axios would surface as a scary error toast).

    ``failure_category`` is a plain string, NOT the ``FailureCategory``
    enum: the backing column is ``String(30)`` and a strict enum here would
    silently 422 the response if a stored value ever drifts out of vocab
    (backend/CLAUDE.md pitfall).
    """
    analysis_id: Optional[uuid.UUID] = None
    failure_category: Optional[str] = None
    analyzed_at: Optional[datetime] = None


# ── Feedback endpoints ────────────────────────────────────────────────────────

@router.post("/feedback/{analysis_id}", status_code=201)
async def submit_feedback(
    analysis_id: uuid.UUID,
    body: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    result = await feedback_service.submit_feedback(db, analysis_id, body, current_user)
    await db.commit()
    return result


@router.put("/feedback/{analysis_id}", status_code=200)
async def update_feedback(
    analysis_id: uuid.UUID,
    body: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    result = await feedback_service.update_feedback(db, analysis_id, body, current_user)
    await db.commit()
    return result


@lookup_router.get(
    "/{project_id}/analyses/lookup",
    response_model=AnalysisLookupResponse,
)
async def lookup_latest_analysis(
    project_id: uuid.UUID,
    fingerprint: str = Query(..., min_length=1, max_length=64),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_access()),
):
    """Latest AI analysis for a test fingerprint, project-scoped (US-2.4).

    Bridges the Failure Analysis page (which identifies tests by
    ``test_fingerprint``) to the feedback endpoints (which key on
    ``analysis_id``). Returns 200 with null fields when no analysis exists.
    """
    found = await feedback_service.latest_analysis_for_fingerprint(
        db, project_id, fingerprint,
    )
    if found is None:
        return AnalysisLookupResponse()
    return AnalysisLookupResponse(**found)


@router.get("/feedback/stats")
async def get_feedback_stats(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_active_user),
):
    return await feedback_service.get_feedback_stats(db)


# ── Training management endpoints ─────────────────────────────────────────────

@router.post("/training/export", status_code=202)
async def trigger_export(
    _=Depends(require_role(UserRole.QA_LEAD)),
):
    return feedback_service.trigger_export()


@router.post("/training/finetune", status_code=202)
async def trigger_finetune(
    track: str = Body(..., embed=True),
    _=Depends(require_role(UserRole.QA_LEAD)),
):
    return feedback_service.trigger_finetune(track)


@router.post("/training/promote", status_code=200)
async def promote_model(
    body: PromoteModelRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.ADMIN)),
):
    result = await feedback_service.promote_model(db, body, settings_provider())
    await db.commit()
    return result


@router.get("/training/status")
async def get_training_status(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_active_user),
):
    return await feedback_service.get_training_status(db, settings)


@router.post("/feedback/jira-webhook", status_code=200)
async def jira_resolution_webhook(
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    result = await feedback_service.jira_resolution_webhook(db, payload)
    await db.commit()
    return result


def settings_provider() -> str:
    from app.core.config import settings
    return settings.LLM_PROVIDER
