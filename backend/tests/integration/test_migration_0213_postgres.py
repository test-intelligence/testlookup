"""Migration 0213 (DEF-01) on real PostgreSQL: ``defects.assignee_user_id``.

The upgrade adds the nullable column with its FK (``ON DELETE SET NULL``) and
builds the partial index CONCURRENTLY: the index is VALID afterwards
(``pg_index.indisvalid``), deleting the assigned user leaves the defect
unassigned, re-running the index build is a no-op (``IF NOT EXISTS``), and the
downgrade removes both. A round trip (up, down, up) works.

The migration runs through a real Alembic ``Operations`` context against a
SCRATCH schema (``defects`` as ``LIKE ... INCLUDING DEFAULTS`` without the new
column, ``users`` a one-column stand-in). A CONCURRENT index build cannot run
inside a transaction, so this work commits; the scratch schema is dropped
afterwards and the shared tables are never written.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
M0213 = "0213_defect_assignee.py"
INDEX = "ix_defects_project_assignee"


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _migration():
    spec = importlib.util.spec_from_file_location("m_0213_pg", VERSIONS / M0213)
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
    scratch = f"w06a_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    await conn.execute(text(f'CREATE TABLE "{scratch}".defects (LIKE "{base}".defects INCLUDING DEFAULTS)'))
    # The table as 0212 left it: no assignee column yet.
    await conn.execute(text(f'ALTER TABLE "{scratch}".defects DROP COLUMN IF EXISTS assignee_user_id'))
    await conn.execute(text(f'ALTER TABLE "{scratch}".defects ADD PRIMARY KEY (id)'))
    await conn.execute(text(f'CREATE TABLE "{scratch}".users (id uuid PRIMARY KEY)'))
    await conn.execute(text(f'SET search_path TO "{scratch}", "{base}"'))
    await conn.commit()
    return scratch


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


async def _columns(conn, scratch: str) -> dict[str, str]:
    rows = (await conn.execute(text(
        "SELECT column_name, is_nullable FROM information_schema.columns "
        "WHERE table_schema = :s AND table_name = 'defects'"
    ), {"s": scratch})).all()
    return {name: nullable for name, nullable in rows}


async def _index_state(conn, scratch: str) -> list[tuple[bool, str]]:
    rows = (await conn.execute(text(
        "SELECT i.indisvalid, pg_get_indexdef(i.indexrelid) FROM pg_index i "
        "JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname = :name AND n.nspname = :s"
    ), {"name": INDEX, "s": scratch})).all()
    return [(bool(valid), definition) for valid, definition in rows]


async def _defect(conn, assignee) -> uuid.UUID:
    did = uuid.uuid4()
    await conn.execute(text(
        "INSERT INTO defects (id, project_id, resolution_status, is_duplicate, approval_status, "
        "recurrence_count, external_status_conflict, assignee_user_id) "
        "VALUES (:id, gen_random_uuid(), 'OPEN', false, 'approved', 0, false, :a)"
    ), {"id": did, "a": assignee})
    return did


async def test_0213_adds_a_valid_partial_index_and_set_null_fk_and_round_trips(conn):
    scratch = await _scratch(conn)

    await _migrate(conn, "upgrade")
    assert _columns_has(await _columns(conn, scratch), "assignee_user_id", nullable="YES")
    ((valid, definition),) = await _index_state(conn, scratch)
    assert valid, "the CONCURRENT build left a VALID index"
    assert "(project_id, assignee_user_id)" in definition
    assert "WHERE (assignee_user_id IS NOT NULL)" in definition

    fk = (await conn.execute(text(
        "SELECT CAST(confdeltype AS text) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = :s AND c.contype = 'f' AND c.conrelid = CAST(:t AS regclass)"
    ), {"s": scratch, "t": f'"{scratch}".defects'})).scalar_one()
    assert fk == "n", "ON DELETE SET NULL"

    # Deleting the assigned user leaves the defect unassigned, never deleted.
    user = uuid.uuid4()
    await conn.execute(text("INSERT INTO users (id) VALUES (:u)"), {"u": user})
    assigned = await _defect(conn, user)
    unassigned = await _defect(conn, None)
    await conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": user})
    rows = dict((await conn.execute(text("SELECT id, assignee_user_id FROM defects"))).all())
    assert rows == {assigned: None, unassigned: None}
    await conn.commit()

    # Re-running the index build is a no-op (IF NOT EXISTS): no error, still one valid index.
    migration = _migration()
    await conn.execute(text(
        f"CREATE INDEX IF NOT EXISTS {INDEX} ON defects (project_id, {migration.COLUMN}) WHERE {migration.INDEX_WHERE}"
    ))
    assert [valid for valid, _ in await _index_state(conn, scratch)] == [True]
    await conn.commit()

    await _migrate(conn, "downgrade")
    assert "assignee_user_id" not in await _columns(conn, scratch)
    assert await _index_state(conn, scratch) == []

    # The round trip: the upgrade runs again on the downgraded table.
    await _migrate(conn, "upgrade")
    assert "assignee_user_id" in await _columns(conn, scratch)
    assert [valid for valid, _ in await _index_state(conn, scratch)] == [True]


def _columns_has(columns: dict[str, str], name: str, *, nullable: str) -> bool:
    return columns.get(name) == nullable
