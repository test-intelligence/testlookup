"""
Immutable pipeline event log — append-only MongoDB collection.

Records every significant pipeline action as an immutable event:
  - stage_started, stage_completed, stage_failed, stage_skipped
  - tool_invoked, llm_called
  - cache_hit, checkpoint_restored
  - error_occurred, pipeline_completed

This enables:
  1. Crash replay — reconstruct exact pipeline state from event history
  2. Debugging — trace why an AI made a specific recommendation
  3. Audit — immutable timeline of all pipeline actions
  4. Cost tracking — count LLM calls and tool invocations per run

Events are append-only (insert, never update or delete).
"""
import logging
import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from app.db.mongo import get_mongo_db

logger = logging.getLogger("services.pipeline_event_log")

_COLLECTION = "pipeline_event_log"
_MAX_WRITE_ATTEMPTS = 2
_DEAD_LETTER_LIMIT = 200
_WRITE_FAILURE_COUNT = 0
_DEAD_LETTER_EVENTS: list[dict[str, Any]] = []


def _record_dead_letter(event: dict[str, Any], error: Exception) -> None:
    """Keep bounded accounting for audit writes that could not reach Mongo."""
    global _WRITE_FAILURE_COUNT
    _WRITE_FAILURE_COUNT += 1
    _DEAD_LETTER_EVENTS.append({
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "error": str(error)[:500],
        "event": {
            "pipeline_run_id": event.get("pipeline_run_id"),
            "event_type": event.get("event_type"),
            "stage_name": event.get("stage_name"),
            "test_case_id": event.get("test_case_id"),
            "detail": event.get("detail", {}),
        },
    })
    if len(_DEAD_LETTER_EVENTS) > _DEAD_LETTER_LIMIT:
        del _DEAD_LETTER_EVENTS[: len(_DEAD_LETTER_EVENTS) - _DEAD_LETTER_LIMIT]


async def emit_event(
    pipeline_run_id: str,
    event_type: str,
    *,
    stage_name: Optional[str] = None,
    test_case_id: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> None:
    """
    Append an immutable event to the pipeline event log.

    Args:
        pipeline_run_id: The pipeline run this event belongs to.
        event_type: One of: stage_started, stage_completed, stage_failed,
                    stage_skipped, tool_invoked, llm_called, cache_hit,
                    checkpoint_restored, error_occurred, pipeline_completed.
        stage_name: The pipeline stage (optional, depends on event type).
        test_case_id: The test case ID (optional, for per-test events).
        detail: Additional event-specific data.
    """
    event = {
        "pipeline_run_id": pipeline_run_id,
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc),
        "stage_name": stage_name,
        "test_case_id": test_case_id,
        "detail": detail or {},
    }
    last_error: Exception | None = None
    for attempt in range(1, _MAX_WRITE_ATTEMPTS + 1):
        try:
            db = get_mongo_db()
            await db[_COLLECTION].insert_one(event)
            return
        except Exception as exc:
            last_error = exc
            if attempt < _MAX_WRITE_ATTEMPTS:
                await asyncio.sleep(0)

    # Event logging is best-effort — never fail the pipeline — but failed
    # writes are now counted and retained so operators can audit gaps.
    if last_error is not None:
        _record_dead_letter(event, last_error)
        logger.warning(
            "Pipeline event log write failed after %s attempts: %s",
            _MAX_WRITE_ATTEMPTS,
            last_error,
        )


async def get_pipeline_timeline(pipeline_run_id: str) -> list[dict]:
    """
    Retrieve the full event timeline for a pipeline run.

    Returns events sorted by timestamp (oldest first).
    """
    try:
        db = get_mongo_db()
        cursor = db[_COLLECTION].find(
            {"pipeline_run_id": pipeline_run_id},
            {"_id": 0},
        ).sort("timestamp", 1)
        return await cursor.to_list(length=500)
    except Exception as exc:
        logger.warning("Pipeline timeline query failed: %s", exc)
        return []


async def get_pipeline_event_stats(pipeline_run_id: str) -> dict:
    """Return aggregate stats for a pipeline run's events."""
    try:
        db = get_mongo_db()
        pipeline: list[dict[str, Any]] = [
            {"$match": {"pipeline_run_id": pipeline_run_id}},
            {"$group": {
                "_id": "$event_type",
                "count": {"$sum": 1},
            }},
        ]
        cursor = db[_COLLECTION].aggregate(pipeline)
        stats = {}
        async for doc in cursor:
            stats[doc["_id"]] = doc["count"]
        return stats
    except Exception as exc:
        logger.warning("Pipeline event stats query failed: %s", exc)
        return {}


def get_event_log_health() -> dict[str, Any]:
    """Expose bounded write-failure accounting for audit observability."""
    return {
        "write_failure_count": _WRITE_FAILURE_COUNT,
        "dead_letter_count": len(_DEAD_LETTER_EVENTS),
        "dead_letter_limit": _DEAD_LETTER_LIMIT,
        "recent_dead_letters": list(_DEAD_LETTER_EVENTS),
    }
