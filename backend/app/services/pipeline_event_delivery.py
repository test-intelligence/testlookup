"""Bounded, append-only materialization of shared pipeline-event intents."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import time
from typing import Any, cast
import uuid

import pymongo
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.read_preferences import ReadPreference
from pymongo.write_concern import WriteConcern
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import PipelineEventOutbox

STATUSES = ("pending", "sending", "delivered", "failed")
MAX_ATTEMPTS = 8
LEASE_SECONDS = 120
MONGO_TIMEOUT_SECONDS = 2.0
DELIVERED_RETENTION_SECONDS = 3600
PRUNE_BATCH_SIZE = 100


class EventIdentityConflict(ValueError):
    """An existing immutable Mongo document has different content."""


class EventPayloadInvalid(ValueError):
    """The stored intent is not a valid scoped event envelope."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _document(row: PipelineEventOutbox) -> dict[str, Any]:
    document = copy.deepcopy(row.payload)
    if not isinstance(document, dict) or document.get("pipeline_run_id") != row.pipeline_run_id:
        raise EventPayloadInvalid()
    if document.get("event_id", str(row.id)) != str(row.id) or document.get("project_id", str(row.project_id)) != str(row.project_id):
        raise EventPayloadInvalid()
    try:
        timestamp = datetime.fromisoformat(document["timestamp"])
    except (KeyError, TypeError, ValueError) as error:
        raise EventPayloadInvalid() from error
    if timestamp.tzinfo is None or not isinstance(document.get("event_type"), str):
        raise EventPayloadInvalid()
    timestamp = _utc(timestamp)
    # BSON dates have millisecond precision; normalize before insert and
    # duplicate comparison instead of misclassifying a legitimate retry.
    document["timestamp"] = timestamp.replace(microsecond=timestamp.microsecond // 1000 * 1000)
    document.update(_id=str(row.id), event_id=str(row.id), project_id=str(row.project_id))
    return document


def _same_document(existing: dict[str, Any] | None, expected: dict[str, Any]) -> bool:
    if not isinstance(existing, dict):
        return False
    document = copy.deepcopy(existing)
    if isinstance(document.get("timestamp"), datetime):
        document["timestamp"] = _utc(document["timestamp"])
    # An explicitly reused event ID can be staged after its SQL receipt was
    # pruned. Preserve Mongo's original timestamp; a new intake timestamp is
    # transport metadata, never permission to modify logical event content.
    document.pop("timestamp", None)
    canonical = copy.deepcopy(expected)
    canonical.pop("timestamp", None)
    return document == canonical


async def _materialize(row: PipelineEventOutbox) -> None:
    from app.db.mongo import get_mongo_db

    document = _document(row)
    source = get_mongo_db()["pipeline_event_log"]
    concern = dict(source.write_concern.document)
    # Never downgrade a site's majority/custom acknowledgement requirement.
    # An unacknowledged collection cannot settle durable SQL evidence.
    if concern.get("w") in (None, 0):
        concern["w"] = 1
    concern["j"] = True
    configured_timeout = concern.get("wtimeout")
    limit_ms = int(MONGO_TIMEOUT_SECONDS * 1000)
    concern["wtimeout"] = min(configured_timeout, limit_ms) if isinstance(configured_timeout, int) and configured_timeout > 0 else limit_ms
    collection = source.with_options(
        # Motor's annotation names the ReadPreference container, whereas
        # PyMongo supplies its supported Primary mode instance.
        write_concern=WriteConcern(**concern), read_preference=cast(Any, ReadPreference.PRIMARY),
    )
    # Driver-level CSOT bounds selection, checkout and network operations for
    # Motor as well. No detached Mongo task may outlive the SQL purge fence.
    with pymongo.timeout(MONGO_TIMEOUT_SECONDS):
        try:
            await collection.insert_one(document)
        except DuplicateKeyError:
            existing = await collection.find_one({"_id": str(row.id)})
            if not _same_document(existing, document):
                raise EventIdentityConflict() from None


async def _claim_one() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID] | None:
    from app.db.postgres import AsyncSessionLocal

    now = _utcnow()
    eligible = or_(
        (PipelineEventOutbox.status == "pending") & (
            PipelineEventOutbox.next_attempt_at.is_(None) | (PipelineEventOutbox.next_attempt_at <= now)
        ),
        (PipelineEventOutbox.status == "sending") & (
            PipelineEventOutbox.lease_expires_at.is_(None) | (PipelineEventOutbox.lease_expires_at <= now)
        ),
    )
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(PipelineEventOutbox).where(eligible)
            .order_by(PipelineEventOutbox.created_at, PipelineEventOutbox.id)
            .with_for_update(skip_locked=True).limit(1)
        )).scalar_one_or_none()
        if row is None:
            return None
        if row.attempts >= MAX_ATTEMPTS:
            row.status = "failed"
            row.last_error_code = row.last_error_code or "attempts_exhausted"
            row.lease_token = None
            row.lease_expires_at = None
            row.next_attempt_at = None
            await db.commit()
            return row.id, row.project_id, uuid.UUID(int=0)
        token = uuid.uuid4()
        row.status = "sending"
        row.attempts += 1
        row.lease_token = token
        row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        await db.commit()
        return row.id, row.project_id, token


def _error_code(error: Exception) -> str:
    if isinstance(error, EventIdentityConflict):
        return "identity_conflict"
    if isinstance(error, EventPayloadInvalid):
        return "payload_invalid"
    if isinstance(error, PyMongoError):
        return "mongo_timeout" if error.timeout else "mongo_unavailable"
    if isinstance(error, TimeoutError):
        return "mongo_timeout"
    return "delivery_error"


async def _deliver_owned(event_id: uuid.UUID, project_id: uuid.UUID, token: uuid.UUID) -> str:
    from app.db.postgres import AsyncSessionLocal
    from app.services.pipeline_event_admission import lock_project_event_history

    if token.int == 0:
        return "failed"
    async with AsyncSessionLocal() as db:
        # Match purge's project -> row lock order. Holding the row lock alone
        # while waiting for a project lock would introduce a deadlock.
        if not await lock_project_event_history(db, project_id):
            return "deferred"
        row = (await db.execute(
            select(PipelineEventOutbox).where(
                PipelineEventOutbox.id == event_id,
                PipelineEventOutbox.project_id == project_id,
                PipelineEventOutbox.status == "sending",
                PipelineEventOutbox.lease_token == token,
            ).with_for_update()
        )).scalar_one_or_none()
        if row is None:
            return "stale"
        try:
            await _materialize(row)
        except Exception as error:
            code = _error_code(error)
            row.last_error_code = code
            terminal = code in {"identity_conflict", "payload_invalid"} or row.attempts >= MAX_ATTEMPTS
            row.status = "failed" if terminal else "pending"
            row.next_attempt_at = None if terminal else _utcnow() + timedelta(seconds=min(300, 2 ** row.attempts))
            outcome = "failed" if terminal else "retry"
        else:
            row.status = "delivered"
            row.last_error_code = None
            row.delivered_at = _utcnow()
            row.next_attempt_at = None
            outcome = "delivered"
        row.lease_token = None
        row.lease_expires_at = None
        await db.commit()
        return outcome


async def prune_delivered_events(*, limit: int = PRUNE_BATCH_SIZE) -> int:
    """Release receipt capacity only after durable Mongo acknowledgement."""
    from app.db.postgres import AsyncSessionLocal
    from app.services.pipeline_event_admission import PIPELINE_EVENT_CAPACITY

    if not 1 <= limit <= PRUNE_BATCH_SIZE:
        raise ValueError("invalid pipeline event prune limit")
    async with AsyncSessionLocal() as db:
        total = await db.scalar(select(func.count()).select_from(PipelineEventOutbox))
        pressure = int(total or 0) * 10 >= PIPELINE_EVENT_CAPACITY * 9
        eligible = (PipelineEventOutbox.status == "delivered") & PipelineEventOutbox.delivered_at.is_not(None)
        if not pressure:
            eligible = eligible & (PipelineEventOutbox.delivered_at < _utcnow() - timedelta(seconds=DELIVERED_RETENTION_SECONDS))
        ids = list((await db.scalars(
            select(PipelineEventOutbox.id).where(eligible)
            .order_by(PipelineEventOutbox.delivered_at).with_for_update(skip_locked=True).limit(limit)
        )).all())
        if ids:
            await db.execute(delete(PipelineEventOutbox).where(PipelineEventOutbox.id.in_(ids)))
            await db.commit()
        return len(ids)


async def relay_pipeline_events(*, batch_size: int = 50, max_seconds: float = 20.0) -> dict[str, int]:
    """Materialize at most one bounded batch; cancellation preserves its lease."""
    if not 1 <= batch_size <= 100 or not 0 < max_seconds <= 30:
        raise ValueError("invalid pipeline event replay bound")
    results = dict.fromkeys(("delivered", "retry", "failed", "deferred", "stale"), 0)
    deadline = time.monotonic() + max_seconds
    for _ in range(batch_size):
        if time.monotonic() >= deadline:
            break
        claim = await _claim_one()
        if claim is None:
            break
        outcome = await _deliver_owned(*claim)
        results[outcome] += 1
    results["pruned"] = await prune_delivered_events()
    return results


async def requeue_failed_event(
    db: AsyncSession, event_id: uuid.UUID, *, project_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Stage one explicit retry, with authorization/audit/commit owned by caller."""
    from app.services.pipeline_event_admission import lock_project_event_history

    if not await lock_project_event_history(db, project_id):
        raise RuntimeError("pipeline_event_history_busy")
    row = (await db.execute(select(PipelineEventOutbox).where(
        PipelineEventOutbox.id == event_id, PipelineEventOutbox.project_id == project_id,
    ).with_for_update())).scalar_one_or_none()
    if row is None:
        return None
    if row.status != "failed":
        raise ValueError("pipeline_event_not_failed")
    previous_attempts = row.attempts
    row.status = "pending"
    row.attempts = 0
    row.next_attempt_at = _utcnow()
    row.lease_token = None
    row.lease_expires_at = None
    # Keep the compact previous error until a successful materialization.
    await db.flush()
    return {"event_id": str(row.id), "project_id": str(row.project_id), "status": "pending", "previous_attempts": previous_attempts}
