"""
Celery tasks for the continuous fine-tuning pipeline.

Beat schedule (auto-configured in celery_app.py):
  export_training_data    — weekly (Sunday 02:00 UTC)
  check_finetune_trigger  — daily (03:00 UTC)  checks if enough new examples

Manual triggers available via POST /api/v1/training/export and /finetune.
"""
import asyncio
import logging

from app.worker.celery_app import celery_app

logger = logging.getLogger("training.tasks")


def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(
    name="app.worker.training_tasks.export_training_data",
    bind=True,
    max_retries=2,
    queue="default",
    time_limit=600,
)
def export_training_data(self):
    """
    Weekly task: export verified training examples to MinIO for all three tracks.
    After export, checks if any track has crossed its fine-tuning trigger threshold.
    """
    from app.services.training.exporter import TrainingDataExporter

    logger.info("[Task %s] Starting training data export", self.request.id)
    try:
        exporter = TrainingDataExporter()
        counts = _run_async(exporter.run())
        logger.info("[Task %s] Export complete: %s", self.request.id, counts)

        # Check whether any track now has enough examples to trigger fine-tuning
        _run_async(_maybe_trigger_finetune(counts))
        return counts

    except Exception as exc:
        logger.error("[Task %s] Export failed: %s", self.request.id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=3600)


@celery_app.task(
    name="app.worker.training_tasks.check_finetune_trigger",
    queue="default",
    time_limit=60,
)
def check_finetune_trigger():
    """
    Daily task: check if enough new verified feedback has accumulated
    to trigger an incremental fine-tuning run.
    """
    _run_async(_check_incremental_trigger())


@celery_app.task(
    name="app.worker.training_tasks.run_finetune_pipeline",
    bind=True,
    max_retries=1,
    queue="default",
    time_limit=7200,   # 2 hours max (OpenAI jobs can be slow)
)
def run_finetune_pipeline(self, track: str):
    """
    Run the complete fine-tuning pipeline for one track:
      1. Find the latest training JSONL in MinIO
      2. Submit fine-tuning job to provider
      3. Poll until complete (with timeout)
      4. Evaluate candidate model against holdout set
      5. Promote if evaluation passes, retire old version
      6. Persist ModelVersion record
    """
    from app.core.config import settings

    if not settings.FINETUNE_ENABLED:
        logger.info("[Task %s] FINETUNE_ENABLED=false — skipping track=%s", self.request.id, track)
        return {"skipped": True, "reason": "finetune_disabled"}

    logger.info("[Task %s] Fine-tuning pipeline started: track=%s", self.request.id, track)
    try:
        result = _run_async(_run_pipeline(track))
        logger.info("[Task %s] Fine-tuning pipeline complete: %s", self.request.id, result)
        return result
    except Exception as exc:
        logger.error("[Task %s] Fine-tuning pipeline failed: %s", self.request.id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=1800)


# ── Async pipeline implementation ─────────────────────────────────────────────

async def _maybe_trigger_finetune(export_counts: dict[str, int]) -> None:
    """Trigger fine-tuning for tracks that crossed their minimum threshold."""
    from app.core.config import settings

    if not settings.FINETUNE_ENABLED:
        return

    thresholds = {
        "classifier": settings.FINETUNE_CLASSIFIER_MIN_EXAMPLES,
        "reasoning":  settings.FINETUNE_REASONING_MIN_EXAMPLES,
        "embedding":  settings.FINETUNE_EMBED_MIN_PAIRS,
    }
    for track, count in export_counts.items():
        if count >= thresholds.get(track, 99999):
            logger.info("Threshold crossed for track=%s (count=%d) — queuing fine-tune", track, count)
            run_finetune_pipeline.apply_async(
                kwargs={"track": track},
                queue="default",
            )


async def _check_incremental_trigger() -> None:
    """Check if FINETUNE_INCREMENTAL_TRIGGER new unexported examples exist."""
    from sqlalchemy import func, select
    from app.core.config import settings
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import AIFeedback

    if not settings.FINETUNE_ENABLED:
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(func.count(AIFeedback.id)).where(AIFeedback.exported.is_(False))
        )
        unexported = result.scalar_one()

    if unexported >= settings.FINETUNE_INCREMENTAL_TRIGGER:
        logger.info(
            "Incremental trigger: %d unexported feedback examples — exporting + evaluating",
            unexported,
        )
        export_training_data.apply_async(queue="default")


async def _run_pipeline(track: str) -> dict:
    """Full fine-tuning pipeline for a single track."""
    import asyncio
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.core.config import settings
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import ModelVersion
    from app.services.model_registry import ModelRegistry
    from app.services.training.finetuner import FineTuningPipeline
    from app.services.training.evaluator import ModelEvaluator

    # Step 1: Find the latest training JSONL path in MinIO
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    training_path = f"{track}/{today}.jsonl"
    holdout_path = f"{track}/holdout-{today}.jsonl"

    # Step 2: Submit fine-tuning job
    job = await FineTuningPipeline.submit(track=track, training_path=training_path)

    if job.get("status") in ("skipped", "manual_required"):
        return job

    provider_job_id = job["provider_job_id"]
    candidate_model = job["model_name"]

    # Step 3: Poll until complete (max 90 minutes, checking every 60s)
    if job["status"] == "running":
        for _ in range(90):
            await asyncio.sleep(60)
            status = await FineTuningPipeline.poll_status(provider_job_id)
            if status["status"] == "succeeded":
                candidate_model = status.get("fine_tuned_model") or candidate_model
                break
            elif status["status"] == "failed":
                raise RuntimeError(f"Fine-tuning job {provider_job_id} failed")
        else:
            raise TimeoutError(f"Fine-tuning job {provider_job_id} timed out after 90 minutes")

    # Step 4: Evaluate candidate
    eval_result = await ModelEvaluator.evaluate(
        track=track,
        candidate_model=candidate_model,
        holdout_path=holdout_path,
    )

    # Step 5: Persist ModelVersion record
    async with AsyncSessionLocal() as db:
        version = ModelVersion(
            track=track,
            model_name=candidate_model,
            provider=settings.LLM_PROVIDER,
            status="active" if eval_result.get("approved") else "failed",
            eval_accuracy=eval_result.get("candidate_accuracy"),
            baseline_accuracy=eval_result.get("baseline_accuracy"),
            eval_details=eval_result,
            provider_job_id=provider_job_id,
            training_file_path=training_path,
            promoted_at=datetime.now(timezone.utc) if eval_result.get("approved") else None,
        )
        db.add(version)

        # Step 6: Retire previous active version if promoting new one
        if eval_result.get("approved"):
            prev = await db.execute(
                select(ModelVersion)
                .where(ModelVersion.track == track)
                .where(ModelVersion.status == "active")
                .order_by(ModelVersion.created_at.desc())
                .limit(1)
            )
            prev_version = prev.scalar_one_or_none()
            if prev_version and prev_version.model_name != candidate_model:
                prev_version.status = "retired"
                prev_version.retired_at = datetime.now(timezone.utc)

        await db.commit()

    # Step 7: Hot-swap active model in Redis if approved
    if eval_result.get("approved"):
        await ModelRegistry.promote(
            track=track,
            model_name=candidate_model,
            metrics={
                "eval_accuracy": eval_result.get("candidate_accuracy"),
                "baseline_accuracy": eval_result.get("baseline_accuracy"),
                "improvement": eval_result.get("improvement"),
            },
        )
        logger.info(
            "PROMOTED model: track=%s model=%s accuracy=%.3f (was %.3f, +%.3f)",
            track, candidate_model,
            eval_result.get("candidate_accuracy", 0),
            eval_result.get("baseline_accuracy", 0),
            eval_result.get("improvement", 0),
        )
    else:
        logger.info(
            "NOT promoted — insufficient improvement: track=%s model=%s improvement=%.3f (need %.3f)",
            track, candidate_model,
            eval_result.get("improvement", 0),
            settings.FINETUNE_MIN_ACCURACY_GAIN,
        )

    return {
        "track": track,
        "candidate_model": candidate_model,
        "promoted": eval_result.get("approved", False),
        **eval_result,
    }
