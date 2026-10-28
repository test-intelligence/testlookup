"""Migrations 0241 / 0242 (W22b ACT-03 / ACT-06) on real PostgreSQL.

* 0241 adds the twelve export columns to ``deletion_jobs`` with their three
  CHECKs: an unknown tier, an unknown target and a tier without a target are
  each refused; a valid pair is admitted.
* 0242 relaxes the six policy columns WITHOUT touching a row: a policy row
  with explicit values ``(true, 120, 400, 200, 800, 25)`` and a row filled by
  the old server defaults ``(false, 90, 365, 180, 2555, 10)`` read EXACTLY the
  same after the upgrade; the six columns are nullable with no default; the
  instance row exists, OFF, every value NULL; a second instance row, a window
  of 24 and an instance floor of 1001 are refused.
* Downgrade 0242 freezes every inherited value into what the project resolved
  to (instance value, else code default) and NEVER switches retention on (a
  NULL ``enabled`` under an enabled instance default comes back ``false``);
  the explicit rows still read their values; NOT NULL and the old defaults are
  back. Downgrade 0241 removes the export columns; upgrading again rebuilds the
  same catalog.
* The shared database (migrated to head) carries both migrations.

The migrations run through a real Alembic ``Operations`` context against a
SCRATCH schema first on the search path, holding only what the two migrations
touch or reference (``users``, ``deletion_jobs``, ``project_retention_policies``
as 0240 left it); it is dropped afterwards and the shared tables are only read.
The fixture awaits ``app.db.postgres.dispose_engine_for_loop()`` before building
its engine and after disposing it; it binds no Redis / Mongo client and closes
none. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
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
M0241 = "0241_deletion_job_exports.py"
M0242 = "0242_retention_schedule_instance_policy.py"
SIX = ("enabled", "raw_events_days", "runs_days", "artifacts_days", "audit_days", "keep_last_runs")
EXPORT_CHECKS = {"ck_deletion_jobs_export_tier", "ck_deletion_jobs_export_target", "ck_deletion_jobs_export_pair"}
INSTANCE_CHECKS = {
    "ck_instance_retention_single_row", "ck_instance_retention_window", "ck_instance_retention_keep_last",
}
EXPLICIT = (True, 120, 400, 200, 800, 25)
OLD_DEFAULTS = (False, 90, 365, 180, 2555, 10)

#: What 0241 / 0242 touch or reference, ``project_retention_policies`` exactly as 0240 left it.
_SCRATCH_TABLES = (
    "CREATE TABLE {s}.users (id uuid PRIMARY KEY)",
    "CREATE TABLE {s}.deletion_jobs (id uuid PRIMARY KEY, status varchar(20) NOT NULL DEFAULT 'queued')",
    "CREATE TABLE {s}.project_retention_policies (id uuid PRIMARY KEY, project_id uuid NOT NULL, "
    "enabled boolean NOT NULL DEFAULT false, raw_events_days integer NOT NULL DEFAULT 90, "
    "runs_days integer NOT NULL DEFAULT 365, artifacts_days integer NOT NULL DEFAULT 180, "
    "audit_days integer NOT NULL DEFAULT 2555, keep_last_runs integer NOT NULL DEFAULT 10, "
    "CONSTRAINT ck_retention_keep_last_runs CHECK (keep_last_runs >= 0 AND keep_last_runs <= 1000))",
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
    scratch = f"w22ba_{uuid.uuid4().hex[:10]}"
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


async def _exec(conn, sql: str, **params) -> None:
    await conn.execute(text(sql), params)
    await conn.commit()


async def _refused(conn, sql: str, **params) -> str:
    """Run one statement that must violate a constraint; return the server's message."""
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


async def _policy(conn, policy_id: uuid.UUID) -> tuple:
    (row,) = await _rows(conn, f"SELECT {', '.join(SIX)} FROM project_retention_policies WHERE id = :id", id=policy_id)
    return tuple(row)


async def _seed_two_policy_rows(conn) -> tuple[uuid.UUID, uuid.UUID]:
    """At 0240: row A with explicit values, row B filled by the old server defaults."""
    a, b = uuid.uuid4(), uuid.uuid4()
    await _exec(
        conn,
        f"INSERT INTO project_retention_policies (id, project_id, {', '.join(SIX)}) "
        "VALUES (:id, :p, :e, :raw, :runs, :art, :aud, :keep)",
        id=a, p=uuid.uuid4(), e=EXPLICIT[0], raw=EXPLICIT[1], runs=EXPLICIT[2], art=EXPLICIT[3],
        aud=EXPLICIT[4], keep=EXPLICIT[5],
    )
    await _exec(conn, "INSERT INTO project_retention_policies (id, project_id) VALUES (:id, :p)", id=b, p=uuid.uuid4())
    assert await _policy(conn, a) == EXPLICIT and await _policy(conn, b) == OLD_DEFAULTS
    return a, b


_JOB = "INSERT INTO deletion_jobs (id, export_tier, export_target) VALUES (:id, :tier, :target)"


# ── 0241 ─────────────────────────────────────────────────────────────────────


async def test_0241_adds_the_export_columns_and_their_three_checks(conn):
    scratch = await _scratch(conn)
    old = uuid.uuid4()
    await _exec(conn, "INSERT INTO deletion_jobs (id) VALUES (:id)", id=old)
    await _run(conn, M0241, "upgrade")

    assert await _checks(conn, scratch, "deletion_jobs") == EXPORT_CHECKS
    columns = await _columns(conn, scratch, "deletion_jobs")
    assert {name: columns[name] for name in ("export_tier", "export_bytes", "export_meta", "acknowledged_by_id")} == {
        "export_tier": ("character varying", "YES", None),
        "export_bytes": ("bigint", "YES", None),
        "export_meta": ("jsonb", "YES", None),
        "acknowledged_by_id": ("uuid", "YES", None),
    }
    assert columns["collect_expires_at"] == ("timestamp with time zone", "YES", None)
    assert len(columns) == 2 + 12
    # A job written before 0241 reads NULL in every new column.
    (row,) = await _rows(conn, "SELECT export_tier, export_target, export_meta, export_verified_at FROM deletion_jobs")
    assert tuple(row) == (None, None, None, None)

    assert "ck_deletion_jobs_export_tier" in await _refused(conn, _JOB, id=uuid.uuid4(), tier="bogus", target="collect")
    assert "ck_deletion_jobs_export_target" in await _refused(conn, _JOB, id=uuid.uuid4(), tier="full", target="external")
    assert "ck_deletion_jobs_export_pair" in await _refused(conn, _JOB, id=uuid.uuid4(), tier="summary", target=None)
    assert "ck_deletion_jobs_export_pair" in await _refused(conn, _JOB, id=uuid.uuid4(), tier=None, target="retain")
    await _exec(conn, _JOB, id=uuid.uuid4(), tier="evidence", target="collect")
    await _exec(conn, _JOB, id=uuid.uuid4(), tier=None, target=None)
    message = await _refused(
        conn, "UPDATE deletion_jobs SET acknowledged_by_id = :u WHERE id = :id", u=uuid.uuid4(), id=old
    )
    assert "fk_deletion_jobs_acknowledged_by" in message


# ── 0242 ─────────────────────────────────────────────────────────────────────


async def test_0242_keeps_every_existing_explicit_value_and_relaxes_the_columns(conn):
    scratch = await _scratch(conn)
    a, b = await _seed_two_policy_rows(conn)
    await _run(conn, M0241, "upgrade")
    await _run(conn, M0242, "upgrade")

    # No row rewritten: the explicit row and the server-default row read EXACTLY as before, no NULL.
    assert await _policy(conn, a) == EXPLICIT
    assert await _policy(conn, b) == OLD_DEFAULTS
    columns = await _columns(conn, scratch, "project_retention_policies")
    for col in SIX:
        assert columns[col][1:] == ("YES", None), col
    assert columns["purge_window_start_utc"] == ("smallint", "YES", None)
    assert columns["purge_paused_until"] == ("timestamp with time zone", "YES", None)
    assert {"ck_retention_purge_window", "ck_retention_keep_last_runs"} <= await _checks(
        conn, scratch, "project_retention_policies"
    )

    # The instance row: one, OFF (D12), every value NULL (= the code default).
    rows = await _rows(
        conn,
        "SELECT id, enabled, raw_events_days, runs_days, artifacts_days, audit_days, keep_last_runs, "
        "purge_window_start_utc, enabled_at, enabled_by_id, updated_by_user_id FROM instance_retention_policy",
    )
    assert [tuple(r) for r in rows] == [(1, False, None, None, None, None, None, None, None, None, None)]
    assert await _checks(conn, scratch, "instance_retention_policy") == INSTANCE_CHECKS
    instance_columns = await _columns(conn, scratch, "instance_retention_policy")
    assert instance_columns["enabled"] == ("boolean", "NO", "false")
    assert instance_columns["id"] == ("smallint", "NO", None)   # no sequence
    assert instance_columns["updated_at"][1:] == ("NO", "now()")

    # The CHECKs.
    assert "ck_instance_retention_single_row" in await _refused(
        conn, "INSERT INTO instance_retention_policy (id) VALUES (2)"
    )
    assert "ck_instance_retention_window" in await _refused(
        conn, "UPDATE instance_retention_policy SET purge_window_start_utc = 24 WHERE id = 1"
    )
    assert "ck_instance_retention_keep_last" in await _refused(
        conn, "UPDATE instance_retention_policy SET keep_last_runs = 1001 WHERE id = 1"
    )
    for bad in (24, -1):
        assert "ck_retention_purge_window" in await _refused(
            conn, "UPDATE project_retention_policies SET purge_window_start_utc = :h WHERE id = :id", h=bad, id=a
        )
    await _exec(conn, "UPDATE project_retention_policies SET purge_window_start_utc = 23 WHERE id = :id", id=a)
    # A NULL keep_last_runs passes the unchanged floor CHECK (NULL = inherit).
    await _exec(conn, "UPDATE project_retention_policies SET keep_last_runs = NULL WHERE id = :id", id=a)
    # A new row that names only its project stores NULL everywhere (inherit), not the old defaults.
    c = uuid.uuid4()
    await _exec(conn, "INSERT INTO project_retention_policies (id, project_id) VALUES (:id, :p)", id=c, p=uuid.uuid4())
    assert await _policy(conn, c) == (None,) * 6


# ── downgrades, and upgrading again ──────────────────────────────────────────


async def test_0242_downgrade_freezes_inherited_values_and_never_enables_retention(conn):
    scratch = await _scratch(conn)
    a, b = await _seed_two_policy_rows(conn)
    await _run(conn, M0241, "upgrade")
    await _run(conn, M0242, "upgrade")
    # A inherits its runs window and its on/off flag; the instance sets runs 180 AND is switched on.
    await _exec(conn, "UPDATE project_retention_policies SET runs_days = NULL, enabled = NULL WHERE id = :id", id=a)
    await _exec(conn, "UPDATE instance_retention_policy SET runs_days = 180, enabled = :on WHERE id = 1", on=True)
    # C inherits everything (a row that named only its project).
    c = uuid.uuid4()
    await _exec(conn, "INSERT INTO project_retention_policies (id, project_id) VALUES (:id, :p)", id=c, p=uuid.uuid4())

    await _run(conn, M0242, "downgrade")

    # A: the inherited window is frozen at the instance's 180; the inherited flag is frozen OFF, never on.
    assert await _policy(conn, a) == (False, 120, 180, 200, 800, 25)
    assert await _policy(conn, b) == OLD_DEFAULTS
    assert await _policy(conn, c) == (False, 90, 180, 180, 2555, 10)
    columns = await _columns(conn, scratch, "project_retention_policies")
    assert {col: columns[col][1:] for col in SIX} == {
        "enabled": ("NO", "false"), "raw_events_days": ("NO", "90"), "runs_days": ("NO", "365"),
        "artifacts_days": ("NO", "180"), "audit_days": ("NO", "2555"), "keep_last_runs": ("NO", "10"),
    }
    assert "purge_window_start_utc" not in columns and "purge_paused_until" not in columns
    assert "ck_retention_purge_window" not in await _checks(conn, scratch, "project_retention_policies")
    assert await _columns(conn, scratch, "instance_retention_policy") == {}
    assert await _checks(conn, scratch, "deletion_jobs") == EXPORT_CHECKS   # 0241 untouched


async def test_0242_downgrade_without_an_instance_row_uses_the_code_defaults(conn):
    await _scratch(conn)
    await _run(conn, M0241, "upgrade")
    await _run(conn, M0242, "upgrade")
    c = uuid.uuid4()
    await _exec(conn, "INSERT INTO project_retention_policies (id, project_id) VALUES (:id, :p)", id=c, p=uuid.uuid4())
    await _exec(conn, "DELETE FROM instance_retention_policy")
    await _run(conn, M0242, "downgrade")
    assert await _policy(conn, c) == OLD_DEFAULTS


async def test_each_downgrade_undoes_its_upgrade_and_the_upgrade_runs_again(conn):
    scratch = await _scratch(conn)
    a, b = await _seed_two_policy_rows(conn)
    await _run(conn, M0241, "upgrade")
    await _run(conn, M0242, "upgrade")

    async def catalog():
        return (
            await _columns(conn, scratch, "deletion_jobs"),
            await _checks(conn, scratch, "deletion_jobs"),
            await _columns(conn, scratch, "project_retention_policies"),
            await _checks(conn, scratch, "project_retention_policies"),
            await _columns(conn, scratch, "instance_retention_policy"),
            await _checks(conn, scratch, "instance_retention_policy"),
        )

    first = await catalog()
    await _run(conn, M0242, "downgrade")
    await _run(conn, M0241, "downgrade")
    jobs = await _columns(conn, scratch, "deletion_jobs")
    assert set(jobs) == {"id", "status"}
    assert await _checks(conn, scratch, "deletion_jobs") == set()
    fks = await _rows(
        conn,
        "SELECT c.conname FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = :s AND c.contype = 'f'",
        s=scratch,
    )
    assert fks == []
    # The explicit rows survive the round trip unchanged.
    assert await _policy(conn, a) == EXPLICIT and await _policy(conn, b) == OLD_DEFAULTS

    await _run(conn, M0241, "upgrade")
    await _run(conn, M0242, "upgrade")
    assert await catalog() == first
    assert await _policy(conn, a) == EXPLICIT and await _policy(conn, b) == OLD_DEFAULTS
    assert await _rows(conn, "SELECT id, enabled FROM instance_retention_policy") == [(1, False)]


# ── the shared database, at head ─────────────────────────────────────────────


async def test_the_shared_database_at_head_carries_both_migrations(conn):
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    await conn.commit()
    assert EXPORT_CHECKS <= await _checks(conn, base, "deletion_jobs")
    assert (await _columns(conn, base, "deletion_jobs"))["export_location"] == ("character varying", "YES", None)
    policy = await _columns(conn, base, "project_retention_policies")
    for col in SIX:
        assert policy[col][1:] == ("YES", None), col
    assert "ck_retention_purge_window" in await _checks(conn, base, "project_retention_policies")
    assert await _checks(conn, base, "instance_retention_policy") == INSTANCE_CHECKS
    (row,) = await _rows(conn, "SELECT id FROM instance_retention_policy")
    assert row[0] == 1
