"""Exercise SQL evidence admission without any network or user data."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import event as sa_event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.postgres import PipelineEventOutbox
from app.services import pipeline_event_admission, pipeline_event_log

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def outbox(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")

    @sa_event.listens_for(engine.sync_engine, "begin")
    def explicit_begin(conn):
        conn.exec_driver_sql("BEGIN")

    project, run, pipeline, other_project, other_run = (uuid.uuid4() for _ in range(5))
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE projects (id char(32) PRIMARY KEY)"))
        await conn.execute(text("CREATE TABLE test_runs (id char(32) PRIMARY KEY, project_id char(32))"))
        await conn.execute(text("CREATE TABLE agent_pipeline_runs (id char(32) PRIMARY KEY, test_run_id char(32))"))
        await conn.execute(text("CREATE TABLE test_cases (id char(32) PRIMARY KEY, test_run_id char(32))"))
        await conn.execute(text("INSERT INTO projects VALUES (:id), (:other)"), {
            "id": project.hex, "other": other_project.hex,
        })
        await conn.execute(text("INSERT INTO test_runs VALUES (:run, :p), (:other, :other_p)"), {
            "run": run.hex, "p": project.hex, "other": other_run.hex, "other_p": other_project.hex,
        })
        await conn.execute(text("INSERT INTO agent_pipeline_runs VALUES (:pipeline, :run)"), {
            "pipeline": pipeline.hex, "run": run.hex,
        })
        await conn.run_sync(PipelineEventOutbox.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(pipeline_event_log, "get_session_factory", lambda: factory)
    try:
        yield factory, project, run, pipeline, other_project, other_run
    finally:
        await engine.dispose()


async def test_intent_persists_across_sessions_and_distinguishes_retries(outbox):
    from app.core.metrics import pipeline_event_admission_failures

    def conflicts():
        return next(
            sample.value
            for metric in pipeline_event_admission_failures.collect()
            for sample in metric.samples
            if sample.name.endswith("_total") and sample.labels == {"reason": "conflict"}
        )

    before = conflicts()
    factory, project, run, pipeline, other_project, _ = outbox
    event_id = uuid.uuid4()
    detail = {"decision": "inspect", "contact": "fixture@example.invalid"}
    assert await pipeline_event_log.emit_event(str(pipeline), "decision_made", detail=detail, event_id=event_id)
    assert await pipeline_event_log.emit_event(str(pipeline), "decision_made", detail=detail, event_id=event_id)
    assert not await pipeline_event_log.emit_event(str(pipeline), "decision_made", event_id=event_id)
    assert conflicts() == before + 1
    assert not await pipeline_event_log.emit_event(str(pipeline), "decision_made", project_id=other_project)
    assert await pipeline_event_log.emit_event(str(pipeline), "decision_made", detail=detail)
    async with factory() as db:
        rows = (await db.scalars(select(PipelineEventOutbox))).all()
    assert len(rows) == 2
    assert {row.project_id for row in rows} == {project}
    assert {row.test_run_id for row in rows} == {run}
    assert {row.payload["event_id"] for row in rows} == {str(row.id) for row in rows}
    assert all("fixture@example.invalid" not in str(row.payload) for row in rows)


async def test_source_rollback_leaves_no_successful_evidence(outbox):
    factory, project, run, _, _, _ = outbox
    correlation = str(uuid.uuid4())
    async with factory() as db:
        assert await pipeline_event_log.emit_event(
            correlation, "agent_run_recorded", db=db, project_id=project, test_run_id=run,
        )
        await db.rollback()
    async with factory() as db:
        assert (await db.scalars(select(PipelineEventOutbox))).all() == []


async def test_quota_rejection_and_oversize_detail_retains_identity(outbox, monkeypatch):
    factory, _, _, pipeline, _, _ = outbox
    monkeypatch.setattr(pipeline_event_admission, "PIPELINE_EVENT_CAPACITY", 1)
    detail = {"oversize": ["a" * 5000 for _ in range(8)]}
    assert await pipeline_event_log.emit_event(str(pipeline), "decision_made", detail=detail)
    assert not await pipeline_event_log.emit_event(str(pipeline), "decision_made")
    async with factory() as db:
        row = (await db.scalars(select(PipelineEventOutbox))).one()
    assert row.payload["detail"] == {"detail_omitted": "size_or_content"}


async def test_timeline_keeps_accepted_evidence_and_deduplicates_mongo(outbox, monkeypatch):
    factory, _, _, pipeline, _, _ = outbox
    assert await pipeline_event_log.emit_event(str(pipeline), "decision_made")
    assert await pipeline_event_log.emit_event(str(pipeline), "stage_started")
    async with factory() as db:
        accepted = (await db.scalars(select(PipelineEventOutbox.payload))).all()
    history = MagicMock()
    cursor = history.__getitem__.return_value.find.return_value.sort.return_value
    cursor.to_list = AsyncMock(return_value=[accepted[0]])
    monkeypatch.setattr(pipeline_event_log, "get_mongo_db", lambda: history)
    assert [row["event_type"] for row in await pipeline_event_log.get_pipeline_timeline(str(pipeline))] == [
        "decision_made", "stage_started",
    ]
    cursor.to_list.side_effect = OSError("fixture unavailable")
    assert len(await pipeline_event_log.get_pipeline_timeline(str(pipeline))) == 2


async def test_purge_cancels_only_the_requested_source_intents(outbox):
    factory, project, run, pipeline, other_project, other_run = outbox
    assert await pipeline_event_log.emit_event(str(pipeline), "decision_made")
    assert await pipeline_event_log.emit_event(
        str(uuid.uuid4()), "agent_run_recorded", project_id=other_project, test_run_id=other_run,
    )
    async with factory.begin() as db:
        await pipeline_event_admission.purge_pipeline_event_intents(
            db, project, pipeline_run_ids=set(), test_run_ids={run, other_run},
        )
    async with factory() as db:
        kept = (await db.scalars(select(PipelineEventOutbox))).one()
    assert kept.project_id == other_project
