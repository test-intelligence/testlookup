"""Redis-backed status for async report uploads (PRD MRU-5/6).

The ``ingest_uploaded_file`` Celery task processes an upload out-of-band, so the
HTTP 202 can't report whether parsing/ingestion actually succeeded. This module
records a small per-task status record in Redis that the task updates at each
stage and the ``GET /api/v1/ingest/uploads/{task_id}`` endpoint reads, so the UI
can surface real success/parse-error feedback instead of a silent background run.

The record is intentionally lightweight (a single JSON string with a TTL) — no
schema migration, no durable table. ``project_id`` is stored so the read
endpoint can authorize against project membership (no IDOR on task_id).
"""
from __future__ import annotations

import json
from typing import Any, Optional

import structlog

logger = structlog.get_logger("services.upload_status")

# 24h — long enough for a user to see the outcome; short enough to self-expire.
STATUS_TTL_SECONDS = 24 * 60 * 60
_KEY = "upload:status:{task_id}"

# State machine: pending → parsing → ingesting → succeeded | failed
STATE_PENDING = "pending"
STATE_PARSING = "parsing"
STATE_INGESTING = "ingesting"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED = "failed"


def _key(task_id: str) -> str:
    return _KEY.format(task_id=task_id)


async def set_status(
    task_id: str,
    *,
    run_id: Optional[str] = None,
    project_id: Optional[str] = None,
    state: str,
    progress: Optional[dict[str, Any]] = None,
    result: Optional[dict[str, Any]] = None,
    error: Optional[dict[str, Any]] = None,
) -> None:
    """Upsert the status record for ``task_id`` (best-effort — never raises into
    the caller; a Redis blip must not fail the ingest itself)."""
    from app.db.redis_client import get_redis

    record = {
        "task_id": task_id,
        "run_id": run_id,
        "project_id": project_id,
        "state": state,
        "progress": progress,
        "result": result,
        "error": error,
    }
    try:
        redis = get_redis()
        await redis.set(_key(task_id), json.dumps(record), ex=STATUS_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001 — status is advisory; don't break ingest
        logger.warning("upload_status_write_failed", task_id=task_id, error=str(exc))


async def get_status(task_id: str) -> Optional[dict[str, Any]]:
    """Return the parsed status record, or None if absent/expired.

    Best-effort and symmetric with set_status: a Redis outage returns None
    (so the endpoint degrades to a clean 404 / "still processing") rather than
    surfacing a 500 to the polling client.
    """
    from app.db.redis_client import get_redis

    try:
        redis = get_redis()
        raw = await redis.get(_key(task_id))
    except Exception as exc:  # noqa: BLE001 — read is advisory; degrade gracefully
        logger.warning("upload_status_read_failed", task_id=task_id, error=str(exc))
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    # Only an object is a valid record; anything else (bare number/list) → None
    # so the endpoint's record.get(...) can't raise.
    return parsed if isinstance(parsed, dict) else None
