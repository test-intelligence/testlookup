"""Project-scoped admission and purge fencing for pipeline event evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import BigInteger, delete, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AgentPipelineRun, PipelineEventOutbox, TestCase, TestRun
from app.services.evidence_sanitizer import sanitize_persistence_payload

PIPELINE_EVENT_CAPACITY = 10_000
MAX_PIPELINE_EVENT_BYTES = 16_384
_ADMISSION_LOCK = 0x544C50454F555442
# The resolved run UUID can replace JSON null after source scope is checked.
_RUN_ID_GROWTH_BYTES = 34


class EventAdmissionError(Exception):
    """A safe, bounded reason for an event that could not be admitted."""


def as_uuid(value: UUID | str | None) -> UUID | None:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(value) if value else None
    except ValueError:
        return None


def _json_value(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError("Unsupported event value")


def bounded_event_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Retain event identity even when optional detail cannot be stored."""
    payload = event.copy()
    try:
        detail, _ = sanitize_persistence_payload(event["detail"])
        payload["detail"] = detail
        serialized = json.dumps(payload, default=_json_value, allow_nan=False, ensure_ascii=False)
        reserved = _RUN_ID_GROWTH_BYTES if payload.get("test_run_id") is None else 0
        if len(serialized.encode("utf-8")) + reserved <= MAX_PIPELINE_EVENT_BYTES:
            return cast(dict[str, Any], json.loads(serialized))
    except (TypeError, ValueError, UnicodeError, OverflowError):
        pass
    payload["detail"] = {"detail_omitted": "size_or_content"}
    return cast(dict[str, Any], json.loads(json.dumps(payload, default=_json_value, allow_nan=False)))


async def lock_project_event_history(db: AsyncSession, project_id: UUID, *, exclusive: bool = False) -> bool:
    """Serialize Mongo-first policy deletion with SQL admission and delivery."""
    if db.get_bind().dialect.name != "postgresql":
        return True
    key = literal(int.from_bytes(hashlib.blake2b(
        project_id.bytes, digest_size=8, person=b"tl-event-project",
    ).digest(), "big", signed=True), type_=BigInteger())
    if exclusive:
        await db.execute(select(func.pg_advisory_xact_lock(key)))
        return True
    return bool(await db.scalar(select(func.pg_try_advisory_xact_lock_shared(key))))


async def _source_scope(
    db: AsyncSession, event: dict[str, Any], supplied_project_id: UUID | str | None,
) -> tuple[UUID, UUID | None, UUID | None, UUID | None]:
    project_id = as_uuid(supplied_project_id)
    pipeline_id = as_uuid(event["pipeline_run_id"])
    test_run_id = as_uuid(event.get("test_run_id"))
    case_id = as_uuid(event["test_case_id"])
    if pipeline_id:
        pipeline = (await db.execute(
            select(TestRun.id, TestRun.project_id).join(AgentPipelineRun, AgentPipelineRun.test_run_id == TestRun.id)
            .where(AgentPipelineRun.id == pipeline_id)
        )).one_or_none()
        if pipeline is None:
            if project_id is None or event["event_type"] != "agent_run_recorded":
                raise EventAdmissionError("scope")
            # These ledger writers use the committed investigation or fixer
            # identity as their correlation ID, rather than a pipeline UUID.
            pipeline_id = None
        else:
            if (project_id and pipeline.project_id != project_id) or (test_run_id and pipeline.id != test_run_id):
                raise EventAdmissionError("scope")
            project_id, test_run_id = pipeline.project_id, pipeline.id
    if case_id:
        case = (await db.execute(
            select(TestRun.id, TestRun.project_id).join(TestCase, TestCase.test_run_id == TestRun.id)
            .where(TestCase.id == case_id)
        )).one_or_none()
        if case is None or (project_id and case.project_id != project_id) or (test_run_id and case.id != test_run_id):
            raise EventAdmissionError("scope")
        project_id, test_run_id = case.project_id, case.id
    if test_run_id and not (pipeline_id or case_id):
        run_project = await db.scalar(select(TestRun.project_id).where(TestRun.id == test_run_id))
        if run_project is None or (project_id and run_project != project_id):
            raise EventAdmissionError("scope")
        project_id = run_project
    if project_id is None:
        raise EventAdmissionError("scope")
    return project_id, pipeline_id, test_run_id, case_id


async def stage_pipeline_event(
    db: AsyncSession, *, event_id: UUID, event: dict[str, Any], project_id: UUID | str | None,
) -> None:
    """Stage the event in the caller's transaction without committing it."""
    scope, pipeline_id, test_run_id, case_id = await _source_scope(db, event, project_id)
    event = {**event, "test_run_id": str(test_run_id) if test_run_id else None}
    if not await lock_project_event_history(db, scope):
        raise EventAdmissionError("contention")
    if db.get_bind().dialect.name == "postgresql":
        quota_key = literal(_ADMISSION_LOCK, type_=BigInteger())
        if not await db.scalar(select(func.pg_try_advisory_xact_lock(quota_key))):
            raise EventAdmissionError("contention")
    previous = await db.get(PipelineEventOutbox, event_id)
    if previous is not None:
        prior = {key: value for key, value in previous.payload.items() if key != "timestamp"}
        current = {key: value for key, value in event.items() if key != "timestamp"}
        if previous.project_id != scope or prior != current:
            raise EventAdmissionError("conflict")
        return
    existing = await db.scalar(select(func.count()).select_from(PipelineEventOutbox))
    if existing is None or existing >= PIPELINE_EVENT_CAPACITY:
        raise EventAdmissionError("capacity")
    db.add(PipelineEventOutbox(
        id=event_id, project_id=scope, pipeline_id=pipeline_id, test_run_id=test_run_id, test_case_id=case_id,
        pipeline_run_id=event["pipeline_run_id"], payload=event, next_attempt_at=datetime.fromisoformat(event["timestamp"]),
    ))
    await db.flush()


async def purge_pipeline_event_intents(
    db: AsyncSession, project_id: UUID, *, pipeline_run_ids: set[str], test_run_ids: set[UUID],
) -> None:
    """Fence replay before the existing Mongo deletes, within the purge transaction."""
    if not pipeline_run_ids and not test_run_ids:
        return
    await lock_project_event_history(db, project_id, exclusive=True)
    statement = delete(PipelineEventOutbox).where(PipelineEventOutbox.project_id == project_id)
    ordered = sorted(pipeline_run_ids)
    for offset in range(0, len(ordered), 500):
        await db.execute(statement.where(PipelineEventOutbox.pipeline_run_id.in_(ordered[offset:offset + 500])))
    runs = sorted(test_run_ids)
    for offset in range(0, len(runs), 500):
        await db.execute(statement.where(PipelineEventOutbox.test_run_id.in_(runs[offset:offset + 500])))
