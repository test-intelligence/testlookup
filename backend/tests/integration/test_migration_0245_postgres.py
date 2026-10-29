"""Migration 0245 (W23b FAIL-13 / FAIL-12) on real PostgreSQL.

* The upgrade adds ``test_runs.run_context``: ``jsonb``, nullable, no default;
  rows that existed before keep their values and read ``run_context IS NULL``
  ("not recorded by this reporter"); a context object round-trips as JSONB.
* ``ix_test_runs_project_branch_created`` is VALID after the CONCURRENT build
  (``pg_index.indisvalid``), and ``pg_get_indexdef`` renders
  ``(project_id, COALESCE(lower((branch)::text), ''::text), created_at)``.
* A branch-filtered walk -- the bisect narrowing's series read and its
  ``main`` / ``master`` fallback -- uses the index (``EXPLAIN`` under
  ``SET LOCAL enable_seqscan = off``).
* An INVALID leftover holding the index's name (a failed CONCURRENT build,
  planted here by a unique build over duplicate keys -- no superuser needed)
  is dropped and rebuilt as the real, VALID index.
* Downgrade removes both the index and the column; upgrading again restores
  both (the round trip).
* The shared database (migrated to head) carries the column and the index.

The migration runs through a real Alembic ``Operations`` context against a
SCRATCH schema first on the search path (``test_runs`` as ``LIKE …
INCLUDING DEFAULTS`` without the new column, as 0244 left it). A CONCURRENT
build cannot run inside a transaction, so this work commits; the scratch schema
is dropped afterwards and the shared tables are only read. The fixture awaits
``app.db.postgres.dispose_engine_for_loop()`` before building its engine and
after disposing it; it binds no Redis / Mongo client and closes none.
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
M0245 = "0245_run_context_and_bisect_index.py"
INDEX = "ix_test_runs_project_branch_created"
EXPECTED_DEF_TAIL = "USING btree (project_id, COALESCE(lower((branch)::text), ''::text), created_at)"


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _migration():
    spec = importlib.util.spec_from_file_location("m_0245_pg", VERSIONS / M0245)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def conn():
    """A connection whose work COMMITS (the index is built CONCURRENTLY); the scratch schema is dropped afterwards."""
    # Redis and Mongo are never bound here, so they are not closed here either.
    from app.db.postgres import dispose_engine_for_loop

    await dispose_engine_for_loop()
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
        await dispose_engine_for_loop()


async def _scratch(conn) -> str:
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    scratch = f"w23ba_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    await conn.execute(text(f'CREATE TABLE "{scratch}".test_runs (LIKE "{base}".test_runs INCLUDING DEFAULTS)'))
    # The table as 0244 left it: no run_context yet.
    await conn.execute(text(f'ALTER TABLE "{scratch}".test_runs DROP COLUMN IF EXISTS run_context'))
    await conn.execute(text(f'ALTER TABLE "{scratch}".test_runs ADD PRIMARY KEY (id)'))
    await conn.execute(text(f'SET search_path TO "{scratch}", "{base}"'))
    await conn.commit()
    return scratch


async def _seed(conn, project: uuid.UUID, branches: list[str | None], *, start: datetime) -> list[uuid.UUID]:
    ids = []
    for index, branch in enumerate(branches):
        run = uuid.uuid4()
        at = start + timedelta(minutes=index)
        await conn.execute(text(
            "INSERT INTO test_runs (id, project_id, build_number, branch, status, ingestion_source, "
            "created_at, updated_at) VALUES (:id, :p, :b, :branch, 'FAILED', 'unknown', :at, :at)"
        ), {"id": run, "p": project, "b": f"build-sentinel-{index}", "branch": branch, "at": at})
        ids.append(run)
    await conn.commit()
    return ids


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
    await conn.commit()


async def _column(conn, schema: str) -> list[tuple]:
    rows = (await conn.execute(text(
        "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = :s AND table_name = 'test_runs' AND column_name = 'run_context'"
    ), {"s": schema})).all()
    await conn.commit()
    return [tuple(r) for r in rows]


async def _index_state(conn, schema: str) -> list[tuple[bool, str]]:
    rows = (await conn.execute(text(
        "SELECT i.indisvalid, pg_get_indexdef(i.indexrelid) FROM pg_index i "
        "JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname = :name AND n.nspname = :s"
    ), {"name": INDEX, "s": schema})).all()
    await conn.commit()
    return [(bool(valid), definition) for valid, definition in rows]


async def _plan(conn, sql: str) -> str:
    """The plan of ``sql`` (literal values only: an EXPLAIN takes no bound parameters) with seq scans off."""
    await conn.execute(text("ANALYZE test_runs"))
    await conn.commit()
    await conn.execute(text("SET LOCAL enable_seqscan = off"))
    rows = (await conn.execute(text("EXPLAIN " + sql))).all()
    await conn.rollback()
    return "\n".join(r[0] for r in rows)


# ── the upgrade, the walk's plan, the round trip ─────────────────────────────


async def test_0245_adds_the_column_builds_a_valid_index_the_walk_uses_and_round_trips(conn):
    scratch = await _scratch(conn)
    project = uuid.uuid4()
    other = uuid.uuid4()
    start = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    branches = ["feature/checkout-sentinel", "Feature/Checkout-Sentinel", None, "main", "master", "dev"] * 40
    before = await _seed(conn, project, branches, start=start)
    await _seed(conn, other, ["feature/checkout-sentinel"] * 40, start=start)
    assert await _column(conn, scratch) == []

    await _migrate(conn, "upgrade")

    assert await _column(conn, scratch) == [("jsonb", "YES", None)]
    nulls = (await conn.execute(text(
        "SELECT count(*) FROM test_runs WHERE project_id = :p AND run_context IS NULL"
    ), {"p": project})).scalar_one()
    await conn.commit()
    assert nulls == len(before), "rows from before 0245 read 'not recorded', never a default"
    ((valid, definition),) = await _index_state(conn, scratch)
    assert valid, "the CONCURRENT build left a VALID index"
    assert definition.endswith(EXPECTED_DEF_TAIL), definition

    # A context object round-trips as JSONB.
    context = {"v": 1, "runtime": {"python": "3.11.9"}, "env": {"TZ": "Europe/Sentinel"}}
    await conn.execute(
        text("UPDATE test_runs SET run_context = CAST(:c AS jsonb) WHERE id = :id"),
        {"c": json.dumps(context), "id": before[0]},
    )
    await conn.commit()
    stored = (await conn.execute(
        text("SELECT run_context FROM test_runs WHERE id = :id"), {"id": before[0]}
    )).scalar_one()
    await conn.commit()
    assert stored == context

    # The bisect walk: one series (case-insensitive branch), newest first, bounded -- through the index.
    pivot = (start + timedelta(days=2)).isoformat()
    series = await _plan(conn, (
        f"SELECT id FROM test_runs WHERE project_id = '{project}'::uuid "
        f"AND coalesce(lower(branch), '') = 'feature/checkout-sentinel' "
        f"AND created_at <= '{pivot}'::timestamptz ORDER BY created_at DESC, id DESC LIMIT 200"
    ))
    assert INDEX in series, series
    no_branch = await _plan(conn, (
        f"SELECT id FROM test_runs WHERE project_id = '{project}'::uuid AND coalesce(lower(branch), '') = '' "
        f"ORDER BY created_at DESC, id DESC LIMIT 200"
    ))
    assert INDEX in no_branch, no_branch
    fallback = await _plan(conn, (
        f"SELECT id FROM test_runs WHERE project_id = '{project}'::uuid "
        f"AND coalesce(lower(branch), '') IN ('main', 'master') "
        f"AND created_at < '{pivot}'::timestamptz ORDER BY created_at DESC, id DESC LIMIT 200"
    ))
    assert INDEX in fallback, fallback
    # The series really is case-insensitive, and project-scoped.
    count = (await conn.execute(text(
        "SELECT count(*) FROM test_runs WHERE project_id = :p AND coalesce(lower(branch), '') = :k"
    ), {"p": project, "k": "feature/checkout-sentinel"})).scalar_one()
    await conn.commit()
    assert count == 80

    await _migrate(conn, "downgrade")
    assert await _column(conn, scratch) == []
    assert await _index_state(conn, scratch) == []

    # The round trip: the upgrade runs again on the downgraded table.
    await _migrate(conn, "upgrade")
    assert await _column(conn, scratch) == [("jsonb", "YES", None)]
    ((valid, definition),) = await _index_state(conn, scratch)
    assert valid and definition.endswith(EXPECTED_DEF_TAIL)


async def test_an_invalid_leftover_holding_the_name_is_rebuilt_as_the_real_index(conn):
    scratch = await _scratch(conn)
    project = uuid.uuid4()
    await _seed(conn, project, ["main", "main"], start=datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc))
    # A failed CONCURRENT build leaves an INVALID index behind: a UNIQUE build over duplicate keys.
    await conn.commit()
    autocommit = await conn.execution_options(isolation_level="AUTOCOMMIT")
    with pytest.raises(DBAPIError):
        await autocommit.execute(text(f"CREATE UNIQUE INDEX CONCURRENTLY {INDEX} ON test_runs (project_id)"))
    await conn.rollback()
    await conn.execution_options(isolation_level="READ COMMITTED")
    ((valid, definition),) = await _index_state(conn, scratch)
    assert not valid and "UNIQUE" in definition

    await _migrate(conn, "upgrade")

    ((valid, definition),) = await _index_state(conn, scratch)
    assert valid, "the leftover was dropped and the real index built"
    assert "UNIQUE" not in definition and definition.endswith(EXPECTED_DEF_TAIL), definition


# ── the shared database, at head ─────────────────────────────────────────────


async def test_the_shared_database_at_head_carries_the_column_and_the_index(conn):
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    head = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    await conn.commit()
    assert head >= "0245"
    assert await _column(conn, base) == [("jsonb", "YES", None)]
    ((valid, definition),) = await _index_state(conn, base)
    assert valid and definition.endswith(EXPECTED_DEF_TAIL), definition
