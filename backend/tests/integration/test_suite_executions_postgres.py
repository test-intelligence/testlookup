"""EXEC-02 on real PostgreSQL: migration 0203, the suite-execution upsert, and the backfill.

``suite_execution_service`` writes one ``suite_executions`` row per run per effective suite and the run's
``exec_started_at`` / ``exec_finished_at`` / ``wall_clock_ms`` / ``timing_source``. The unit tests
(``tests/services/test_suite_execution_service.py``) pin the source precedence; these pin what only a real
database shows: the ``ON CONFLICT`` upsert is idempotent (a re-finalize keeps one row per suite, counts not
doubled), a suite the run lost loses its row, the effective-suite SQL groups a live-stream run under its label,
the keyset backfill walks runs in chunks and a re-run writes nothing twice, and ``test_runs.duration_ms`` keeps
its summed meaning throughout.

The migration's downgrade and upgrade run inside a transaction that is rolled back, so the shared database keeps
its schema. Seeded runs are dated in 2001, far from any other test's rows, and each test removes its project.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head; skips otherwise. Listed in the
``postgres-integration`` job's file list in ``.github/workflows/ci.yml``.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0203_suite_executions.py"
T0 = datetime(2026, 10, 14, 10, 0, 0, tzinfo=timezone.utc)


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def factory():
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def project(factory):
    from app.models.postgres import Project

    token = uuid.uuid4().hex
    project_id = uuid.uuid4()
    async with factory() as db:
        db.add(Project(id=project_id, name=f"exec02 {token}", slug=f"exec02-{token}"))
        await db.commit()
    yield project_id
    from app.models.postgres import TestRun

    async with factory() as db:
        # test_cases and suite_executions go with their runs (CASCADE).
        await db.execute(delete(TestRun).where(TestRun.project_id == project_id))
        await db.execute(delete(Project).where(Project.id == project_id))
        await db.commit()


async def _seed_run(factory, project_id, cases, *, created_at=None, trigger_source=None,
                    primary_suite_name=None, duration_ms=None, timing_source=None) -> uuid.UUID:
    """One run; ``cases`` = (suite_name, duration_ms, started_offset_s | None, finished_offset_s | None)."""
    from app.models.postgres import LaunchStatus, TestCase, TestRun, TestStatus

    run_id = uuid.uuid4()
    async with factory() as db:
        run = TestRun(
            id=run_id,
            project_id=project_id,
            build_number=f"exec02-{run_id.hex[:10]}",
            status=LaunchStatus.PASSED,
            branch="main",
            trigger_source=trigger_source,
            primary_suite_name=primary_suite_name,
            duration_ms=duration_ms,
            timing_source=timing_source,
        )
        if created_at is not None:
            run.created_at = created_at
        db.add(run)
        await db.flush()
        for index, (suite, duration, start, finish) in enumerate(cases):
            db.add(
                TestCase(
                    id=uuid.uuid4(),
                    test_run_id=run_id,
                    test_name=f"case_{index}",
                    class_name=suite,
                    suite_name=suite,
                    test_fingerprint=f"{run_id.hex[:8]}{index:04d}",
                    status=TestStatus.FAILED if index == 0 else TestStatus.PASSED,
                    duration_ms=duration,
                    started_at=T0 + timedelta(seconds=start) if start is not None else None,
                    finished_at=T0 + timedelta(seconds=finish) if finish is not None else None,
                )
            )
        await db.commit()
    return run_id


async def _upsert(factory, run_id, timings=None):
    from app.services.suite_execution_service import upsert_for_run

    async with factory() as db:
        summary = await upsert_for_run(db, run_id, timings)
        await db.commit()
    return summary


async def _rows(factory, run_id):
    from app.models.postgres import SuiteExecution

    async with factory() as db:
        result = await db.execute(
            select(SuiteExecution).where(SuiteExecution.test_run_id == run_id).order_by(SuiteExecution.suite_key)
        )
        return list(result.scalars().all())


async def _run(factory, run_id):
    from app.models.postgres import TestRun

    async with factory() as db:
        return await db.get(TestRun, run_id)


# ── Migration ────────────────────────────────────────────────────────────────


async def test_migration_creates_the_table_indexes_and_run_columns_and_downgrades_exactly(factory):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location("migration_0203_pg", MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("0203", "0202")

    async def shape(conn):
        tables = (await conn.execute(text("SELECT to_regclass('suite_executions') IS NOT NULL"))).scalar_one()
        run_columns = {
            row[0]
            for row in await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'test_runs' "
                    "AND column_name IN ('exec_started_at', 'exec_finished_at', 'wall_clock_ms', 'timing_source')"
                )
            )
        }
        indexes = {
            row[0]: row[1]
            for row in await conn.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'suite_executions'")
            )
        }
        return tables, run_columns, indexes

    def run_operation(sync_conn, operation):
        context = MigrationContext.configure(sync_conn)
        with Operations.context(context):
            operation()

    engine = factory.kw["bind"]
    async with engine.connect() as conn:
        tables, run_columns, indexes = await shape(conn)
        assert tables is True
        assert run_columns == {"exec_started_at", "exec_finished_at", "wall_clock_ms", "timing_source"}
        assert "run_created_at DESC" in indexes["ix_suite_executions_project_suite_run"]
        assert "(project_id, suite_key, run_created_at DESC)" in indexes["ix_suite_executions_project_suite_run"]
        assert "(test_run_id)" in indexes["ix_suite_executions_test_run_id"]
        assert "(test_run_id, suite_key)" in indexes["uq_suite_executions_run_suite"]
        await conn.rollback()  # end the autobegun read; the DDL below gets its own transaction

        tx = await conn.begin()
        try:
            await conn.execute(text("SET LOCAL lock_timeout = '10s'"))
            await conn.run_sync(run_operation, migration.downgrade)
            assert await shape(conn) == (False, set(), {})
            await conn.run_sync(run_operation, migration.upgrade)
            tables, run_columns, indexes = await shape(conn)
            assert tables is True and len(run_columns) == 4
            assert set(indexes) == {
                "suite_executions_pkey",
                "uq_suite_executions_run_suite",
                "ix_suite_executions_project_suite_run",
                "ix_suite_executions_test_run_id",
            }
        finally:
            await tx.rollback()


# ── Upsert ───────────────────────────────────────────────────────────────────


async def test_parallel_suites_upsert_idempotently_and_duration_ms_keeps_its_sum(factory, project):
    cases = [
        ("Checkout", 4000, 0.0, 4.0),
        ("Checkout", 5000, 4.0, 9.0),
        ("Search", 4200, 0.2, 4.4),
        ("Search", 4200, 4.4, 8.6),
    ]
    run_id = await _seed_run(factory, project, cases, duration_ms=17400)

    first = await _upsert(factory, run_id)
    assert first["timing_source"] == "derived"
    assert first["wall_clock_ms"] == 9000 and first["summed_ms"] == 17400
    assert first["parallelism"] == 1.93
    rows = await _rows(factory, run_id)
    assert [(r.suite_key, r.source, r.wall_ms, r.summed_ms, r.test_count, r.failed) for r in rows] == [
        ("checkout", "derived", 9000, 9000, 2, 1),
        ("search", "derived", 8400, 8400, 2, 0),
    ]
    run = await _run(factory, run_id)
    assert (run.wall_clock_ms, run.timing_source, run.duration_ms) == (9000, "derived", 17400)
    assert run.exec_started_at == T0 and run.exec_finished_at == T0 + timedelta(seconds=9)

    # A re-finalize: same rows (same ids), counts not doubled.
    ids = {r.suite_key: r.id for r in rows}
    await _upsert(factory, run_id)
    again = await _rows(factory, run_id)
    assert {r.suite_key: r.id for r in again} == ids
    assert [r.test_count for r in again] == [2, 2]

    # Report timing arrives (the upload path passes it): the same rows become ``report``.
    timings = {"run_started_at": None, "run_finished_at": None, "run_wall_ms": None,
               "suites": [{"name": "checkout", "started_at": T0 - timedelta(seconds=1), "wall_ms": 10500,
                           "setup_ms": 400}]}
    await _upsert(factory, run_id, timings)
    report = {r.suite_key: r for r in await _rows(factory, run_id)}
    assert report["checkout"].id == ids["checkout"]
    assert (report["checkout"].source, report["checkout"].wall_ms, report["checkout"].setup_ms) == ("report", 10500, 400)
    assert report["search"].source == "derived"
    run = await _run(factory, run_id)
    assert run.duration_ms == 17400, "test_runs.duration_ms keeps its summed meaning"
    assert run.wall_clock_ms == 10500 and run.timing_source == "derived"


async def test_a_sum_only_run_has_no_wall_clock(factory, project):
    run_id = await _seed_run(factory, project, [("AuthSuite", 1234, None, None), ("AuthSuite", 892, None, None)])
    summary = await _upsert(factory, run_id)
    assert summary["timing_source"] == "derived_sum" and summary["wall_clock_ms"] is None
    (row,) = await _rows(factory, run_id)
    assert (row.source, row.wall_ms, row.parallelism, row.summed_ms) == ("derived_sum", None, None, 2126)
    run = await _run(factory, run_id)
    assert (run.wall_clock_ms, run.exec_started_at, run.timing_source) == (None, None, "derived_sum")


async def test_a_live_stream_run_groups_under_its_label_and_a_lost_suite_loses_its_row(factory, project):
    from app.models.postgres import TestCase

    # Old-SDK live runs stamp the test class on every case; the run label is the suite.
    run_id = await _seed_run(
        factory, project,
        [("com.example.OrderTests", 100, None, None), ("com.example.CartTests", 200, None, None)],
        trigger_source="live_stream", primary_suite_name="API Regression",
    )
    await _upsert(factory, run_id)
    (row,) = await _rows(factory, run_id)
    assert (row.suite_name, row.suite_key, row.test_count, row.summed_ms) == ("API Regression", "api regression", 2, 300)

    # The same cases as a file upload: per-row suites, and the label row is gone.
    from app.models.postgres import TestRun

    async with factory() as db:
        run = await db.get(TestRun, run_id)
        run.trigger_source = "api"
        await db.commit()
    await _upsert(factory, run_id)
    assert [r.suite_key for r in await _rows(factory, run_id)] == ["com.example.carttests", "com.example.ordertests"]

    async with factory() as db:
        await db.execute(delete(TestCase).where(TestCase.test_run_id == run_id, TestCase.suite_name == "com.example.CartTests"))
        await db.commit()
    await _upsert(factory, run_id)
    assert [r.suite_key for r in await _rows(factory, run_id)] == ["com.example.ordertests"]


# ── Backfill ─────────────────────────────────────────────────────────────────


async def test_backfill_walks_100_runs_in_chunks_and_a_rerun_writes_nothing_twice(factory, project):
    from app.models.postgres import SuiteExecution
    from app.services.suite_execution_service import advance_state, backfill_chunk, cursor_from_state

    window = datetime(2001, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=uuid.uuid4().int % 500_000)
    run_ids = []
    for index in range(100):
        run_ids.append(
            await _seed_run(
                factory, project,
                [("Suite A", 100 + index, None, None), ("Suite B", 50, None, None)],
                created_at=window + timedelta(seconds=index), duration_ms=150 + index,
            )
        )
    # One run a finalize already timed from its report: the backfill must leave it alone.
    timed = run_ids[37]
    await _upsert(factory, timed, {"suites": [{"name": "Suite A", "started_at": T0, "wall_ms": 999}]})

    start = (window - timedelta(seconds=1), uuid.UUID(int=0))
    state: dict = {"created_at": start[0].isoformat(), "id": str(start[1])}
    for expected_last in (24, 49, 74, 99):
        async with factory() as db:
            chunk = await backfill_chunk(db, cursor_from_state(state), limit=25)
            await db.commit()
        assert chunk.scanned == 25
        assert chunk.last_id == run_ids[expected_last], "the cursor advances in (created_at, id) order"
        assert chunk.project_ids == [project]
        state = advance_state(state, chunk)
    assert (state["scanned"], state["written"], state["skipped"]) == (100, 99, 1)

    async with factory() as db:
        counts = (await db.execute(text(
            "SELECT count(*), count(DISTINCT test_run_id), "
            "count(*) FILTER (WHERE source = 'derived_sum'), count(*) FILTER (WHERE source = 'report') "
            "FROM suite_executions WHERE test_run_id = ANY(:ids)"
        ), {"ids": run_ids})).one()
        durations = (await db.execute(text(
            "SELECT count(*) FROM test_runs WHERE id = ANY(:ids) AND duration_ms = 150 + "
            "(EXTRACT(EPOCH FROM created_at - CAST(:window AS timestamptz)))::int "
            "AND timing_source IS NOT NULL"
        ), {"ids": run_ids, "window": window})).scalar_one()
    assert tuple(counts) == (200, 100, 199, 1)
    assert durations == 100, "no run's duration_ms changed, and every run is timed"

    # Re-run from the start: everything is timed now, nothing is written, still no duplicates.
    async with factory() as db:
        rerun = await backfill_chunk(db, start, limit=100)
        await db.commit()
    assert (rerun.scanned, rerun.written, rerun.skipped) == (100, 0, 100)
    async with factory() as db:
        report_row = (await db.execute(
            select(SuiteExecution).where(SuiteExecution.test_run_id == timed, SuiteExecution.suite_key == "suite a")
        )).scalar_one()
        total = (await db.execute(text(
            "SELECT count(*) FROM suite_executions WHERE test_run_id = ANY(:ids)"
        ), {"ids": run_ids})).scalar_one()
    assert (report_row.source, report_row.wall_ms) == ("report", 999)
    assert total == 200
