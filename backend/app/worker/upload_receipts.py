"""Transaction and worker boundaries for durable upload receipts.

A worker reserves one connection from its existing bounded engine while an
upload runs. AUTOCOMMIT keeps PostgreSQL out of an idle transaction. Session
advisory locks exclude duplicate consumers and disappear on process/connection
loss. The ingestion pipeline resumes the same run after a committed partial
attempt. This is logical-result idempotence across stores, not atomic delivery.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.models.postgres import Project, UploadDispatchReceipt
from app.services import upload_receipts as receipts
from app.worker.celery_app import TimeLimitedTask

logger = structlog.get_logger(__name__)
_ACTIVE_RECEIPT: ContextVar[str | None] = ContextVar("active_upload_receipt", default=None)
_ACTIVE_CONNECTION: ContextVar[AsyncConnection | None] = ContextVar("active_upload_connection", default=None)
RECEIPT_RETENTION_DAYS = 30
CLEANUP_BATCH_SIZE = 20
OBJECT_OPERATION_TIMEOUT_SECONDS = 10.0


def active_receipt_id() -> str | None:
    return _ACTIVE_RECEIPT.get()


async def reserve_upload(**kwargs: Any) -> UploadDispatchReceipt:
    from app.db.postgres import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        row = await receipts.reserve_receipt(db, **kwargs)
        await db.commit()
        return row


async def mark_payload_ready(task_id: str) -> None:
    from app.db.postgres import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(UploadDispatchReceipt).where(
            UploadDispatchReceipt.task_id == uuid.UUID(task_id)
        ).with_for_update())).scalar_one()
        if row.state not in receipts.TERMINAL_STATES and row.payload_ready_at is None:
            row.payload_ready_at = datetime.now(timezone.utc)
            row.updated_at = row.payload_ready_at
        await db.commit()


async def persist_active_status(task_id: str, **fields: Any) -> None:
    """Strict durable write before an advisory Redis status or payload deletion."""
    if active_receipt_id() != task_id:
        raise RuntimeError("Upload status does not match the claimed receipt")
    connection = _ACTIVE_CONNECTION.get()
    if connection is None or connection.closed or connection.invalidated:
        raise RuntimeError("Upload consumer lock connection was lost")
    await connection.execute(text("SELECT 1"))
    await connection.rollback()
    from app.db.postgres import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await receipts.advance_status(db, task_id, **fields)
        await db.commit()


async def acquire_upload_lock(task_id: str) -> AsyncConnection | None:
    from app.db.postgres import get_engine

    connection = await get_engine().connect()
    try:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        acquired = (await connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {
            "key": receipts.advisory_key("upload-consumer", task_id)
        })).scalar_one()
        await connection.rollback()  # SQLAlchemy's virtual transaction only.
        if acquired:
            return connection
    except BaseException:
        await connection.invalidate()
        await connection.close()
        raise
    await connection.close()
    return None


async def release_upload_lock(connection: AsyncConnection, task_id: str) -> None:
    try:
        unlocked = (await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {
            "key": receipts.advisory_key("upload-consumer", task_id)
        })).scalar_one()
        await connection.rollback()
        if not unlocked:
            await connection.invalidate()
    except BaseException:
        # A failed unlock must never return a still-locked session to the pool.
        await connection.invalidate()
        raise
    finally:
        await connection.close()


async def read_upload(task_id: str) -> UploadDispatchReceipt | None:
    from app.db.postgres import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        return await receipts.get_receipt(db, task_id)


async def upload_project_active(row: UploadDispatchReceipt) -> bool:
    from app.db.postgres import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        return (await db.execute(select(Project.is_active).where(Project.id == row.project_id))).scalar_one_or_none() is True


async def abandon_upload(task_id: str, *, confirmation_task_id: str) -> bool:
    """Privileged operator recovery for stranded work. Never interrupts a consumer.

    Invoke from an authorized maintenance shell, with the exact receipt UUID
    repeated as confirmation. Already committed run data remains intact.
    """
    if str(uuid.UUID(task_id)) != confirmation_task_id:
        raise ValueError("Confirmation must match the upload task UUID")
    connection = await acquire_upload_lock(task_id)
    if connection is None:
        raise RuntimeError("The upload is active; abandonment was refused")
    from app.db.postgres import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            row = await receipts.get_receipt(db, task_id)
            if row is None or row.state in receipts.TERMINAL_STATES:
                return False
            changed = await receipts.advance_status(db, task_id, state="failed", progress=None, result=None,
                error={"code": "operator_abandoned", "message": "An operator stopped this upload. Already committed run data is retained."})
            await db.commit()
        if changed:
            logger.warning("upload_operator_abandoned", task_id=task_id)
        return changed
    finally:
        await release_upload_lock(connection, task_id)


async def ensure_payload_ready(row: UploadDispatchReceipt) -> None:
    """Recover object-write/mark-ready interruption using the durable digest."""
    if row.state in receipts.TERMINAL_STATES:
        return
    from app.db.storage import get_storage_provider

    content = await asyncio.wait_for(
        get_storage_provider().get_object_content(row.storage_key), OBJECT_OPERATION_TIMEOUT_SECONDS
    )
    if hashlib.sha256(content).hexdigest() != row.payload_sha256:
        raise ValueError("Stored upload content does not match its receipt")
    await mark_payload_ready(str(row.task_id))


async def cleanup_terminal_upload(task_id: str) -> bool:
    from app.db.postgres import AsyncSessionLocal
    from app.db.storage import get_storage_provider

    # Terminal state is immutable. Release the read transaction before the
    # storage proxy resolves configuration, which can itself need a DB slot.
    # A worker already reserves one connection for its consumer lock.
    terminal = await read_upload(task_id)
    if terminal is None or terminal.state not in receipts.TERMINAL_STATES:
        return False
    await asyncio.wait_for(get_storage_provider().delete_object(terminal.storage_key), OBJECT_OPERATION_TIMEOUT_SECONDS)
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(UploadDispatchReceipt).where(
            UploadDispatchReceipt.task_id == uuid.UUID(task_id)
        ).with_for_update())).scalar_one_or_none()
        if row is None or row.state not in receipts.TERMINAL_STATES:
            return False
        row.payload_deleted_at = datetime.now(timezone.utc)
        row.task_kwargs = {}  # Metadata needed only while work is recoverable.
        row.updated_at = row.payload_deleted_at
        await db.commit()
        return True


async def sweep_terminal_uploads(*, limit: int = CLEANUP_BATCH_SIZE) -> dict[str, int]:
    """Finite operator maintenance. Unresolved receipts and payloads are retained."""
    from app.db.postgres import AsyncSessionLocal

    limit = max(1, min(limit, 100))
    async with AsyncSessionLocal() as db:
        ids = (await db.execute(select(UploadDispatchReceipt.task_id).where(
            UploadDispatchReceipt.completed_at.is_not(None),
        ).order_by(UploadDispatchReceipt.payload_deleted_at.asc().nulls_first(), UploadDispatchReceipt.completed_at).limit(limit))).scalars().all()
    cleaned = failed = 0
    for task_id in ids:
        try:
            cleaned += int(await cleanup_terminal_upload(str(task_id)))
        except Exception as exc:  # noqa: BLE001 — a failed delete remains discoverable for the next sweep
            failed += 1
            logger.warning("upload_terminal_cleanup_failed", task_id=str(task_id), error_type=type(exc).__name__)
    async with AsyncSessionLocal() as db:
        expired = (await db.execute(select(UploadDispatchReceipt.task_id).where(
            UploadDispatchReceipt.completed_at < datetime.now(timezone.utc) - timedelta(days=RECEIPT_RETENTION_DAYS),
            UploadDispatchReceipt.payload_deleted_at >= datetime.now(timezone.utc) - timedelta(minutes=1),
        ).order_by(UploadDispatchReceipt.completed_at).limit(limit))).scalars().all()
        if expired:
            await db.execute(delete(UploadDispatchReceipt).where(UploadDispatchReceipt.task_id.in_(expired)))
        await db.commit()
    return {"cleaned": cleaned, "failed": failed, "expired": len(expired)}


class UploadTrackedTask(TimeLimitedTask):
    """Fence new upload deliveries while preserving legacy task signatures."""

    abstract = True

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        receipt_id = kwargs.get("upload_receipt_id")
        if receipt_id is None:
            return super().__call__(*args, **kwargs)
        from app.worker.loop_runner import run_async

        task_id = str(uuid.UUID(str(receipt_id)))
        if self.request.id != task_id or args:
            raise ValueError("Upload delivery does not match its receipt identity")
        connection = run_async(acquire_upload_lock(task_id))
        if connection is None:
            return {"duplicate": True, "task_id": task_id}
        token = _ACTIVE_RECEIPT.set(task_id)
        connection_token = _ACTIVE_CONNECTION.set(connection)
        try:
            row = run_async(read_upload(task_id))
            if row is None:
                # Old broker deliveries after receipt retention cannot recreate work.
                return {"expired": True, "task_id": task_id}
            if row.state in receipts.TERMINAL_STATES:
                return {"duplicate": True, "task_id": task_id}
            expected = {**row.task_kwargs, "upload_receipt_id": task_id}
            if self.name != receipts.TASK_NAMES[row.kind] or kwargs != expected:
                raise ValueError("Upload delivery metadata does not match its durable receipt")
            if not run_async(upload_project_active(row)):
                run_async(persist_active_status(task_id, state="failed", progress=None, result=None,
                    error={"code": "project_unavailable", "message": "The upload project is no longer active."}))
                return {"refused": True, "task_id": task_id}
            run_async(ensure_payload_ready(row))
            return super().__call__(*args, **kwargs)
        finally:
            try:
                # Deletion can fail independently of terminal business success.
                try:
                    run_async(cleanup_terminal_upload(task_id))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("upload_terminal_cleanup_deferred", task_id=task_id, error_type=type(exc).__name__)
            finally:
                _ACTIVE_RECEIPT.reset(token)
                _ACTIVE_CONNECTION.reset(connection_token)
                run_async(release_upload_lock(connection, task_id))
