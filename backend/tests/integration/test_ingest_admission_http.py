"""The ingest admission gates, driven through the real app over HTTP.

Re-audit M4. The handler tests in
``tests/regression/test_ingest_routes_are_gated.py`` call the route functions
directly: they prove the order of the calls, but not what a client receives.
These go through the ASGI app with a fake Redis behind both gates, so what is
asserted is the response a CI uploader actually gets -- the status, the
``Retry-After`` header it backs off by -- and that nothing was stored or queued.

The fake Redis answers only the calls the two gates make, so anything else on
the request path reaching for Redis fails loudly instead of passing unobserved.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jose")
pytest.importorskip("asyncpg")

from app.core.config import settings  # noqa: E402
from app.services import ingestion_backpressure  # noqa: E402

pytestmark = pytest.mark.asyncio

JUNIT = b"<testsuite name='s' tests='1'><testcase name='t'/></testsuite>"


def _batch(project_id: uuid.UUID) -> dict:
    return {
        "project_id": str(project_id),
        "build_number": "b-1",
        "results": [{"test_name": "t1", "status": "PASSED", "duration_ms": 10}],
    }


@pytest.fixture
def redis(monkeypatch):
    """Both gates' Redis: under budget, with memory to spare, by default."""
    fake = SimpleNamespace(
        incrby=AsyncMock(return_value=1),
        expire=AsyncMock(return_value=True),
        incr=AsyncMock(return_value=1),
        info=AsyncMock(return_value={"used_memory": 1024, "maxmemory": 0}),
    )
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake)
    # The backpressure gate caches its reading for 5s; start every test cold.
    monkeypatch.setattr(ingestion_backpressure, "_CACHE", None)
    monkeypatch.setattr(settings, "INGEST_RATE_LIMIT_PER_MINUTE", 200)
    monkeypatch.setattr(settings, "INGEST_REDIS_MEMORY_THRESHOLD_PCT", 75)
    monkeypatch.setattr(settings, "INGEST_REDIS_MEMORY_ABSOLUTE_BYTES", 10_000_000)
    return fake


@pytest.fixture
def downstream():
    """Storage and the two Celery entry points: what must NOT run on a refusal."""
    stored: list[str] = []
    queued: list[str] = []

    class _Storage:
        async def put_object(self, key, content, content_type="application/json", bucket=None):
            stored.append(key)
            self.last = (key, content, content_type)

        async def delete_object(self, key, bucket=None):
            return None

        async def get_object_content(self, key, bucket=None):
            return self.last[1]

    def _delay(*_args, **_kwargs):
        queued.append("batch")
        return SimpleNamespace(id="task-batch-1")

    def _apply_async(*, kwargs, task_id):
        queued.append("file")
        return SimpleNamespace(id=task_id)

    batch_task = AsyncMock()
    batch_task.delay = _delay
    file_task = AsyncMock()
    file_task.apply_async = _apply_async

    with (
        patch("app.db.storage.get_storage_provider", return_value=_Storage()),
        patch("app.worker.tasks.ingest_uploaded_results", batch_task),
        patch("app.worker.tasks.ingest_uploaded_file", file_task),
    ):
        yield SimpleNamespace(stored=stored, queued=queued)


async def test_a_spent_budget_is_a_429_the_uploader_can_back_off_by(
    client, auth_as, redis, downstream
):
    project_id = uuid.uuid4()
    auth_as(accessible_projects={project_id})
    redis.incrby.return_value = 201  # one past the 200-batch budget

    resp = await client.post("/api/v1/ingest", json=_batch(project_id))

    assert resp.status_code == 429, resp.text
    retry_after = resp.headers.get("Retry-After")
    assert retry_after is not None and 1 <= int(retry_after) <= 60, (
        f"a 429 without a usable Retry-After leaves the uploader guessing: {retry_after!r}"
    )
    assert "batches" in resp.json()["detail"]
    assert downstream.stored == [] and downstream.queued == [], (
        "a refused upload was still stored or queued"
    )


async def test_a_spent_budget_refuses_a_file_upload(client, auth_as, redis, downstream):
    project_id = uuid.uuid4()
    auth_as(accessible_projects={project_id})
    redis.incrby.return_value = 201

    resp = await client.post(
        "/api/v1/ingest/file",
        data={"project_id": str(project_id), "build_number": "b-1"},
        files={"file": ("results.xml", JUNIT, "application/xml")},
    )

    assert resp.status_code == 429, resp.text
    assert resp.headers.get("Retry-After")
    assert downstream.stored == [] and downstream.queued == []


async def test_memory_pressure_is_a_503_and_spends_no_quota(
    client, auth_as, redis, downstream, monkeypatch
):
    project_id = uuid.uuid4()
    auth_as(accessible_projects={project_id})
    monkeypatch.setattr(settings, "INGEST_REDIS_MEMORY_ABSOLUTE_BYTES", 1000)
    redis.info.return_value = {"used_memory": 5000, "maxmemory": 0}

    resp = await client.post("/api/v1/ingest", json=_batch(project_id))

    assert resp.status_code == 503, resp.text
    assert resp.headers.get("Retry-After") == "5"
    assert redis.incrby.await_count == 0, (
        "the project was charged for an upload the system then shed"
    )
    assert downstream.stored == [] and downstream.queued == []


async def test_uploads_draw_on_the_sdk_batch_bucket(client, auth_as, redis, downstream):
    """One per-project batch budget, whichever route a batch arrives by."""
    project_id = uuid.uuid4()
    auth_as(accessible_projects={project_id})

    resp = await client.post("/api/v1/ingest", json=_batch(project_id))

    assert resp.status_code == 202, resp.text
    key = redis.incrby.await_args.args[0]
    assert key.rsplit(":", 1)[0] == f"testlookup:rate:ingest:{project_id}", key
    assert downstream.queued == ["batch"]


async def test_a_non_member_is_refused_before_either_gate_runs(
    client, auth_as, redis, downstream
):
    """Charging before the scope check would let anyone spend another tenant's quota."""
    project_id = uuid.uuid4()
    auth_as(accessible_projects={uuid.uuid4()})  # a member of some OTHER project

    resp = await client.post("/api/v1/ingest", json=_batch(project_id))

    assert resp.status_code == 403, resp.text
    assert redis.incrby.await_count == 0, "a non-member spent the project's quota"
    assert redis.info.await_count == 0
