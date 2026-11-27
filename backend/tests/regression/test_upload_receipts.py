"""Receipt-backed HTTP recovery and real Celery boundary controls, without services."""
from __future__ import annotations

import asyncio
import hashlib
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from celery import Celery
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_api_key_context, get_db
from app.models.postgres import UploadDispatchReceipt
from app.routers import ingest
from app.services import upload_receipts as service, upload_status
from app.worker import tasks, upload_dispatch, upload_receipts as boundary


class Storage:
    def __init__(self):
        self.objects = {}
        self.deleted = []
        self.before_put = None
        self.fail_put = False

    async def put_object(self, key, content, **_options):
        if self.before_put:
            self.before_put(key)
        if self.fail_put:
            raise ConnectionError("synthetic storage refusal")
        self.objects[key] = content

    async def get_object_content(self, key):
        return self.objects[key]

    async def delete_object(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)


@pytest.fixture
def world(monkeypatch):
    app = FastAPI()
    app.include_router(ingest.router)
    project_id, actor_id = uuid.uuid4(), uuid.uuid4()
    user = SimpleNamespace(id=actor_id, username="receipt-proof", full_name="Receipt proof", role="QA_ENGINEER", is_active=True)
    auth_value = [user, None]

    async def db():
        yield AsyncMock()

    async def auth():
        return tuple(auth_value)

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_api_key_context] = auth
    rows, keys, locks = {}, {}, set()
    storage = Storage()

    async def reserve(**fields):
        key = (fields["project_id"], fields["actor_id"], fields["kind"], fields["key_digest"])
        if fields["key_digest"] and key in keys:
            row = rows[keys[key]]
            if row.request_digest != fields["content_digest"]:
                raise service.UploadIdentityConflict("Idempotency-Key identifies different content")
            return row
        row = UploadDispatchReceipt(
            task_id=uuid.UUID(fields["task_id"]), run_id=uuid.UUID(fields["run_id"]),
            project_id=uuid.UUID(fields["project_id"]), actor_digest=hashlib.sha256(fields["actor_id"].encode()).hexdigest(),
            kind=fields["kind"], idempotency_digest=fields["key_digest"], request_digest=fields["content_digest"],
            payload_sha256=fields["payload_sha256"], storage_key=fields["storage_key"], task_kwargs=fields["task_kwargs"],
            state="pending", status_payload={}, created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        )
        rows[str(row.task_id)] = row
        if fields["key_digest"]:
            keys[key] = str(row.task_id)
        return row

    async def read(task_id):
        return rows.get(str(task_id))

    async def lock(task_id):
        if task_id in locks:
            return None
        locks.add(task_id)
        return task_id

    async def unlock(_connection, task_id):
        locks.remove(task_id)

    async def ready(task_id):
        rows[task_id].payload_ready_at = datetime.now(timezone.utc)

    async def cleanup(task_id):
        row = rows[task_id]
        if row.state not in service.TERMINAL_STATES:
            return False
        await storage.delete_object(row.storage_key)
        row.payload_deleted_at = datetime.now(timezone.utc)
        row.task_kwargs = {}
        return True

    monkeypatch.setattr(boundary, "reserve_upload", reserve)
    monkeypatch.setattr(boundary, "read_upload", read)
    monkeypatch.setattr(boundary, "acquire_upload_lock", lock)
    monkeypatch.setattr(boundary, "release_upload_lock", unlock)
    monkeypatch.setattr(boundary, "mark_payload_ready", ready)
    monkeypatch.setattr(boundary, "cleanup_terminal_upload", cleanup)
    monkeypatch.setattr(service, "get_receipt", lambda _db, task_id: read(task_id))
    monkeypatch.setattr("app.db.storage.get_storage_provider", lambda: storage)
    monkeypatch.setattr(ingest, "resolve_project_scope", AsyncMock())
    monkeypatch.setattr(ingest, "record_activity", AsyncMock())
    monkeypatch.setattr(ingest, "_resolve_run_id", AsyncMock(side_effect=lambda *_a, **_k: str(uuid.uuid4())))
    monkeypatch.setattr(ingest, "run_is_tombstoned", AsyncMock(return_value=False))
    monkeypatch.setattr("app.services.ingestion_backpressure.enforce_redis_memory_backpressure", AsyncMock())
    monkeypatch.setattr("app.services.ingestion_rate_limit.enforce_ingest_rate_limit", AsyncMock())
    # Durable uploads must stay independent of Redis status availability.
    monkeypatch.setattr(upload_status, "set_status", AsyncMock(side_effect=AssertionError("unexpected Redis seed")))
    monkeypatch.setattr(upload_status, "get_status", AsyncMock(side_effect=AssertionError("unexpected legacy status read")))
    executor = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(upload_dispatch, "_PUBLISH_EXECUTOR", executor)
    monkeypatch.setattr(upload_dispatch, "_PUBLISH_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(upload_dispatch, "UPLOAD_PUBLISH_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(ingest, "_PREPARATION_SLOTS", threading.BoundedSemaphore(1))
    try:
        yield SimpleNamespace(app=app, rows=rows, storage=storage, locks=locks, project_id=project_id,
                              auth_value=auth_value, user=user)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


async def post(world, kind, *, key="same-logical-upload", content=None):
    async with AsyncClient(transport=ASGITransport(app=world.app), base_url="http://testserver") as client:
        headers = {"Idempotency-Key": key} if key is not None else {}
        if kind == "batch":
            return await client.post("/api/v1/ingest", headers=headers, json={
                "project_id": str(world.project_id), "build_number": "receipt-proof",
                "results": [{"test_name": content or "test_one", "status": "PASSED"}],
            })
        return await client.post("/api/v1/ingest/file", headers=headers,
            data={"project_id": str(world.project_id), "build_number": "receipt-proof", "format": "junit"},
            files={"file": ("results.xml", content or b'<testsuite><testcase name="one"/></testsuite>', "application/xml")})


async def call(world, path, method="GET"):
    async with AsyncClient(transport=ASGITransport(app=world.app), base_url="http://testserver") as client:
        return await client.request(method, path)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["batch", "file"])
async def test_ack_loss_repost_and_fresh_poll_keep_one_durable_identity(world, monkeypatch, kind):
    published = []
    task = tasks.ingest_uploaded_results if kind == "batch" else tasks.ingest_uploaded_file

    def accepted_then_error(**message):
        published.append(message)
        assert len(world.rows) == 1
        raise ConnectionError("synthetic lost acknowledgement after acceptance")

    world.storage.before_put = lambda _key: len(world.rows) == 1 or pytest.fail("storage preceded receipt")
    monkeypatch.setattr(task, "apply_async", accepted_then_error)
    first = await post(world, kind)
    assert first.status_code == 503
    task_id = first.headers["X-Upload-Task-ID"]
    row = world.rows[task_id]
    assert row.storage_key in world.storage.objects
    assert world.storage.deleted == []
    second = await post(world, kind)
    assert second.status_code == 503
    assert second.headers["X-Upload-Task-ID"] == task_id
    assert second.headers["X-Upload-Run-ID"] == str(row.run_id)
    assert len(world.rows) == 1
    assert published[0]["kwargs"] == published[1]["kwargs"]
    assert published[0]["kwargs"]["upload_receipt_id"] == task_id
    poll = await call(world, f"/api/v1/ingest/uploads/{task_id}")
    assert poll.status_code == 200
    assert poll.json()["payload_state"] == "ready" and poll.json()["accepted_at"]
    conflict = await post(world, kind, content="different" if kind == "batch" else b"different")
    assert conflict.status_code == 409 and len(published) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["batch", "file"])
async def test_timeout_late_publish_and_explicit_retry_keep_payload_and_task(world, monkeypatch, kind):
    accepted, release, published = threading.Event(), threading.Event(), []

    def slow(**message):
        published.append(message)
        accepted.set()
        assert release.wait(2)

    task = tasks.ingest_uploaded_results if kind == "batch" else tasks.ingest_uploaded_file
    monkeypatch.setattr(task, "apply_async", slow)
    try:
        response = await post(world, kind)
        assert accepted.is_set() and response.status_code == 503
        task_id = response.headers["X-Upload-Task-ID"]
        row = world.rows[task_id]
        assert row.storage_key in world.storage.objects
    finally:
        release.set()
    for _ in range(100):
        if upload_dispatch._PUBLISH_SLOTS.acquire(blocking=False):
            upload_dispatch._PUBLISH_SLOTS.release()
            break
        await asyncio.sleep(0.002)
    monkeypatch.setattr(task, "apply_async", lambda **message: published.append(message))
    retried = await call(world, f"/api/v1/ingest/uploads/{task_id}/retry", "POST")
    assert retried.status_code == 202 and retried.json()["task_id"] == task_id
    assert published[0]["task_id"] == published[1]["task_id"]
    assert published[0]["kwargs"] == published[1]["kwargs"]
    assert world.storage.deleted == []


@pytest.mark.asyncio
async def test_storage_interruption_is_reserved_and_same_key_repost_repairs_it(world, monkeypatch):
    monkeypatch.setattr(tasks.ingest_uploaded_file, "apply_async", lambda **_message: None)
    world.storage.fail_put = True
    first = await post(world, "file")
    assert first.status_code == 503
    task_id = first.headers["X-Upload-Task-ID"]
    poll = await call(world, first.headers["Location"])
    assert poll.json()["payload_state"] == "reserved"
    world.storage.fail_put = False
    second = await post(world, "file")
    assert second.status_code == 202 and second.json()["task_id"] == task_id
    assert len(world.rows) == 1 and world.rows[task_id].storage_key in world.storage.objects


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", [False, True])
async def test_late_storage_preparation_keeps_lock_and_known_full_slot_admits_nothing(world, monkeypatch, disconnect):
    started, release = asyncio.Event(), asyncio.Event()
    real_put = world.storage.put_object

    async def slow_put(*args, **kwargs):
        started.set()
        await release.wait()
        await real_put(*args, **kwargs)

    monkeypatch.setattr(world.storage, "put_object", slow_put)
    monkeypatch.setattr(boundary, "OBJECT_OPERATION_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(tasks.ingest_uploaded_file, "apply_async", lambda **_message: None)
    request = asyncio.create_task(post(world, "file"))
    await started.wait()
    try:
        task_id, = world.rows
        if disconnect:
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        else:
            response = await request
            assert response.status_code == 503 and response.headers["X-Upload-Task-ID"] == task_id
        assert task_id in world.locks
        assert (await boundary.acquire_upload_lock(task_id)) is None
        refusal = await post(world, "file", key="another-upload")
        assert refusal.status_code == 503 and refusal.headers["Retry-After"] == "1"
        assert "X-Upload-Task-ID" not in refusal.headers
        assert len(world.rows) == 1  # Known shedding never creates a stranded receipt.
    finally:
        release.set()
        await asyncio.gather(*tuple(ingest._UPLOAD_PREPARATIONS))
    assert not world.locks
    assert world.rows[task_id].payload_ready_at is not None
    assert world.rows[task_id].storage_key in world.storage.objects
    retried = await call(world, f"/api/v1/ingest/uploads/{task_id}/retry", "POST")
    assert retried.status_code == 202 and retried.json()["task_id"] == task_id


@pytest.mark.asyncio
async def test_independent_manual_uploads_and_scoped_keys_remain_independent(world, monkeypatch):
    monkeypatch.setattr(tasks.ingest_uploaded_file, "apply_async", lambda **_message: None)
    first = await post(world, "file", key=None)
    second = await post(world, "file", key=None)
    assert first.status_code == second.status_code == 202
    assert first.json()["run_id"] != second.json()["run_id"]
    task_id = first.json()["task_id"]
    world.auth_value[1] = uuid.uuid4()
    assert (await call(world, f"/api/v1/ingest/uploads/{task_id}/retry", "POST")).status_code == 403
    assert (await call(world, f"/api/v1/ingest/uploads/{task_id}/cleanup", "POST")).status_code == 403
    world.auth_value[1] = None
    world.user.role = "VIEWER"
    assert (await call(world, f"/api/v1/ingest/uploads/{task_id}/retry", "POST")).status_code == 403


@pytest.mark.asyncio
async def test_cleanup_requires_terminal_commit_and_terminal_key_never_rewrites_object(world, monkeypatch):
    published = []
    monkeypatch.setattr(tasks.ingest_uploaded_file, "apply_async", lambda **message: published.append(message))
    first = await post(world, "file")
    task_id = first.json()["task_id"]
    row = world.rows[task_id]
    path = f"/api/v1/ingest/uploads/{task_id}/cleanup"
    assert (await call(world, path, "POST")).status_code == 409
    assert not world.storage.deleted
    row.state, row.completed_at = "succeeded", datetime.now(timezone.utc)
    assert (await call(world, path, "POST")).json()["cleaned"] is True
    assert row.storage_key not in world.storage.objects
    again = await post(world, "file")
    assert again.status_code == 202 and again.json()["task_id"] == task_id
    assert ingest.record_activity.await_count == 1  # Terminal replay is not another receipt event.
    assert len(published) == 1 and row.storage_key not in world.storage.objects
    assert (await call(world, f"/api/v1/ingest/uploads/{task_id}")).json()["state"] == "succeeded"
    # A retained locator can be checked again if a remote object appeared
    # after a lost storage acknowledgement. Terminal status stays immutable.
    world.storage.objects[row.storage_key] = b"synthetic late object"
    assert (await call(world, path, "POST")).json()["cleaned"] is True
    assert row.storage_key not in world.storage.objects


@pytest.mark.asyncio
async def test_strict_durable_status_failure_does_not_fall_back_to_redis(monkeypatch):
    task_id = str(uuid.uuid4())
    token = boundary._ACTIVE_RECEIPT.set(task_id)
    failure = AsyncMock(side_effect=ConnectionError("synthetic durable write failure"))
    monkeypatch.setattr(boundary, "persist_active_status", failure)
    redis_getter = AsyncMock(side_effect=AssertionError("Redis must not replace durable truth"))
    monkeypatch.setattr("app.db.redis_client.get_redis", redis_getter)
    try:
        with pytest.raises(ConnectionError):
            await upload_status.set_status(task_id, state="succeeded", result={"total": 1})
        redis_getter.assert_not_called()
    finally:
        boundary._ACTIVE_RECEIPT.reset(token)


@pytest.mark.parametrize("key", ["", " space", "a/b", "é", "x" * 129])
def test_idempotency_key_rejects_unbounded_or_non_ascii_input(key):
    with pytest.raises(ValueError):
        service.idempotency_digest(key)


def test_key_digest_is_bounded_and_request_digest_binds_semantics():
    assert len(service.idempotency_digest("receipt:123")) == 64
    assert service.request_digest(b"one", {"a": 1, "b": 2}) == service.request_digest(b"one", {"b": 2, "a": 1})
    assert service.request_digest(b"one", {"a": 1}) != service.request_digest(b"two", {"a": 1})


@pytest.mark.parametrize("terminal,busy,altered", [(False, False, False), (True, False, False), (False, True, False), (False, False, True)])
def test_real_celery_receipt_boundary_fences_duplicate_and_altered_deliveries(monkeypatch, terminal, busy, altered):
    app = Celery("upload-receipt-proof", broker="memory://", backend="cache+memory://", set_as_current=False)
    task_id = str(uuid.uuid4())
    kwargs = {"run_id": str(uuid.uuid4()), "payload_storage_key": "synthetic/payload"}
    row = SimpleNamespace(kind="batch", state="succeeded" if terminal else "pending", task_kwargs=kwargs)
    executed = []

    @app.task(bind=True, base=boundary.UploadTrackedTask, name=service.TASK_NAMES["batch"], shared=False)
    def work(self, **fields):
        assert self.request.id == task_id
        assert boundary.active_receipt_id() == task_id
        executed.append(fields)
        return "business-result"

    lock, unlock, cleanup = AsyncMock(return_value=None if busy else SimpleNamespace()), AsyncMock(), AsyncMock()
    monkeypatch.setattr(boundary, "acquire_upload_lock", lock)
    monkeypatch.setattr(boundary, "release_upload_lock", unlock)
    monkeypatch.setattr(boundary, "read_upload", AsyncMock(return_value=row))
    monkeypatch.setattr(boundary, "upload_project_active", AsyncMock(return_value=True))
    payload_check = AsyncMock()
    monkeypatch.setattr(boundary, "ensure_payload_ready", payload_check)
    monkeypatch.setattr(boundary, "cleanup_terminal_upload", cleanup)
    sent = {**kwargs, "upload_receipt_id": task_id}
    if altered:
        sent["payload_storage_key"] = "other/payload"
    if altered:
        with pytest.raises(ValueError, match="metadata"):
            work.apply(kwargs=sent, task_id=task_id, throw=True)
    else:
        result = work.apply(kwargs=sent, task_id=task_id, throw=True).get()
        assert result == ("business-result" if not terminal and not busy else {"duplicate": True, "task_id": task_id})
    assert len(executed) == int(not terminal and not busy and not altered)
    assert payload_check.await_count == int(not terminal and not busy and not altered)
    assert unlock.await_count == int(not busy)
    assert boundary.active_receipt_id() is None
    assert issubclass(boundary.UploadTrackedTask, __import__("app.worker.celery_app", fromlist=["TimeLimitedTask"]).TimeLimitedTask)
    app.close()
