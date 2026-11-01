"""W25e E-1 T-A2 — the immediate publish of a closed run's persist on real PostgreSQL.

``publish_staged_now`` publishes a just-committed close's ``persist_live_session``
intent at once; the relay stays the safety net. Pinned here against PostgreSQL:
one publication per claim; a broker failure leaves a relay-recoverable
``sending`` row with the SAME token and no dispatch failure; the relay and the
immediate path racing publish once; a held row lock is skipped, never waited
for; a rolled-back close stages nothing; and the stale-session reaper survives
the publish's rollback expiring its rows (D-A1).

Own engine (``pool_size=2, max_overflow=0``), own project per test, every row
removed in ``finally`` through the project cascade (CI runs every
``*_postgres.py`` module in ONE database). Never the app engine.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import postgres as app_postgres
from app.models.postgres import (
    LiveSession,
    Project,
    RunDownstreamOutbox,
    TestRun as DbTestRun,
)

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def _delete_projects(db, *project_ids: uuid.UUID) -> None:
    """SQL DELETE so the database cascades own the cleanup (runs, outbox rows, sessions)."""
    await db.execute(delete(Project).where(Project.id.in_(project_ids)))


@pytest.fixture
async def pg():
    """The test's own engine + sessionmaker; the app engine is never bound to this loop."""
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    created: list[uuid.UUID] = []
    try:
        yield SimpleNamespace(sessions=sessions, created=created)
    finally:
        if created:
            async with sessions() as db:
                await _delete_projects(db, *created)
                await db.commit()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _project_and_run(pg) -> tuple[uuid.UUID, uuid.UUID]:
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    pg.created.append(project_id)
    async with pg.sessions() as db:
        db.add(
            Project(
                id=project_id,
                name=f"w25e-now-{project_id.hex}",
                slug=f"w25e-now-{project_id.hex}",
            )
        )
        await db.flush()
        db.add(_run_row(project_id, run_id))
        await db.commit()
    return project_id, run_id


def _run_row(project_id: uuid.UUID, run_id: uuid.UUID) -> DbTestRun:
    return DbTestRun(
        id=run_id,
        project_id=project_id,
        build_number=f"w25e-now-{run_id.hex}",
        status="PASSED",
        total_tests=0,
        passed_tests=0,
        failed_tests=0,
        skipped_tests=0,
        broken_tests=0,
        unknown_tests=0,
    )


async def _stage_persist(db, *, project_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Stage exactly what ``stream_service.close_session`` stages (caller commits)."""
    from app.services.run_downstream_outbox import stage_live_persist_operation

    assert await stage_live_persist_operation(
        db,
        canonical_run_id=run_id,
        session=SimpleNamespace(
            id=run_id,
            project_id=project_id,
            build_number="b-7",
            client_name="zz-client-q",
            framework="pytest",
            branch="main",
            commit_hash="",
            suite_name="smoke",
            completed_at=None,
        ),
        final_state={"total": 2, "passed": 2},
    )


async def _persist_rows(pg, run_id: uuid.UUID) -> list[RunDownstreamOutbox]:
    # Closing the session (no rollback) detaches the rows with their loaded
    # values; a rollback would expire them.
    async with pg.sessions() as db:
        rows = (
            await db.execute(
                select(RunDownstreamOutbox).where(
                    RunDownstreamOutbox.run_id == run_id,
                    RunDownstreamOutbox.operation == "persist_live_session",
                )
            )
        ).scalars().all()
        return list(rows)


def _recorder(published: list[tuple[uuid.UUID, uuid.UUID]]):
    def _publish(row: RunDownstreamOutbox) -> None:
        published.append((row.id, row.dispatch_token))

    return _publish


async def test_a_staged_persist_is_published_once_with_its_token(pg, monkeypatch):
    """The REAL relay publisher (``_publish_downstream``) runs in the worker thread
    on the claimed ORM row; only the Celery task at the broker boundary is faked."""
    from app.services import run_downstream_outbox as outbox
    from app.worker.downstream_dispatch import publish_staged_now
    from app.worker.ingestion_routing import queue_for_project

    project_id, run_id = await _project_and_run(pg)
    async with pg.sessions() as db:
        await _stage_persist(db, project_id=project_id, run_id=run_id)
        await db.commit()
    sent: list[dict] = []
    monkeypatch.setattr(
        outbox,
        "downstream_tasks",
        lambda: {"persist_live_session": SimpleNamespace(apply_async=lambda **kw: sent.append(kw))},
    )

    async with pg.sessions() as db:
        assert await publish_staged_now(db, run_id=run_id) == "published"
    async with pg.sessions() as db:
        assert await publish_staged_now(db, run_id=str(run_id)) == "nothing_pending"

    [row] = await _persist_rows(pg, run_id)
    assert row.status == "published"
    assert row.published_at is not None
    assert row.attempts == 1 and row.dispatch_failures == 0
    assert len(sent) == 1, "exactly one publication"
    [message] = sent
    assert message["task_id"] == f"run-downstream-{row.id}"
    assert message["headers"] == {
        "downstream_outbox_id": str(row.id),
        "downstream_dispatch_token": str(row.dispatch_token),
    }
    assert message["queue"] == queue_for_project(str(project_id))
    assert message["priority"] == 7
    assert message["kwargs"]["run_id"] == str(run_id)


async def test_a_consumer_that_begins_before_the_mark_keeps_its_completion_fence(
    pg, monkeypatch
):
    """W25e demo regression: under API pool pressure the mark ran ~0.6 s after the
    publish, the ingest worker had already begun (row 'processing'), and the mark —
    on the claim's own session, whose ORM object still said 'sending' — overwrote
    it to 'published'; the worker's completion fence was lost and the relay re-ran
    the persist 300 s later. The mark must read the locked row's current state."""
    from app.services import run_downstream_outbox as outbox
    from app.worker.downstream_dispatch import publish_staged_now

    project_id, run_id = await _project_and_run(pg)
    async with pg.sessions() as db:
        await _stage_persist(db, project_id=project_id, run_id=run_id)
        await db.commit()
    published: list[tuple[uuid.UUID, uuid.UUID]] = []
    monkeypatch.setattr(outbox, "AsyncSessionLocal", pg.sessions)
    monkeypatch.setattr(outbox, "_publish_downstream", _recorder(published))
    real_mark = outbox.mark_downstream_dispatch_published

    async def _fast_consumer_then_mark(db, *, outbox_id, dispatch_token):
        # The worker received the message and began before the API's mark ran.
        assert await outbox.begin_downstream_execution(
            outbox_id=outbox_id, dispatch_token=dispatch_token, task_id="zz-fast-consumer-q"
        )
        return await real_mark(db, outbox_id=outbox_id, dispatch_token=dispatch_token)

    monkeypatch.setattr(outbox, "mark_downstream_dispatch_published", _fast_consumer_then_mark)
    async with pg.sessions() as db:
        assert await publish_staged_now(db, run_id=run_id) == "published"

    [row] = await _persist_rows(pg, run_id)
    assert row.status == "processing", "the mark must not overwrite a consumer that began"
    assert published == [(row.id, row.dispatch_token)]
    assert await outbox.complete_downstream_execution(
        outbox_id=row.id, dispatch_token=row.dispatch_token
    ), "the consumer's completion fence holds"
    [done] = await _persist_rows(pg, run_id)
    assert done.status == "completed" and done.execution_attempts == 1 and done.attempts == 1


async def test_a_broker_failure_leaves_a_relay_recoverable_row_with_the_same_token(
    pg, monkeypatch
):
    from app.services import run_downstream_outbox as outbox
    from app.worker.downstream_dispatch import publish_staged_now

    project_id, run_id = await _project_and_run(pg)
    async with pg.sessions() as db:
        await _stage_persist(db, project_id=project_id, run_id=run_id)
        await db.commit()

    def _broker_down(_row):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(outbox, "_publish_downstream", _broker_down)
    before = datetime.now(timezone.utc)
    async with pg.sessions() as db:
        assert await publish_staged_now(db, run_id=run_id) == "deferred"

    [row] = await _persist_rows(pg, run_id)
    assert row.status == "sending"
    assert row.dispatch_failures == 0, "a broker timeout is not a failure"
    assert row.dispatch_token is not None
    assert before < row.lease_expires_at <= datetime.now(timezone.utc) + timedelta(
        seconds=outbox.IMMEDIATE_LEASE_SECONDS
    )
    token = row.dispatch_token

    # The relay's first tick after the lease expires republishes it, same token.
    async with pg.sessions() as db:
        await db.execute(
            update(RunDownstreamOutbox)
            .where(RunDownstreamOutbox.id == row.id)
            .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        await db.commit()
    published: list[tuple[uuid.UUID, uuid.UUID]] = []
    monkeypatch.setattr(outbox, "AsyncSessionLocal", pg.sessions)
    monkeypatch.setattr(outbox, "_publish_downstream", _recorder(published))

    assert await outbox.relay_downstream_outbox(limit=20, max_batches=1) == {
        "claimed": 1,
        "published": 1,
        "failed": 0,
    }
    assert published == [(row.id, token)]

    # Exactly once: the first delivery is admitted, a second copy is fenced.
    assert await outbox.begin_downstream_execution(
        outbox_id=row.id, dispatch_token=token, task_id="zz-first-q"
    )
    assert await outbox.complete_downstream_execution(outbox_id=row.id, dispatch_token=token)
    assert not await outbox.begin_downstream_execution(
        outbox_id=row.id, dispatch_token=token, task_id="zz-second-q"
    )
    [done] = await _persist_rows(pg, run_id)
    assert done.status == "completed" and done.execution_attempts == 1


async def test_the_relay_and_the_immediate_publish_racing_publish_once(pg, monkeypatch):
    from app.services import run_downstream_outbox as outbox
    from app.worker.downstream_dispatch import publish_staged_now

    project_id, run_id = await _project_and_run(pg)
    async with pg.sessions() as db:
        await _stage_persist(db, project_id=project_id, run_id=run_id)
        await db.commit()
    published: list[tuple[uuid.UUID, uuid.UUID]] = []
    monkeypatch.setattr(outbox, "AsyncSessionLocal", pg.sessions)
    monkeypatch.setattr(outbox, "_publish_downstream", _recorder(published))

    async def _immediate() -> str:
        async with pg.sessions() as db:
            return await publish_staged_now(db, run_id=run_id)

    relay, immediate = await asyncio.gather(
        outbox.relay_downstream_outbox(limit=20, max_batches=1), _immediate()
    )

    [row] = await _persist_rows(pg, run_id)
    assert len(published) == 1, (relay, immediate, published)
    assert published == [(row.id, row.dispatch_token)]
    assert row.status == "published" and row.attempts == 1
    assert (relay["published"], immediate) in {(1, "nothing_pending"), (0, "published")}


async def test_a_row_another_transaction_holds_is_skipped_not_waited_for(pg, monkeypatch):
    from app.services import run_downstream_outbox as outbox
    from app.worker.downstream_dispatch import publish_staged_now

    project_id, run_id = await _project_and_run(pg)
    async with pg.sessions() as db:
        await _stage_persist(db, project_id=project_id, run_id=run_id)
        await db.commit()
    published: list[tuple[uuid.UUID, uuid.UUID]] = []
    monkeypatch.setattr(outbox, "_publish_downstream", _recorder(published))

    async with pg.sessions() as holder:
        await holder.execute(
            select(RunDownstreamOutbox.id)
            .where(RunDownstreamOutbox.run_id == run_id)
            .with_for_update()
        )
        async with pg.sessions() as db:
            outcome = await asyncio.wait_for(publish_staged_now(db, run_id=run_id), timeout=10)
        await holder.rollback()

    assert outcome == "nothing_pending"
    assert published == []
    [row] = await _persist_rows(pg, run_id)
    assert row.status == "pending" and row.attempts == 0


async def test_a_close_that_rolls_back_stages_nothing_to_publish(pg, monkeypatch):
    from app.services import run_downstream_outbox as outbox
    from app.worker.downstream_dispatch import publish_staged_now

    project_id, run_id = await _project_and_run(pg)
    async with pg.sessions() as db:
        await _stage_persist(db, project_id=project_id, run_id=run_id)
        await db.rollback()
    publish = AsyncMock()
    monkeypatch.setattr(outbox, "_publish_downstream", publish)

    async with pg.sessions() as db:
        assert await publish_staged_now(db, run_id=run_id) == "nothing_pending"

    assert await _persist_rows(pg, run_id) == []
    publish.assert_not_called()


async def test_the_reaper_survives_publishes_that_defer_or_find_nothing(pg, monkeypatch):
    """D-A1 on real SQLAlchemy. The first close stages a persist whose publish
    defers (broker down); the later closes stage nothing, so their publish rolls
    back the claim's open transaction — which expires every LiveSession the sweep
    loaded. The sweep must still close them all (the loop reads its rows up front)."""
    import app.db.postgres as pg_module
    from app.services import cache_service, stream_service
    from app.services import run_downstream_outbox as outbox
    from app.worker import tasks

    project_id = uuid.uuid4()
    pg.created.append(project_id)
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    session_ids = [uuid.uuid4() for _ in range(3)]
    async with pg.sessions() as db:
        db.add(
            Project(id=project_id, name=f"w25e-reap-{project_id.hex}", slug=f"w25e-reap-{project_id.hex}")
        )
        await db.flush()
        for index, session_id in enumerate(session_ids):
            db.add(_run_row(project_id, session_id))
            db.add(
                LiveSession(
                    id=session_id,
                    project_id=project_id,
                    run_id=f"w25e-reap-{index}-{session_id.hex[:8]}",
                    client_name="zz-client-q",
                    session_token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                    status="active",
                    started_at=old,
                )
            )
        await db.commit()

    staged: list[uuid.UUID] = []

    async def _close(db, session_id, **_k):
        # Stand-in for stream_service.close_session (whose Redis half is out of
        # scope here): a real ORM write on the sweep's own session, plus the
        # same persist staging for the first close only.
        live = await db.get(LiveSession, uuid.UUID(session_id))
        live.status = "completed"
        if not staged:
            await _stage_persist(db, project_id=live.project_id, run_id=live.id)
            staged.append(live.id)
        return live.project_id

    def _broker_down(_row):
        raise ConnectionError("broker unavailable")

    mine = {str(session_id) for session_id in session_ids}

    async def _state(key):
        # Only this test's sessions look idle: any other active session in the
        # shared database is "recent" and left untouched.
        if key in mine:
            return None
        return {"last_event_at": datetime.now(timezone.utc).isoformat()}

    running_loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        tasks,
        "_run_async",
        lambda coro: asyncio.run_coroutine_threadsafe(coro, running_loop).result(),
    )
    monkeypatch.setattr(pg_module, "AsyncSessionLocal", pg.sessions)
    monkeypatch.setattr("app.streams.live_run_state.RedisLiveRunState.get", _state)
    monkeypatch.setattr(stream_service, "close_session", _close)
    monkeypatch.setattr(stream_service, "finalize_closed_session_redis", AsyncMock())
    monkeypatch.setattr(cache_service, "bump_analytics_epochs", AsyncMock())
    monkeypatch.setattr(outbox, "_publish_downstream", _broker_down)

    result = await asyncio.to_thread(tasks.close_stale_live_sessions.run, idle_minutes=5)

    assert result["closed"] == 3 and result["errors"] == 0, result
    async with pg.sessions() as db:
        statuses = (
            await db.execute(
                select(LiveSession.status).where(LiveSession.project_id == project_id)
            )
        ).scalars().all()
        rows = (
            await db.execute(
                select(RunDownstreamOutbox).where(RunDownstreamOutbox.project_id == project_id)
            )
        ).scalars().all()
        assert await db.scalar(
            select(func.count()).select_from(RunDownstreamOutbox).where(
                RunDownstreamOutbox.project_id == project_id,
                RunDownstreamOutbox.status == "failed",
            )
        ) == 0
    assert sorted(statuses) == ["completed"] * 3
    assert [(row.run_id, row.status, row.dispatch_failures) for row in rows] == [
        (staged[0], "sending", 0)
    ], "the deferred persist waits for the relay with its lease; nothing failed"
