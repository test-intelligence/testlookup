"""Optional retained-intent workload proof against disposable PostgreSQL.

Only isolated LIKE copies receive synthetic rows. No public rows, workers,
queues, integrations, or live application databases are modified.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import importlib.util
import uuid

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.config import settings
from app.models.postgres import AgentPipelineRun as Pipeline, RunDownstreamOutbox as Outbox
from app.services import journey_sli_metrics as service
from app.services.journey_sli_contracts import COHORT_OUTCOMES

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_native_cohort_workload_rederives_across_independent_readers(monkeypatch):
    dsn = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    schema = "journey_sli_proof_" + uuid.uuid4().hex
    control = create_async_engine(dsn)
    engine = None
    try:
        async with control.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            for table in ("run_downstream_outbox", "agent_pipeline_runs"):
                await connection.execute(text(f'CREATE TABLE "{schema}"."{table}" (LIKE public."{table}" INCLUDING ALL)'))
        engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema + ",public"}})
        migration_path = Path(service.__file__).resolve().parents[2] / "migrations/versions/0264_journey_cohort_indexes.py"
        spec = importlib.util.spec_from_file_location("journey_indexes_native_proof", migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        def migrate(connection, direction):
            context = MigrationContext.configure(connection)
            with context.begin_transaction():
                with Operations.context(context):
                    getattr(migration, direction)()

        async with engine.connect() as connection:
            await connection.run_sync(lambda sync: migrate(sync, "upgrade"))
            await connection.run_sync(lambda sync: migrate(sync, "upgrade"))  # idempotent retry
        async with engine.begin() as connection:
            rows = (await connection.execute(text(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname=:schema AND indexname IN "
                "('ix_run_downstream_outbox_created_at','ix_agent_pipeline_runs_created_at')"
            ), {"schema": schema})).all()
            assert len(rows) == 2 and all(definition.endswith("(created_at)") for _name, definition in rows)
            for name, table in migration.INDEXES:
                model = Outbox if table == "run_downstream_outbox" else Pipeline
                orm = next(index for index in model.__table__.indexes if index.name == name)
                assert [column.name for column in orm.columns] == ["created_at"]
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(settings, "OBSERVABILITY_INSTALLATION", "synthetic-pg-site")
        monkeypatch.setattr(settings, "AI_PIPELINE_DEADLINE_SECONDS", 1500)
        from app.db import postgres
        monkeypatch.setattr(postgres, "AsyncSessionLocal", sessions)
        now = datetime.now(timezone.utc)
        accepted = now - timedelta(seconds=7200)
        async with sessions() as db:
            for status in ("waiting", "pending", "sending", "published", "processing", "completed", "failed"):
                db.add(Outbox(
                    id=uuid.uuid4(), run_id=uuid.uuid4(), project_id=uuid.uuid4(),
                    operation="persist_live_session", input_version="a" * 64, status=status,
                    created_at=accepted, completed_at=accepted + timedelta(seconds=10) if status == "completed" else None,
                ))
            db.add(Outbox(id=uuid.uuid4(), run_id=uuid.uuid4(), project_id=uuid.uuid4(), operation="run_notifications",
                          input_version="b" * 64, status="completed", created_at=accepted,
                          completed_at=accepted + timedelta(seconds=4201)))
            for quality in ({"fallback_used": False}, {"fallback_used": True},
                            *({"fallback_used": value} for value in (0, "false", None, [], {}))):
                db.add(Pipeline(id=uuid.uuid4(), test_run_id=uuid.uuid4(), status="completed",
                                created_at=now - timedelta(seconds=2000), completed_at=now - timedelta(seconds=1900),
                                execution_metadata=quality))
            await db.commit()
        assert await service.refresh_journey_cohort_metrics(now=now)
        # A different API process/session has no local event checkpoint; it
        # derives exactly the same logical-intent denominator after restart.
        async with sessions() as db:
            first = await service.read_journey_cohorts(db, now=now)
        assert {name: first["downstream"][("1h", name)] for name in COHORT_OUTCOMES} == {
            "good": 1, "failed": 1, "late": 1, "overdue": 5, "degraded": 0, "unknown": 0,
        }
        assert first["ai_reported_full_quality"][("1h", "good")] == 1
        assert first["ai_reported_full_quality"][("1h", "degraded")] == 6
        async with sessions() as db:
            await db.execute(update(Outbox).values(attempts=5, execution_attempts=2))
            await db.commit()
        async with sessions() as db:
            assert await service.read_journey_cohorts(db, now=now) == first
            pending = (await db.execute(select(Outbox).where(Outbox.status == "pending"))).scalar_one()
            pending.status, pending.completed_at = "completed", now
            await db.commit()
        async with sessions() as db:
            recovered = await service.read_journey_cohorts(db, now=now)
        assert recovered["downstream"][("1h", "overdue")] == 4
        assert recovered["downstream"][("1h", "late")] == 2
        assert sum(recovered["downstream"][("1h", name)] for name in COHORT_OUTCOMES) == 8
        # Enough old retained history to verify natural selective horizon
        # plans. This is index eligibility evidence, not a capacity benchmark.
        async with engine.begin() as connection:
            await connection.execute(text(
                "INSERT INTO run_downstream_outbox (id,run_id,project_id,operation,input_version,payload,queue,priority,"
                "status,attempts,dispatch_failures,execution_attempts,created_at,completed_at) "
                "SELECT gen_random_uuid(),gen_random_uuid(),gen_random_uuid(),'run_notifications',repeat('c',64),"
                "'{}'::jsonb,'default',5,'completed',0,0,0, :old,:old FROM generate_series(1,20000)"
            ), {"old": now - timedelta(days=365)})
            await connection.execute(text(
                "INSERT INTO agent_pipeline_runs (id,test_run_id,workflow_type,status,attempt,max_attempts,spawn_depth,cancel_requested,"
                "review_policy,created_at,completed_at) SELECT gen_random_uuid(),gen_random_uuid(),'offline','completed',1,5,0,false,"
                "'not_applicable',:old,:old FROM generate_series(1,20000)"
            ), {"old": now - timedelta(days=365)})
            for table in ("run_downstream_outbox", "agent_pipeline_runs"):
                await connection.execute(text(f"ANALYZE {table}"))
                plan = " ".join((await connection.execute(text(
                    f"EXPLAIN (COSTS OFF) SELECT count(*) FROM {table} WHERE created_at > :earliest AND created_at <= :latest"
                ), {"earliest": now - timedelta(days=30), "latest": now - timedelta(seconds=4200)})).scalars())
                assert "Index" in plan and "created_at" in plan
        async with engine.connect() as connection:
            await connection.run_sync(lambda sync: migrate(sync, "downgrade"))
        async with engine.begin() as connection:
            assert (await connection.execute(text(
                "SELECT count(*) FROM pg_indexes WHERE schemaname=:schema AND indexname IN "
                "('ix_run_downstream_outbox_created_at','ix_agent_pipeline_runs_created_at')"
            ), {"schema": schema})).scalar_one() == 0
    finally:
        if engine is not None:
            await engine.dispose()
        async with control.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await control.dispose()
