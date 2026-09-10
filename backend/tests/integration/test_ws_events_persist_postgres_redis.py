"""A run streamed through ``POST /ws/events`` is persisted like an SDK run (re-audit N14).

The endpoint used to put each event on the shared live stream and nothing else:
no LiveSession, no TestRun, no persistence. A run streamed through it showed on
the live dashboard and then vanished -- it never reached /runs, was never
analysed, never finalised. An event sent with a project-scoped key now goes
through the SDK stream's own ingest path.

These drive the real route against real Postgres and Redis, up to the hand-off
every SDK run reaches when it completes: a completed LiveSession, its TestRun,
and a staged ``persist_live_session`` operation. What happens after that
hand-off is the SDK path's own, and is tested where it lives.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and ``REDIS_URL``, with the database
migrated to head.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


def _slug() -> str:
    return f"ws-n14-{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def infra(monkeypatch):
    redis_asyncio = pytest.importorskip("redis.asyncio")
    client = redis_asyncio.Redis.from_url(_env("REDIS_URL"), decode_responses=True)
    engine = create_async_engine(
        _env("TESTLOOKUP_POSTGRES_TEST_DSN"), pool_size=2, max_overflow=0
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    for target in (
        "app.db.redis_client.get_redis",
        "app.streams.live_run_state.get_redis",
        "app.services.stream_service.get_redis",
    ):
        monkeypatch.setattr(target, lambda: client, raising=False)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)

    class _Coll:
        async def insert_one(self, _doc):
            return None

    monkeypatch.setattr(
        "app.db.mongo.get_mongo_db", lambda: {"live_execution_events": _Coll()}
    )
    from app.core.config import settings

    monkeypatch.setattr(settings, "LIVE_EVENTS_REQUIRE_PROJECT_KEY", True)

    from app.models.postgres import Project, RunDownstreamOutbox, TestRun

    project_id = uuid.uuid4()
    async with sessions() as db:
        db.add(Project(
            id=project_id, name="n14", slug=f"n14-{project_id.hex}", is_active=True,
        ))
        await db.commit()
    # The credential is someone else's test; here the key simply names the project.
    monkeypatch.setattr(
        "app.services.live_event_authz.cached_streaming_project",
        AsyncMock(return_value=str(project_id)),
    )

    yield SimpleNamespace(client=client, engine=engine, sessions=sessions, project=project_id)

    async with sessions() as db:
        runs = select(TestRun.id).where(TestRun.project_id == project_id)
        await db.execute(delete(RunDownstreamOutbox).where(RunDownstreamOutbox.run_id.in_(runs)))
        await db.execute(delete(Project).where(Project.id == project_id))
        await db.commit()
    await client.aclose()
    await engine.dispose()


async def _post(infra, run_id: str, event: dict) -> dict:
    from app.routers.live import ingest_live_event

    async with infra.sessions() as db:
        result = await ingest_live_event(
            run_id=run_id, event=event, x_api_key="qai_key", x_webhook_secret=None, db=db,
        )
        await db.commit()  # what get_db does when the request ends
    return result


async def test_a_ws_events_run_is_persisted_like_an_sdk_run(infra):
    from app.models.postgres import LiveSession, RunDownstreamOutbox, TestRun
    from app.services.ws_event_ingest import session_cache_key
    from app.streams.live_run_state import RedisLiveRunState

    slug = _slug()
    started = await _post(infra, slug, {"type": "run_start", "build_number": "42", "total_tests": 3})
    session_id = started["session_id"]
    await _post(infra, slug, {
        "type": "test_result", "test_name": "checkout", "status": "FAILED", "error_message": "boom",
    })
    # Byte-identical results: two executions of one test, not a retry of one POST.
    for _ in range(2):
        await _post(infra, slug, {"type": "test_result", "test_name": "login", "status": "PASSED"})

    state = await RedisLiveRunState.get(session_id)
    assert state is not None, "the run has no live state under its session"
    assert int(state["failed"]) == 1
    assert int(state["passed"]) == 2, (
        "two identical results were merged into one, or one result was counted twice"
    )

    done = await _post(infra, slug, {"type": "run_complete"})
    assert done["session_id"] == session_id

    async with infra.sessions() as db:
        session = (await db.execute(
            select(LiveSession).where(
                LiveSession.project_id == infra.project, LiveSession.run_id == slug,
            )
        )).scalar_one()
        run = await db.get(TestRun, session.id)
        staged = (await db.execute(
            select(RunDownstreamOutbox).where(
                RunDownstreamOutbox.run_id == session.id,
                RunDownstreamOutbox.operation == "persist_live_session",
            )
        )).scalars().all()

    assert str(session.id) == session_id
    assert session.status == "completed"
    assert run is not None, "no TestRun: a /ws/events run still vanishes when it completes"
    assert run.project_id == infra.project
    assert run.trigger_source == "live_stream"
    assert len(staged) == 1, "the run's persistence was never staged"
    assert await infra.client.get(session_cache_key(infra.project, slug)) is None, (
        "the session stayed cached after the run completed"
    )


async def test_after_its_first_event_a_result_costs_no_database_round_trip(infra):
    """The per-event database load the credential cache exists to avoid."""
    slug = _slug()
    await _post(infra, slug, {"type": "run_start", "build_number": "1"})

    seen: list[str] = []

    def _before(_conn, _cursor, statement, *_rest):
        seen.append(statement)

    sa_event.listen(infra.engine.sync_engine, "before_cursor_execute", _before)
    try:
        await _post(infra, slug, {"type": "test_result", "test_name": "t", "status": "PASSED"})
    finally:
        sa_event.remove(infra.engine.sync_engine, "before_cursor_execute", _before)

    assert seen == [], f"one live result ran {len(seen)} SQL statements: {seen[:3]}"


async def test_another_projects_key_cannot_append_to_the_run(infra, monkeypatch):
    slug = _slug()
    await _post(infra, slug, {"type": "run_start", "build_number": "1"})

    monkeypatch.setattr(
        "app.services.live_event_authz.cached_streaming_project",
        AsyncMock(return_value=str(uuid.uuid4())),
    )
    with pytest.raises(HTTPException) as exc:
        await _post(infra, slug, {"type": "test_result", "test_name": "t", "status": "PASSED"})
    assert exc.value.status_code == 403


async def test_an_event_after_the_run_completed_is_refused_not_lost(infra):
    """A late result is a 409 the producer sees, not a write into a finished run."""
    slug = _slug()
    await _post(infra, slug, {"type": "run_start", "build_number": "1"})
    await _post(infra, slug, {"type": "run_complete"})

    with pytest.raises(HTTPException) as exc:
        await _post(infra, slug, {"type": "test_result", "test_name": "late", "status": "PASSED"})
    assert exc.value.status_code == 409
