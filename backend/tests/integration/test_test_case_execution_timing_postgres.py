"""EXEC-01 against real Postgres: migration 0202 up and down, and an upload's timing round trip.

* At head, ``test_cases`` has the seven nullable timing columns with their
  types, and ``ix_test_cases_run_duration`` exists, VALID, in its definition.
* The migration's own ``upgrade()`` / ``downgrade()`` run through a real
  Alembic ``MigrationContext`` -- including the CONCURRENTLY build inside the
  autocommit block -- on a copy of ``test_cases`` in a scratch schema, so the
  shared database's table is never altered. Up adds exactly the columns and
  REPLACES 0004's ``(test_run_id, duration_ms)`` index of the same name (the
  first version's ``IF NOT EXISTS`` silently kept it: this test found that);
  down removes exactly the columns, puts 0004's index back, and leaves the
  rest of the table alone.
* A TestNG native report parsed by the upload dispatch and ingested through
  ``ingest_test_results`` stores each test's start / finish / setup / call /
  teardown; the JUnit sample (no per-test clock) stores NULL timing, not zero.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head;
skips otherwise. Runs in CI in the protected PostgreSQL step of
``.github/workflows/ci.yml``.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

BACKEND = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "migrations" / "versions" / "0202_test_case_execution_timing.py"
FIXTURES = BACKEND / "tests" / "fixtures" / "timing"
SAMPLES = BACKEND.parent / "samples"
INDEX = "ix_test_cases_run_duration"

#: column -> (information_schema data_type, character_maximum_length)
COLUMNS = {
    "started_at": ("timestamp with time zone", None),
    "finished_at": ("timestamp with time zone", None),
    "setup_ms": ("integer", None),
    "call_ms": ("integer", None),
    "teardown_ms": ("integer", None),
    "worker_id": ("character varying", 64),
    "timing_source": ("character varying", 16),
}


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0202_pg", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _at(second: int) -> datetime:
    return datetime(2026, 10, 14, 10, 0, second, tzinfo=timezone.utc)


def _timing_columns(conn, schema: str) -> dict:
    rows = conn.execute(
        text(
            "SELECT column_name, data_type, character_maximum_length, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = 'test_cases' AND column_name = ANY(:names)"
        ),
        {"schema": schema, "names": list(COLUMNS)},
    ).all()
    return {row.column_name: (row.data_type, row.character_maximum_length, row.is_nullable) for row in rows}


def _index(conn, schema: str):
    return conn.execute(
        text(
            "SELECT i.indisvalid AS valid, pg_get_indexdef(i.indexrelid) AS definition "
            "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relname = :index"
        ),
        {"schema": schema, "index": INDEX},
    ).first()


async def test_head_has_the_timing_columns_and_a_valid_run_duration_index():
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as conn:
            def _probe(sync):
                schema = sync.execute(text("SELECT current_schema()")).scalar_one()
                return _timing_columns(sync, schema), _index(sync, schema)

            columns, index = await conn.run_sync(_probe)
    finally:
        await engine.dispose()
    assert columns == {name: (kind, length, "YES") for name, (kind, length) in COLUMNS.items()}
    assert index is not None and index.valid is True
    assert index.definition.endswith("USING btree (test_run_id, duration_ms DESC NULLS LAST)")


async def test_the_migration_goes_up_and_down_on_a_copy_of_test_cases():
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    migration = _migration()
    scratch = f"w02a_{uuid.uuid4().hex[:10]}"
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)

    def _round_trip(conn):
        base = conn.execute(text("SELECT current_schema()")).scalar_one()
        conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
        # The table as 0201 left it: every column but the seven, and 0004's
        # index under the name 0202 takes over.
        conn.execute(text(f'CREATE TABLE "{scratch}".test_cases (LIKE "{base}".test_cases)'))
        for name in COLUMNS:
            conn.execute(text(f'ALTER TABLE "{scratch}".test_cases DROP COLUMN {name}'))
        conn.execute(text(f'CREATE INDEX {INDEX} ON "{scratch}".test_cases (test_run_id, duration_ms)'))
        others = conn.execute(
            text("SELECT count(*) FROM information_schema.columns WHERE table_schema = :s AND table_name = 'test_cases'"),
            {"s": scratch},
        ).scalar_one()
        # Session-level, so it outlives the commit the autocommit block makes.
        conn.execute(text(f'SET search_path TO "{scratch}"'))
        conn.commit()
        try:
            context = MigrationContext.configure(conn)
            migration.op = Operations(context)
            with context.begin_transaction():
                migration.upgrade()
            after_up = (_timing_columns(conn, scratch), _index(conn, scratch))
            conn.commit()
            with context.begin_transaction():
                migration.downgrade()
            after_down = (_timing_columns(conn, scratch), _index(conn, scratch))
            remaining = conn.execute(
                text("SELECT count(*) FROM information_schema.columns WHERE table_schema = :s AND table_name = 'test_cases'"),
                {"s": scratch},
            ).scalar_one()
            probes = conn.execute(
                text("SELECT count(*) FROM pg_class WHERE relname LIKE '\\_alembic\\_0202%'")
            ).scalar_one()
            conn.commit()
            return after_up, after_down, others, remaining, probes
        finally:
            conn.rollback()
            conn.execute(text(f'SET search_path TO "{base}"'))
            conn.execute(text(f'DROP SCHEMA "{scratch}" CASCADE'))
            conn.commit()

    try:
        async with engine.connect() as conn:
            (up_columns, up_index), (down_columns, down_index), before, after, probes = await conn.run_sync(
                _round_trip
            )
    finally:
        await engine.dispose()

    assert up_columns == {name: (kind, length, "YES") for name, (kind, length) in COLUMNS.items()}
    # 0004's index of the same name was replaced, not kept by IF NOT EXISTS.
    assert up_index is not None and up_index.valid is True
    assert up_index.definition.endswith("USING btree (test_run_id, duration_ms DESC NULLS LAST)")
    # Down: the columns are gone and 0004's index is back, exactly as before.
    assert down_columns == {}
    assert down_index is not None and down_index.valid is True
    assert down_index.definition.endswith("USING btree (test_run_id, duration_ms)")
    assert after == before, "the downgrade must drop exactly what the upgrade added"
    assert probes == 0, "the definition probe must leave no table behind"


async def test_an_uploaded_report_round_trips_its_timing_and_a_junit_report_stores_none():
    from app.models.postgres import LaunchStatus, Project, TestCase, TestRun
    from app.services import ingestion_pipeline as pipeline
    from app.worker.tasks import _parse_file_to_results_with_timings

    token = uuid.uuid4().hex[:12]
    project_id, testng_run, junit_run = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    reports = {
        testng_run: ((FIXTURES / "testng-results-config.xml").read_text("utf-8"), "testng", "testng-results.xml"),
        junit_run: ((SAMPLES / "junit" / "auth-suite.xml").read_text("utf-8"), "junit", "auth-suite.xml"),
    }
    try:
        async with factory() as db:
            db.add(Project(id=project_id, name=f"EXEC-01 timing {token}", slug=f"exec01-timing-{token}"))
            await db.flush()
            for run_id, (content, fmt, filename) in reports.items():
                run = TestRun(
                    id=run_id, project_id=project_id, build_number=f"{fmt}-{token}",
                    jenkins_job="exec-01", status=LaunchStatus.IN_PROGRESS,
                )
                db.add(run)
                await db.flush()
                results, _timings = _parse_file_to_results_with_timings(content, fmt, filename, str(run_id))
                assert await pipeline.ingest_test_results(db, run, results) == len(results)
            await db.commit()

        async with factory() as db:
            rows = (
                await db.execute(
                    select(
                        TestCase.test_run_id, TestCase.test_name, TestCase.duration_ms,
                        TestCase.started_at, TestCase.finished_at, TestCase.setup_ms,
                        TestCase.call_ms, TestCase.teardown_ms, TestCase.worker_id, TestCase.timing_source,
                    ).where(TestCase.test_run_id.in_(list(reports)))
                )
            ).all()
    finally:
        async with factory() as cleanup:
            await cleanup.execute(delete(Project).where(Project.id == project_id))
            await cleanup.commit()
        await engine.dispose()

    testng = {row.test_name: row for row in rows if row.test_run_id == testng_run}
    assert set(testng) == {"addsItem", "appliesCoupon", "paysByCard", "paysByVoucher"}
    adds = testng["addsItem"]
    assert (adds.started_at, adds.finished_at) == (_at(1), _at(3))
    assert (adds.setup_ms, adds.call_ms, adds.teardown_ms, adds.duration_ms) == (120, 1600, 80, 1600)
    assert (adds.worker_id, adds.timing_source) == (None, "report")
    card = testng["paysByCard"]
    assert (card.started_at, card.finished_at, card.setup_ms, card.call_ms) == (_at(7), _at(8), None, None)

    junit = [row for row in rows if row.test_run_id == junit_run]
    assert len(junit) == 6
    for row in junit:
        assert (row.started_at, row.finished_at, row.setup_ms, row.call_ms, row.teardown_ms,
                row.worker_id, row.timing_source) == (None,) * 7
        assert row.duration_ms is not None
