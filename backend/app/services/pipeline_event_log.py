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

The SQL outbox accepts event intent. Materialization creates one immutable
Mongo document per event ID. Authorized retention handles deletion.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.mongo import Collections, get_mongo_db
from app.db.postgres import get_session_factory
from app.models.postgres import PipelineEventOutbox
from app.services.pipeline_event_admission import EventAdmissionError, bounded_event_payload, stage_pipeline_event

logger = logging.getLogger("services.pipeline_event_log")

_COLLECTION = Collections.PIPELINE_EVENT_LOG
_SOURCE_NAME = re.compile(r"[A-Za-z0-9._:-]{0,128}")
_ADMISSION_SECONDS = 2


def _admission_failure(event_id: UUID, reason: str) -> bool:
    from app.core.metrics import pipeline_event_admission_failures

    pipeline_event_admission_failures.labels(reason=reason).inc()
    logger.warning("Pipeline event not confirmed, event_id=%s reason=%s", event_id, reason)
    return False


async def emit_event(
    pipeline_run_id: str,
    event_type: str,
    *,
    stage_name: Optional[str] = None,
    test_case_id: str | UUID | None = None,
    detail: Optional[dict[str, Any]] = None,
    db: AsyncSession | None = None,
    project_id: UUID | str | None = None,
    test_run_id: UUID | None = None,
    event_id: UUID | None = None,
) -> bool:
    """Admit one event or stage it atomically with an uncommitted source write."""
    identity = event_id or uuid4()
    if isinstance(test_case_id, UUID):
        test_case_id = str(test_case_id)
    required = (pipeline_run_id, event_type)
    optional = (stage_name, test_case_id)
    if not event_type or any(not isinstance(x, str) or not _SOURCE_NAME.fullmatch(x) for x in required):
        return _admission_failure(identity, "payload")
    if any(x is not None and (not isinstance(x, str) or not _SOURCE_NAME.fullmatch(x)) for x in optional):
        return _admission_failure(identity, "payload")
    event = {
        "event_id": str(identity),
        "pipeline_run_id": pipeline_run_id,
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stage_name": stage_name,
        "test_case_id": test_case_id,
        "test_run_id": str(test_run_id) if test_run_id else None,
        "detail": detail or {},
    }
    try:
        payload = bounded_event_payload(event)
        async with asyncio.timeout(_ADMISSION_SECONDS):
            if db is not None:
                async with db.begin_nested():
                    await stage_pipeline_event(db, event_id=identity, event=payload, project_id=project_id)
            else:
                async with get_session_factory().begin() as session:
                    await stage_pipeline_event(session, event_id=identity, event=payload, project_id=project_id)
    except EventAdmissionError as exc:
        return _admission_failure(identity, str(exc))
    except Exception:  # noqa: BLE001 - do not replace business outcomes with optional event errors
        return _admission_failure(identity, "storage")
    return True


def _event_time(event: dict) -> datetime:
    timestamp = event.get("timestamp")
    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp)
        except ValueError:
            timestamp = None
    if not isinstance(timestamp, datetime):
        return datetime.min.replace(tzinfo=timezone.utc)
    return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)


async def _read_retained_events(pipeline_run_id: str, db: AsyncSession | None) -> list[dict]:
    statement = (
        select(PipelineEventOutbox.payload)
        .where(PipelineEventOutbox.pipeline_run_id == pipeline_run_id)
        .order_by(PipelineEventOutbox.created_at, PipelineEventOutbox.id)
        .limit(500)
    )
    if db is not None:
        return list((await db.scalars(statement)).all())
    async with get_session_factory()() as session:
        return list((await session.scalars(statement)).all())


async def get_pipeline_timeline(pipeline_run_id: str, *, db: AsyncSession | None = None) -> list[dict]:
    """Read both accepted intents and materialized history, oldest first."""
    try:
        if db is None:
            async with asyncio.timeout(_ADMISSION_SECONDS):
                accepted = await _read_retained_events(pipeline_run_id, db)
        else:
            # Keep an unavailable or pre-migration outbox from aborting this
            # request's transaction and the already-authorized Mongo read.
            async with db.begin_nested():
                accepted = await _read_retained_events(pipeline_run_id, db)
    except Exception as exc:  # noqa: BLE001 - preserve the materialized view
        logger.warning("Pipeline event outbox timeline unavailable (%s)", type(exc).__name__)
        accepted = []
    try:
        mongo = get_mongo_db()
        cursor = mongo[_COLLECTION].find(
            {"pipeline_run_id": pipeline_run_id},
            {"_id": 0},
        ).sort([("timestamp", 1), ("_id", 1)])
        materialized = await cursor.to_list(length=500)
    except Exception as exc:
        logger.warning("Pipeline timeline query failed (%s)", type(exc).__name__)
        materialized = []
    seen = {event.get("event_id") for event in materialized}
    merged = [*materialized, *(event for event in accepted if event["event_id"] not in seen)]
    merged.sort(key=lambda item: (_event_time(item), item.get("event_id") or ""))
    return [{**event, "timestamp": _event_time(event)} for event in merged[:500]]


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
        logger.warning("Pipeline event stats query failed (%s)", type(exc).__name__)
        return {}
