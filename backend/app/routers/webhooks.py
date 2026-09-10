"""MinIO webhook handler — receives ObjectCreated events and queues ingestion."""
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from app.core.deps import verify_webhook_secret
from app.models.schemas import MinIOWebhookEvent
from app.services.minio_sentinel import SENTINEL_NAME, sentinel_key_problem
from app.worker.tasks import ingest_test_run

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post("/minio", status_code=200, dependencies=[Depends(verify_webhook_secret)])
async def minio_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Receive MinIO ObjectCreated events.
    Requires X-Webhook-Secret header matching WEBHOOK_SECRET env var.
    Only processes uploads of upload_complete.json sentinel files.
    Always returns 200 OK quickly to prevent MinIO retry loops.
    """
    try:
        body = await request.json()
    except Exception:
        logger.warning("Received non-JSON webhook payload — ignoring")
        return {"status": "ignored", "reason": "invalid_json"}

    # Parse event
    try:
        event = MinIOWebhookEvent(**body)
    except Exception as e:
        logger.warning("Webhook payload parse failed: %s", e)
        return {"status": "ignored", "reason": "parse_error"}

    key = event.Key or ""

    # Only process sentinel files
    if not key.endswith(SENTINEL_NAME):
        logger.debug("Ignoring non-sentinel upload: %s", key)
        return {"status": "ignored", "reason": "not_sentinel"}

    # {project_id}/runs/{build_number}/upload_complete.json, made of plain
    # names. A key with an empty, "." or ".." segment, a backslash or a
    # leading "/" is refused here, before anything is queued, and again by the
    # task's reader (code review of the N10 follow-up): on the local storage
    # backend a ".." let the key name one project while the sentinel came
    # from another project's prefix.
    problem = sentinel_key_problem(key)
    if problem is not None:
        logger.warning("Refusing sentinel key %r: %s", key, problem)
        return {"status": "ignored", "reason": "unexpected_key_format"}

    logger.info("Sentinel file received: %s", key)

    # The S3 prefix the run's result files are read from.
    parts = key.split("/")
    minio_prefix = "/".join(parts[:-1]) + "/"

    # The sentinel is read by the ingestion task, not here (code review of
    # re-audit N10). Reading it here answered 200 "ignored" on ANY storage
    # error -- and MinIO treats a 200 as delivered, so one transient 503 lost
    # the upload. The task retries a storage hiccup with backoff, reads through
    # a size cap, and keeps the object off the API process. It takes the
    # project from this key, never from the request body or from what the
    # sentinel claims: see services/minio_sentinel.py.
    task = ingest_test_run.delay(sentinel_key=key, minio_prefix=minio_prefix)

    logger.info("Queued ingestion task %s for sentinel %s", task.id, key)
    return {
        "status": "queued",
        "task_id": task.id,
        "project_id": parts[0],
        "sentinel_key": key,
    }
