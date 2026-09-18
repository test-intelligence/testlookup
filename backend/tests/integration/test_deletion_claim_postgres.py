"""Real PostgreSQL serialization proof for criteria deletion claims."""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.postgres import DeletionJob, Project, TestRun as DbTestRun
from app.services import deletion_job_service as jobs

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def test_two_sessions_cannot_claim_one_preview_twice():
    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, job_id, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    slug = f"delete-claim-{project_id.hex}"

    async with sessions() as setup:
        setup.add(Project(id=project_id, name=slug, slug=slug))
        await setup.flush()
        setup.add(
            DeletionJob(
                id=job_id,
                project_id=project_id,
                job_kind=jobs.KIND_CRITERIA,
                status=jobs.PREVIEWED,
                resolved_run_ids=[str(run_id)],
                candidate_hash=jobs.candidate_hash([run_id]),
            )
        )
        await setup.commit()

    first_locked = asyncio.Event()

    async def first_claim():
        async with sessions() as db:
            claimed = await jobs.claim_frozen_set(
                db, job_id=job_id, project_id=project_id
            )
            first_locked.set()
            await asyncio.sleep(0.25)
            await db.commit()
            return claimed

    async def duplicate_claim():
        await first_locked.wait()
        started = time.monotonic()
        async with sessions() as db:
            with pytest.raises(jobs.FrozenSetRejected) as exc:
                await jobs.claim_frozen_set(
                    db, job_id=job_id, project_id=project_id
                )
            await db.rollback()
        return exc.value, time.monotonic() - started

    try:
        claimed, (rejected, waited) = await asyncio.gather(
            first_claim(), duplicate_claim()
        )
        assert claimed == [run_id]
        assert rejected.status_code == 409
        assert "queued" in rejected.detail
        assert waited >= 0.20
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(delete(DeletionJob).where(DeletionJob.id == job_id))
            await cleanup.execute(delete(Project).where(Project.id == project_id))
            await cleanup.commit()
        await engine.dispose()


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *_args):
        return self

    def limit(self, *_args):
        return self

    async def to_list(self, length=1):
        return self.rows[:length]


class _BlockingReports:
    def __init__(self):
        self.docs = []
        self.insert_started = asyncio.Event()
        self.allow_insert = asyncio.Event()

    def find(self, query, *_args):
        rows = [
            row
            for row in self.docs
            if all(row.get(key) == value for key, value in query.items())
        ]
        return _Cursor(rows)

    async def insert_one(self, document):
        self.insert_started.set()
        await self.allow_insert.wait()
        self.docs.append(document)


class _Mongo:
    def __init__(self, reports):
        self.reports = reports

    def __getitem__(self, _name):
        return self.reports


class _RecordingCollection:
    def __init__(self):
        self.inserts = []
        self.updates = []

    def find(self, *_args):
        return _Cursor([])

    async def insert_one(self, document):
        self.inserts.append(document)

    async def update_one(self, *args, **kwargs):
        self.updates.append((args, kwargs))


class _RecordingMongo:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, _RecordingCollection())

    def write_count(self):
        return sum(
            len(collection.inserts) + len(collection.updates)
            for collection in self.collections.values()
        )


async def test_report_publication_and_deletion_share_the_run_lock(monkeypatch):
    from app.services import decision_report_service

    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    slug = f"report-delete-lock-{project_id.hex}"
    reports = _BlockingReports()

    async with sessions() as setup:
        setup.add(Project(id=project_id, name=slug, slug=slug))
        await setup.flush()
        setup.add(
            DbTestRun(
                id=run_id,
                project_id=project_id,
                build_number=f"m21-{run_id.hex[:12]}",
            )
        )
        await setup.commit()

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions)

    async def publish():
        return await decision_report_service.publish_decision_report(
            _Mongo(reports),
            state={
                "project_id": str(project_id),
                "test_run_id": str(run_id),
                "pipeline_run_id": str(uuid.uuid4()),
            },
            decision={"verification": {"status": "passed"}},
            markdown="protected",
        )

    async def take_delete_lock():
        async with sessions() as db:
            row = (
                await db.execute(
                    select(DbTestRun)
                    .where(DbTestRun.id == run_id)
                    .with_for_update()
                )
            ).scalar_one()
            locked_run_id = row.id
            await db.rollback()
            return locked_run_id

    try:
        publishing = asyncio.create_task(publish())
        await reports.insert_started.wait()
        deleting = asyncio.create_task(take_delete_lock())
        await asyncio.sleep(0.05)
        assert not deleting.done(), "deletion bypassed the publisher's share lock"

        reports.allow_insert.set()
        published = await publishing
        assert await deleting == run_id
        assert reports.docs[0]["report_id"] == published["report_id"]
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(delete(DbTestRun).where(DbTestRun.id == run_id))
            await cleanup.execute(delete(Project).where(Project.id == project_id))
            await cleanup.commit()
        await engine.dispose()


async def test_deletion_winning_the_run_lock_suppresses_failure_evidence(
    monkeypatch,
):
    from app.agents import decision_report_critic_agent as critic_module
    from app.agents.decision_report_critic_agent import DecisionReportCriticAgent

    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    slug = f"failure-delete-lock-{project_id.hex}"
    mongo = _RecordingMongo()

    async with sessions() as setup:
        setup.add(Project(id=project_id, name=slug, slug=slug))
        await setup.flush()
        setup.add(
            DbTestRun(
                id=run_id,
                project_id=project_id,
                build_number=f"m21-{run_id.hex[:12]}",
            )
        )
        await setup.commit()

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions)
    monkeypatch.setattr(critic_module, "get_mongo_db", lambda: mongo)
    delete_locked = asyncio.Event()
    allow_delete = asyncio.Event()

    async def delete_first():
        async with sessions() as db:
            run = (
                await db.execute(
                    select(DbTestRun)
                    .where(DbTestRun.id == run_id)
                    .with_for_update()
                )
            ).scalar_one()
            delete_locked.set()
            await allow_delete.wait()
            await db.delete(run)
            await db.commit()

    async def persist_failure():
        agent = DecisionReportCriticAgent.__new__(DecisionReportCriticAgent)
        await agent._persist_failure(
            {
                "project_id": str(project_id),
                "test_run_id": str(run_id),
                "pipeline_run_id": str(uuid.uuid4()),
            },
            "publication lost deletion race",
        )

    try:
        deleting = asyncio.create_task(delete_first())
        await delete_locked.wait()
        failing = asyncio.create_task(persist_failure())
        await asyncio.sleep(0.05)
        assert not failing.done(), "failure persistence bypassed the deletion lock"

        allow_delete.set()
        await deleting
        await failing
        assert mongo.write_count() == 0
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(delete(DbTestRun).where(DbTestRun.id == run_id))
            await cleanup.execute(delete(Project).where(Project.id == project_id))
            await cleanup.commit()
        await engine.dispose()
