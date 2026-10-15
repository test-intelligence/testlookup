"""ML classifier training: the status the Settings > AI card shows, and "Train now" (AIC-05).

``GET  /api/v1/settings/ai/ml/status``   QA lead and above (the same guard as
                                          ``GET /settings/ai``; the page is a
                                          QA-lead page). Reads only.
``POST /api/v1/settings/ai/ml/retrain``  ADMIN; a project-bound API key is
                                          refused (``require_role`` default).

No route here takes a path or query id, so no ownership guard applies: the
classifier is deployment-wide, like the rest of Settings > AI.

The POST is idempotent while a job holds ``ml:retrain:lock``: a second press
answers 202 with the running job's ``task_id`` and ``already_running: true``
and queues nothing. Below ``ML_MIN_TRAINING_SAMPLES`` (or without
scikit-learn) it answers 409 with a machine-readable ``detail`` the card
renders, and neither takes the lock nor queues. The job itself
(``services/ml/training_job.run_training_job``) releases the lock.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.db.postgres import get_db
from app.models.ml_training_schemas import MLRetrainAccepted, MLStatusRead
from app.models.postgres import User, UserRole
from app.services.ml import training_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/settings/ai/ml", tags=["AI Settings"])

QUEUE_UNAVAILABLE_DETAIL = "Training queue unavailable"


def _accepted_for(lock: dict, *, already_running: bool) -> MLRetrainAccepted:
    state = training_job.training_state(lock)["state"]
    return MLRetrainAccepted(task_id=str(lock["task_id"]), state=state, already_running=already_running)


@router.get("/status", response_model=MLStatusRead)
async def get_ml_status(
    _: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
) -> MLStatusRead:
    """Samples, the current model, the retrain lock and the last attempt. Never 500s on an outage."""
    return MLStatusRead.model_validate(await training_job.build_ml_status(db))


# activity: none -- the classifier is deployment-wide, no project changes; a trained model is audited in settings_audit_log (ml_classifier / trained) by the task.
@router.post("/retrain", response_model=MLRetrainAccepted, status_code=status.HTTP_202_ACCEPTED)
async def retrain_ml_classifier(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> MLRetrainAccepted:
    """Queue one training run of the ML classifier ("Train now")."""
    from app.db.redis_client import get_redis

    redis = get_redis()
    try:
        held = await training_job.read_lock(redis)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ml_retrain_lock_unreadable: %s", str(exc)[:300])
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=QUEUE_UNAVAILABLE_DETAIL)
    if held is not None:
        return _accepted_for(held, already_running=True)

    try:
        refusal = await training_job.retrain_refusal(db)
    except Exception as exc:  # noqa: BLE001 -- never queue on a count nobody could read
        logger.warning("ml_retrain_counts_unreadable: %s", str(exc)[:300])
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=QUEUE_UNAVAILABLE_DETAIL)
    if refusal is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal)

    task_id = str(uuid.uuid4())
    triggered_by = training_job.user_trigger(getattr(current_user, "username", None))
    try:
        acquired = await training_job.acquire_lock(redis, task_id=task_id, triggered_by=triggered_by)
        if not acquired:
            # Another press won the race between the read above and this SET NX.
            held = await training_job.read_lock(redis)
            if held is not None:
                return _accepted_for(held, already_running=True)
            raise RuntimeError("retrain lock is taken but unreadable")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("ml_retrain_lock_failed: %s", str(exc)[:300])
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=QUEUE_UNAVAILABLE_DETAIL)

    try:
        from app.worker.tasks import train_ml_classifier

        train_ml_classifier.apply_async(kwargs={"triggered_by": triggered_by}, task_id=task_id)
    except Exception as exc:  # noqa: BLE001 -- the broker refused: free the lock, say so
        logger.warning("ml_retrain_enqueue_failed: %s", str(exc)[:300])
        try:
            await training_job.release_lock(redis, task_id)
        except Exception:  # noqa: BLE001 -- the lock expires by itself
            pass
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=QUEUE_UNAVAILABLE_DETAIL)

    logger.info("ml_retrain_queued task_id=%s triggered_by=%s", task_id, triggered_by)
    return MLRetrainAccepted(task_id=task_id, state=training_job.STATE_QUEUED, already_running=False)
