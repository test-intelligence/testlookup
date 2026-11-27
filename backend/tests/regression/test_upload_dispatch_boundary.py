"""OBS-23/24: broker uncertainty must not destroy a queued upload or stall HTTP.

The real HTTP handlers, upload status state machine and task signatures run
against fake auth/storage/Redis. Publication runs on the actual bounded helper;
thread events simulate a broker that accepts, errors, or answers after timeout.
"""
from __future__ import annotations

import asyncio
import inspect
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from celery import Celery
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from kombu.exceptions import OperationalError

from app.bootstrap import configure_middlewares
from app.core.config import settings
from app.core.deps import get_api_key_context, get_db
from app.routers import health, ingest
from app.worker import tasks, upload_dispatch

pytestmark = pytest.mark.asyncio


class _Storage:
    def __init__(self):
        self.objects = {}
        self.deleted = []

    async def put_object(self, key, content, **_options):
        self.objects[key] = content

    async def delete_object(self, key, **_options):
        self.deleted.append(key)
        self.objects.pop(key, None)


@pytest.fixture
def publisher(monkeypatch):
    """One real thread/slot per test; no late work escapes its fixture."""
    executor = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(upload_dispatch, "_PUBLISH_EXECUTOR", executor)
    monkeypatch.setattr(upload_dispatch, "_PUBLISH_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(upload_dispatch, "UPLOAD_PUBLISH_TIMEOUT_SECONDS", 0.25)
    try:
        yield
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


@pytest.fixture
def world(monkeypatch, publisher, upload_receipt_store):
    app = FastAPI()
    app.include_router(ingest.router)
    app.include_router(health.router)
    project_id = uuid.uuid4()
    user = SimpleNamespace(
        id=uuid.uuid4(), username="upload-review", full_name="Upload review",
        role="QA_ENGINEER", is_active=True,
    )

    async def db():
        yield AsyncMock()

    async def auth():
        return user, None

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_api_key_context] = auth
    storage = _Storage()
    monkeypatch.setattr("app.db.storage.get_storage_provider", lambda: storage)
    monkeypatch.setattr(ingest, "resolve_project_scope", AsyncMock())
    monkeypatch.setattr(ingest, "record_activity", AsyncMock())
    monkeypatch.setattr(ingest, "_resolve_run_id", AsyncMock(return_value=str(uuid.uuid4())))
    monkeypatch.setattr(ingest, "run_is_tombstoned", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "app.services.ingestion_backpressure.enforce_redis_memory_backpressure", AsyncMock()
    )
    monkeypatch.setattr(
        "app.services.ingestion_rate_limit.enforce_ingest_rate_limit", AsyncMock()
    )
    return SimpleNamespace(
        app=app, project_id=project_id, storage=storage,
        receipts=upload_receipt_store,
    )


async def _post(world, kind):
    async with AsyncClient(
        transport=ASGITransport(app=world.app), base_url="http://testserver"
    ) as client:
        if kind == "batch":
            return await client.post("/api/v1/ingest", json={
                "project_id": str(world.project_id), "build_number": "review-build",
                "results": [{"test_name": "review_test", "status": "PASSED"}],
            })
        return await client.post(
            "/api/v1/ingest/file",
            data={"project_id": str(world.project_id), "build_number": "review-build", "format": "junit"},
            files={"file": ("results.xml", b'<testsuite><testcase name="review_test"/></testsuite>', "application/xml")},
        )


async def _poll(world, task_id):
    async with AsyncClient(
        transport=ASGITransport(app=world.app), base_url="http://testserver"
    ) as client:
        return await client.get(f"/api/v1/ingest/uploads/{task_id}")


def _task(kind):
    return tasks.ingest_uploaded_results if kind == "batch" else tasks.ingest_uploaded_file


def _storage_key(message):
    return message["kwargs"].get("payload_storage_key") or message["kwargs"]["file_storage_key"]


@pytest.mark.parametrize("kind", ["batch", "file"])
async def test_confirmed_publish_keeps_202_and_the_original_task_contract(world, monkeypatch, kind):
    task = _task(kind)
    published = []

    def publish(*, kwargs, task_id, **options):
        task.__header__(**kwargs)
        inspect.signature(task.run).bind(**kwargs)
        published.append({"kwargs": kwargs, "task_id": task_id, "options": options})

    monkeypatch.setattr(task, "apply_async", publish)
    response = await _post(world, kind)
    assert response.status_code == 202, response.text
    message, = published
    assert response.json()["task_id"] == message["task_id"]
    assert response.json()["run_id"] == message["kwargs"]["run_id"]
    connection = message["options"].pop("connection")
    assert message["options"] == {"ignore_result": True, "retry": False}
    assert connection.transport_options["socket_connect_timeout"] == 2
    assert connection.transport_options["socket_timeout"] == 2
    assert connection.transport_options["retry_on_timeout"] is False
    assert connection.transport_options["max_retries"] == 0
    assert _storage_key(message) in world.storage.objects
    assert (await _poll(world, message["task_id"])).json()["state"] == "pending"


@pytest.mark.parametrize("kind", ["batch", "file"])
async def test_accepted_then_error_retains_payload_and_pending_poll_identity(world, monkeypatch, kind):
    accepted = []

    def publish(**message):
        accepted.append(message)
        raise ConnectionError("fake confirmation lost after broker acceptance")

    monkeypatch.setattr(_task(kind), "apply_async", publish)
    response = await _post(world, kind)
    assert response.status_code == 503, response.text
    message, = accepted
    assert _storage_key(message) in world.storage.objects
    assert world.storage.deleted == []
    assert response.headers["X-Upload-Task-ID"] == message["task_id"]
    assert response.headers["X-Upload-Run-ID"] == message["kwargs"]["run_id"]
    assert response.headers["Location"] == f'/api/v1/ingest/uploads/{message["task_id"]}'
    assert response.headers["Location"] in response.json()["detail"]
    poll = await _poll(world, message["task_id"])
    assert poll.status_code == 200
    assert poll.json()["state"] == "pending"
    assert poll.json()["run_id"] == message["kwargs"]["run_id"]


@pytest.mark.parametrize("kind", ["batch", "file"])
async def test_worker_terminal_result_survives_an_unconfirmed_publish(world, monkeypatch, kind):
    def publish(*, kwargs, task_id, **_options):
        # Model a worker that completed before the broker client's error.
        row = world.receipts[task_id]
        assert str(row.run_id) == kwargs["run_id"]
        row.state = "succeeded"
        row.status_payload = {"result": {"total": 1}}
        raise ConnectionError("fake acknowledgement lost")

    monkeypatch.setattr(_task(kind), "apply_async", publish)
    response = await _post(world, kind)
    assert response.status_code == 503
    poll = await _poll(world, response.headers["X-Upload-Task-ID"])
    assert poll.json()["state"] == "succeeded"
    assert poll.json()["result"] == {"total": 1}


@pytest.mark.parametrize("kind", ["batch", "file"])
async def test_timeout_keeps_health_responsive_and_late_publication_identity(world, monkeypatch, kind):
    monkeypatch.setattr(upload_dispatch, "UPLOAD_PUBLISH_TIMEOUT_SECONDS", 1.25)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    accepted = []

    def publish(**message):
        started.set()
        try:
            assert release.wait(4), "test did not release the fake broker"
            accepted.append(message)
        finally:
            finished.set()

    monkeypatch.setattr(_task(kind), "apply_async", publish)
    upload = asyncio.create_task(_post(world, kind))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        async with AsyncClient(
            transport=ASGITransport(app=world.app), base_url="http://testserver"
        ) as client:
            # Leave headroom inside the shipped five-second liveness timeout.
            live = await asyncio.wait_for(client.get("/health/live"), timeout=1)
            assert live.status_code == 200
            assert live.json()["status"] == "alive"
            assert not upload.done(), "the health request waited for the upload"
            assert not finished.is_set(), "the health request waited for the broker"

        response = await asyncio.wait_for(asyncio.shield(upload), timeout=2)
        assert response.status_code == 503
        assert not finished.is_set(), "HTTP must return while publication is still blocked"
        task_id = response.headers["X-Upload-Task-ID"]
        run_id = response.headers["X-Upload-Run-ID"]
        assert (await _poll(world, task_id)).json()["state"] == "pending"
        assert len(world.storage.objects) == 1
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
        message, = accepted
        assert message["task_id"] == task_id
        assert message["kwargs"]["run_id"] == run_id
        assert _storage_key(message) in world.storage.objects
        world.receipts[task_id].state = "succeeded"
        assert (await _poll(world, task_id)).json()["state"] == "succeeded"
    finally:
        release.set()
        if not upload.done():
            upload.cancel()
        await asyncio.gather(upload, return_exceptions=True)
        await asyncio.to_thread(finished.wait, 1)


async def test_timeout_does_not_release_capacity_until_the_real_publish_finishes(publisher, monkeypatch):
    monkeypatch.setattr(upload_dispatch, "UPLOAD_PUBLISH_TIMEOUT_SECONDS", 0.05)
    release = threading.Event()
    finished = threading.Event()
    calls = []

    def publish(**message):
        calls.append(message["task_id"])
        try:
            assert release.wait(2)
        finally:
            finished.set()

    task = SimpleNamespace(apply_async=publish)
    try:
        with pytest.raises(TimeoutError):
            await upload_dispatch.publish_upload(task, kwargs={}, task_id="first")
        with pytest.raises(upload_dispatch.UploadPublisherBusy):
            await upload_dispatch.publish_upload(task, kwargs={}, task_id="second")
        assert calls == ["first"], "a timeout must not admit an unbounded queue of late tasks"
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)


async def test_retry_free_broker_socket_deadline_releases_a_stalled_publisher(publisher, monkeypatch):
    requests = []
    disconnected = asyncio.Event()

    async def withhold_reply(reader, writer):
        try:
            requests.append(await reader.read(4096))
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            disconnected.set()

    monkeypatch.setattr(upload_dispatch, "_BROKER_SOCKET_TIMEOUT_SECONDS", 0.15)
    monkeypatch.setattr(upload_dispatch, "UPLOAD_PUBLISH_TIMEOUT_SECONDS", 1)
    async with await asyncio.start_server(withhold_reply, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with Celery(broker=f"redis://127.0.0.1:{port}/0", set_as_current=False) as app:
            @app.task(name="test.upload_publish_probe", shared=False)
            def probe():
                return None

            monkeypatch.setattr(upload_dispatch, "celery_app", app)
            with pytest.raises(OperationalError):
                await upload_dispatch.publish_upload(probe, kwargs={}, task_id="socket-probe")
            await asyncio.wait_for(disconnected.wait(), timeout=1)
            assert len(requests) == 1 and requests[0]
            assert upload_dispatch._PUBLISH_SLOTS.acquire(blocking=False)
            upload_dispatch._PUBLISH_SLOTS.release()


async def test_allowed_origin_can_send_upload_key_and_read_receipt(monkeypatch):
    origin = "https://fixture.example"
    monkeypatch.setattr(settings, "CORS_ORIGINS_RAW", origin)
    app = FastAPI()
    configure_middlewares(app)
    app.include_router(ingest.router)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        preflight = await client.options(
            "/api/v1/ingest/file",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,idempotency-key",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["Access-Control-Allow-Origin"] == origin
        assert "idempotency-key" in preflight.headers["Access-Control-Allow-Headers"].lower()
        response = await client.post("/api/v1/ingest/file", headers={"Origin": origin})
        exposed = {value.strip().lower() for value in response.headers["Access-Control-Expose-Headers"].split(",")}
        assert {"location", "x-upload-task-id", "x-upload-run-id"} <= exposed


async def test_request_cancellation_keeps_the_slot_until_publication_ends(publisher):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def publish(**_message):
        started.set()
        try:
            assert release.wait(2)
        finally:
            finished.set()

    task = SimpleNamespace(apply_async=publish)
    pending = asyncio.create_task(
        upload_dispatch.publish_upload(task, kwargs={}, task_id="cancelled-waiter")
    )
    try:
        assert await asyncio.to_thread(started.wait, 1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        with pytest.raises(upload_dispatch.UploadPublisherBusy):
            await upload_dispatch.publish_upload(task, kwargs={}, task_id="not-submitted")
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)


@pytest.mark.parametrize("kind", ["batch", "file"])
async def test_publisher_capacity_refusal_keeps_the_durably_staged_upload(world, monkeypatch, kind):
    # A full publisher semaphore proves no call started. The accepted receipt
    # and bytes remain available for recovery without a second logical upload.
    assert upload_dispatch._PUBLISH_SLOTS.acquire(blocking=False)
    publish = AsyncMock()
    monkeypatch.setattr(_task(kind), "apply_async", publish)
    try:
        response = await _post(world, kind)
    finally:
        upload_dispatch._PUBLISH_SLOTS.release()
    assert response.status_code == 503
    task_id = response.headers["X-Upload-Task-ID"]
    row = world.receipts[task_id]
    assert row.storage_key in world.storage.objects
    assert row.payload_ready_at is not None
    assert (await _poll(world, task_id)).json()["state"] == "pending"
    publish.assert_not_called()


@pytest.mark.parametrize("kind", ["batch", "file"])
async def test_authorization_refusal_still_precedes_storage_and_publication(world, monkeypatch, kind):
    from fastapi import HTTPException

    monkeypatch.setattr(ingest, "resolve_project_scope", AsyncMock(side_effect=HTTPException(403, "denied")))
    dispatch = AsyncMock()
    monkeypatch.setattr(upload_dispatch, "publish_upload", dispatch)
    response = await _post(world, kind)
    assert response.status_code == 403
    assert world.storage.objects == {}
    assert world.receipts == {}
    dispatch.assert_not_awaited()
