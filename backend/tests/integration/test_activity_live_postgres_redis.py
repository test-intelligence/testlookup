"""The activity live tail on a real Postgres and a real Redis (W21d ACT-02 §2.1).

``service.record`` through a real ``AsyncSession`` → commit → the row is in the
project's Redis stream, byte-for-byte the item ``GET /projects/{id}/activity``
answers for it; a rollback publishes nothing; a SAVEPOINT rollback publishes
only the row outside it; an attempt row publishes after its own commit; a Redis
that cannot be reached never fails the commit.

The fixture awaits ``dispose_engine_for_loop()`` before building its engine and
after disposing it, and closes the app's Redis client and its own, so the next
module never meets "Event loop is closed". Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` (at head) and ``REDIS_URL``; skips otherwise.
"""
from __future__ import annotations

import json
import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


@pytest.fixture
async def world(monkeypatch):
    import redis.asyncio as redis_asyncio
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.db.redis_client import close_redis
    from app.main import app
    from app.models.postgres import Project, ProjectActivityEvent, ProjectMember, User, UserRole
    from app.services.activity import live

    dsn, redis_url = _env("TESTLOOKUP_POSTGRES_TEST_DSN"), _env("REDIS_URL")
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=3, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    own_redis = redis_asyncio.Redis.from_url(redis_url, decode_responses=True)

    async def _get_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _get_db
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)
    monkeypatch.setattr("app.services.activity.live.get_redis", lambda: own_redis)
    monkeypatch.setattr(live, "PUBLISH_ENABLED", True)

    tag = "t" + uuid.uuid4().hex[:9]  # starts with a letter: never read as a phone number
    project_id, user_id = uuid.uuid4(), uuid.uuid4()
    async with sessions.begin() as db:
        db.add(Project(id=project_id, name=f"Live {tag}", slug=f"live-{tag}", is_active=True))
        db.add(User(id=user_id, email=f"live-{tag}@example.com", username=f"live_{tag}",
                    full_name=f"Ana {tag}", hashed_password="!unusable", role=UserRole.QA_ENGINEER.value))
        await db.flush()
        db.add(ProjectMember(project_id=project_id, user_id=user_id, role=UserRole.QA_ENGINEER.value))
    key = f"testlookup:stream:activity:{project_id}"
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        async with sessions() as db:
            user = await db.get(User, user_id)
        yield SimpleNamespace(
            sessions=sessions, redis=own_redis, http=http, key=key, tag=tag, live=live,
            project_id=project_id, user=user,
            auth={"Authorization": f"Bearer {create_access_token(str(user_id))}"},
        )
    finally:
        await http.aclose()
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous
        async with sessions.begin() as db:
            await db.execute(delete(ProjectActivityEvent).where(ProjectActivityEvent.project_id == project_id))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.execute(delete(User).where(User.id == user_id))
        try:
            await own_redis.delete(key)
        finally:
            await own_redis.aclose()
            await close_redis()
            await engine.dispose()
            await app_postgres.dispose_engine_for_loop()


async def _record_policy(w, db, label: str) -> None:
    from app.services.activity.service import ActorRef, record

    await record(
        db,
        project_id=w.project_id,
        event_type="policy.updated",
        actor=ActorRef.from_user(w.user),
        entity_id=uuid.uuid4(),
        entity_label=f"{label} {w.tag}",
        changed_fields=["threshold"],
    )


async def _stream(w) -> list[tuple[str, dict]]:
    await w.live.drain()
    return [(entry_id, json.loads(fields["payload"])) for entry_id, fields in await w.redis.xrange(w.key)]


async def _feed(w) -> tuple[list[dict], str]:
    response = await w.http.get(f"/api/v1/projects/{w.project_id}/activity", headers=w.auth)
    assert response.status_code == 200, response.text
    return response.json()["items"], response.text


async def test_a_committed_row_streams_byte_identical_to_the_feeds_item(world):
    w = world
    async with w.sessions() as db:
        await _record_policy(w, db, "Strict gate")
        await db.commit()

    await w.live.drain()
    ((entry_id, fields),) = await w.redis.xrange(w.key)
    (item,), body = await _feed(w)
    assert fields["payload"] == w.live.encode_payload(item)
    assert fields["payload"] in body, "the stored string IS the feed response's own bytes"
    assert json.loads(fields["payload"]) == item and "diff" not in item
    assert 86_000 < await w.redis.ttl(w.key) <= 86_400


async def test_a_rolled_back_transaction_streams_nothing(world):
    w = world
    async with w.sessions() as db:
        await _record_policy(w, db, "Rolled back")
        await db.rollback()
    assert await _stream(w) == []
    assert (await _feed(w))[0] == []


async def test_a_savepoint_rollback_streams_only_the_row_outside_it(world):
    w = world
    async with w.sessions() as db:
        await _record_policy(w, db, "Outside")
        savepoint = await db.begin_nested()
        await _record_policy(w, db, "Inside")
        await db.flush()
        await savepoint.rollback()
        await db.commit()
    streamed = [payload["entity"]["label"] for _, payload in await _stream(w)]
    assert streamed == [f"Outside {w.tag}"]
    assert [i["entity"]["label"] for i in (await _feed(w))[0]] == streamed


async def test_an_attempt_row_streams_after_its_own_commit(world):
    from app.services.activity.service import ActorRef, record

    w = world
    await record(
        None,
        project_id=w.project_id,
        event_type="project.reset",
        actor=ActorRef.from_user(w.user),
        entity_id=w.project_id,
        entity_label=f"Live {w.tag}",
        context={"mode": "full"},
    )
    ((_, payload),) = await _stream(w)
    (item,), _ = await _feed(w)
    assert payload == item and payload["event_type"] == "project.reset"


async def test_an_unreachable_redis_never_fails_the_commit(world, monkeypatch):
    import redis.asyncio as redis_asyncio

    from app.core import metrics
    from app.models.postgres import ProjectActivityEvent

    w = world
    dead = redis_asyncio.Redis.from_url(
        "redis://127.0.0.1:1/0", decode_responses=True, socket_connect_timeout=0.5
    )
    monkeypatch.setattr("app.services.activity.live.get_redis", lambda: dead)
    counter = metrics.activity_live_published_total.labels(result="dropped_error")
    before = counter._value.get()
    try:
        async with w.sessions() as db:
            await _record_policy(w, db, "Redis down")
            await db.commit()
        await w.live.drain()
    finally:
        await dead.aclose()

    async with w.sessions() as db:
        rows = (await db.execute(
            select(ProjectActivityEvent).where(ProjectActivityEvent.project_id == w.project_id)
        )).scalars().all()
    assert [r.entity_label for r in rows] == [f"Redis down {w.tag}"], "the mutation landed"
    assert counter._value.get() - before == 1
    assert await w.redis.xrange(w.key) == []
