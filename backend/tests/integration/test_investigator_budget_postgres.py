"""Opt-in PostgreSQL contention coverage for Investigator reservations."""
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


async def test_reservation_row_lock_admits_one_call_and_preserves_invalid_settlement(
    monkeypatch,
):
    from app.agents.investigator import persistence

    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(persistence, "AsyncSessionLocal", lambda: factory())
    investigation_id = uuid.uuid4()
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
                    "INSERT INTO agent_investigations "
                    "(id,project_id,run_id,scope_type,cluster_member_test_ids,spawn_depth,status,mode,triggered_by,budget,spend,hypotheses,cancel_requested) "
                    "VALUES (:id,:project,:run,'run','[]'::jsonb,0,'queued','shadow','manual',"
                    "CAST(:budget AS jsonb),CAST(:spend AS jsonb),'[]'::jsonb,false)"
                ),
                {
                    "id": investigation_id,
                    "project": project_id,
                    "run": run_id,
                    "budget": '{"max_llm_calls":1,"max_tokens":100,"max_cost_usd":1.0}',
                    "spend": '{"ledger_version":2,"llm_calls":0,"tokens":0,"cost_usd":0.0,"reservations":{},"completed_reservations":{}}',
                },
            )

        async def reserve(index: int):
            return await persistence.reserve_investigation_budget(
                str(investigation_id),
                reservation_id=f"pg-budget-{index}-{uuid.uuid4().hex}",
                llm_calls=1,
                tokens=50,
                project_id=str(project_id),
                run_id=str(run_id),
                pipeline_run_id="",
                stage_name="hypothesis",
            )

        results = await asyncio.gather(*(reserve(index) for index in range(2)))
        allowed = [item for item in results if item.allowed]
        denied = [item for item in results if not item.allowed]
        assert len(allowed) == 1
        assert len(denied) == 1
        assert denied[0].stop_reason == "llm_call_budget_exhausted"

        # Invalid observed usage must leave the active receipt available for a
        # safe retry rather than consuming the reservation.
        assert await persistence.settle_investigation_budget(
            str(investigation_id),
            allowed[0],
            actual_llm_calls="forged",  # type: ignore[arg-type]
            actual_tokens="forged",  # type: ignore[arg-type]
        ) is False
        valid = await persistence.settle_investigation_budget(
            str(investigation_id), allowed[0], actual_llm_calls=1, actual_tokens=37
        )
        assert valid is True
    finally:
        async with engine.begin() as db:
            await db.execute(
                text("DELETE FROM agent_investigations WHERE id=:id"),
                {"id": investigation_id},
            )
        await engine.dispose()


async def test_concurrent_settlement_creates_one_completed_receipt(
    monkeypatch,
):
    """Two workers settling one reservation cannot double-count provider usage."""
    from app.agents.investigator import persistence

    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(persistence, "AsyncSessionLocal", lambda: factory())
    investigation_id = uuid.uuid4()
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
                    "INSERT INTO agent_investigations "
                    "(id,project_id,run_id,scope_type,cluster_member_test_ids,spawn_depth,status,mode,triggered_by,budget,spend,hypotheses,cancel_requested) "
                    "VALUES (:id,:project,:run,'run','[]'::jsonb,0,'queued','shadow','manual',"
                    "CAST(:budget AS jsonb),CAST(:spend AS jsonb),'[]'::jsonb,false)"
                ),
                {
                    "id": investigation_id,
                    "project": project_id,
                    "run": run_id,
                    "budget": '{"max_llm_calls":1,"max_tokens":100,"max_cost_usd":1.0}',
                    "spend": '{"ledger_version":2,"llm_calls":0,"tokens":0,"cost_usd":0.0,"reservations":{},"completed_reservations":{}}',
                },
            )

        reservation = await persistence.reserve_investigation_budget(
            str(investigation_id),
            reservation_id=f"pg-settle-{uuid.uuid4().hex}",
            llm_calls=1,
            tokens=50,
            project_id=str(project_id),
            run_id=str(run_id),
            pipeline_run_id="",
            stage_name="hypothesis",
        )
        assert reservation.allowed

        results = await asyncio.gather(
            *(
                persistence.settle_investigation_budget(
                    str(investigation_id),
                    reservation,
                    actual_llm_calls=1,
                    actual_tokens=37,
                )
                for _ in range(2)
            )
        )
        assert sorted(results) == [False, True]

        async with factory() as db:
            spend = (
                await db.execute(
                    text("SELECT spend FROM agent_investigations WHERE id=:id"),
                    {"id": investigation_id},
                )
            ).scalar_one()
        assert spend["llm_calls"] == 1
        assert spend["tokens"] == 37
        assert len(spend["completed_reservations"]) == 1
        assert spend["reservations"] == {}
    finally:
        async with engine.begin() as db:
            await db.execute(
                text("DELETE FROM agent_investigations WHERE id=:id"),
                {"id": investigation_id},
            )
        await engine.dispose()
