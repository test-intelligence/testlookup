"""MinIO webhook handler — receives ObjectCreated events and queues ingestion."""
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from app.core.deps import verify_webhook_secret
from app.models.schemas import MinIOWebhookEvent
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
    if not key.endswith("upload_complete.json"):
        logger.debug("Ignoring non-sentinel upload: %s", key)
        return {"status": "ignored", "reason": "not_sentinel"}

    logger.info("Sentinel file received: %s", key)

    # Extract S3 prefix from key path: {project_id}/runs/{build_number}/upload_complete.json
    parts = key.split("/")
    if len(parts) < 3:
        logger.warning("Unexpected sentinel key format: %s", key)
        return {"status": "ignored", "reason": "unexpected_key_format"}

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
