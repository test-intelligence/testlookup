"""The activity live tail publishes a ledger row only AFTER its commit (W21d ACT-02 §2.1).

Real ``AsyncSession`` commits, rollbacks and SAVEPOINTs on in-memory SQLite
(with pysqlite's SAVEPOINT workaround, so a nested rollback is a real one);
Redis is a recorder bound through ``app.services.activity.live.get_redis``.
``tests/conftest.py`` turns publishing OFF for every test; each test here turns
it back on and asserts the POSITIVE (the recorder saw the row, with the feed's
exact shape), so a test that forgot the switch would fail rather than pass.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import metrics
from app.models.postgres import Base, Project, ProjectActivityEvent, User
from app.services.activity import live
from app.services.activity import query as activity_query
from app.services.activity.service import ActorRef, record

_TABLES = [Project.__table__, User.__table__, ProjectActivityEvent.__table__]


class _AwareDateTime(SQLITE_DATETIME):
    """SQLite drops tzinfo; the ledger's rows are UTC-aware (as on Postgres)."""

    def result_processor(self, dialect, coltype):
        inner = super().result_processor(dialect, coltype)

        def process(value):
            parsed = inner(value) if inner is not None else value
            if isinstance(parsed, datetime) and parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed

        return process


def _real_savepoints(sync_engine) -> None:
    """pysqlite's own BEGIN handling breaks SAVEPOINT; emit BEGIN ourselves."""

    @event.listens_for(sync_engine, "connect")
    def _connect(dbapi_connection, _record):
        dbapi_connection.isolation_level = None

    @event.listens_for(sync_engine, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")


@pytest.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, sqltypes.DateTime: _AwareDateTime}
    _real_savepoints(engine.sync_engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def db(session_factory):
    async with session_factory() as session:
        yield session


@pytest.fixture
async def project(session_factory):
    row = Project(id=uuid.uuid4(), name="Auth Service", slug=f"auth-{uuid.uuid4().hex[:8]}")
    async with session_factory() as s:
        s.add(row)
        await s.commit()
    return row


class _Pipeline:
    def __init__(self, redis: "_RecorderRedis") -> None:
        self._redis = redis
        self._ops: list[tuple] = []

    def xadd(self, key, fields, maxlen=None, approximate=True):
        self._ops.append(("xadd", key, dict(fields), maxlen, approximate))
        return self

    def expire(self, key, seconds):
        self._ops.append(("expire", key, seconds))
        return self

    async def execute(self):
        if self._redis.on_execute is not None:
            await self._redis.on_execute()
        self._redis.ops.extend(self._ops)
        return [True] * len(self._ops)


class _RecorderRedis:
    """Records what one pipeline sent; ``on_execute`` can raise or hang."""

    def __init__(self) -> None:
        self.ops: list[tuple] = []
        self.on_execute: Any = None

    def pipeline(self, transaction=True):
        return _Pipeline(self)

    @property
    def xadds(self) -> list[tuple]:
        return [op for op in self.ops if op[0] == "xadd"]


class _LogRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def warning(self, event_name, **kwargs):
        self.events.append((event_name, kwargs))

    info = debug = error = warning


@pytest.fixture
def redis(monkeypatch):
    fake = _RecorderRedis()
    monkeypatch.setattr("app.services.activity.live.get_redis", lambda: fake)
    monkeypatch.setattr(live, "PUBLISH_ENABLED", True)
    return fake


@pytest.fixture
def slog(monkeypatch):
    recorder = _LogRecorder()
    monkeypatch.setattr(live, "_slog", recorder)
    return recorder


def _published(result: str) -> float:
    return metrics.activity_live_published_total.labels(result=result)._value.get()


async def _record_policy(db, project_id, label="Strict gate") -> None:
    await record(
        db,
        project_id=project_id,
        event_type="policy.updated",
        actor=ActorRef("user", actor_name="Marcus"),
        entity_id=uuid.uuid4(),
        entity_label=label,
        changed_fields=["threshold"],
    )


async def _rows(session_factory) -> list[ProjectActivityEvent]:
    async with session_factory() as s:
        result = await s.execute(
            select(ProjectActivityEvent).order_by(ProjectActivityEvent.occurred_at)
        )
        return list(result.scalars().all())


# ── (a) a committed outcome row is published once, with the feed's shape ─────


async def test_a_committed_outcome_row_is_published_once_with_the_feeds_exact_shape(
    db, project, session_factory, redis
):
    await _record_policy(db, project.id)
    assert redis.ops == [], "nothing may reach Redis before the commit"
    await db.commit()
    await live.drain()

    (row,) = await _rows(session_factory)
    (xadd,) = redis.xadds
    _, key, fields, maxlen, approximate = xadd
    assert key == f"testlookup:stream:activity:{project.id}"
    assert (maxlen, approximate) == (1_000, True)
    # Byte-for-byte the feed's item for the same row, as the feed encodes it.
    assert fields == {"payload": live.encode_payload(activity_query.serialize(row))}
    assert json.loads(fields["payload"]) == activity_query.serialize(row)
    assert "diff" not in json.loads(fields["payload"])
    assert ("expire", key, 86_400) in redis.ops


async def test_one_commit_with_two_rows_is_one_pipeline_and_both_rows(
    db, project, session_factory, redis
):
    before = _published("published")
    await _record_policy(db, project.id, label="Gate A")
    await _record_policy(db, project.id, label="Gate B")
    await db.commit()
    await live.drain()

    labels = [json.loads(op[2]["payload"])["entity"]["label"] for op in redis.xadds]
    assert labels == ["Gate A", "Gate B"]
    assert _published("published") - before == 2


# ── (b) a rollback publishes nothing, and a reused session leaks nothing ────


async def test_b_a_rolled_back_session_publishes_nothing(db, project, redis):
    before = _published("skipped_rolled_back")
    await _record_policy(db, project.id, label="Rolled back")
    await db.rollback()
    await live.drain()
    assert redis.ops == []
    assert _published("skipped_rolled_back") - before == 1

    # The same session, reused after the rollback, publishes only its new row.
    await _record_policy(db, project.id, label="Kept")
    await db.commit()
    await live.drain()
    labels = [json.loads(op[2]["payload"])["entity"]["label"] for op in redis.xadds]
    assert labels == ["Kept"]


# ── (c) SAVEPOINTs: a rolled-back savepoint's row is skipped, its sibling kept ─


async def test_c_a_row_inside_a_rolled_back_savepoint_is_not_published(
    db, project, session_factory, redis
):
    before = _published("skipped_rolled_back")
    await _record_policy(db, project.id, label="Outside")
    savepoint = await db.begin_nested()
    await _record_policy(db, project.id, label="Inside")
    await db.flush()
    await savepoint.rollback()
    await db.commit()
    await live.drain()

    assert [r.entity_label for r in await _rows(session_factory)] == ["Outside"]
    labels = [json.loads(op[2]["payload"])["entity"]["label"] for op in redis.xadds]
    assert labels == ["Outside"]
    assert _published("skipped_rolled_back") - before == 1


async def test_a_released_savepoint_does_not_publish_before_the_outer_commit(
    db, project, redis
):
    """SQLAlchemy fires ``after_commit`` for a SAVEPOINT release too."""
    savepoint = await db.begin_nested()
    await _record_policy(db, project.id, label="Released")
    await savepoint.commit()
    await live.drain()
    assert redis.ops == [], "a SAVEPOINT release is not the commit"

    await db.rollback()
    await live.drain()
    assert redis.ops == [], "the outer rollback took the released row with it"


# ── (d) Redis raising or hanging never reaches the mutation ─────────────────


async def test_d_redis_raising_is_logged_counted_and_never_fails_the_commit(
    db, project, session_factory, redis, slog
):
    async def _boom():
        raise ConnectionError("redis is down")

    redis.on_execute = _boom
    before = _published("dropped_error")
    await _record_policy(db, project.id)
    await db.commit()  # returns normally: the publish is a task
    await live.drain()

    assert len(await _rows(session_factory)) == 1
    assert redis.ops == []
    assert _published("dropped_error") - before == 1
    (warning,) = [e for e in slog.events if e[0] == "activity_live_publish_dropped"]
    assert warning[1] == {
        "count": 1,
        "event_types": ["policy.updated"],
        "error_type": "ConnectionError",
    }


async def test_d_a_hanging_redis_is_cut_at_the_publish_timeout(
    db, project, session_factory, redis, slog
):
    async def _hang():
        await asyncio.sleep(30)

    redis.on_execute = _hang
    before = _published("dropped_error")
    await _record_policy(db, project.id)
    started = time.monotonic()
    await db.commit()
    assert time.monotonic() - started < 0.5, "the commit never waits for Redis"
    await live.drain()
    elapsed = time.monotonic() - started
    assert live.PUBLISH_TIMEOUT_SECONDS <= elapsed < live.PUBLISH_TIMEOUT_SECONDS + 1.0
    assert len(await _rows(session_factory)) == 1
    assert _published("dropped_error") - before == 1
    assert [e[1]["error_type"] for e in slog.events] == ["TimeoutError"]


# ── (e) attempt rows: after their own commit, not on a group-key duplicate ───


async def test_e_an_attempt_row_is_published_after_its_own_commit_and_not_twice(
    project, session_factory, redis, monkeypatch
):
    import app.db.postgres as pg

    monkeypatch.setattr(pg, "AsyncSessionLocal", session_factory, raising=False)
    seen_committed: list[int] = []

    async def _check_committed():
        seen_committed.append(len(await _rows(session_factory)))

    redis.on_execute = _check_committed
    for _ in range(2):
        await record(
            None,
            project_id=project.id,
            event_type="project.reset",
            actor=ActorRef("user", actor_name="Sam"),
            entity_id=project.id,
            entity_label="Auth Service",
            context={"mode": "full"},
            group_key="reset-once",
        )

    (row,) = await _rows(session_factory)
    xadds = redis.xadds
    assert len(xadds) == 1, "the group-key duplicate wrote nothing and publishes nothing"
    assert xadds[0][2] == {"payload": live.encode_payload(activity_query.serialize(row))}
    assert seen_committed == [1], "the row was committed before it was published"


# ── (f) an oversize row is skipped, its batch-mates are not ─────────────────


async def test_f_an_oversize_payload_is_skipped_and_counted(redis):
    project_id = str(uuid.uuid4())
    small = {"project_id": project_id, "event_type": "policy.updated", "summary": "small"}
    big = {
        "project_id": project_id,
        "event_type": "policy.updated",
        "context": {"note": "x" * (live.MAX_PAYLOAD_BYTES + 1)},
    }
    before = _published("dropped_oversize")
    assert await live.publish_rows([big, small]) == 1
    (xadd,) = redis.xadds
    assert json.loads(xadd[2]["payload"]) == small
    assert _published("dropped_oversize") - before == 1


# ── (g) the switch: off = no listener, no staged row ─────────────────────────


async def test_g_publishing_off_installs_no_listener_and_stages_nothing(
    db, project, redis, monkeypatch
):
    monkeypatch.setattr(live, "PUBLISH_ENABLED", False)
    await _record_policy(db, project.id)
    assert not event.contains(db.sync_session, "after_commit", live._on_commit)
    assert "activity.live.pending" not in db.sync_session.info
    await db.commit()
    await live.drain()
    assert redis.ops == []

    monkeypatch.setattr(live, "PUBLISH_ENABLED", True)
    await _record_policy(db, project.id)
    assert event.contains(db.sync_session, "after_commit", live._on_commit)
    assert event.contains(db.sync_session, "after_soft_rollback", live._on_rollback)


async def test_a_fake_session_is_a_silent_no_op(redis):
    for fake in (SimpleNamespace(add=lambda row: None), SimpleNamespace(info="x")):
        live.stage(fake, object())  # never raises, never touches Redis
    assert redis.ops == []


def test_a_commit_with_no_running_loop_drops_and_counts(monkeypatch):
    """A synchronous commit (no event loop) cannot schedule a publish."""
    monkeypatch.setattr(live, "PUBLISH_ENABLED", True)
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=_TABLES)
    before = _published("dropped_no_loop")
    with Session(engine) as session:
        row = _sync_row(session)
        live.stage(session, row)
        session.commit()
    assert _published("dropped_no_loop") - before == 1
    engine.dispose()


def _sync_row(session: Session) -> ProjectActivityEvent:
    project = Project(id=uuid.uuid4(), name="Sync", slug=f"sync-{uuid.uuid4().hex[:8]}")
    session.add(project)
    row = ProjectActivityEvent(
        id=uuid.uuid4(),
        project_id=project.id,
        occurred_at=datetime.now(timezone.utc),
        category="configuration",
        event_type="policy.updated",
        schema_version=1,
        actor_type="system",
        entity_type="policy",
        entity_id="p1",
        summary="sync row",
    )
    session.add(row)
    return row


async def test_mention_ids_survive_the_write_time_redaction(db, project, redis):
    """§1.1: ``context.mentions`` holds user ids that the redaction must keep."""
    mentioned = [str(uuid.uuid4()) for _ in range(4)]
    comment_id = str(uuid.uuid4())
    await record(
        db,
        project_id=project.id,
        event_type="test_case.commented",
        actor=ActorRef("user", actor_id=uuid.uuid4(), actor_name="Bob"),
        entity_id=uuid.uuid4(),
        entity_label="Login works",
        context={
            "comment_id": comment_id,
            "mentions": mentioned,
            "mention_count": 4,
            "step_number": 3,
        },
    )
    await db.commit()
    await live.drain()
    (xadd,) = redis.xadds
    context = json.loads(xadd[2]["payload"])["context"]
    assert context["mentions"] == mentioned
    assert context["comment_id"] == comment_id
