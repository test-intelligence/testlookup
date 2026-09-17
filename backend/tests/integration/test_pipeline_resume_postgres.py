"""Opt-in PostgreSQL concurrency coverage for same-pipeline resume claims.

These tests call the production resume claim function against a disposable or
approved PostgreSQL database.  They stay skipped in the fast suite unless
``TESTLOOKUP_POSTGRES_TEST_DSN`` is configured.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _metadata(*, authoritative: bool = True) -> str:
    if not authoritative:
        return '{"initial_workflow_plan": {"stages": []}}'
    return (
        '{"initial_workflow_plan": {"schema_version": 2, "stages": '
        '[{"stage": "ingestion", "selected": true}]}, '
        '"analysis_mode_resolution": {"resolved": "deep", "source": "test"}}'
    )


async def _cleanup(factory, pipeline_id: uuid.UUID) -> None:
    async with factory.begin() as db:
        await db.execute(
            text("DELETE FROM agent_pipeline_runs WHERE id=:id"),
            {"id": pipeline_id},
        )


async def test_same_pipeline_resume_claim_is_single_winner_and_resets_only_incomplete_stages(
    monkeypatch,
):
    from app.agents import workflow

    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(workflow, "AsyncSessionLocal", lambda: factory())
    pipeline_id = uuid.uuid4()
    stage_ids = (uuid.uuid4(), uuid.uuid4())
    try:
        async with engine.begin() as db:
            run = (
                await db.execute(
                    text("SELECT id, project_id FROM test_runs ORDER BY created_at LIMIT 1")
                )
            ).first()
            if run is None:
                pytest.skip("homelab database has no test run fixture")
            run_id, project_id = run
            await db.execute(
                text(
                    "INSERT INTO agent_pipeline_runs "
                    "(id,test_run_id,workflow_type,status,execution_metadata) "
                    "VALUES (:id,:run,'deep','failed',CAST(:metadata AS json))"
                ),
                {
                    "id": pipeline_id,
                    "run": run_id,
                    "metadata": _metadata(),
                },
            )
            await db.execute(
                text(
                    "INSERT INTO agent_stage_results "
                    "(id,pipeline_run_id,stage_name,status,attempt,idempotency_key,result_data) "
                    "VALUES "
                    "(:complete,:pipeline,'ingestion','completed',1,:complete_key,'{}'::json),"
                    "(:pending,:pipeline,'summary','failed',1,:pending_key,'{}'::json)"
                ),
                {
                    "complete": stage_ids[0],
                    "pending": stage_ids[1],
                    "pipeline": pipeline_id,
                    "complete_key": "a" * 64,
                    "pending_key": "b" * 64,
                },
            )

        async def claim():
            return await workflow._claim_pipeline_resume(str(pipeline_id))  # noqa: SLF001

        results = await asyncio.gather(claim(), claim())
        winners = [item for item in results if item is not None]
        assert len(winners) == 1
        assert winners[0]["resume_attempt"] == 1

        async with factory() as db:
            pipeline = (
                await db.execute(
                    text(
                        "SELECT status, execution_metadata "
                        "FROM agent_pipeline_runs WHERE id=:id"
                    ),
                    {"id": pipeline_id},
                )
            ).one()
            stages = (
                await db.execute(
                    text(
                        "SELECT stage_name,status,attempt,idempotency_key,result_data "
                        "FROM agent_stage_results WHERE pipeline_run_id=:id "
                        "ORDER BY stage_name"
                    ),
                    {"id": pipeline_id},
                )
            ).all()

        assert pipeline.status == "running"
        assert pipeline.execution_metadata["resume_attempt"] == 1
        assert [(row.stage_name, row.status, row.attempt) for row in stages] == [
            ("ingestion", "completed", 1),
            ("summary", "pending", 2),
        ]
        assert stages[0].idempotency_key == "a" * 64
        assert stages[1].idempotency_key is None
        assert stages[1].result_data["_resume"]["previous_attempt"] == 1
    finally:
        await _cleanup(factory, pipeline_id)
        await engine.dispose()


async def test_resume_claim_without_authority_is_fail_closed_and_non_mutating(monkeypatch):
    from app.agents import workflow

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(workflow, "AsyncSessionLocal", lambda: factory())
    pipeline_id = uuid.uuid4()
    try:
        async with engine.begin() as db:
            run = (
                await db.execute(
                    text("SELECT id FROM test_runs ORDER BY created_at LIMIT 1")
                )
            ).scalar_one_or_none()
            if run is None:
                pytest.skip("homelab database has no test run fixture")
            await db.execute(
                text(
                    "INSERT INTO agent_pipeline_runs "
                    "(id,test_run_id,workflow_type,status,execution_metadata) "
                    "VALUES (:id,:run,'deep','failed',CAST(:metadata AS json))"
                ),
                {
                    "id": pipeline_id,
                    "run": run,
                    "metadata": _metadata(authoritative=False),
                },
            )

        assert await workflow._claim_pipeline_resume(str(pipeline_id)) is None  # noqa: SLF001
        async with factory() as db:
            status, metadata = (
                await db.execute(
                    text(
                        "SELECT status, execution_metadata "
                        "FROM agent_pipeline_runs WHERE id=:id"
                    ),
                    {"id": pipeline_id},
                )
            ).one()
        assert status == "failed"
        assert "resume_attempt" not in metadata
    finally:
        await _cleanup(factory, pipeline_id)
        await engine.dispose()


async def test_queued_resume_cannot_claim_a_review_rejected_row(monkeypatch):
    from app.agents import workflow

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(workflow, "AsyncSessionLocal", lambda: factory())
    pipeline_id = uuid.uuid4()
    try:
        async with engine.begin() as db:
            run = (
                await db.execute(
                    text("SELECT id FROM test_runs ORDER BY created_at LIMIT 1")
                )
            ).scalar_one_or_none()
            if run is None:
                pytest.skip("homelab database has no test run fixture")
            await db.execute(
                text(
                    "INSERT INTO agent_pipeline_runs "
                    "(id,test_run_id,workflow_type,status,error,execution_metadata) "
                    "VALUES (:id,:run,'deep','failed','review_rejected: stale_data',"
                    "CAST(:metadata AS json))"
                ),
                {"id": pipeline_id, "run": run, "metadata": _metadata()},
            )

        assert await workflow._claim_pipeline_resume(str(pipeline_id)) is None  # noqa: SLF001
        async with factory() as db:
            status, error, metadata = (
                await db.execute(
                    text(
                        "SELECT status,error,execution_metadata "
                        "FROM agent_pipeline_runs WHERE id=:id"
                    ),
                    {"id": pipeline_id},
                )
            ).one()
        assert (status, error) == ("failed", "review_rejected: stale_data")
        assert "resume_attempt" not in metadata
    finally:
        await _cleanup(factory, pipeline_id)
        await engine.dispose()
