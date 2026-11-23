"""GAP-B06: an expired, deactivated or orphaned API key is refused everywhere it authenticates.

API keys are long-lived bearer credentials, and their lifecycle is checked in
two copies of the same three rules in ``app/core/deps.py``:

* ``_validate_api_key`` -- every read and write that takes ``X-API-Key``
  through ``get_current_user_or_api_key`` / ``get_api_key_context``, which
  includes ``POST /api/v1/ingest``;
* ``get_streaming_api_key_context`` -- ``POST /api/v1/stream/ingest``.

No test named the three refusals ("API key is inactive", "API key has
expired", "API key owner account is inactive"): every fake key in the suite
was active with ``expires_at=None``, so deleting either expiry check left the
suite green while an expired CI key kept reading and ingesting.

Real ``api_keys`` rows (hashes of obviously fake keys), the real app, no auth
override. Each key fails exactly ONE rule, and a valid key with a future
expiry is the positive control on every path, so a refusal can't come from a
rule that refuses everything. On a refusal nothing reaches storage, Celery or
the live-session table. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and
``REDIS_URL``, migrated to head.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.integration.security_seed_cleanup import (
    purge_live_sessions,
    purge_redis_keys,
    purge_seeds,
)

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


#: key name -> (expires in days or None, is_active, owner). Each refused key
#: breaks exactly one rule; ``valid`` breaks none and HAS an expiry, so the
#: expiry check is exercised on the accepted side too.
_KEYS = {
    "valid": (30, True, "owner"),
    "expired": (-1, True, "owner"),
    "inactive": (30, False, "owner"),
    "owner_inactive": (30, True, "retired_owner"),
}

#: refused key -> the 401 detail it must get, on every path.
_REFUSED = {
    "expired": "API key has expired",
    "inactive": "API key is inactive",
    "owner_inactive": "API key owner account is inactive",
}


class _Storage:
    """Upload storage in memory: the JSON ingest route writes the batch here before queueing it."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def put_object(
        self, key, content, content_type="application/json", bucket=None
    ):
        self.objects[key] = content

    async def delete_object(self, key, bucket=None):
        self.objects.pop(key, None)

    async def get_object_content(self, key, bucket=None):
        return self.objects[key]


@pytest.fixture
async def world(monkeypatch):
    redis_asyncio = pytest.importorskip("redis.asyncio")
    from httpx import ASGITransport, AsyncClient

    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.main import app
    from app.models.postgres import (
        ApiKey,
        LiveSession,
        Project,
        TestRun,
        User,
        UserRole,
    )
    from app.worker.tasks import ingest_uploaded_results

    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(
        _env("TESTLOOKUP_POSTGRES_TEST_DSN"), pool_size=4, max_overflow=0
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = redis_asyncio.Redis.from_url(_env("REDIS_URL"), decode_responses=True)

    async def _get_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _get_db
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)

    storage = _Storage()
    monkeypatch.setattr("app.db.storage.get_storage_provider", lambda: storage)

    # The JSON ingest route queues the batch; bind every dispatch to the real
    # task signature (the argument check a broker hop runs) and keep it here.
    queued: list[dict] = []
    task = ingest_uploaded_results._get_current_object()
    signature = inspect.signature(task.run)

    def _apply_async(args=None, kwargs=None, task_id=None, **_options):
        bound = signature.bind(*(args or ()), **(kwargs or {}))
        queued.append(dict(bound.arguments))
        return SimpleNamespace(id=task_id)

    monkeypatch.setattr(task, "apply_async", _apply_async)

    tag = uuid.uuid4().hex[:10]
    project = uuid.uuid4()
    owners = {"owner": uuid.uuid4(), "retired_owner": uuid.uuid4()}
    now = datetime.now(timezone.utc)
    raw = {name: f"qai_test_only_secb1_{tag}_{name}" for name in _KEYS}

    async with sessions() as db:
        db.add(
            Project(
                id=project, name=f"secb1-{tag}", slug=f"secb1-{tag}", is_active=True
            )
        )
        for name, uid in owners.items():
            db.add(
                User(
                    id=uid,
                    email=f"secb1-{name}-{tag}@example.test",
                    username=f"secb1_{name}_{tag}",
                    full_name=f"secb1 {name}",
                    hashed_password="!unusable",
                    role=UserRole.ADMIN.value,
                    is_active=(name == "owner"),
                )
            )
        await db.flush()
        for name, (days, active, owner) in _KEYS.items():
            db.add(
                ApiKey(
                    id=uuid.uuid4(),
                    user_id=owners[owner],
                    name=f"secb1 {name}",
                    key_hash=hashlib.sha256(raw[name].encode()).hexdigest(),
                    key_hint=raw[name][:8] + "...",
                    scopes=["stream:write"],
                    project_id=project,
                    expires_at=now + timedelta(days=days),
                    is_active=active,
                )
            )
        await db.commit()

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    world = SimpleNamespace(
        client=client,
        sessions=sessions,
        tag=tag,
        project=project,
        storage=storage,
        queued=queued,
        headers={name: {"X-API-Key": value} for name, value in raw.items()},
        models=SimpleNamespace(ApiKey=ApiKey, LiveSession=LiveSession, TestRun=TestRun),
        live_sessions=[],
    )
    try:
        yield world
    finally:
        await client.aclose()
        app.dependency_overrides.pop(get_db, None)
        try:
            # Every live session the project got, including one a refused key
            # was wrongly let open (its test failed before recording the id).
            async with sessions() as db:
                opened = (
                    (
                        await db.execute(
                            select(LiveSession.id).where(
                                LiveSession.project_id == project
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            await purge_live_sessions(redis, {*world.live_sessions, *map(str, opened)})
            await purge_redis_keys(redis, str(project))
            await purge_seeds(
                sessions, project_ids=[project], user_ids=list(owners.values())
            )
        finally:
            await redis.aclose()
            await engine.dispose()
            await app_postgres.dispose_engine_for_loop()


def _ingest_body(world, test_name: str) -> dict:
    return {
        "project_id": str(world.project),
        "build_number": f"secb1-{world.tag}-{test_name}",
        "results": [
            {
                "test_name": test_name,
                "suite_name": "secb1",
                "status": "PASSED",
                "duration_ms": 5,
            }
        ],
    }


def _stream_body(world, test_name: str) -> dict:
    return {
        "run_id": f"secb1-{world.tag}-{test_name}",
        "events": [
            {
                "event_type": "test_result",
                "test_name": test_name,
                "suite_name": "secb1",
                "status": "PASSED",
                "duration_ms": 5,
            }
        ],
    }


async def _counts(world) -> dict[str, int]:
    """Rows a request could have written for the project, plus what reached storage and Celery."""
    LiveSession, TestRun = world.models.LiveSession, world.models.TestRun
    async with world.sessions() as db:
        live = (
            await db.execute(
                select(func.count())
                .select_from(LiveSession)
                .where(LiveSession.project_id == world.project)
            )
        ).scalar_one()
        runs = (
            await db.execute(
                select(func.count())
                .select_from(TestRun)
                .where(TestRun.project_id == world.project)
            )
        ).scalar_one()
    return {
        "live_sessions": live,
        "test_runs": runs,
        "stored": len(world.storage.objects),
        "queued": len(world.queued),
    }


async def _last_used(world, name: str):
    ApiKey = world.models.ApiKey
    async with world.sessions() as db:
        return (
            await db.execute(
                select(ApiKey.last_used_at).where(ApiKey.name == f"secb1 {name}")
            )
        ).scalar_one()


# ── positive controls: the valid key works on all three paths ───────────────


async def test_a_valid_key_reads_its_project(world):
    resp = await world.client.get("/api/v1/projects", headers=world.headers["valid"])

    assert resp.status_code == 200, resp.text
    assert str(world.project) in {row["id"] for row in resp.json()}
    assert await _last_used(world, "valid") is not None


async def test_a_valid_key_ingests_a_batch(world):
    test_name = f"secb1_valid_{world.tag}"

    resp = await world.client.post(
        "/api/v1/ingest",
        headers=world.headers["valid"],
        json=_ingest_body(world, test_name),
    )

    assert resp.status_code == 202, resp.text
    assert resp.json()["total_results"] == 1
    assert len(world.queued) == 1, world.queued
    queued = world.queued[0]
    assert queued["run_id"] == resp.json()["run_id"]
    stored = json.loads(world.storage.objects[queued["payload_storage_key"]])
    assert stored["project_id"] == str(world.project)
    assert [r["test_name"] for r in stored["results"]] == [test_name]


async def test_a_valid_key_streams_a_result(world):
    LiveSession = world.models.LiveSession
    body = _stream_body(world, f"secb1_valid_{world.tag}")

    resp = await world.client.post(
        "/api/v1/stream/ingest", headers=world.headers["valid"], json=body
    )

    assert resp.status_code == 202, resp.text
    payload = resp.json()
    world.live_sessions.append(payload["session_id"])
    assert payload["accepted"] == 1 and payload["created_session"] is True, payload
    async with world.sessions() as db:
        session = (
            await db.execute(
                select(LiveSession).where(LiveSession.project_id == world.project)
            )
        ).scalar_one()
    assert str(session.id) == payload["session_id"]
    assert session.run_id == body["run_id"]
    assert session.client_name == "secb1 valid"


# ── refusals: each broken rule, on each path ────────────────────────────────


@pytest.mark.parametrize("key", sorted(_REFUSED))
async def test_a_broken_key_cannot_read(world, key):
    resp = await world.client.get("/api/v1/projects", headers=world.headers[key])

    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == _REFUSED[key]
    assert await _last_used(world, key) is None


@pytest.mark.parametrize("key", sorted(_REFUSED))
async def test_a_broken_key_cannot_ingest_and_nothing_lands(world, key):
    before = await _counts(world)

    resp = await world.client.post(
        "/api/v1/ingest",
        headers=world.headers[key],
        json=_ingest_body(world, f"secb1_{key}_{world.tag}"),
    )

    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == _REFUSED[key]
    assert (
        await _counts(world)
        == before
        == {"live_sessions": 0, "test_runs": 0, "stored": 0, "queued": 0}
    )


@pytest.mark.parametrize("key", sorted(_REFUSED))
async def test_a_broken_key_cannot_stream_and_no_session_opens(world, key):
    before = await _counts(world)

    resp = await world.client.post(
        "/api/v1/stream/ingest",
        headers=world.headers[key],
        json=_stream_body(world, f"secb1_{key}_{world.tag}"),
    )

    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == _REFUSED[key]
    assert (
        await _counts(world)
        == before
        == {"live_sessions": 0, "test_runs": 0, "stored": 0, "queued": 0}
    )
    assert await _last_used(world, key) is None


async def test_the_refused_keys_differ_from_the_valid_one_in_one_field_each(world):
    """Guards the fixture: a refused key that broke two rules would hide a missing check."""
    ApiKey = world.models.ApiKey
    async with world.sessions() as db:
        rows = {
            row.name.removeprefix("secb1 "): row
            for row in (
                await db.execute(
                    select(ApiKey).where(ApiKey.project_id == world.project)
                )
            ).scalars()
        }
    now = datetime.now(timezone.utc)

    def state(row):
        return (
            row.is_active,
            row.expires_at > now,
            row.user_id == rows["valid"].user_id,
        )

    assert state(rows["valid"]) == (True, True, True)
    assert state(rows["expired"]) == (True, False, True)
    assert state(rows["inactive"]) == (False, True, True)
    assert state(rows["owner_inactive"]) == (True, True, False)
