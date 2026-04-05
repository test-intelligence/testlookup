"""
AI feedback endpoints — capture human signals for continuous fine-tuning.

POST /api/v1/feedback/{analysis_id}      — rate an AI analysis result
PUT  /api/v1/feedback/{analysis_id}      — update a previously submitted rating
GET  /api/v1/feedback/stats              — feedback summary for dashboard
POST /api/v1/training/export             — manually trigger training data export
POST /api/v1/training/promote            — manually promote a fine-tuned model
GET  /api/v1/training/status             — model registry + pending example counts
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, require_role
from app.core.config import settings
from app.db.postgres import get_db
from app.models.postgres import (
    FeedbackRating,
    FailureCategory,
    UserRole
)
from app.services import feedback_service

router = APIRouter(prefix="/api/v1", tags=["Feedback & Training"])


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


# ── Feedback endpoints ────────────────────────────────────────────────────────

@router.post("/feedback/{analysis_id}", status_code=201)
async def submit_feedback(
    analysis_id: uuid.UUID,
    body: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return await feedback_service.submit_feedback(db, analysis_id, body, current_user)


@router.put("/feedback/{analysis_id}", status_code=200)
async def update_feedback(
    analysis_id: uuid.UUID,
    body: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return await feedback_service.update_feedback(db, analysis_id, body, current_user)


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
    return await feedback_service.promote_model(db, body, settings_provider())


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
    return await feedback_service.jira_resolution_webhook(db, payload)


def settings_provider() -> str:
    from app.core.config import settings
    return settings.LLM_PROVIDER
