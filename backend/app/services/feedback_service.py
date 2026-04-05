from __future__ import annotations

from datetime import datetime, timezone
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AIAnalysis, AIFeedback, Defect, FeedbackRating, ModelVersion


async def submit_feedback(db: AsyncSession, analysis_id: uuid.UUID, body, current_user) -> dict:
    analysis = (await db.execute(select(AIAnalysis).where(AIAnalysis.id == analysis_id))).scalar_one_or_none()
    if not analysis:
        raise HTTPException(404, detail="Analysis not found")

    feedback = AIFeedback(
        analysis_id=analysis_id,
        test_case_id=analysis.test_case_id,
        user_id=current_user.id,
        rating=body.rating,
        corrected_category=body.corrected_category,
        corrected_root_cause=body.corrected_root_cause,
        comment=body.comment,
        source="manual",
        exported=False,
    )
    db.add(feedback)

    if body.corrected_category and body.rating == FeedbackRating.INCORRECT:
        analysis.failure_category = body.corrected_category
        if body.corrected_root_cause:
            analysis.root_cause_summary = body.corrected_root_cause
        analysis.requires_human_review = False

    await db.commit()
    return {"feedback_id": str(feedback.id), "message": "Feedback recorded — thank you!"}


async def update_feedback(db: AsyncSession, analysis_id: uuid.UUID, body, current_user) -> dict:
    feedback = (
        await db.execute(
            select(AIFeedback)
            .where(AIFeedback.analysis_id == analysis_id)
            .where(AIFeedback.user_id == current_user.id)
            .where(AIFeedback.source == "manual")
            .order_by(AIFeedback.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not feedback:
        raise HTTPException(404, detail="No feedback found for this analysis from current user")

    feedback.rating = body.rating
    feedback.corrected_category = body.corrected_category
    feedback.corrected_root_cause = body.corrected_root_cause
    feedback.comment = body.comment
    feedback.exported = False
    await db.commit()
    return {"message": "Feedback updated"}


async def get_feedback_stats(db: AsyncSession) -> dict:
    rows = await db.execute(select(AIFeedback.rating, func.count(AIFeedback.id)).group_by(AIFeedback.rating))
    counts = {str(row[0]): row[1] for row in rows.all()}
    total = (await db.execute(select(func.count(AIFeedback.id)))).scalar_one()
    unexported = (await db.execute(select(func.count(AIFeedback.id)).where(AIFeedback.exported.is_(False)))).scalar_one()
    return {"total_feedback": total, "unexported": unexported, "by_rating": counts}


def trigger_export() -> dict:
    from app.worker.training_tasks import export_training_data

    task = export_training_data.apply_async(queue="default")
    return {"message": "Training data export queued", "task_id": task.id}


def trigger_finetune(track: str) -> dict:
    if track not in ("classifier", "reasoning", "embedding"):
        raise HTTPException(400, detail="track must be classifier | reasoning | embedding")

    from app.worker.training_tasks import run_finetune_pipeline

    task = run_finetune_pipeline.apply_async(kwargs={"track": track}, queue="default")
    return {"message": f"Fine-tuning queued for track={track}", "task_id": task.id}


async def promote_model(db: AsyncSession, body, provider: str) -> dict:
    from app.services.model_registry import ModelRegistry

    await ModelRegistry.promote(
        track=body.track,
        model_name=body.model_name,
        metrics={
            "eval_accuracy": body.eval_accuracy,
            "baseline_accuracy": body.baseline_accuracy,
            "promoted_by": "manual",
        },
    )

    version = ModelVersion(
        track=body.track,
        model_name=body.model_name,
        provider=provider,
        status="active",
        eval_accuracy=body.eval_accuracy,
        baseline_accuracy=body.baseline_accuracy,
        promoted_at=datetime.now(timezone.utc),
    )
    db.add(version)
    await db.commit()
    return {"message": f"Model {body.model_name} promoted for track={body.track}"}


async def get_training_status(db: AsyncSession, settings) -> dict:
    from app.services.model_registry import ModelRegistry

    registry = await ModelRegistry.get_all_status()
    unexported = (await db.execute(select(func.count(AIFeedback.id)).where(AIFeedback.exported.is_(False)))).scalar_one()
    total = (await db.execute(select(func.count(AIFeedback.id)))).scalar_one()
    return {
        "finetune_enabled": settings.FINETUNE_ENABLED,
        "feedback": {"total": total, "unexported": unexported},
        "thresholds": {
            "classifier": settings.FINETUNE_CLASSIFIER_MIN_EXAMPLES,
            "reasoning": settings.FINETUNE_REASONING_MIN_EXAMPLES,
            "embedding": settings.FINETUNE_EMBED_MIN_PAIRS,
            "incremental_retrigger": settings.FINETUNE_INCREMENTAL_TRIGGER,
        },
        "active_models": registry,
    }


async def jira_resolution_webhook(db: AsyncSession, payload: dict) -> dict:
    issue_key = payload.get("issue", {}).get("key", "")
    status = (payload.get("issue", {}).get("fields", {}).get("status", {}).get("name", "")).lower()
    resolution = (payload.get("issue", {}).get("fields", {}).get("resolution", {}) or {}).get("name", "").lower()

    is_resolved = status in ("done", "resolved", "closed", "fixed")
    is_invalid = resolution in ("won't fix", "duplicate", "invalid", "not a bug", "cannot reproduce")

    if not is_resolved and not is_invalid:
        return {"message": "no action — not a resolution event"}

    defect = (await db.execute(select(Defect).where(Defect.jira_ticket_id == issue_key))).scalar_one_or_none()
    if not defect:
        return {"message": f"no defect found for {issue_key}"}

    defect.jira_status = status
    defect.resolution_status = "INVALID" if is_invalid else "RESOLVED"
    if is_resolved and not is_invalid:
        defect.resolved_at = datetime.now(timezone.utc)

    analysis = (await db.execute(select(AIAnalysis).where(AIAnalysis.test_case_id == defect.test_case_id))).scalar_one_or_none()
    rating = None
    if analysis:
        rating = FeedbackRating.INCORRECT if is_invalid else FeedbackRating.CORRECT
        source = "jira_invalid" if is_invalid else "jira_resolved"
        db.add(
            AIFeedback(
                analysis_id=analysis.id,
                test_case_id=defect.test_case_id,
                user_id=None,
                rating=rating,
                source=source,
                exported=False,
            )
        )

    await db.commit()
    return {
        "message": f"Feedback recorded: {issue_key} → {rating if analysis else 'no analysis found'}",
        "defect_id": str(defect.id),
    }
