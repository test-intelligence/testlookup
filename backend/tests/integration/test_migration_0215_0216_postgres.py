"""Migrations 0215 (FAIL-08) and 0216 (FAIL-10) on real PostgreSQL.

0215 adds the nullable ``projects.source_roots`` JSON column; 0216 creates
``classification_hints``: both CHECKs refuse a value outside their vocabulary,
the case-insensitive unique index refuses ``ECONNREFUSED`` after
``econnrefused`` for the same project and field (another field or project is
fine), deleting the project removes its hints and deleting the author keeps
them (``created_by`` NULL). Each downgrade removes exactly what its upgrade
added; a round trip works.

Scratch schema: ``projects`` as ``LIKE ... INCLUDING DEFAULTS`` without the new
column, ``users`` a one-column stand-in. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN``
and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import json
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
    scratch = f"w07a_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    await conn.execute(text(f'CREATE TABLE "{scratch}".projects (LIKE "{base}".projects INCLUDING DEFAULTS)'))
    await conn.execute(text(f'ALTER TABLE "{scratch}".projects DROP COLUMN IF EXISTS source_roots'))
    await conn.execute(text(f'ALTER TABLE "{scratch}".projects ADD PRIMARY KEY (id)'))
    await conn.execute(text(f'CREATE TABLE "{scratch}".users (id uuid PRIMARY KEY)'))
    await conn.execute(text(f'SET search_path TO "{scratch}", "{base}"'))
    await conn.commit()
    return scratch


async def _migrate(conn, filename: str, step: str) -> None:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    module = _migration(filename)

    def _run(sync_conn):
        context = MigrationContext.configure(sync_conn)
        module.op = Operations(context)
        with context.begin_transaction():
            getattr(module, step)()

    await conn.commit()
    await conn.run_sync(_run)


async def _columns(conn, scratch: str, table: str) -> dict[str, tuple[str, str]]:
    rows = (await conn.execute(text(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = :s AND table_name = :t"
    ), {"s": scratch, "t": table})).all()
    return {name: (kind, nullable) for name, kind, nullable in rows}


async def _project(conn) -> uuid.UUID:
    pid = uuid.uuid4()
    await conn.execute(text("INSERT INTO projects (id, name, slug) VALUES (:id, 'p', :s)"),
                       {"id": pid, "s": f"s-{pid.hex}"})
    return pid


async def _hint(conn, project, field, pattern, category="INFRASTRUCTURE", created_by=None) -> None:
    await conn.execute(text(
        "INSERT INTO classification_hints (id, project_id, match_field, pattern, category, created_by) "
        "VALUES (gen_random_uuid(), :p, :f, :pat, :c, :u)"
    ), {"p": project, "f": field, "pat": pattern, "c": category, "u": created_by})


async def _refused(conn, coro) -> str:
    try:
        await coro
    except IntegrityError as exc:
        await conn.rollback()
        return str(exc.orig)
    await conn.rollback()
    raise AssertionError("the database accepted it")


async def test_0215_adds_a_nullable_json_column_and_round_trips(conn):
    scratch = await _scratch(conn)
    await _migrate(conn, "0215_project_source_roots.py", "upgrade")
    assert (await _columns(conn, scratch, "projects"))["source_roots"] == ("json", "YES")
    pid = await _project(conn)
    roots = [{"prefix": "com.acme.pay", "root": "payments/src/test/java"}, {"prefix": "", "root": "src/test/java"}]
    await conn.execute(text("UPDATE projects SET source_roots = CAST(:r AS json) WHERE id = :id"),
                       {"r": json.dumps(roots), "id": pid})
    stored = (await conn.execute(text("SELECT source_roots FROM projects WHERE id = :id"), {"id": pid})).scalar_one()
    assert stored == roots, "the priority order is kept"
    await conn.commit()
    await _migrate(conn, "0215_project_source_roots.py", "downgrade")
    assert "source_roots" not in await _columns(conn, scratch, "projects")
    await _migrate(conn, "0215_project_source_roots.py", "upgrade")
    assert "source_roots" in await _columns(conn, scratch, "projects")


async def test_0216_creates_the_hints_table_with_its_rules_and_round_trips(conn):
    scratch = await _scratch(conn)
    await _migrate(conn, "0216_classification_hints.py", "upgrade")
    columns = await _columns(conn, scratch, "classification_hints")
    assert set(columns) == {
        "id", "project_id", "match_field", "pattern", "category", "note",
        "enabled", "created_by", "created_at", "updated_at",
    }
    assert [c for c, (_, nullable) in columns.items() if nullable == "YES"] in (
        ["note", "created_by"], ["created_by", "note"],
    )

    p = await _project(conn)
    other = await _project(conn)
    author = uuid.uuid4()
    await conn.execute(text("INSERT INTO users (id) VALUES (:u)"), {"u": author})
    await _hint(conn, p, "error_message", "econnrefused", created_by=author)
    await conn.commit()
    enabled = (await conn.execute(text("SELECT enabled FROM classification_hints"))).scalar_one()
    assert enabled is True, "enabled defaults to true"

    assert "ck_classification_hints_match_field" in await _refused(
        conn, _hint(conn, p, "stack_trace", "boom"))
    assert "ck_classification_hints_category" in await _refused(
        conn, _hint(conn, p, "test_name", "boom", category="UNKNOWN"))
    assert "uq_classification_hints_project_rule" in await _refused(
        conn, _hint(conn, p, "error_message", "ECONNREFUSED"))
    # Another field, or another project, may carry the same text.
    await _hint(conn, p, "test_name", "ECONNREFUSED")
    await _hint(conn, other, "error_message", "ECONNREFUSED")
    await conn.commit()

    # Deleting the author keeps the hint; deleting the project removes its hints.
    await conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": author})
    assert (await conn.execute(text(
        "SELECT created_by FROM classification_hints WHERE project_id = :p AND match_field = 'error_message'"
    ), {"p": p})).scalar_one() is None
    await conn.execute(text("DELETE FROM projects WHERE id = :p"), {"p": p})
    remaining = (await conn.execute(text("SELECT project_id FROM classification_hints"))).scalars().all()
    assert remaining == [other]
    await conn.commit()

    indexes = set((await conn.execute(text(
        "SELECT indexname FROM pg_indexes WHERE schemaname = :s AND tablename = 'classification_hints'"
    ), {"s": scratch})).scalars().all())
    assert {"ix_classification_hints_project_enabled", "uq_classification_hints_project_rule"} <= indexes

    await _migrate(conn, "0216_classification_hints.py", "downgrade")
    assert await _columns(conn, scratch, "classification_hints") == {}
    await _migrate(conn, "0216_classification_hints.py", "upgrade")
    assert "pattern" in await _columns(conn, scratch, "classification_hints")
