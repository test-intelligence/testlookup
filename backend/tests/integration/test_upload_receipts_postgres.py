"""Receipt lifecycle proof using isolated tables in an explicit disposable PG.

Public migrated tables provide DDL only. Public rows, brokers and external
integrations remain untouched. Each proof cleans its unique private schema.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.postgres import Project, UploadDispatchReceipt
from app.services import upload_receipts as service
from app.worker import upload_receipts as boundary

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def world(monkeypatch):
    dsn = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    schema = "upload_receipt_proof_" + uuid.uuid4().hex
    control = create_async_engine(dsn)
    engine = None
    try:
        async with control.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            for name in ("projects", "upload_dispatch_receipts"):
                await connection.execute(text(f'CREATE TABLE "{schema}"."{name}" (LIKE public."{name}" INCLUDING ALL)'))
        engine = create_async_engine(dsn, pool_size=2, max_overflow=0, pool_timeout=1,
            connect_args={"server_settings": {"search_path": schema + ",public"}})
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions)
        monkeypatch.setattr("app.db.postgres.get_engine", lambda: engine)
        project_id = uuid.uuid4()
        async with sessions() as db:
            db.add(Project(id=project_id, name="Synthetic upload receipt proof", slug="receipt-proof", is_active=True))
            await db.commit()
        yield sessions, engine, project_id
    finally:
        if engine is not None:
            await engine.dispose()
        async with control.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await control.dispose()


def fields(project_id, *, key="logical-upload", digest="a" * 64):
    task_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
    return dict(task_id=task_id, run_id=run_id, project_id=str(project_id), actor_id="synthetic-actor",
        kind="batch", key_digest=service.idempotency_digest(key) if key is not None else None,
        content_digest=digest, payload_sha256=hashlib.sha256(b"synthetic payload").hexdigest(),
        storage_key=f"uploads/{project_id}/{run_id}/queued/synthetic.json",
        task_kwargs={"run_id": run_id, "payload_storage_key": f"uploads/{project_id}/{run_id}/queued/synthetic.json"})


async def test_independent_reservations_same_key_conflict_and_serialized_last_slot(world, monkeypatch):
    sessions, _engine, project_id = world
    first = await boundary.reserve_upload(**fields(project_id))
    second = await boundary.reserve_upload(**fields(project_id))
    assert first.task_id == second.task_id and first.storage_key == second.storage_key
    with pytest.raises(service.UploadIdentityConflict):
        await boundary.reserve_upload(**fields(project_id, digest="b" * 64))
    monkeypatch.setattr(service, "MAX_OUTSTANDING_PER_PROJECT", 2)
    results = await asyncio.gather(
        boundary.reserve_upload(**fields(project_id, key="second")),
        boundary.reserve_upload(**fields(project_id, key="third")), return_exceptions=True)
    assert sum(isinstance(result, UploadDispatchReceipt) for result in results) == 1
    assert sum(isinstance(result, service.UploadAdmissionFull) for result in results) == 1
    # Reused identities bypass the allowance, across a fresh session/reader.
    assert (await boundary.reserve_upload(**fields(project_id))).task_id == first.task_id
    async with sessions() as db:
        assert len((await db.execute(select(UploadDispatchReceipt))).scalars().all()) == 2


async def test_duplicate_lock_has_no_open_transaction_and_connection_loss_releases_it(world):
    _sessions, engine, project_id = world
    row = await boundary.reserve_upload(**fields(project_id))
    task_id = str(row.task_id)
    lock = await boundary.acquire_upload_lock(task_id)
    assert lock is not None
    assert await boundary.acquire_upload_lock(task_id) is None
    lock_pid = (await lock.execute(text("SELECT pg_backend_pid()"))).scalar_one()
    await lock.rollback()
    async with engine.connect() as observer:
        state = (await observer.execute(text("SELECT state FROM pg_stat_activity WHERE pid=:pid"), {"pid": lock_pid})).scalar_one()
        assert state == "idle"
    # This path represents process/connection loss. The session lock must not
    # remain stranded in the bounded connection pool.
    await lock.invalidate()
    await lock.close()
    replacement = await boundary.acquire_upload_lock(task_id)
    assert replacement is not None
    await boundary.release_upload_lock(replacement, task_id)
    assert engine.pool.checkedout() == 0


async def test_terminal_write_failure_retains_payload_then_fresh_reader_and_cleanup_succeed(world, monkeypatch):
    sessions, _engine, project_id = world
    row = await boundary.reserve_upload(**fields(project_id))
    task_id = str(row.task_id)
    storage = type("Storage", (), {})()
    storage.get_object_content = AsyncMock(return_value=b"synthetic payload")
    storage.delete_object = AsyncMock()
    monkeypatch.setattr("app.db.storage.get_storage_provider", lambda: storage)
    await boundary.ensure_payload_ready(row)
    lock = await boundary.acquire_upload_lock(task_id)
    token = boundary._ACTIVE_RECEIPT.set(task_id)
    connection_token = boundary._ACTIVE_CONNECTION.set(lock)
    original = service.advance_status
    try:
        monkeypatch.setattr(service, "advance_status", AsyncMock(side_effect=ConnectionError("synthetic terminal commit failure")))
        with pytest.raises(ConnectionError):
            await boundary.persist_active_status(task_id, state="succeeded", progress=None, result={"total": 1}, error=None)
        assert await boundary.cleanup_terminal_upload(task_id) is False
        storage.delete_object.assert_not_awaited()
        monkeypatch.setattr(service, "advance_status", original)
        await boundary.persist_active_status(task_id, state="ingesting", progress={"total": 1}, result=None, error=None)
        # A retried early phase cannot erase the more advanced durable state.
        await boundary.persist_active_status(task_id, state="parsing", progress=None, result=None, error=None)
        await boundary.persist_active_status(task_id, state="succeeded", progress=None, result={"total": 1}, error=None)
        await boundary.persist_active_status(task_id, state="failed", progress=None, result=None, error={"code": "late_duplicate"})
    finally:
        boundary._ACTIVE_RECEIPT.reset(token)
        boundary._ACTIVE_CONNECTION.reset(connection_token)
        await boundary.release_upload_lock(lock, task_id)
    async with sessions() as reader:
        durable = await service.get_receipt(reader, task_id)
        assert durable.state == "succeeded" and durable.status_payload["result"] == {"total": 1}
        assert durable.completed_at is not None and durable.payload_deleted_at is None
    assert await boundary.cleanup_terminal_upload(task_id) is True
    storage.delete_object.assert_awaited_once_with(row.storage_key)
    assert (await boundary.read_upload(task_id)).task_kwargs == {}
    async with sessions() as db:
        durable = await service.get_receipt(db, task_id)
        durable.completed_at = datetime.now(timezone.utc) - timedelta(days=31)
        await db.commit()
    assert (await boundary.sweep_terminal_uploads(limit=1))["expired"] == 1
    assert await boundary.read_upload(task_id) is None


async def test_reserved_object_reconciliation_abandonment_and_deleted_project_custody(world, monkeypatch):
    sessions, _engine, project_id = world
    row = await boundary.reserve_upload(**fields(project_id))
    task_id = str(row.task_id)
    storage = type("Storage", (), {})()
    storage.get_object_content, storage.delete_object = AsyncMock(return_value=b"synthetic payload"), AsyncMock()
    monkeypatch.setattr("app.db.storage.get_storage_provider", lambda: storage)
    assert row.payload_ready_at is None
    await boundary.ensure_payload_ready(row)
    assert (await boundary.read_upload(task_id)).payload_ready_at is not None
    lock = await boundary.acquire_upload_lock(task_id)
    with pytest.raises(RuntimeError, match="active"):
        await boundary.abandon_upload(task_id, confirmation_task_id=task_id)
    await boundary.release_upload_lock(lock, task_id)
    with pytest.raises(ValueError, match="Confirmation"):
        await boundary.abandon_upload(task_id, confirmation_task_id="different")
    async with sessions() as db:
        await db.execute(delete(Project).where(Project.id == project_id))
        await db.commit()
    assert await boundary.upload_project_active(row) is False
    assert await boundary.read_upload(task_id) is not None
    with pytest.raises(service.UploadProjectUnavailable):
        await boundary.reserve_upload(**fields(project_id, key="new-upload"))
    assert await boundary.abandon_upload(task_id, confirmation_task_id=task_id) is True
    terminal = await boundary.read_upload(task_id)
    assert terminal.state == "failed" and terminal.status_payload["error"]["code"] == "operator_abandoned"
    assert (await boundary.sweep_terminal_uploads(limit=1))["cleaned"] == 1
    storage.delete_object.assert_awaited_once_with(row.storage_key)


async def test_terminal_cleanup_leaves_room_for_cold_storage_configuration(world, monkeypatch):
    sessions, engine, project_id = world
    row = await boundary.reserve_upload(**fields(project_id))
    task_id = str(row.task_id)
    async with sessions() as db:
        await service.advance_status(db, task_id, state="succeeded", progress=None, result={"total": 1}, error=None)
        await db.commit()
    lock = await boundary.acquire_upload_lock(task_id)
    configured = []

    class Storage:
        async def delete_object(self, key):
            # The real dynamic storage proxy can do this on a cold cache.
            async with sessions() as config_reader:
                await config_reader.execute(text("SELECT 1"))
                configured.append(key)

    monkeypatch.setattr("app.db.storage.get_storage_provider", Storage)
    try:
        assert await asyncio.wait_for(boundary.cleanup_terminal_upload(task_id), timeout=2)
    finally:
        await boundary.release_upload_lock(lock, task_id)
    assert configured == [row.storage_key]
    assert engine.pool.checkedout() == 0
