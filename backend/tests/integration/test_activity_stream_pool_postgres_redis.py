"""An open activity stream holds NO pooled Postgres connection (W21d ACT-02 §2.2, §0.16).

The production pool is 2 + 1. A stream that kept its request session for 15
minutes would take one connection per open tab, and three tabs would starve
every other request into the 30 s pool timeout. The handler gives the guard
chain's connection back (``release_read_connection``) before the response
starts, and the generator never touches Postgres.

Proven on a REAL engine with ONE connection (``pool_size=1, max_overflow=0``),
the app's ``get_db`` / ``AsyncSessionLocal`` re-pointed at it, the stream opened
as RAW ASGI (httpx's ``ASGITransport`` and Starlette's ``TestClient`` buffer a
streaming body until it ends): while the stream is open,
``engine.pool.checkedout() == 0``, a second request on that one-connection pool
answers, and a row recorded in another session arrives as an ``activity``
frame within 3 s. ``MAX_STREAM_SECONDS`` is patched to 5 so the stream ends by
itself. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` (at head) and ``REDIS_URL``.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi import Request
from sqlalchemy import delete
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
    engine = create_async_engine(dsn, pool_size=1, max_overflow=0, pool_timeout=3)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    own_redis = redis_asyncio.Redis.from_url(redis_url, decode_responses=True)

    deferred: list = []

    async def _get_db(request: Request):
        """``get_db``'s shape on the one-connection pool -- with ONE difference.

        FastAPI 0.115.5 runs a yield dependency's exit before a streaming body
        starts; 0.118+ runs it AFTER the response. For the stream route this
        override defers its exit (the commit and the close) the newer way, so
        the only thing that can give the connection back while the stream is
        open is the handler's own ``release_read_connection`` -- what the
        assertions below pin, on any FastAPI.
        """
        session = sessions()
        try:
            yield session
        except Exception:
            await session.rollback()
            await session.close()
            raise
        if request.url.path.endswith("/activity/stream"):
            deferred.append(session)
            return
        try:
            await session.commit()
        finally:
            await session.close()

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _get_db
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)
    monkeypatch.setattr("app.services.activity.live.get_redis", lambda: own_redis)
    monkeypatch.setattr("app.services.activity.live_vocab.MAX_STREAM_SECONDS", 5)
    monkeypatch.setattr(live, "PUBLISH_ENABLED", True)
    live.SLOTS.clear()

    tag = "t" + uuid.uuid4().hex[:9]
    project_id, member_id, outsider_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with sessions.begin() as db:
        db.add(Project(id=project_id, name=f"Pool {tag}", slug=f"pool-{tag}", is_active=True))
        for uid, label in ((member_id, "member"), (outsider_id, "outsider")):
            db.add(User(id=uid, email=f"pool-{label}-{tag}@example.com", username=f"pool_{label}_{tag}",
                        full_name=f"Pool {label}", hashed_password="!unusable",
                        role=UserRole.QA_ENGINEER.value))
        await db.flush()
        db.add(ProjectMember(project_id=project_id, user_id=member_id, role=UserRole.QA_ENGINEER.value))
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        async with sessions() as db:
            member = await db.get(User, member_id)
        yield SimpleNamespace(
            app=app, engine=engine, sessions=sessions, http=http, live=live, tag=tag,
            project_id=project_id, member=member, member_id=member_id,
            member_token=create_access_token(str(member_id)),
            outsider_token=create_access_token(str(outsider_id)),
            key=f"testlookup:stream:activity:{project_id}",
        )
    finally:
        await http.aclose()
        live.SLOTS.clear()
        for session in deferred:  # the deferred exits, after every stream ended
            await session.close()
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous
        async with sessions.begin() as db:
            await db.execute(delete(ProjectActivityEvent).where(ProjectActivityEvent.project_id == project_id))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.execute(delete(User).where(User.id.in_([member_id, outsider_id])))
        try:
            await own_redis.delete(f"testlookup:stream:activity:{project_id}")
        finally:
            await own_redis.aclose()
            await close_redis()
            await engine.dispose()
            await app_postgres.dispose_engine_for_loop()


class _RawStream:
    """One GET as raw ASGI: ``send`` collects, ``receive`` disconnects on demand."""

    def __init__(self, app, path: str, token: str) -> None:
        self.messages: list[dict] = []
        self._hang_up = asyncio.Event()
        self._asked = False
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
            "query_string": b"", "root_path": "",
            "headers": [
                (b"host", b"testserver"),
                (b"authorization", f"Bearer {token}".encode()),
                (b"accept", b"text/event-stream"),
            ],
            "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
        }
        self.task = asyncio.create_task(app(scope, self._receive, self._send))

    async def _receive(self) -> dict:
        if not self._asked:
            self._asked = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._hang_up.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: dict) -> None:
        self.messages.append(message)

    @property
    def status(self) -> int | None:
        starts = [m for m in self.messages if m["type"] == "http.response.start"]
        return starts[0]["status"] if starts else None

    @property
    def body(self) -> str:
        return b"".join(m.get("body", b"") for m in self.messages if m["type"] == "http.response.body").decode()

    async def wait_for(self, needle: str, timeout: float) -> float:
        started = time.monotonic()
        while needle not in self.body:
            if self.task.done() or time.monotonic() - started > timeout:
                raise AssertionError(f"{needle!r} not streamed within {timeout}s; body={self.body!r}")
            await asyncio.sleep(0.02)
        return time.monotonic() - started

    async def close(self) -> None:
        self._hang_up.set()
        await asyncio.wait_for(self.task, timeout=10)


def _path(w) -> str:
    return f"/api/v1/projects/{w.project_id}/activity/stream"


async def test_an_open_stream_holds_no_connection_and_a_new_row_arrives_within_3s(world):
    from app.services.activity.service import ActorRef, record

    w = world
    stream = _RawStream(w.app, _path(w), w.member_token)
    try:
        await stream.wait_for("event: ready", timeout=5)
        assert stream.status == 200
        # THE pool assertion: the guard chain's connection went back before the first byte.
        assert w.engine.pool.checkedout() == 0
        # ... so a second request on the same ONE-connection pool is served (pool_timeout=3).
        feed = await w.http.get(
            f"/api/v1/projects/{w.project_id}/activity",
            headers={"Authorization": f"Bearer {w.member_token}"},
        )
        assert feed.status_code == 200, feed.text

        label = f"Gate {w.tag}"
        async with w.sessions() as db:
            await record(
                db, project_id=w.project_id, event_type="policy.updated",
                actor=ActorRef.from_user(w.member), entity_id=uuid.uuid4(),
                entity_label=label, changed_fields=["threshold"],
            )
            await db.commit()
        waited = await stream.wait_for(label, timeout=3)
        assert waited < 3
        assert w.engine.pool.checkedout() == 0, "still nothing held while streaming"
    finally:
        await stream.close()

    frames = [f for f in stream.body.split("\n\n") if f.startswith("event: activity")]
    (frame,) = frames
    data = json.loads(frame.split("data: ", 1)[1])
    assert data["entity"]["label"] == label and data["project_id"] == str(w.project_id)
    assert w.live.SLOTS.held(w.member_id) == 0, "the finished stream gave its slot back"


async def test_the_stream_ends_by_itself_at_its_lifetime(world):
    w = world
    stream = _RawStream(w.app, _path(w), w.member_token)
    await stream.wait_for("event: ready", timeout=5)
    ready = json.loads(stream.body.split("event: ready", 1)[1].split("data: ", 1)[1].split("\n", 1)[0])
    assert ready["max_seconds"] == 5
    await asyncio.wait_for(stream.task, timeout=10)
    assert stream.body.rstrip().endswith('data: {"reason":"max_age"}')
    assert w.engine.pool.checkedout() == 0


async def test_a_non_member_is_refused_by_the_guard(world):
    w = world
    response = await w.http.get(_path(w), headers={"Authorization": f"Bearer {w.outsider_token}"})
    assert response.status_code == 403
    assert response.json() == {"detail": "You do not have access to this project"}
    assert w.engine.pool.checkedout() == 0
