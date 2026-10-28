"""A worker task's last-statement commit still reaches the activity live tail (W21d §2.1).

An outcome row's publish is a task its session's ``after_commit`` schedules. A
run a worker finalizes commits as its LAST statement, and ``run_async`` cancels
every task left pending the moment the task's coroutine returns -- so before
the settle, every worker-produced row (``run.completed``) was cancelled before
it reached Redis, while the API's rows streamed fine. ``run_async`` (and
``shutdown_worker_loop``) now let in-flight publishes finish, bounded at
``SETTLE_TIMEOUT_SECONDS``, before they cancel stragglers; with nothing in
flight the settle is one dict lookup and never runs the loop.

Driven through the REAL ``loop_runner.run_async`` with a real ``AsyncSession``
(in-memory SQLite) and a Redis recorder whose pipeline takes loop time, so the
publish cannot finish by accident before the stragglers are cancelled.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.postgres import Base, Project, ProjectActivityEvent, User
from app.services.activity import live
from app.services.activity.service import ActorRef, record
from app.worker import loop_runner

pytestmark = pytest.mark.regression

_TABLES = [Project.__table__, User.__table__, ProjectActivityEvent.__table__]


class _SlowRecorder:
    """A pipeline that needs the loop for a moment, like a real round trip."""

    def __init__(self) -> None:
        self.payloads: list[str] = []

    def pipeline(self, transaction=True):
        recorder = self
        ops: list[str] = []

        class _Pipe:
            def xadd(self, key, fields, maxlen=None, approximate=True):
                ops.append(fields["payload"])
                return self

            def expire(self, key, seconds):
                return self

            async def execute(self):
                await asyncio.sleep(0.05)
                recorder.payloads.extend(ops)
                return []

        return _Pipe()


@pytest.fixture(autouse=True)
def _quiet_drains(monkeypatch):
    """The teardown drains are pinned elsewhere; here they must not reach out."""
    import app.core.http_client as http
    import app.db.mongo as mongo
    import app.db.postgres as pg
    import app.db.redis_client as redis_client

    async def _noop():
        return None

    monkeypatch.setattr(pg, "dispose_engine_for_loop", _noop)
    monkeypatch.setattr(http, "close_http_client", _noop)
    monkeypatch.setattr(redis_client, "close_redis", _noop)
    monkeypatch.setattr(mongo, "close_mongo", _noop)
    loop_runner.shutdown_worker_loop()
    yield
    loop_runner.shutdown_worker_loop()


@pytest.fixture
def recorder(monkeypatch):
    fake = _SlowRecorder()
    monkeypatch.setattr("app.services.activity.live.get_redis", lambda: fake)
    monkeypatch.setattr(live, "PUBLISH_ENABLED", True)
    return fake


@pytest.fixture
def world():
    """An engine and a project built ON the worker loop (it is reused)."""
    state: dict = {}

    async def _build():
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        project = Project(id=uuid.uuid4(), name="Payments", slug=f"pay-{uuid.uuid4().hex[:8]}")
        async with factory() as s:
            s.add(project)
            await s.commit()
        state.update(engine=engine, factory=factory, project_id=project.id)

    loop_runner.run_async(_build())
    yield state
    loop_runner.run_async(state["engine"].dispose())


def _finalize_task(world) -> object:
    """A task body whose commit is its LAST statement (``finalize_run``'s shape)."""

    async def _task():
        async with world["factory"]() as db:
            await record(
                db,
                project_id=world["project_id"],
                event_type="run.completed",
                actor=ActorRef.system("worker"),
                entity_id=uuid.uuid4(),
                entity_label="nightly #42",
                context={"failed": 2, "total": 40},
            )
            await db.commit()

    return _task()


def test_a_commit_as_the_tasks_last_statement_still_publishes(world, recorder):
    loop_runner.run_async(_finalize_task(world))

    assert len(recorder.payloads) == 1, "the worker's publish was cancelled unsent"
    assert '"event_type":"run.completed"' in recorder.payloads[0]
    assert live.inflight_count(loop_runner._worker_loop()) == 0


def test_worker_shutdown_settles_before_it_cancels(monkeypatch):
    """``run_async`` and ``shutdown_worker_loop`` both settle BEFORE they cancel."""
    seen: list[str] = []
    real_cancel = loop_runner._cancel_stragglers

    def _spy_cancel(loop):
        seen.append("cancel")
        return real_cancel(loop)

    def _spy_settle(loop):
        seen.append("settle")

    monkeypatch.setattr(loop_runner, "_cancel_stragglers", _spy_cancel)
    monkeypatch.setattr(loop_runner, "_settle_activity_publishes", _spy_settle)
    loop_runner.run_async(asyncio.sleep(0))
    assert seen == ["settle", "cancel"]
    seen.clear()
    loop_runner.shutdown_worker_loop()
    assert seen == ["settle", "cancel"]


def test_with_nothing_in_flight_the_settle_never_runs_the_loop(monkeypatch):
    calls: list[str] = []

    async def _spy_drain(timeout=0.0):
        calls.append("drain")

    monkeypatch.setattr(live, "drain", _spy_drain)
    assert loop_runner.run_async(asyncio.sleep(0, result="done")) == "done"
    assert calls == []


class _LogRecorder:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def warning(self, message, *args):
        self.messages.append(message % args if args else message)


def test_a_failing_settle_is_logged_and_the_task_still_returns(monkeypatch):
    def _broken(loop):
        raise RuntimeError("settle exploded")

    recorder = _LogRecorder()
    monkeypatch.setattr(live, "inflight_count", _broken)
    monkeypatch.setattr(loop_runner, "logger", recorder)
    assert loop_runner.run_async(asyncio.sleep(0, result=7)) == 7
    assert recorder.messages == [
        "worker_loop_activity_settle_failed error=RuntimeError('settle exploded')"
    ]
