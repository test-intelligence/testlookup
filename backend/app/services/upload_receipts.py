"""Upload receipt transitions. Callers own transactions and publication."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Project, UploadDispatchReceipt

TERMINAL_STATES = frozenset(("succeeded", "failed"))
STATE_RANK = {"pending": 0, "parsing": 1, "ingesting": 2, "succeeded": 3, "failed": 3}
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
TASK_NAMES = {"batch": "app.worker.tasks.ingest_uploaded_results", "file": "app.worker.tasks.ingest_uploaded_file"}
MAX_OUTSTANDING_PER_PROJECT = 100


class UploadIdentityConflict(ValueError):
    """The scoped key already identifies different accepted content."""


class UploadAdmissionFull(RuntimeError):
    """The project's finite unresolved receipt allowance is occupied."""


class UploadProjectUnavailable(ValueError):
    """Missing/inactive projects cannot accept new durable work."""


def advisory_key(namespace: str, identity: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{namespace}:{identity}".encode()).digest()[:8], "big", signed=True)


def idempotency_digest(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(IDEMPOTENCY_KEY_PATTERN, value) is None:
        raise ValueError("Idempotency-Key must contain 1–128 ASCII letters, digits, _, ., :, or - and start with a letter or digit")
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def request_digest(content: bytes, metadata: dict[str, Any]) -> str:
    """Bind content and accepted semantic metadata, excluding minted IDs."""
    encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    return hashlib.sha256(hashlib.sha256(content).digest() + encoded).hexdigest()


async def get_receipt(db: AsyncSession, task_id: str | uuid.UUID) -> UploadDispatchReceipt | None:
    try:
        identity = uuid.UUID(str(task_id))
    except ValueError:
        return None
    return (await db.execute(select(UploadDispatchReceipt).where(UploadDispatchReceipt.task_id == identity))).scalar_one_or_none()


async def reserve_receipt(db: AsyncSession, *, task_id: str, run_id: str, project_id: str,
                          actor_id: str, kind: str, key_digest: str | None,
                          content_digest: str, payload_sha256: str, storage_key: str,
                          task_kwargs: dict[str, Any]) -> UploadDispatchReceipt:
    actor_digest = hashlib.sha256(actor_id.encode()).hexdigest()
    await db.execute(text("SELECT set_config('statement_timeout', '1000', true)"))
    project = (await db.execute(select(Project).where(Project.id == uuid.UUID(project_id)).with_for_update(read=True))).scalar_one_or_none()
    if project is None or not project.is_active:
        raise UploadProjectUnavailable("Upload project is unavailable")
    # Serialize only this project's short reservation transaction. The bounded
    # query cannot scan an unbounded backlog and a reused key consumes no slot.
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": advisory_key("upload-admission", project_id)})
    if key_digest is not None:
        prior = (await db.execute(select(UploadDispatchReceipt).where(
            UploadDispatchReceipt.project_id == uuid.UUID(project_id),
            UploadDispatchReceipt.actor_digest == actor_digest,
            UploadDispatchReceipt.kind == kind,
            UploadDispatchReceipt.idempotency_digest == key_digest,
        ))).scalar_one_or_none()
        if prior is not None:
            if prior.request_digest != content_digest:
                raise UploadIdentityConflict("Idempotency-Key already identifies different upload content or metadata")
            return prior
    occupied = (await db.execute(select(UploadDispatchReceipt.task_id).where(
        UploadDispatchReceipt.project_id == uuid.UUID(project_id),
        UploadDispatchReceipt.payload_deleted_at.is_(None),
    ).limit(MAX_OUTSTANDING_PER_PROJECT))).all()
    if len(occupied) >= MAX_OUTSTANDING_PER_PROJECT:
        raise UploadAdmissionFull("Resolve existing uploads before admitting another upload")
    statement = insert(UploadDispatchReceipt).values(
        task_id=uuid.UUID(task_id), run_id=uuid.UUID(run_id), project_id=uuid.UUID(project_id),
        actor_digest=actor_digest, kind=kind, idempotency_digest=key_digest,
        request_digest=content_digest, payload_sha256=payload_sha256,
        storage_key=storage_key, task_kwargs=task_kwargs, state="pending", status_payload={},
    ).on_conflict_do_nothing(index_elements=["project_id", "actor_digest", "kind", "idempotency_digest"])
    await db.execute(statement)
    if key_digest is None:
        row = await get_receipt(db, task_id)
    else:
        row = (await db.execute(select(UploadDispatchReceipt).where(
            UploadDispatchReceipt.project_id == uuid.UUID(project_id),
            UploadDispatchReceipt.actor_digest == actor_digest,
            UploadDispatchReceipt.kind == kind,
            UploadDispatchReceipt.idempotency_digest == key_digest,
        ))).scalar_one()
    if row is None:
        raise RuntimeError("Upload receipt was not reserved")
    if row.request_digest != content_digest:
        raise UploadIdentityConflict("Idempotency-Key already identifies different upload content or metadata")
    return row


def status_record(row: UploadDispatchReceipt) -> dict[str, Any]:
    return {
        "task_id": str(row.task_id), "run_id": str(row.run_id), "project_id": str(row.project_id),
        "state": row.state, "progress": row.status_payload.get("progress"),
        "result": row.status_payload.get("result"), "error": row.status_payload.get("error"),
    }


async def advance_status(db: AsyncSession, task_id: str, *, state: str,
                         progress: dict | None, result: dict | None, error: dict | None) -> bool:
    row = (await db.execute(select(UploadDispatchReceipt).where(
        UploadDispatchReceipt.task_id == uuid.UUID(task_id)
    ).with_for_update())).scalar_one()
    if state not in STATE_RANK:
        raise ValueError("Unknown upload state")
    if row.state in TERMINAL_STATES or STATE_RANK[state] < STATE_RANK[row.state]:
        return False
    row.state = state
    row.status_payload = {"progress": progress, "result": result, "error": error}
    row.updated_at = datetime.now(timezone.utc)
    if state in TERMINAL_STATES:
        row.completed_at = row.updated_at
    return True
