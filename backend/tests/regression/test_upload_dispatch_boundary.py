"""OBS-23/24: broker uncertainty must not destroy a queued upload or stall HTTP.

The real HTTP handlers, upload status state machine and task signatures run
against fake auth/storage/Redis. Publication runs on the actual bounded helper;
thread events simulate a broker that accepts, errors, or answers after timeout.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_api_key_context, get_db
from app.routers import ingest
from app.services import upload_status
from app.worker import tasks, upload_dispatch
from tests.regression.test_upload_status import _fake_redis_with_store

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
def world(monkeypatch, publisher):
    app = FastAPI()
    app.include_router(ingest.router)
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
    redis, records = _fake_redis_with_store()
    storage = _Storage()
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
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
        app=app, project_id=project_id, records=records, storage=storage
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
    assert message["options"] == {"ignore_result": True, "retry": False}
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
        world.records[f"upload:status:{task_id}"] = json.dumps({
            "task_id": task_id, "run_id": kwargs["run_id"],
            "project_id": str(world.project_id), "state": "succeeded",
            "result": {"total": 1},
        })
        raise ConnectionError("fake acknowledgement lost")

    monkeypatch.setattr(_task(kind), "apply_async", publish)
    response = await _post(world, kind)
    assert response.status_code == 503
    poll = await _poll(world, response.headers["X-Upload-Task-ID"])
    assert poll.json()["state"] == "succeeded"
    assert poll.json()["result"] == {"total": 1}


@pytest.mark.parametrize("kind", ["batch", "file"])
async def test_timeout_keeps_event_loop_responsive_and_late_publication_identity(world, monkeypatch, kind):
    monkeypatch.setattr(upload_dispatch, "UPLOAD_PUBLISH_TIMEOUT_SECONDS", 0.05)
    release = threading.Event()
    finished = threading.Event()
    accepted = []
    ticks = []
    stop = asyncio.Event()

    def publish(**message):
        try:
            assert release.wait(2), "test did not release the fake broker"
            accepted.append(message)
        finally:
            finished.set()

    async def heartbeat():
        while not stop.is_set():
            ticks.append(1)
            await asyncio.sleep(0.002)

    monkeypatch.setattr(_task(kind), "apply_async", publish)
    ticker = asyncio.create_task(heartbeat())
    try:
        response = await asyncio.wait_for(_post(world, kind), timeout=1)
        assert response.status_code == 503
        assert not finished.is_set(), "HTTP must return while publication is still blocked"
        assert len(ticks) >= 2, "the synchronous publisher blocked the event loop"
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
        # A later worker still advances the SAME monotonic polling record.
        await upload_status.set_status(
            task_id, run_id=run_id, project_id=str(world.project_id),
            state="succeeded", result={"total": 1},
        )
        assert (await _poll(world, task_id)).json()["state"] == "succeeded"
    finally:
        release.set()
        stop.set()
        await ticker
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
async def test_known_capacity_refusal_discards_only_its_unsubmitted_upload(world, monkeypatch, kind):
    # Unlike a publish error, a full admission semaphore proves no call started.
    assert upload_dispatch._PUBLISH_SLOTS.acquire(blocking=False)
    publish = AsyncMock()
    monkeypatch.setattr(_task(kind), "apply_async", publish)
    try:
        response = await _post(world, kind)
    finally:
        upload_dispatch._PUBLISH_SLOTS.release()
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert "busy" in response.json()["detail"]
    assert world.storage.objects == {}
    assert world.records == {}
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
    assert world.records == {}
    dispatch.assert_not_awaited()
