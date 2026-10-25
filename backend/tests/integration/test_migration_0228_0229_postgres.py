"""Migrations 0228 and 0229 (W13 TCL-03 / TCL-06) on real PostgreSQL.

* 0228: an existing project reads ``automation_target_pct = 60`` after the
  upgrade (a constant default); ``ck_projects_automation_target_pct`` refuses
  101 and -1.
* 0229: ``uq_tcrl_case_key`` refuses a second ``REQ-1`` on one case (another
  case may link it); the two checks refuse a bad link type and an empty
  normalised key; deleting the case deletes its links (CASCADE), deleting the
  person who added one keeps it (SET NULL).
* The flag row: ON on an EMPTY database (a new install), OFF on one holding a
  user (an upgraded install), and a row that already exists is left exactly as
  it is. The downgrade deletes only the untouched seed (the 0209 rule).
* upgrade -> downgrade 0227 -> upgrade again works and leaves exactly the head
  shape.

The migrations run through a real Alembic ``Operations`` context against a
SCRATCH schema: one-column ``users`` / ``managed_test_cases`` (the FK targets),
a two-column ``projects`` (0228 adds its column there) and a copy of
``feature_flags`` (``LIKE … INCLUDING ALL``: its unique key and defaults). The
scratch schema is dropped afterwards; the shared tables are never written, and
the last test checks they carry head's column, check, table, index and flag row.

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
M0228 = "0228_project_automation_target.py"
M0229 = "0229_test_case_requirement_links.py"
FLAG = "test_case_requirement_traceability"
LINK_COLUMNS = {
    "id", "project_id", "test_case_id", "requirement_key", "requirement_key_norm", "requirement_title",
    "link_type", "created_by_id", "created_at",
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
    scratch = f"w13a_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    await conn.execute(text(f'CREATE TABLE "{scratch}".users (id uuid PRIMARY KEY)'))
    await conn.execute(text(f'CREATE TABLE "{scratch}".projects (id uuid PRIMARY KEY, name text)'))
    await conn.execute(text(f'CREATE TABLE "{scratch}".managed_test_cases (id uuid PRIMARY KEY)'))
    await conn.execute(text(
        f'CREATE TABLE "{scratch}".feature_flags (LIKE "{base}".feature_flags INCLUDING ALL)'
    ))
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
    await _run(conn, M0228, "upgrade")
    await _run(conn, M0229, "upgrade")


async def _downgrade_to_0227(conn) -> None:
    await _run(conn, M0229, "downgrade")
    await _run(conn, M0228, "downgrade")


async def _rows(conn, sql: str, **params):
    rows = (await conn.execute(text(sql), params)).all()
    await conn.commit()
    return rows


async def _write(conn, sql: str, **params) -> None:
    await conn.execute(text(sql), params)
    await conn.commit()


async def _columns(conn, schema: str, table: str) -> set[str]:
    rows = await _rows(
        conn, "SELECT column_name FROM information_schema.columns WHERE table_schema = :s AND table_name = :t",
        s=schema, t=table,
    )
    return {r[0] for r in rows}


async def _constraint(conn, schema: str, name: str) -> str | None:
    rows = await _rows(
        conn,
        "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = :s AND c.conname = :c",
        s=schema, c=name,
    )
    return rows[0][0] if rows else None


async def _flag(conn) -> list[tuple]:
    return await _rows(
        conn, "SELECT enabled_global, description, rollout_percent FROM feature_flags WHERE key = :k", k=FLAG,
    )


async def _link(conn, project: uuid.UUID, case: uuid.UUID, norm: str, *, link_type: str = "verifies",
                created_by: uuid.UUID | None = None) -> uuid.UUID:
    link_id = uuid.uuid4()
    await _write(
        conn,
        "INSERT INTO test_case_requirement_links (id, project_id, test_case_id, requirement_key, "
        "requirement_key_norm, link_type, created_by_id) VALUES (:id, :p, :c, :k, :n, :t, :u)",
        id=link_id, p=project, c=case, k=norm.lower(), n=norm, t=link_type, u=created_by,
    )
    return link_id


async def test_0228_an_existing_project_reads_60_and_the_check_refuses_out_of_range(conn):
    scratch = await _scratch(conn)
    existing = uuid.uuid4()
    await _write(conn, "INSERT INTO projects (id, name) VALUES (:id, 'Payments')", id=existing)

    await _run(conn, M0228, "upgrade")

    assert "automation_target_pct" in await _columns(conn, scratch, "projects")
    assert await _rows(conn, "SELECT automation_target_pct FROM projects WHERE id = :id", id=existing) == [(60,)]
    await _write(conn, "UPDATE projects SET automation_target_pct = 0 WHERE id = :id", id=existing)
    await _write(conn, "UPDATE projects SET automation_target_pct = 100 WHERE id = :id", id=existing)
    for bad in (101, -1):
        with pytest.raises(IntegrityError):
            await _write(conn, "UPDATE projects SET automation_target_pct = :v WHERE id = :id", v=bad, id=existing)
        await conn.rollback()
    assert await _constraint(conn, scratch, "ck_projects_automation_target_pct") == (
        "CHECK (((automation_target_pct >= 0) AND (automation_target_pct <= 100)))"
    )


async def test_0229_one_key_per_case_the_checks_and_the_cascades(conn):
    await _scratch(conn)
    await _upgrade(conn)
    project, case, other_case, person = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _write(conn, "INSERT INTO projects (id, name) VALUES (:id, 'Payments')", id=project)
    await _write(conn, "INSERT INTO managed_test_cases (id) VALUES (:a), (:b)", a=case, b=other_case)
    await _write(conn, "INSERT INTO users (id) VALUES (:id)", id=person)

    first = await _link(conn, project, case, "REQ-1", created_by=person)
    assert await _rows(conn, "SELECT link_type, created_at IS NOT NULL FROM test_case_requirement_links "
                             "WHERE id = :id", id=first) == [("verifies", True)]
    with pytest.raises(IntegrityError, match="uq_tcrl_case_key"):
        await _link(conn, project, case, "REQ-1", link_type="related")
    await conn.rollback()
    await _link(conn, project, other_case, "REQ-1")                  # another case may link the same key
    with pytest.raises(IntegrityError, match="ck_tcrl_link_type"):
        await _link(conn, project, case, "REQ-2", link_type="covers")
    await conn.rollback()
    with pytest.raises(IntegrityError, match="ck_tcrl_key_norm_nonempty"):
        await _link(conn, project, case, "")
    await conn.rollback()

    await _write(conn, "DELETE FROM users WHERE id = :id", id=person)
    assert await _rows(conn, "SELECT created_by_id FROM test_case_requirement_links WHERE id = :id", id=first) == [
        (None,),
    ], "deleting the person who added a link keeps the link"
    await _write(conn, "DELETE FROM managed_test_cases WHERE id = :id", id=case)
    assert await _rows(conn, "SELECT test_case_id FROM test_case_requirement_links") == [(other_case,)], (
        "deleting the case deletes its links; another case's stay"
    )
    await _write(conn, "DELETE FROM projects WHERE id = :id", id=project)
    assert await _rows(conn, "SELECT count(*) FROM test_case_requirement_links") == [(0,)]


async def test_the_flag_is_on_for_a_new_install(conn):
    await _scratch(conn)
    await _upgrade(conn)
    (row,) = await _flag(conn)
    assert row[0] is True and row[2] == 100
    assert row[1].startswith("Requirement keys on test cases")


@pytest.mark.parametrize("used_by", ["user", "project"])
async def test_the_flag_is_off_for_an_upgraded_install(conn, used_by):
    await _scratch(conn)
    await _run(conn, M0228, "upgrade")
    if used_by == "user":
        await _write(conn, "INSERT INTO users (id) VALUES (:id)", id=uuid.uuid4())
    else:
        await _write(conn, "INSERT INTO projects (id, name) VALUES (:id, 'Payments')", id=uuid.uuid4())
    await _run(conn, M0229, "upgrade")
    (row,) = await _flag(conn)
    assert row[0] is False, "an install that has a user or a project keeps the flag OFF until an admin turns it on"


async def test_an_existing_flag_row_is_left_as_it_is_and_survives_the_downgrade(conn):
    await _scratch(conn)
    await _write(
        conn,
        "INSERT INTO feature_flags (id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
        "VALUES (:id, :k, 'made by hand', false, 40, now(), now())",
        id=uuid.uuid4(), k=FLAG,
    )
    await _upgrade(conn)
    assert await _flag(conn) == [(False, "made by hand", 40)]
    await _downgrade_to_0227(conn)
    assert await _flag(conn) == [(False, "made by hand", 40)], "the downgrade deletes only its own untouched seed"


async def test_the_downgrade_keeps_a_seed_an_admin_edited(conn):
    await _scratch(conn)
    await _upgrade(conn)
    await _write(
        conn, "UPDATE feature_flags SET enabled_global = false, updated_at = now() + interval '1 second' WHERE key = :k",
        k=FLAG,
    )
    await _downgrade_to_0227(conn)
    assert [r[0] for r in await _flag(conn)] == [False], "an edited row is kept"


async def test_upgrade_downgrade_0227_upgrade_again(conn):
    scratch = await _scratch(conn)
    await _upgrade(conn)
    assert await _columns(conn, scratch, "test_case_requirement_links") == LINK_COLUMNS
    assert len(await _flag(conn)) == 1

    await _downgrade_to_0227(conn)
    assert await _columns(conn, scratch, "test_case_requirement_links") == set()
    assert "automation_target_pct" not in await _columns(conn, scratch, "projects")
    assert await _constraint(conn, scratch, "ck_projects_automation_target_pct") is None
    assert await _flag(conn) == [], "the untouched seed goes with the downgrade"

    await _upgrade(conn)                                            # upgrade again
    assert await _columns(conn, scratch, "test_case_requirement_links") == LINK_COLUMNS
    assert "automation_target_pct" in await _columns(conn, scratch, "projects")
    assert [r[0] for r in await _flag(conn)] == [True]


async def test_the_shared_tables_carry_heads_column_check_table_index_and_flag(conn):
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    await conn.commit()
    assert "automation_target_pct" in await _columns(conn, base, "projects")
    assert await _constraint(conn, base, "ck_projects_automation_target_pct") is not None
    assert await _columns(conn, base, "test_case_requirement_links") == LINK_COLUMNS
    for name in ("uq_tcrl_case_key", "ck_tcrl_link_type", "ck_tcrl_key_norm_nonempty"):
        assert await _constraint(conn, base, name) is not None, name
    indexes = dict(await _rows(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = :s "
              "AND tablename = 'test_case_requirement_links'", s=base,
    ))
    assert "(project_id, requirement_key_norm)" in indexes["ix_tcrl_project_key"]
    assert len(await _rows(conn, f'SELECT 1 FROM "{base}".feature_flags WHERE key = :k', k=FLAG)) == 1
