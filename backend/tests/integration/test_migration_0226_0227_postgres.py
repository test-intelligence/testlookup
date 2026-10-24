"""Migrations 0226 and 0227 (W12 SRCH-02 / SRCH-05) on real PostgreSQL.

* 0226: an existing ``semantic_reindex_jobs`` row reads
  ``embedder_key = 'default'`` and ``collection = 'test_case_search'`` after
  the upgrade (every pre-0226 job is a legacy-generation rebuild of
  ``test_case_search``); a row written later keeps what it is given.
* 0227: ``ck_search_query_events_nonnegative`` refuses a negative
  ``latency_ms`` (and a negative count); deleting a project deletes its
  events (``ON DELETE CASCADE``); an All-Projects row (NULL project) is
  accepted; the defaults fill ``word_count`` / ``filter_count`` /
  ``counts_exact`` / ``created_at``.
* upgrade -> downgrade 0225 -> upgrade again works and leaves exactly the
  head shape.

The migrations run through a real Alembic ``Operations`` context against a
SCRATCH schema: ``semantic_reindex_jobs`` copied as 0225 left it (``LIKE ...
INCLUDING DEFAULTS`` minus the two new columns) and a one-column ``projects``
(the FK target). The scratch schema is dropped afterwards; the shared tables
are never written, and the last test checks they carry head's table, check,
indexes and columns.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
M0226 = "0226_semantic_reindex_job_generation.py"
M0227 = "0227_search_query_events.py"
JOB_COLUMNS = ("collection", "embedder_key")
INDEXES = ("ix_search_query_events_created", "ix_search_query_events_project_created")
EVENT_COLUMNS = {
    "id", "project_id", "q_hash", "q_length", "word_count", "filter_count", "entity_scope",
    "mode_requested", "mode_used", "fallback_reason", "result_count", "counts_exact", "latency_ms",
    "created_at",
}


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _migration(filename: str):
    spec = importlib.util.spec_from_file_location(f"m_{filename[:4]}_pg", VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def conn():
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
    scratch = f"w12a_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    await conn.execute(text(
        f'CREATE TABLE "{scratch}".semantic_reindex_jobs (LIKE "{base}".semantic_reindex_jobs INCLUDING DEFAULTS)'
    ))
    for column in JOB_COLUMNS:
        await conn.execute(text(f'ALTER TABLE "{scratch}".semantic_reindex_jobs DROP COLUMN {column}'))
    await conn.execute(text(f'CREATE TABLE "{scratch}".projects (id uuid PRIMARY KEY)'))
    await conn.execute(text(f'SET search_path TO "{scratch}", "{base}"'))
    await conn.commit()
    return scratch


async def _run(conn, filename: str, step: str) -> None:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    module = _migration(filename)

    def _go(sync_conn):
        context = MigrationContext.configure(sync_conn)
        module.op = Operations(context)
        with context.begin_transaction():
            getattr(module, step)()

    await conn.commit()
    await conn.run_sync(_go)
    await conn.commit()


async def _upgrade(conn) -> None:
    await _run(conn, M0226, "upgrade")
    await _run(conn, M0227, "upgrade")


async def _downgrade_to_0225(conn) -> None:
    await _run(conn, M0227, "downgrade")
    await _run(conn, M0226, "downgrade")


async def _rows(conn, sql: str, **params):
    rows = (await conn.execute(text(sql), params)).all()
    await conn.commit()
    return rows


async def _columns(conn, schema: str, table: str) -> set[str]:
    rows = await _rows(
        conn, "SELECT column_name FROM information_schema.columns WHERE table_schema = :s AND table_name = :t",
        s=schema, t=table,
    )
    return {r[0] for r in rows}


async def _tables(conn, schema: str) -> set[str]:
    rows = await _rows(conn, "SELECT table_name FROM information_schema.tables WHERE table_schema = :s", s=schema)
    return {r[0] for r in rows}


async def _indexes(conn, schema: str) -> dict[str, str]:
    rows = await _rows(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = :s AND tablename = 'search_query_events'",
        s=schema,
    )
    return {r[0]: r[1] for r in rows}


async def _check_def(conn, schema: str) -> str | None:
    rows = await _rows(
        conn,
        "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = :s AND c.conname = 'ck_search_query_events_nonnegative'",
        s=schema,
    )
    return rows[0][0] if rows else None


async def _job(conn, scope_key: str, **extra) -> None:
    columns = ["scope_key", "job_id", "high_water_created_at", "high_water_id", *extra]
    values = [":scope", ":job", "now()", ":hw", *(f":{k}" for k in extra)]
    await conn.execute(
        text(f"INSERT INTO semantic_reindex_jobs ({', '.join(columns)}) VALUES ({', '.join(values)})"),
        {"scope": scope_key, "job": uuid.uuid4(), "hw": uuid.uuid4(), **extra},
    )
    await conn.commit()


async def _project(conn) -> uuid.UUID:
    pid = uuid.uuid4()
    await conn.execute(text("INSERT INTO projects (id) VALUES (:id)"), {"id": pid})
    await conn.commit()
    return pid


async def _event(conn, project_id: uuid.UUID | None, **overrides) -> uuid.UUID:
    row = {
        "id": uuid.uuid4(), "project_id": project_id, "q_hash": "ab" * 32, "q_length": 8,
        "entity_scope": "all", "mode_requested": "keyword", "mode_used": "keyword", "result_count": 3,
        "latency_ms": 40, **overrides,
    }
    await conn.execute(text(
        "INSERT INTO search_query_events (id, project_id, q_hash, q_length, entity_scope, mode_requested, "
        "mode_used, result_count, latency_ms) VALUES (:id, :project_id, :q_hash, :q_length, :entity_scope, "
        ":mode_requested, :mode_used, :result_count, :latency_ms)"
    ), row)
    await conn.commit()
    return row["id"]


async def test_0226_backfills_existing_jobs_to_the_legacy_generation(conn):
    await _scratch(conn)
    await _job(conn, "global")
    await _job(conn, f"project:{uuid.uuid4()}")

    await _run(conn, M0226, "upgrade")

    assert await _rows(conn, "SELECT DISTINCT embedder_key, collection FROM semantic_reindex_jobs") == [
        ("default", "test_case_search"),
    ]
    # A row written after the upgrade keeps what it is given (the backfill ran once).
    await _job(conn, "generation:e3f9a1c2b", collection="test_case_search__e3f9a1c2b", embedder_key="ollama:nomic-embed-text:v1.5")
    await _job(conn, "global-later")
    assert await _rows(
        conn, "SELECT scope_key, embedder_key, collection FROM semantic_reindex_jobs "
        "WHERE scope_key IN ('generation:e3f9a1c2b', 'global-later') ORDER BY scope_key",
    ) == [
        ("generation:e3f9a1c2b", "ollama:nomic-embed-text:v1.5", "test_case_search__e3f9a1c2b"),
        ("global-later", None, None),
    ]


async def test_0227_check_cascade_null_project_and_defaults(conn):
    scratch = await _scratch(conn)
    await _upgrade(conn)
    assert "search_query_events" in await _tables(conn, scratch)
    project = await _project(conn)
    other = await _project(conn)

    kept = await _event(conn, other)
    await _event(conn, project)
    await _event(conn, project, result_count=0)
    all_projects = await _event(conn, None)
    assert await _rows(
        conn, "SELECT word_count, filter_count, counts_exact, created_at IS NOT NULL, fallback_reason "
        "FROM search_query_events WHERE id = :id", id=all_projects,
    ) == [(0, 0, True, True, None)]

    with pytest.raises(IntegrityError):
        await _event(conn, project, latency_ms=-1)
    await conn.rollback()
    with pytest.raises(IntegrityError):
        await _event(conn, project, result_count=-5)
    await conn.rollback()

    await conn.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project})
    await conn.commit()
    assert sorted(r[0] for r in await _rows(conn, "SELECT id FROM search_query_events")) == sorted(
        [kept, all_projects]
    ), "the deleted project's events cascade; another project's and the All-Projects row stay"


async def test_upgrade_downgrade_0225_upgrade_again(conn):
    scratch = await _scratch(conn)
    await _job(conn, "global")
    await _upgrade(conn)
    assert set(JOB_COLUMNS) <= await _columns(conn, scratch, "semantic_reindex_jobs")
    assert set(await _indexes(conn, scratch)) == {*INDEXES, "search_query_events_pkey"}

    await _downgrade_to_0225(conn)
    assert "search_query_events" not in await _tables(conn, scratch)
    assert not set(JOB_COLUMNS) & await _columns(conn, scratch, "semantic_reindex_jobs")
    assert await _indexes(conn, scratch) == {}
    assert await _rows(conn, "SELECT scope_key FROM semantic_reindex_jobs") == [("global",)], (
        "the downgrade drops columns, never a job row"
    )

    await _upgrade(conn)                                           # upgrade again
    assert set(JOB_COLUMNS) <= await _columns(conn, scratch, "semantic_reindex_jobs")
    assert await _columns(conn, scratch, "search_query_events") == EVENT_COLUMNS
    assert await _rows(conn, "SELECT embedder_key, collection FROM semantic_reindex_jobs") == [
        ("default", "test_case_search"),
    ]


async def test_the_shared_tables_carry_heads_table_check_indexes_and_columns(conn):
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    await conn.commit()
    assert await _columns(conn, base, "search_query_events") == EVENT_COLUMNS
    assert set(JOB_COLUMNS) <= await _columns(conn, base, "semantic_reindex_jobs")
    indexes = await _indexes(conn, base)
    assert set(INDEXES) <= set(indexes)
    assert "(project_id, created_at DESC)" in indexes["ix_search_query_events_project_created"]
    assert "(created_at)" in indexes["ix_search_query_events_created"]
    check = await _check_def(conn, base)
    assert check is not None
    for column in ("q_length", "word_count", "filter_count", "result_count", "latency_ms"):
        assert f"{column} >= 0" in check, column
