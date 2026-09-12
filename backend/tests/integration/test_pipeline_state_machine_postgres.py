"""Real PostgreSQL proof for E7.1: the closed status vocabulary is enforced by
the database, and the guarded transition loses a race instead of clobbering.

CI runs ``alembic upgrade head`` before this file, so the CHECK constraint from
0173 is live. A mocked session cannot see a constraint (memory: seven green
tests once covered a reset the DB refused), which is why this is here. Rows
are seeded the way ``test_pipeline_resume_postgres`` does it: against an
existing test run, by raw INSERT, with the pipeline row deleted afterwards.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def _seed(engine, status: str) -> uuid.UUID:
    pipeline_id = uuid.uuid4()
    async with engine.begin() as db:
        run = (await db.execute(text("SELECT id FROM test_runs ORDER BY created_at LIMIT 1"))).first()
        if run is None:
            pytest.skip("database has no test run fixture")
        await db.execute(
            text(
                "INSERT INTO agent_pipeline_runs (id, test_run_id, workflow_type, status, started_at) "
                "VALUES (:id, :run, 'offline', :status, now())"
            ),
            {"id": pipeline_id, "run": run[0], "status": status},
        )
    return pipeline_id


async def _cleanup(engine, pipeline_id: uuid.UUID) -> None:
    async with engine.begin() as db:
        await db.execute(text("DELETE FROM agent_pipeline_runs WHERE id = :id"), {"id": pipeline_id})


async def test_check_constraint_rejects_retired_and_unknown_statuses():
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    pipeline_id = await _seed(engine, "running")
    try:
        for bad in ("partial", "cancelled", "bogus"):
            async with engine.begin() as db:
                with pytest.raises(IntegrityError) as exc:
                    await db.execute(
                        text("UPDATE agent_pipeline_runs SET status = :s WHERE id = :id"),
                        {"s": bad, "id": pipeline_id},
                    )
                assert "ck_agent_pipeline_status" in str(exc.value)
        async with engine.begin() as db:
            names = (
                await db.execute(
                    text("SELECT conname FROM pg_constraint WHERE conrelid = 'agent_pipeline_runs'::regclass")
                )
            ).scalars().all()
        assert "ck_agent_pipeline_status" in names
        assert "ck_agent_pipeline_attempts" in names
    finally:
        await _cleanup(engine, pipeline_id)
        await engine.dispose()


async def test_new_columns_have_their_defaults():
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    pipeline_id = await _seed(engine, "pending")
    try:
        async with engine.begin() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT attempt, max_attempts, cancel_requested, review_policy, fencing_token "
                        "FROM agent_pipeline_runs WHERE id = :id"
                    ),
                    {"id": pipeline_id},
                )
            ).one()
        assert tuple(row) == (1, 5, False, "human_required", None)
    finally:
        await _cleanup(engine, pipeline_id)
        await engine.dispose()


async def test_guarded_transition_second_writer_loses():
    from app.services.workflow_run_state import TransitionLost, guarded_transition

    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    pipeline_id = await _seed(engine, "running")
    try:
        async with factory() as db:
            await guarded_transition(db, pipeline_id, expected="running", to="failed", error="first writer")
            await db.commit()
        async with factory() as db:
            with pytest.raises(TransitionLost):
                await guarded_transition(db, pipeline_id, expected="running", to="completed")
            await db.rollback()
        async with engine.begin() as db:
            status, error, completed_at = (
                await db.execute(
                    text("SELECT status, error, completed_at FROM agent_pipeline_runs WHERE id = :id"),
                    {"id": pipeline_id},
                )
            ).one()
        assert status == "failed"
        assert error == "first writer"
        assert completed_at is not None, "terminal => completed_at IS NOT NULL"
    finally:
        await _cleanup(engine, pipeline_id)
        await engine.dispose()
