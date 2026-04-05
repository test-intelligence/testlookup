"""MinIO webhook handler — receives ObjectCreated events and queues ingestion."""
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from app.core.deps import verify_webhook_secret
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

    # Read sentinel content from the webhook records if available
    sentinel_data = {}
    records = event.Records or []
    for record in records:
        s3_obj = record.get("s3", {})
        obj_key = s3_obj.get("object", {}).get("key", "")
        if obj_key.endswith("upload_complete.json"):
            user_meta = record.get("s3", {}).get("object", {}).get("userMetadata", {})
            sentinel_data.update(user_meta)

    # If no sentinel data in event, derive from key path
    if not sentinel_data.get("build_number"):
        project_id = parts[0] if len(parts) > 0 else "unknown"
        build_number = parts[2] if len(parts) > 2 else "unknown"
        sentinel_data = {
            "project_id": project_id,
            "build_number": build_number,
        }

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
