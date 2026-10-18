"""Migration 0222 (W28 test-case editing) on real PostgreSQL.

* an existing project row reads ``require_case_review = false`` after the
  upgrade (owner decision OR-04: review is off for every project until a QA
  lead turns it on), and an insert that names no value reads false too;
* the other four columns arrive nullable and empty (NULL = not recorded);
* a version row can name an assignee, and loses it (NULL) when that user is
  deleted (``ON DELETE SET NULL``, constraint ``fk_tcv_assignee_id_users``);
* the downgrade removes the five columns (and the foreign key), and a
  re-upgrade adds them again.

The migration runs through a real Alembic ``Operations`` context against a
SCRATCH schema (``LIKE ... INCLUDING DEFAULTS`` copies of the five tables with
the 0222 columns dropped, i.e. the tables as 0221 left them); the scratch
schema is dropped afterwards and the shared tables are never written.

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
M0222 = "0222_test_case_editing.py"
TABLES = ("users", "projects", "managed_test_cases", "test_case_versions", "test_plan_items")
COLUMNS = [
    ("projects", "require_case_review"),
    ("managed_test_cases", "edited_since_approval_version"),
    ("test_case_versions", "source"),
    ("test_case_versions", "assignee_id"),
    ("test_plan_items", "executed_case_version"),
]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _migration():
    spec = importlib.util.spec_from_file_location("m_0222_pg", VERSIONS / M0222)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def conn():
    """A connection whose work commits; the scratch schema is dropped afterwards."""
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
    """The five tables as 0221 left them, in a schema of their own."""
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    scratch = f"w28a_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    for table in TABLES:
        await conn.execute(text(
            f'CREATE TABLE "{scratch}".{table} (LIKE "{base}".{table} INCLUDING DEFAULTS)'
        ))
        await conn.execute(text(f'ALTER TABLE "{scratch}".{table} ADD PRIMARY KEY (id)'))
    for table, column in COLUMNS:
        await conn.execute(text(f'ALTER TABLE "{scratch}".{table} DROP COLUMN IF EXISTS {column}'))
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


async def _columns(conn, scratch: str) -> dict[tuple[str, str], tuple[str, str, str | None]]:
    rows = (await conn.execute(text(
        "SELECT table_name, column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema = :s"
    ), {"s": scratch})).all()
    return {(r[0], r[1]): (r[2], r[3], r[4]) for r in rows}


async def _foreign_keys(conn, scratch: str) -> set[str]:
    rows = (await conn.execute(text(
        "SELECT constraint_name FROM information_schema.table_constraints "
        "WHERE table_schema = :s AND table_name = 'test_case_versions' AND constraint_type = 'FOREIGN KEY'"
    ), {"s": scratch})).all()
    return {r[0] for r in rows}


async def _user(conn) -> uuid.UUID:
    user_id = uuid.uuid4()
    token = user_id.hex[:12]
    await conn.execute(text(
        "INSERT INTO users (id, email, username, hashed_password) VALUES (:id, :e, :u, 'x')"
    ), {"id": user_id, "e": f"w28-{token}@example.invalid", "u": f"w28-{token}"})
    return user_id


async def test_an_existing_project_reads_review_off_and_the_columns_arrive(conn):
    scratch = await _scratch(conn)
    existing = uuid.uuid4()
    await conn.execute(text(
        "INSERT INTO projects (id, name, slug) VALUES (:id, 'Checkout', :slug)"
    ), {"id": existing, "slug": f"checkout-{existing.hex[:8]}"})
    await conn.commit()

    await _run(conn, "upgrade")

    assert (await conn.execute(text(
        "SELECT require_case_review FROM projects WHERE id = :id"
    ), {"id": existing})).scalar_one() is False
    fresh = uuid.uuid4()
    await conn.execute(text(
        "INSERT INTO projects (id, name, slug) VALUES (:id, 'Payments', :slug)"
    ), {"id": fresh, "slug": f"payments-{fresh.hex[:8]}"})
    assert (await conn.execute(text(
        "SELECT require_case_review FROM projects WHERE id = :id"
    ), {"id": fresh})).scalar_one() is False

    columns = await _columns(conn, scratch)
    assert columns[("projects", "require_case_review")][:2] == ("boolean", "NO")
    assert "false" in (columns[("projects", "require_case_review")][2] or "")
    assert columns[("managed_test_cases", "edited_since_approval_version")][:2] == ("integer", "YES")
    assert columns[("test_case_versions", "source")][:2] == ("character varying", "YES")
    assert columns[("test_case_versions", "assignee_id")][:2] == ("uuid", "YES")
    assert columns[("test_plan_items", "executed_case_version")][:2] == ("integer", "YES")
    assert "fk_tcv_assignee_id_users" in await _foreign_keys(conn, scratch)


async def test_a_version_loses_its_assignee_when_the_user_is_deleted(conn):
    await _scratch(conn)
    await _run(conn, "upgrade")

    owner = await _user(conn)
    project_id, case_id, version_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await conn.execute(text(
        "INSERT INTO projects (id, name, slug) VALUES (:id, 'Checkout', :slug)"
    ), {"id": project_id, "slug": f"checkout-{project_id.hex[:8]}"})
    await conn.execute(text(
        "INSERT INTO managed_test_cases (id, project_id, title) VALUES (:id, :p, 'Pay with a card')"
    ), {"id": case_id, "p": project_id})
    await conn.execute(text(
        "INSERT INTO test_case_versions (id, test_case_id, version, title, status, source, assignee_id) "
        "VALUES (:id, :c, 1, 'Pay with a card', 'draft', 'person', :a)"
    ), {"id": version_id, "c": case_id, "a": owner})
    await conn.commit()
    assert (await conn.execute(text(
        "SELECT assignee_id, source FROM test_case_versions WHERE id = :id"
    ), {"id": version_id})).one() == (owner, "person")

    await conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": owner})
    await conn.commit()

    assert (await conn.execute(text(
        "SELECT assignee_id, source FROM test_case_versions WHERE id = :id"
    ), {"id": version_id})).one() == (None, "person")


async def test_downgrade_removes_the_five_columns_and_a_re_upgrade_adds_them(conn):
    scratch = await _scratch(conn)
    await _run(conn, "upgrade")
    columns = await _columns(conn, scratch)
    assert all(key in columns for key in COLUMNS)

    await _run(conn, "downgrade")
    columns = await _columns(conn, scratch)
    assert not [key for key in COLUMNS if key in columns]
    assert "fk_tcv_assignee_id_users" not in await _foreign_keys(conn, scratch)
    # Everything else is still there.
    assert ("projects", "allow_unreviewed_distribution") in columns
    assert ("test_case_versions", "changed_fields") in columns

    await _run(conn, "upgrade")
    columns = await _columns(conn, scratch)
    assert all(key in columns for key in COLUMNS)
    assert "fk_tcv_assignee_id_users" in await _foreign_keys(conn, scratch)
