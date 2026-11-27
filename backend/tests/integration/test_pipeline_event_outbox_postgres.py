"""PostgreSQL locking, admission and lease behavior against an isolated schema."""

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import mongo, postgres
from app.models.postgres import PipelineEventOutbox
from app.services import pipeline_event_admission, pipeline_event_delivery, pipeline_event_log


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def postgres_outbox(monkeypatch):
    dsn = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN")
    if not dsn:
        pytest.skip("requires a disposable PostgreSQL DSN")
    schema = f"pipeline_event_fixture_{uuid.uuid4().hex}"
    admin = create_async_engine(dsn)
    engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(pipeline_event_log, "get_session_factory", lambda: factory)
    monkeypatch.setattr(postgres, "get_session_factory", lambda: factory)
    project, run, pipeline = (uuid.uuid4() for _ in range(3))
    try:
        async with admin.begin() as db:
            await db.execute(text(f"CREATE SCHEMA {schema}"))
        async with engine.begin() as db:
            await db.execute(text("CREATE TABLE projects (id uuid PRIMARY KEY)"))
            await db.execute(text("CREATE TABLE test_runs (id uuid PRIMARY KEY, project_id uuid)"))
            await db.execute(text("CREATE TABLE agent_pipeline_runs (id uuid PRIMARY KEY, test_run_id uuid)"))
            await db.execute(text("CREATE TABLE test_cases (id uuid PRIMARY KEY, test_run_id uuid)"))
            await db.execute(text("INSERT INTO projects VALUES (:project)"), {"project": project})
            await db.execute(text("INSERT INTO test_runs VALUES (:run, :project)"), {"run": run, "project": project})
            await db.execute(text("INSERT INTO agent_pipeline_runs VALUES (:pipeline, :run)"), {
                "pipeline": pipeline, "run": run,
            })
            await db.run_sync(PipelineEventOutbox.__table__.create)
        yield factory, project, pipeline
    finally:
        await engine.dispose()
        async with admin.begin() as db:
            await db.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await admin.dispose()


async def test_project_purge_fence_global_capacity_and_single_claim(postgres_outbox, monkeypatch):
    factory, project, pipeline = postgres_outbox
    monkeypatch.setattr(pipeline_event_admission, "PIPELINE_EVENT_CAPACITY", 1)
    async with factory.begin() as purge:
        assert await pipeline_event_admission.lock_project_event_history(purge, project, exclusive=True)
        assert not await pipeline_event_log.emit_event(str(pipeline), "decision_made")

    # Separate producers cannot both take the last admission slot.
    results = await asyncio.gather(*[
        pipeline_event_log.emit_event(str(pipeline), "decision_made") for _ in range(5)
    ])
    assert results.count(True) == 1
    claims = await asyncio.gather(
        pipeline_event_delivery._claim_one(), pipeline_event_delivery._claim_one(),
    )
    assert sum(claim is not None for claim in claims) == 1
    async with factory() as db:
        row = (await db.scalars(select(PipelineEventOutbox))).one()
    assert row.status == "sending" and row.attempts == 1


async def test_postgres_and_mongo_recover_after_uncertain_receipt(postgres_outbox, monkeypatch):
    uri = os.environ.get("TESTLOOKUP_MONGO_EVENT_TEST_URI")
    if not uri:
        pytest.skip("requires a disposable MongoDB URI")
    factory, _, pipeline = postgres_outbox
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    database = client[f"pipeline_event_fixture_{uuid.uuid4().hex}"]
    monkeypatch.setattr(mongo, "get_mongo_db", lambda: database)
    monkeypatch.setattr(pipeline_event_log, "get_mongo_db", lambda: database)
    identity = uuid.uuid4()
    try:
        await client.admin.command("ping")
        assert await pipeline_event_log.emit_event(str(pipeline), "decision_made", event_id=identity)
        assert (await pipeline_event_delivery.relay_pipeline_events())["delivered"] == 1
        original = await database.pipeline_event_log.find_one({"_id": str(identity)})
        assert original["event_id"] == str(identity)

        # Simulate a lost SQL acknowledgement after Mongo committed the event.
        async with factory.begin() as db:
            row = await db.get(PipelineEventOutbox, identity)
            row.status = "pending"
            row.delivered_at = None
            row.next_attempt_at = datetime.now(timezone.utc)
        assert (await pipeline_event_delivery.relay_pipeline_events())["delivered"] == 1
        assert await database.pipeline_event_log.find_one({"_id": str(identity)}) == original
        assert await database.pipeline_event_log.count_documents({}) == 1
        assert [event["event_id"] for event in await pipeline_event_log.get_pipeline_timeline(str(pipeline))] == [
            str(identity),
        ]
    finally:
        await client.drop_database(database.name)
        client.close()
