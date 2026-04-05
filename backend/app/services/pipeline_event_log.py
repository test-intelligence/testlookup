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
from datetime import datetime, timezone
from typing import Any, Optional

from app.db.mongo import get_mongo_db

logger = logging.getLogger("services.pipeline_event_log")

_COLLECTION = "pipeline_event_log"


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
    try:
        db = get_mongo_db()
        event = {
            "pipeline_run_id": pipeline_run_id,
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc),
            "stage_name": stage_name,
            "test_case_id": test_case_id,
            "detail": detail or {},
        }
        await db[_COLLECTION].insert_one(event)
    except Exception as exc:
        # Event logging is best-effort — never fail the pipeline
        logger.debug("Pipeline event log write failed: %s", exc)


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
