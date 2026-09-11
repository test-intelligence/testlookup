"""MinIO webhook handler — receives ObjectCreated events and queues ingestion."""
import logging
from urllib.parse import unquote_plus

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from app.core.config import settings
from app.core.deps import verify_webhook_secret
from app.models.schemas import MinIOWebhookEvent
from app.services.minio_sentinel import SENTINEL_NAME, sentinel_key_problem
from app.worker.tasks import ingest_test_run

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


def _object_keys(event: MinIOWebhookEvent) -> list[str]:
    """The object keys this notification names, in OUR bucket, decoded (re-audit R15).

    MinIO's event carries two keys, and neither is the object key as stored:

    * ``Records[].s3.object.key`` is the object's key URL-encoded (Go's
      ``url.QueryEscape``: ``/`` is ``%2F``, a space is ``+``), beside
      ``Records[].s3.bucket.name``. This is the authoritative one.
    * The top-level ``Key`` is ``<bucket>/<object>``. The handler used to take
      it whole, so the bucket name became the project, and the task read a
      key that does not exist.

    A record in another bucket is not ours to ingest. The top-level ``Key`` is
    used only when no record names an object, and only inside our bucket.
    Decoding happens BEFORE any check, so ``%2E%2E`` is a ``..`` segment.
    """
    bucket = settings.MINIO_BUCKET_NAME
    keys: list[str] = []
    named = False
    for record in event.Records or []:
        s3 = record.get("s3") if isinstance(record, dict) else None
        if not isinstance(s3, dict):
            continue
        obj = s3.get("object")
        raw = obj.get("key") if isinstance(obj, dict) else None
        if not isinstance(raw, str) or not raw:
            continue
        named = True
        bucket_info = s3.get("bucket")
        record_bucket = bucket_info.get("name") if isinstance(bucket_info, dict) else None
        if record_bucket is not None and record_bucket != bucket:
            logger.info("Ignoring an event for bucket %r", record_bucket)
            continue
        keys.append(unquote_plus(raw))
    if not named and event.Key:
        prefix = f"{bucket}/"
        if event.Key.startswith(prefix):
            keys.append(event.Key[len(prefix):])
        else:
            logger.info("Ignoring an event outside bucket %r", bucket)
    return keys


@router.post("/minio", status_code=200, dependencies=[Depends(verify_webhook_secret)])
async def minio_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Receive MinIO ObjectCreated events.
    Requires MinIO's auth_token (``Authorization: Bearer``) or X-Webhook-Secret,
    matching WEBHOOK_SECRET.
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

    keys = _object_keys(event)
    if not keys:
        return {"status": "ignored", "reason": "not_our_bucket"}

    queued: list[dict] = []
    reason = "not_sentinel"
    for key in keys:
        # Only process sentinel files
        if not key.endswith(SENTINEL_NAME):
            logger.debug("Ignoring non-sentinel upload: %s", key)
            continue

        # {project_id}/runs/{build_number}/upload_complete.json, made of plain
        # names. A key with an empty, "." or ".." segment, a backslash or a
        # leading "/" is refused here, before anything is queued, and again by
        # the task's reader (code review of the N10 follow-up): on the local
        # storage backend a ".." let the key name one project while the
        # sentinel came from another project's prefix.
        problem = sentinel_key_problem(key)
        if problem is not None:
            logger.warning("Refusing sentinel key %r: %s", key, problem)
            reason = "unexpected_key_format"
            continue

        logger.info("Sentinel file received: %s", key)

        # The S3 prefix the run's result files are read from.
        parts = key.split("/")
        minio_prefix = "/".join(parts[:-1]) + "/"

        # The sentinel is read by the ingestion task, not here (code review of
        # re-audit N10). Reading it here answered 200 "ignored" on ANY storage
        # error -- and MinIO treats a 200 as delivered, so one transient 503
        # lost the upload. The task retries a storage hiccup with backoff,
        # reads through a size cap, and keeps the object off the API process.
        # It takes the project from this key, never from the request body or
        # from what the sentinel claims: see services/minio_sentinel.py.
        task = ingest_test_run.delay(sentinel_key=key, minio_prefix=minio_prefix)
        logger.info("Queued ingestion task %s for sentinel %s", task.id, key)
        queued.append({"task_id": task.id, "project_id": parts[0], "sentinel_key": key})

    if not queued:
        return {"status": "ignored", "reason": reason}
    first = queued[0]
    return {
        "status": "queued",
        "task_id": first["task_id"],
        "project_id": first["project_id"],
        "sentinel_key": first["sentinel_key"],
        "queued": queued,
    }
