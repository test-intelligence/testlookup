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
_VALID_STATES = {
    STATE_PENDING,
    STATE_PARSING,
    STATE_INGESTING,
    STATE_SUCCEEDED,
    STATE_FAILED,
}

# Compare and write inside Redis so API and worker processes cannot interleave
# a read/check/write sequence. Terminal records are immutable and non-terminal
# states may only advance through the state machine. If the key is absent or
# contains an old/unreadable value, a valid update repairs it.
_SET_STATUS_LUA = """
local current_json = redis.call('GET', KEYS[1])
if current_json then
    local decoded_ok, current = pcall(cjson.decode, current_json)
    if decoded_ok and type(current) == 'table' then
        local current_state = current['state']
        if current_state == 'succeeded' or current_state == 'failed' then
            return 0
        end

        local ranks = {
            pending = 0,
            parsing = 1,
            ingesting = 2,
            succeeded = 3,
            failed = 3
        }
        local current_rank = ranks[current_state]
        local requested_rank = ranks[ARGV[2]]
        if current_rank and requested_rank and requested_rank < current_rank then
            return 0
        end
    end
end

redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
return 1
"""

_CLEAR_PENDING_LUA = """
local current_json = redis.call('GET', KEYS[1])
if not current_json then
    return 0
end

local decoded_ok, current = pcall(cjson.decode, current_json)
if decoded_ok and type(current) == 'table' and current['state'] == 'pending' then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


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
) -> bool:
    """Advance ``task_id`` atomically, returning whether Redis accepted it.

    Writes are best-effort and never raise into the caller; a Redis blip must
    not fail the ingest itself. Terminal states cannot be replaced, and an
    earlier processing state cannot overwrite a later one.
    """
    from app.db.redis_client import get_redis

    if state not in _VALID_STATES:
        logger.warning("upload_status_invalid_state", task_id=task_id, state=state)
        return False

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
        # redis-py's mixed sync/async overloads make ``eval`` appear as a
        # union to mypy even though get_redis() always returns the async client.
        redis: Any = get_redis()
        accepted = await redis.eval(
            _SET_STATUS_LUA,
            1,
            _key(task_id),
            json.dumps(record),
            state,
            str(STATUS_TTL_SECONDS),
        )
        return bool(accepted)
    except Exception as exc:  # noqa: BLE001 — status is advisory; don't break ingest
        logger.warning("upload_status_write_failed", task_id=task_id, error=str(exc))
        return False


async def clear_pending_status(task_id: str) -> bool:
    """Remove a dispatch seed only while it is still in ``pending``.

    The conditional delete is atomic: if an ambiguously published worker has
    already advanced or completed, its newer status is preserved.
    """
    from app.db.redis_client import get_redis

    try:
        redis: Any = get_redis()
        deleted = await redis.eval(_CLEAR_PENDING_LUA, 1, _key(task_id))
        return bool(deleted)
    except Exception as exc:  # noqa: BLE001 — status cleanup is advisory
        logger.warning("upload_pending_cleanup_failed", task_id=task_id, error=str(exc))
        return False


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
