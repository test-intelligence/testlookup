"""Migrations 0239 / 0240 (W22a ACT-05 / ACT-04) on real PostgreSQL.

* 0239 builds ``retention_holds`` with its four CHECKs — a project hold with an
  ``entity_id``, a run hold without one, a 2-character reason and a released
  row without a release reason are each refused — and ``ix_retention_holds_live``
  carries its ``WHERE (released_at IS NULL)`` predicate.
* 0240: a ``project_retention_policies`` row written at 0239 reads
  ``keep_last_runs = 10`` after the upgrade; 1001 (and -1) are refused by
  ``ck_retention_keep_last_runs``; ``run_baselines.baseline_purged_at`` is a
  nullable timestamptz.
* Downgrade 0240 removes both columns (and the CHECK); downgrade 0239 removes
  the table; upgrading again rebuilds the same catalog.
* The shared database (migrated to head) carries all of it.

The migrations run through a real Alembic ``Operations`` context against a
SCRATCH schema first on the search path, holding only the columns the two
migrations touch or reference (``projects``, ``users``,
``project_retention_policies``, ``run_baselines``); it is dropped afterwards
and the shared tables are only read. The fixture awaits
``app.db.postgres.dispose_engine_for_loop()`` before building its engine and
after disposing it; it binds no Redis / Mongo client and closes none.
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
M0239 = "0239_retention_holds.py"
M0240 = "0240_retention_floor.py"
HOLD_CHECKS = {
    "ck_retention_holds_entity_type",
    "ck_retention_holds_entity_id",
    "ck_retention_holds_reason",
    "ck_retention_holds_release_reason",
}

#: What 0239 / 0240 touch or reference, with only the columns they need.
_SCRATCH_TABLES = (
    "CREATE TABLE {s}.projects (id uuid PRIMARY KEY)",
    "CREATE TABLE {s}.users (id uuid PRIMARY KEY)",
    "CREATE TABLE {s}.project_retention_policies (id uuid PRIMARY KEY, project_id uuid NOT NULL, "
    "enabled boolean NOT NULL DEFAULT false)",
    "CREATE TABLE {s}.run_baselines (id uuid PRIMARY KEY, run_id uuid NOT NULL)",
)


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
    scratch = f"w22aa_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    for statement in _SCRATCH_TABLES:
        await conn.execute(text(statement.format(s=f'"{scratch}"')))
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


async def _rows(conn, sql: str, **params):
    rows = (await conn.execute(text(sql), params)).all()
    await conn.commit()
    return rows


async def _refused(conn, sql: str, **params) -> str:
    """Run one statement that must violate a CHECK; return the constraint the server names."""
    with pytest.raises(DBAPIError) as caught:
        await conn.execute(text(sql), params)
    await conn.rollback()
    return str(caught.value.orig)


async def _checks(conn, schema: str, table: str) -> set[str]:
    rows = await _rows(
        conn,
        "SELECT c.conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
        "JOIN pg_namespace n ON n.oid = t.relnamespace "
        "WHERE n.nspname = :s AND t.relname = :t AND c.contype = 'c'",
        s=schema, t=table,
    )
    return {r[0] for r in rows}


async def _columns(conn, schema: str, table: str) -> dict[str, tuple[str, str, str | None]]:
    """{column: (data_type, is_nullable, column_default)} of ``schema.table``."""
    rows = await _rows(
        conn,
        "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = :s AND table_name = :t",
        s=schema, t=table,
    )
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


async def _index_defs(conn, schema: str, table: str) -> dict[str, str]:
    rows = await _rows(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = :s AND tablename = :t",
        s=schema, t=table,
    )
    return {r[0]: r[1] for r in rows}


_HOLD = (
    "INSERT INTO retention_holds (id, project_id, entity_type, entity_id, reason{extra_cols}) "
    "VALUES (:id, :project, :etype, :eid, :reason{extra_vals})"
)


def _hold(extra_cols: str = "", extra_vals: str = "") -> str:
    return _HOLD.format(extra_cols=extra_cols, extra_vals=extra_vals)


# ── 0239 ─────────────────────────────────────────────────────────────────────


async def test_0239_builds_the_table_its_four_checks_and_the_partial_live_index(conn):
    scratch = await _scratch(conn)
    await _run(conn, M0239, "upgrade")
    assert await _checks(conn, scratch, "retention_holds") == HOLD_CHECKS
    indexes = await _index_defs(conn, scratch, "retention_holds")
    assert indexes["ix_retention_holds_live"].endswith(
        "USING btree (project_id, entity_type, entity_id) WHERE (released_at IS NULL)"
    )
    assert indexes["ix_retention_holds_project_placed"].endswith("USING btree (project_id, placed_at)")
    columns = await _columns(conn, scratch, "retention_holds")
    assert columns["entity_id"][1] == "YES" and columns["reason"][1] == "NO"
    assert columns["placed_at"][2] == "now()"


async def test_0239_checks_refuse_each_invalid_hold_and_admit_the_valid_ones(conn):
    await _scratch(conn)
    await _run(conn, M0239, "upgrade")
    project = uuid.uuid4()
    await conn.execute(text("INSERT INTO projects (id) VALUES (:id)"), {"id": project})
    await conn.commit()

    def params(**overrides):
        base = {"id": uuid.uuid4(), "project": project, "etype": "run", "eid": uuid.uuid4(), "reason": "Audit Q4 sentinel"}
        return {**base, **overrides}

    assert "ck_retention_holds_entity_id" in await _refused(conn, _hold(), **params(etype="project"))
    assert "ck_retention_holds_entity_id" in await _refused(conn, _hold(), **params(etype="run", eid=None))
    assert "ck_retention_holds_reason" in await _refused(conn, _hold(), **params(reason="  ab  "))
    assert "ck_retention_holds_entity_type" in await _refused(conn, _hold(), **params(etype="defect"))
    assert "ck_retention_holds_release_reason" in await _refused(
        conn, _hold(", released_at", ", now()"), **params()
    )

    # The valid shapes: a project hold (no entity), a run hold, a released hold with its reason.
    await conn.execute(text(_hold()), params(etype="project", eid=None))
    await conn.execute(text(_hold()), params())
    await conn.execute(
        text(_hold(", released_at, release_reason", ", now(), :why")), {**params(etype="release"), "why": "Audit closed"}
    )
    await conn.commit()
    counts = await _rows(conn, "SELECT entity_type, count(*) FROM retention_holds GROUP BY entity_type")
    assert dict(counts) == {"project": 1, "run": 1, "release": 1}


# ── 0240 ─────────────────────────────────────────────────────────────────────


async def test_0240_gives_an_existing_policy_row_the_floor_and_checks_its_bounds(conn):
    scratch = await _scratch(conn)
    await _run(conn, M0239, "upgrade")
    policy, project = uuid.uuid4(), uuid.uuid4()
    await conn.execute(
        text("INSERT INTO project_retention_policies (id, project_id) VALUES (:id, :p)"), {"id": policy, "p": project}
    )
    await conn.commit()
    await _run(conn, M0240, "upgrade")

    assert await _rows(conn, "SELECT keep_last_runs FROM project_retention_policies WHERE id = :id", id=policy) == [(10,)]
    columns = await _columns(conn, scratch, "project_retention_policies")
    assert columns["keep_last_runs"] == ("integer", "NO", "10")
    assert "ck_retention_keep_last_runs" in await _checks(conn, scratch, "project_retention_policies")
    for bad in (1001, -1):
        message = await _refused(
            conn, "UPDATE project_retention_policies SET keep_last_runs = :n WHERE id = :id", n=bad, id=policy
        )
        assert "ck_retention_keep_last_runs" in message
    for good in (0, 1000):
        await conn.execute(
            text("UPDATE project_retention_policies SET keep_last_runs = :n WHERE id = :id"), {"n": good, "id": policy}
        )
        await conn.commit()

    stamp = (await _columns(conn, scratch, "run_baselines"))["baseline_purged_at"]
    assert stamp == ("timestamp with time zone", "YES", None)


# ── downgrades, and upgrading again ──────────────────────────────────────────


async def test_each_downgrade_undoes_its_upgrade_and_the_upgrade_runs_again(conn):
    scratch = await _scratch(conn)
    await _run(conn, M0239, "upgrade")
    await _run(conn, M0240, "upgrade")
    first = (
        await _checks(conn, scratch, "retention_holds"),
        await _index_defs(conn, scratch, "retention_holds"),
        await _columns(conn, scratch, "project_retention_policies"),
        await _columns(conn, scratch, "run_baselines"),
    )

    await _run(conn, M0240, "downgrade")
    assert "keep_last_runs" not in await _columns(conn, scratch, "project_retention_policies")
    assert "baseline_purged_at" not in await _columns(conn, scratch, "run_baselines")
    assert "ck_retention_keep_last_runs" not in await _checks(conn, scratch, "project_retention_policies")
    assert await _checks(conn, scratch, "retention_holds") == HOLD_CHECKS   # 0239 untouched

    await _run(conn, M0239, "downgrade")
    assert await _columns(conn, scratch, "retention_holds") == {}
    assert await _index_defs(conn, scratch, "retention_holds") == {}

    await _run(conn, M0239, "upgrade")
    await _run(conn, M0240, "upgrade")
    again = (
        await _checks(conn, scratch, "retention_holds"),
        await _index_defs(conn, scratch, "retention_holds"),
        await _columns(conn, scratch, "project_retention_policies"),
        await _columns(conn, scratch, "run_baselines"),
    )
    assert again == first


# ── the shared database, at head ─────────────────────────────────────────────


async def test_the_shared_database_at_head_carries_both_migrations(conn):
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    await conn.commit()
    assert await _checks(conn, base, "retention_holds") == HOLD_CHECKS
    live = (await _index_defs(conn, base, "retention_holds"))["ix_retention_holds_live"]
    assert "WHERE (released_at IS NULL)" in live
    # W22b's 0242 made the column nullable with no default (NULL = inherit the
    # instance default, else 10 in code); 0240's NOT NULL DEFAULT 10 is
    # asserted on the scratch schema above.
    assert (await _columns(conn, base, "project_retention_policies"))["keep_last_runs"] == ("integer", "YES", None)
    assert "ck_retention_keep_last_runs" in await _checks(conn, base, "project_retention_policies")
    assert (await _columns(conn, base, "run_baselines"))["baseline_purged_at"][1] == "YES"
