"""MinIO webhook handler — receives ObjectCreated events and queues ingestion."""
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from app.core.deps import verify_webhook_secret
from app.db.storage import get_storage_provider
from app.models.schemas import MinIOWebhookEvent, SentinelFile
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

    # ── Read the sentinel from STORAGE, not from the notification ─────────
    #
    # Re-audit N10. This used to take the sentinel's fields -- project_id
    # included -- from the request body's ``Records[].s3.object.userMetadata``,
    # falling back to the object key. Both are chosen by whoever sends the
    # request, and the only credential in front of this endpoint is one
    # deployment-wide WEBHOOK_SECRET that names no tenant. Downstream,
    # ``_upsert_test_run`` resolves that project string as a UUID *or a slug*
    # against any project, so a holder of that secret could file a fabricated
    # run into any tenant -- and ``process_sentinel`` then reads result objects
    # from the caller-named prefix, making it a cross-tenant read too. Same
    # shape as H1 on /ws/events.
    #
    # Fetching the object the notification refers to closes it: to be ingested
    # into a project you must be able to WRITE into that project's prefix in
    # the bucket, which is a storage credential and not a shared secret.
    storage = get_storage_provider()
    try:
        raw = await storage.get_object_content(key)
        sentinel_data = json.loads(raw)
    except Exception as e:
        # Fail CLOSED. Falling back to the request body is exactly the
        # behaviour being removed, and a sentinel that cannot be read is not a
        # sentinel -- a notification for an object that is not there is either
        # a race or a forgery, and both should be retried by MinIO rather than
        # guessed at.
        logger.warning("Sentinel object unreadable, refusing: %s (%s)", key, e)
        return {"status": "ignored", "reason": "sentinel_unreadable"}

    if not isinstance(sentinel_data, dict):
        logger.warning("Sentinel is not a JSON object: %s", key)
        return {"status": "ignored", "reason": "invalid_sentinel_content"}

    # The project comes from WHERE THE DATA IS, never from what it claims.
    # process_sentinel reads the run's result files from ``minio_prefix``, which
    # is derived from this same key, so binding the project to the key keeps
    # the two from ever disagreeing.
    sentinel_data = {**sentinel_data, "project_id": parts[0]}
    sentinel_data.setdefault("build_number", parts[2] if len(parts) > 2 else "unknown")

    try:
        sentinel = SentinelFile(**sentinel_data)
    except Exception as e:
        logger.warning("Could not parse sentinel data: %s. Data: %s", e, sentinel_data)
        return {"status": "ignored", "reason": "invalid_sentinel_content"}

    task = ingest_test_run.delay(
        sentinel_dict=sentinel.model_dump(),
        minio_prefix=minio_prefix,
    )

    logger.info(
        "Queued ingestion task %s for project=%s build=%s",
        task.id,
        sentinel.project_id,
        sentinel.build_number,
    )

    return {
        "status": "queued",
        "task_id": task.id,
        "project_id": sentinel.project_id,
        "build_number": sentinel.build_number,
    }
