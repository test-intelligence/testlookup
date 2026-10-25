"""Migration 0230 (W14 AGT-08) on real PostgreSQL.

* ``pk_project_workflow_defaults`` refuses a second row for one
  ``(project, base)`` (the other base, and another project, may have one).
* ``ck_pwd_base`` refuses ``base = 'live'``; ``ck_pwd_workflow_is_fork``
  refuses ``workflow_id = 'offline'`` (a built-in is the fallback, not a row);
  ``ck_pwd_version_positive`` refuses 0 (NULL = the latest published is fine).
* Deleting the project deletes its rows (CASCADE); deleting the person who set
  one keeps it (``updated_by`` SET NULL); ``updated_at`` defaults to now().
* upgrade -> downgrade 0229 -> upgrade again works and leaves exactly the head
  shape (and no index besides the key).

The migration runs through a real Alembic ``Operations`` context against a
SCRATCH schema holding one-column ``users`` / ``projects`` (the FK targets),
dropped afterwards; the shared tables are never written, and the last test
checks they carry head's table, key and checks.

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
M0230 = "0230_project_workflow_defaults.py"
TABLE = "project_workflow_defaults"
COLUMNS = {"project_id", "base", "workflow_id", "workflow_version", "updated_by", "updated_at"}
CONSTRAINTS = {
    "pk_project_workflow_defaults": "PRIMARY KEY (project_id, base)",
    "ck_pwd_base": "CHECK (((base)::text = ANY ((ARRAY['offline'::character varying, 'deep'::character varying])::text[])))",
    "ck_pwd_workflow_is_fork": "CHECK (((workflow_id)::text ~~ 'wf.%'::text))",
    "ck_pwd_version_positive": "CHECK (((workflow_version IS NULL) OR (workflow_version >= 1)))",
}


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _migration():
    spec = importlib.util.spec_from_file_location("m_0230_pg", VERSIONS / M0230)
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
    scratch = f"w14a_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    await conn.execute(text(f'CREATE TABLE "{scratch}".users (id uuid PRIMARY KEY)'))
    await conn.execute(text(f'CREATE TABLE "{scratch}".projects (id uuid PRIMARY KEY)'))
    await conn.execute(text(f'SET search_path TO "{scratch}", "{base}"'))
    await conn.commit()
    return scratch


async def _run(conn, step: str) -> None:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    module = _migration()

    def _go(sync_conn):
        context = MigrationContext.configure(sync_conn)
        module.op = Operations(context)
        with context.begin_transaction():
            getattr(module, step)()

    await conn.commit()
    await conn.run_sync(_go)
    await conn.commit()


async def _rows(conn, sql: str, **params):
    rows = (await conn.execute(text(sql), params)).all()
    await conn.commit()
    return rows


async def _write(conn, sql: str, **params) -> None:
    await conn.execute(text(sql), params)
    await conn.commit()


async def _columns(conn, schema: str) -> set[str]:
    rows = await _rows(
        conn, "SELECT column_name FROM information_schema.columns WHERE table_schema = :s AND table_name = :t",
        s=schema, t=TABLE,
    )
    return {r[0] for r in rows}


async def _constraints(conn, schema: str) -> dict[str, str]:
    rows = await _rows(
        conn,
        "SELECT c.conname, pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "JOIN pg_namespace n ON n.oid = c.connamespace JOIN pg_class t ON t.oid = c.conrelid "
        "WHERE n.nspname = :s AND t.relname = :t AND c.contype IN ('p', 'c')",
        s=schema, t=TABLE,
    )
    return dict(rows)


async def _indexes(conn, schema: str) -> set[str]:
    return {r[0] for r in await _rows(
        conn, "SELECT indexname FROM pg_indexes WHERE schemaname = :s AND tablename = :t", s=schema, t=TABLE,
    )}


async def _default(conn, project, base="offline", workflow_id="wf.my-fork", version=None, by=None) -> None:
    await _write(
        conn,
        "INSERT INTO project_workflow_defaults (project_id, base, workflow_id, workflow_version, updated_by) "
        "VALUES (:p, :b, :w, :v, :u)",
        p=project, b=base, w=workflow_id, v=version, u=by,
    )


async def test_one_row_per_project_and_base_the_checks_and_the_cascades(conn):
    await _scratch(conn)
    await _run(conn, "upgrade")
    project, other, person = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _write(conn, "INSERT INTO projects (id) VALUES (:a), (:b)", a=project, b=other)
    await _write(conn, "INSERT INTO users (id) VALUES (:id)", id=person)

    await _default(conn, project, "offline", "wf.my-fork", None, by=person)
    assert await _rows(conn, "SELECT workflow_version, updated_at IS NOT NULL FROM project_workflow_defaults "
                             "WHERE project_id = :p AND base = 'offline'", p=project) == [(None, True)]
    with pytest.raises(IntegrityError, match="pk_project_workflow_defaults"):
        await _default(conn, project, "offline", "wf.other", 2)
    await conn.rollback()
    await _default(conn, project, "deep", "wf.deep-fork", 1)          # the other base may have one
    await _default(conn, other, "offline", "wf.my-fork", 3)           # so may another project
    for kwargs, check in (
        ({"base": "live"}, "ck_pwd_base"),
        ({"workflow_id": "offline"}, "ck_pwd_workflow_is_fork"),
        ({"version": 0}, "ck_pwd_version_positive"),
    ):
        with pytest.raises(IntegrityError, match=check):
            await _default(conn, other, **{"base": "deep", **kwargs})   # other has no deep row yet
        await conn.rollback()

    await _write(conn, "DELETE FROM users WHERE id = :id", id=person)
    assert await _rows(conn, "SELECT updated_by FROM project_workflow_defaults "
                             "WHERE project_id = :p AND base = 'offline'", p=project) == [(None,)], (
        "deleting the person who set a default keeps the default"
    )
    await _write(conn, "DELETE FROM projects WHERE id = :id", id=project)
    assert await _rows(conn, "SELECT project_id, base FROM project_workflow_defaults") == [(other, "offline")], (
        "deleting the project deletes its defaults; another project's stay"
    )


async def test_upgrade_downgrade_0229_upgrade_again(conn):
    scratch = await _scratch(conn)
    await _run(conn, "upgrade")
    assert await _columns(conn, scratch) == COLUMNS
    assert await _constraints(conn, scratch) == CONSTRAINTS
    assert await _indexes(conn, scratch) == {"pk_project_workflow_defaults"}, "no index besides the key"

    await _run(conn, "downgrade")
    assert await _columns(conn, scratch) == set()
    assert await _constraints(conn, scratch) == {}

    await _run(conn, "upgrade")
    assert await _columns(conn, scratch) == COLUMNS
    assert await _constraints(conn, scratch) == CONSTRAINTS


async def test_the_shared_tables_carry_heads_table_key_and_checks(conn):
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    await conn.commit()
    assert await _columns(conn, base) == COLUMNS
    assert await _constraints(conn, base) == CONSTRAINTS
    assert await _indexes(conn, base) == {"pk_project_workflow_defaults"}
