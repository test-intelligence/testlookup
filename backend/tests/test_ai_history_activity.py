"""AIH-06: ``analysis.report_superseded`` is EMITTED, not only declared.

The spec existed in ``activity/events.py`` with no producer, so the feed never
said that a decision report had been replaced. The producer is
``decision_report_service.publish_decision_report``: after a REAL insert that
replaced an earlier version, it records one attempt-mode row (its own session:
the producer holds only a Mongo handle). What is pinned:

* a row actually lands (an outcome event recorded with no session would be
  dropped and counted -- the "declaration is not emission" lesson);
* exactly once per real supersession: the first publication supersedes
  nothing, and none of the three idempotent returns (a replay before the lock,
  a replay seen under the lock, a version race won by the same pipeline)
  emits;
* a ledger fault never fails the publication.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from pymongo.errors import DuplicateKeyError
from sqlalchemy import select
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.mongo import Collections
from app.models.postgres import Base, Project, ProjectActivityEvent
from app.services import decision_report_service as report_service
from app.services.activity import events as E

RUN_ID = str(uuid.uuid4())


class _AwareDateTime(SQLITE_DATETIME):
    def result_processor(self, dialect, coltype):  # noqa: D102
        inner = super().result_processor(dialect, coltype)

        def process(value):
            parsed = inner(value) if inner is not None else value
            if isinstance(parsed, datetime) and parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed

        return process


@pytest.fixture
async def session_factory(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, sqltypes.DateTime: _AwareDateTime}
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[Project.__table__, ProjectActivityEvent.__table__])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    import app.db.postgres as pg

    monkeypatch.setattr(pg, "AsyncSessionLocal", factory, raising=False)
    yield factory
    await engine.dispose()


@pytest.fixture
async def project(session_factory):
    async with session_factory() as s:
        row = Project(id=uuid.uuid4(), name="Checkout", slug="checkout")
        s.add(row)
        await s.commit()
        return row


@pytest.fixture(autouse=True)
def _unlocked(monkeypatch):
    """The subject lock is a Postgres share lock on test_runs (covered by the
    decision-report service tests); here it is a no-op."""
    @asynccontextmanager
    async def unlocked(_test_run_id):
        yield

    monkeypatch.setattr(report_service, "_lock_report_subject", unlocked)


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *_args):
        return self

    def limit(self, *_args):
        return self

    async def to_list(self, length=1):
        return self.rows[:length]


class _Reports:
    def __init__(self):
        self.docs: list[dict] = []

    def find(self, query, *_args):
        rows = [
            d for d in self.docs
            if d["test_run_id"] == query["test_run_id"] and d["status"] == query["status"]
            and ("pipeline_run_id" not in query or d["pipeline_run_id"] == query["pipeline_run_id"])
        ]
        rows.sort(key=lambda d: d["report_version"], reverse=True)
        return _Cursor(rows)

    async def insert_one(self, document):
        self.docs.append(document)


class _Mongo:
    def __init__(self, reports=None):
        self.reports = reports or _Reports()

    def __getitem__(self, name):
        assert name == Collections.DECISION_REPORTS, name
        return self.reports


def _state(project, pipeline: str, build="1234") -> dict:
    return {"project_id": str(project.id), "test_run_id": RUN_ID, "pipeline_run_id": pipeline,
            "build_number": build}


async def _publish(mongo, project, pipeline, **kw):
    return await report_service.publish_decision_report(
        mongo, state=_state(project, pipeline, **kw), decision={"verification": {"status": "passed"}},
        markdown="verified",
    )


async def _superseded_rows(session_factory) -> list[ProjectActivityEvent]:
    async with session_factory() as s:
        result = await s.execute(
            select(ProjectActivityEvent).where(ProjectActivityEvent.event_type == "analysis.report_superseded")
        )
        return list(result.scalars().all())


def test_the_event_is_an_attempt_write_with_the_contracts_template():
    spec = E.ACTIVITY_EVENTS["analysis.report_superseded"]
    assert spec.write_mode == "attempt", "its producer has no Postgres session: an outcome event would be dropped"
    assert spec.summary_template == "Decision report for {entity_label} was superseded by v{version}"


async def test_a_real_supersession_lands_one_row(session_factory, project):
    mongo = _Mongo()
    first = await _publish(mongo, project, "pipeline-a")
    assert await _superseded_rows(session_factory) == [], "the first publication supersedes nothing"

    second = await _publish(mongo, project, "pipeline-b")
    rows = await _superseded_rows(session_factory)
    assert len(rows) == 1
    row = rows[0]
    assert row.project_id == project.id
    assert row.entity_id == RUN_ID and row.entity_label == "Build 1234"
    assert row.actor_type == "system"
    assert row.context == {"version": 2, "supersedes_report_id": first["report_id"],
                           "pipeline_run_id": "pipeline-b"}
    assert second["report_version"] == 2
    assert row.summary == "Decision report for Build 1234 was superseded by v2"


async def test_a_replay_of_the_same_pipeline_emits_nothing(session_factory, project):
    mongo = _Mongo()
    await _publish(mongo, project, "pipeline-a")
    await _publish(mongo, project, "pipeline-b")
    again = await _publish(mongo, project, "pipeline-b")
    assert again["report_version"] == 2
    assert len(await _superseded_rows(session_factory)) == 1


async def test_a_replay_seen_under_the_lock_emits_nothing(session_factory, project, monkeypatch):
    """The second idempotency check: another publisher of THIS pipeline won
    while this one waited for the lock."""
    mongo = _Mongo()
    await _publish(mongo, project, "pipeline-a")

    @asynccontextmanager
    async def lock_after_a_competitor(_test_run_id):
        await mongo.reports.insert_one({
            "report_id": "competitor", "project_id": str(project.id), "test_run_id": RUN_ID,
            "pipeline_run_id": "pipeline-b", "report_version": 2, "status": "published",
            "supersedes_report_id": "x",
        })
        yield

    monkeypatch.setattr(report_service, "_lock_report_subject", lock_after_a_competitor)
    result = await _publish(mongo, project, "pipeline-b")
    assert result["report_id"] == "competitor"
    assert await _superseded_rows(session_factory) == []


async def test_a_version_race_won_by_the_same_pipeline_emits_nothing(session_factory, project):
    """The third idempotent return: the insert hits the unique version and the
    winner is this very pipeline."""
    reports = _Reports()

    class _Racing(_Reports):
        async def insert_one(self, document):
            if document["pipeline_run_id"] == "pipeline-b" and not any(
                d["pipeline_run_id"] == "pipeline-b" for d in self.docs
            ):
                self.docs.append({**document, "report_id": "winner"})
                raise DuplicateKeyError("version taken")
            self.docs.append(document)

    racing = _Racing()
    racing.docs = reports.docs
    mongo = _Mongo(racing)
    await _publish(mongo, project, "pipeline-a")
    result = await _publish(mongo, project, "pipeline-b")
    assert result["report_id"] == "winner"
    assert await _superseded_rows(session_factory) == []


async def test_a_ledger_fault_never_fails_the_publication(session_factory, project, monkeypatch):
    from app.services.activity import service as activity

    async def _broken(*_args, **_kwargs):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(activity, "record", _broken)
    mongo = _Mongo()
    await _publish(mongo, project, "pipeline-a")
    second = await _publish(mongo, project, "pipeline-b")
    assert second["report_version"] == 2
    assert [d["report_version"] for d in mongo.reports.docs] == [1, 2]


async def test_a_run_without_a_build_number_is_labelled_by_its_id(session_factory, project):
    mongo = _Mongo()
    await _publish(mongo, project, "pipeline-a", build=None)
    await _publish(mongo, project, "pipeline-b", build=None)
    rows = await _superseded_rows(session_factory)
    assert [r.entity_label for r in rows] == [f"Run {RUN_ID[:8]}"]
