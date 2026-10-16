"""Migration 0214 (W07 FAIL-05) on real PostgreSQL: ``test_runs.ai_recommendation``.

The backfill's SQL is a FROZEN twin of ``run_verdict.ai_recommendation_of``.
This binds the two: every ``CASES`` payload (plus a few shapes the table does
not cover) is stored as a servable snapshot, the upgrade runs, and each run's
``ai_recommendation`` must equal the live function's answer for its payload.
Also: a snapshot below the frozen schema version projects nothing, the backfill
leaves ``updated_at`` where it was, keyset paging over several batches gives the
same result, the index is VALID (``pg_index.indisvalid``), re-running the
backfill and the index build is a no-op, and the downgrade removes both.

The migration runs through a real Alembic ``Operations`` context against a
SCRATCH schema (``test_runs`` and ``run_intelligence_snapshots`` as ``LIKE ...
INCLUDING DEFAULTS``, ``test_runs`` without the new column). A CONCURRENT index
build cannot run inside a transaction, so this work commits; the scratch schema
is dropped afterwards and the shared tables are never written.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.services.run_verdict import ai_recommendation_of
from tests.test_run_verdict import CASES

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
M0214 = "0214_test_run_ai_recommendation.py"
INDEX = "ix_test_runs_project_recommendation_created"
EARLIER = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

# Shapes beyond the case table: each exercises one clause of the frozen CASE.
EXTRA = [
    # verified + published but the verification disagrees: not displayable, envelope -> NULL
    {"structured_summary": {"decision_intelligence": {"verification": {"status": "passed"},
                                                       "release_decision": {"recommendation": "GO"}},
                            "decision_report_verification": {"status": "failed"},
                            "latest_decision_attempt": {"status": "published", "verification_status": "passed"}},
     "release_decision": {"recommendation": "NO_GO"}},
    # only a verification object: truthy envelope -> NULL, no fallback
    {"structured_summary": {"decision_report_verification": {"status": "passed"}},
     "release_decision": {"recommendation": "GO"}},
    # an attempt of false is falsy: the release decision answers
    {"structured_summary": {"latest_decision_attempt": False, "decision_report_verification": 0.0},
     "release_decision": {"recommendation": "CONDITIONAL_GO"}},
    # a release decision that is a string: no recommendation
    {"release_decision": "GO"},
    # an empty array summary: not an object, the release decision answers
    {"structured_summary": [], "release_decision": {"recommendation": "GO"}},
    # a NUL in some string (json holds the \u0000 escape, jsonb refuses it): still projected
    {"top_analyses": [{"error": "boom\u0000tail"}], "release_decision": {"recommendation": "NO_GO"}},
]
PAYLOADS = [case["full"] for case in CASES] + EXTRA


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _migration():
    spec = importlib.util.spec_from_file_location("m_0214_pg", VERSIONS / M0214)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def conn():
    """A connection whose work COMMITS (the index is built CONCURRENTLY); the scratch schema is dropped afterwards."""
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as connection:
            connection.info["scratch_schemas"] = []
            try:
                yield connection
            finally:
                await connection.rollback()
                await connection.execute(text("RESET search_path"))
                for schema in connection.info["scratch_schemas"]:
                    await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                await connection.commit()
    finally:
        await engine.dispose()


async def _scratch(conn) -> str:
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    scratch = f"w07a_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    await conn.execute(text(f'CREATE TABLE "{scratch}".test_runs (LIKE "{base}".test_runs INCLUDING DEFAULTS)'))
    # The table as 0213 left it: no ai_recommendation yet.
    await conn.execute(text(f'ALTER TABLE "{scratch}".test_runs DROP COLUMN IF EXISTS ai_recommendation'))
    await conn.execute(text(f'ALTER TABLE "{scratch}".test_runs ADD PRIMARY KEY (id)'))
    await conn.execute(text(
        f'CREATE TABLE "{scratch}".run_intelligence_snapshots '
        f'(LIKE "{base}".run_intelligence_snapshots INCLUDING DEFAULTS)'
    ))
    await conn.execute(text(f'SET search_path TO "{scratch}", "{base}"'))
    await conn.commit()
    return scratch


async def _seed(conn) -> dict[uuid.UUID, object]:
    project = uuid.uuid4()
    expected: dict[uuid.UUID, object] = {}
    for index, payload in enumerate(PAYLOADS):
        run = await _seed_run(conn, project, f"case-{index}", payload, version=5)
        expected[run] = ai_recommendation_of(payload)
    # Below the frozen schema version: not servable, projects nothing (CASES[0] would be GO).
    old = await _seed_run(conn, project, "old-version", CASES[0]["full"], version=4)
    expected[old] = None
    await conn.commit()
    return expected


async def _seed_run(conn, project, build, payload, *, version) -> uuid.UUID:
    run = uuid.uuid4()
    await conn.execute(text(
        "INSERT INTO test_runs (id, project_id, build_number, status, ingestion_source, created_at, updated_at) "
        "VALUES (:id, :p, :b, 'FAILED', 'unknown', :at, :at)"
    ), {"id": run, "p": project, "b": build, "at": EARLIER})
    await conn.execute(text(
        "INSERT INTO run_intelligence_snapshots (id, run_id, schema_version, payload) "
        "VALUES (gen_random_uuid(), :r, :v, CAST(:p AS json))"
    ), {"r": run, "v": version, "p": json.dumps(payload)})
    return run


async def _migrate(conn, step: str) -> None:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    module = _migration()

    def _run(sync_conn):
        context = MigrationContext.configure(sync_conn)
        module.op = Operations(context)
        # As a real run: alembic owns the transaction, so autocommit_block() can commit it.
        with context.begin_transaction():
            getattr(module, step)()

    await conn.commit()
    await conn.run_sync(_run)


async def _stored(conn) -> dict[uuid.UUID, tuple]:
    rows = (await conn.execute(text("SELECT id, ai_recommendation, updated_at FROM test_runs"))).all()
    return {rid: (rec, at) for rid, rec, at in rows}


async def _columns(conn, scratch: str) -> set[str]:
    rows = (await conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = :s AND table_name = 'test_runs'"
    ), {"s": scratch})).scalars().all()
    return set(rows)


async def _index_state(conn, scratch: str) -> list[tuple[bool, str]]:
    rows = (await conn.execute(text(
        "SELECT i.indisvalid, pg_get_indexdef(i.indexrelid) FROM pg_index i "
        "JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname = :name AND n.nspname = :s"
    ), {"name": INDEX, "s": scratch})).all()
    return [(bool(valid), definition) for valid, definition in rows]


async def test_0214_backfills_the_live_functions_answer_and_round_trips(conn):
    scratch = await _scratch(conn)
    expected = await _seed(conn)
    assert any(value is not None for value in expected.values())
    assert any(value is None for value in expected.values())

    await _migrate(conn, "upgrade")
    stored = await _stored(conn)
    assert {rid: rec for rid, (rec, _) in stored.items()} == expected
    assert {at for _, at in stored.values()} == {EARLIER}, "the backfill never moves updated_at"
    ((valid, definition),) = await _index_state(conn, scratch)
    assert valid, "the CONCURRENT build left a VALID index"
    assert "(project_id, ai_recommendation, created_at)" in definition

    # Keyset paging over several batches gives the same answer, and re-running is a no-op.
    migration = _migration()
    await conn.execute(text("UPDATE test_runs SET ai_recommendation = NULL, updated_at = :at"), {"at": EARLIER})
    await conn.commit()
    updated, seen = await conn.run_sync(lambda sync_conn: migration.backfill(sync_conn, batch=3))
    await conn.commit()
    assert seen == len(expected) - 1  # the version-4 snapshot is not a candidate
    assert updated == seen
    assert {rid: rec for rid, (rec, _) in (await _stored(conn)).items()} == expected
    await conn.execute(text(
        f"CREATE INDEX IF NOT EXISTS {INDEX} ON test_runs (project_id, {migration.COLUMN}, created_at)"
    ))
    await conn.commit()
    assert [valid for valid, _ in await _index_state(conn, scratch)] == [True]

    await _migrate(conn, "downgrade")
    assert "ai_recommendation" not in await _columns(conn, scratch)
    assert await _index_state(conn, scratch) == []

    # The round trip: the upgrade runs again on the downgraded table.
    await _migrate(conn, "upgrade")
    assert {rid: rec for rid, (rec, _) in (await _stored(conn)).items()} == expected
    assert [valid for valid, _ in await _index_state(conn, scratch)] == [True]
