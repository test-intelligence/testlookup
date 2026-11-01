"""Migration 0250 (W25d HYG-02) on real PostgreSQL: drop the four dead tables, refuse while any holds a row.

* ``downgrade()`` creates ``contract_violations``, ``ai_provenance_records``,
  ``tenant_metric_snapshots`` and ``quality_gates``, and their REFLECTED shape
  (columns: name, type, nullable, server default; the primary key; indexes:
  name, columns, unique; foreign keys: name, columns, target table, target
  columns, ``ondelete``) equals ``EXPECTED_0249_SHAPES`` -- a snapshot captured
  ONCE from a database migrated to 0249 by the real chain (``df_w25d_b``,
  PostgreSQL 16, read-only; W25d G-B's log ``w25d-gb-capture-0249.log``). The
  orchestrator compares the same reflection (``reflect_dead_table_shapes``)
  in-pod on the homelab before the deploy: a difference is a STOP, because
  that is the database this downgrade must restore.
* ``upgrade()`` drops all four; ``downgrade()`` recreates them again (the round
  trip); the stub tables they reference are untouched.
* One ``quality_gates`` row -> ``upgrade()`` raises naming ``quality_gates has
  1 row``; two rows of ``tenant_metric_snapshots`` more -> both tables named
  with their counts. Each time all four tables still exist -- read INSIDE the
  refused migration's transaction, before anything rolls back (so a migration
  that dropped a table before probing the next one cannot hide behind the
  rollback) and again afterwards, with the seeded rows intact. Once the rows
  are deleted the upgrade drops the four.
* The shared database (migrated to head) holds none of the four tables.

The migration runs through a real Alembic ``Operations`` context against a
SCRATCH schema that is the ONLY schema on the search path while it runs, with
stub ``projects`` / ``test_runs`` / ``test_cases`` / ``users`` tables holding
only ``id uuid primary key`` (the foreign-key targets), so neither direction
can resolve a name in the shared schema. Its work commits; the scratch schema
is dropped afterwards and the shared schema is only read. The fixture awaits
``app.db.postgres.dispose_engine_for_loop()`` before building its engine and
after disposing it; it binds no Redis / Mongo client and closes none.
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
M0250 = "0250_drop_dead_tables.py"
DEAD_TABLES = ("contract_violations", "ai_provenance_records", "tenant_metric_snapshots", "quality_gates")
STUB_TABLES = ("projects", "test_runs", "test_cases", "users")

#: The reflected shape of the four tables in a database at 0249 (captured once; see the module docstring).
#: columns: (name, type compiled for PostgreSQL, nullable, server default as Postgres renders it), in order;
#: pk: (name, columns); indexes / foreign_keys: sorted by name; FK targets by table name.
EXPECTED_0249_SHAPES: dict[str, dict[str, Any]] = {
    "contract_violations": {
        "columns": [
            ("id", "UUID", False, "gen_random_uuid()"),
            ("test_run_id", "UUID", False, None),
            ("test_case_id", "UUID", False, None),
            ("endpoint", "VARCHAR(500)", True, None),
            ("violation_type", "VARCHAR(50)", False, None),
            ("field_path", "VARCHAR(500)", True, None),
            ("expected", "VARCHAR(500)", True, None),
            ("actual", "VARCHAR(500)", True, None),
            ("severity", "VARCHAR(20)", False, "'''warning'''::character varying"),
            ("created_at", "TIMESTAMP WITH TIME ZONE", True, "now()"),
        ],
        "pk": ("contract_violations_pkey", ("id",)),
        "indexes": [
            ("ix_contract_violations_run", ("test_run_id",), False),
            ("ix_contract_violations_tc", ("test_case_id",), False),
        ],
        "foreign_keys": [
            ("contract_violations_test_case_id_fkey", ("test_case_id",), "test_cases", ("id",), "CASCADE"),
            ("contract_violations_test_run_id_fkey", ("test_run_id",), "test_runs", ("id",), "CASCADE"),
        ],
    },
    "ai_provenance_records": {
        "columns": [
            ("id", "UUID", False, None),
            ("entity_type", "VARCHAR(50)", False, None),
            ("entity_id", "UUID", False, None),
            ("run_id", "UUID", True, None),
            ("model_name", "VARCHAR(200)", True, None),
            ("fallback_used", "BOOLEAN", False, "false"),
            ("confidence", "INTEGER", True, None),
            ("confidence_reason", "TEXT", True, None),
            ("evidence_count", "INTEGER", True, "0"),
            ("sources_used", "JSON", True, None),
            ("deterministic_checks_used", "JSON", True, None),
            ("created_at", "TIMESTAMP WITH TIME ZONE", True, "now()"),
            ("project_id", "UUID", True, None),
        ],
        "pk": ("ai_provenance_records_pkey", ("id",)),
        "indexes": [
            ("ix_provenance_entity", ("entity_type", "entity_id"), False),
            ("ix_provenance_project", ("project_id",), False),
            ("ix_provenance_run", ("run_id",), False),
        ],
        "foreign_keys": [
            ("ai_provenance_records_project_id_fkey", ("project_id",), "projects", ("id",), "CASCADE"),
            ("ai_provenance_records_run_id_fkey", ("run_id",), "test_runs", ("id",), "SET NULL"),
        ],
    },
    "tenant_metric_snapshots": {
        "columns": [
            ("id", "UUID", False, None),
            ("project_id", "UUID", False, None),
            ("recorded_at", "TIMESTAMP WITH TIME ZONE", True, "now()"),
            ("total_runs", "INTEGER", True, "0"),
            ("total_tests", "INTEGER", True, "0"),
            ("avg_pass_rate", "DOUBLE PRECISION", True, None),
            ("failed_runs", "INTEGER", True, "0"),
            ("ai_analyses_count", "INTEGER", True, "0"),
            ("release_decisions_count", "INTEGER", True, "0"),
            ("audit_events_count", "INTEGER", True, "0"),
        ],
        "pk": ("tenant_metric_snapshots_pkey", ("id",)),
        "indexes": [("ix_tms_project_time", ("project_id", "recorded_at"), False)],
        "foreign_keys": [
            ("tenant_metric_snapshots_project_id_fkey", ("project_id",), "projects", ("id",), "CASCADE"),
        ],
    },
    "quality_gates": {
        "columns": [
            ("id", "UUID", False, "gen_random_uuid()"),
            ("project_id", "UUID", True, None),
            ("name", "VARCHAR(255)", False, None),
            ("rules", "JSONB", True, "'[]'::jsonb"),
            ("is_active", "BOOLEAN", True, "true"),
            ("created_at", "TIMESTAMP WITH TIME ZONE", True, "now()"),
        ],
        "pk": ("quality_gates_pkey", ("id",)),
        "indexes": [("ix_quality_gates_project", ("project_id",), False)],
        "foreign_keys": [("quality_gates_project_id_fkey", ("project_id",), "projects", ("id",), "CASCADE")],
    },
}


def reflect_dead_table_shapes(sync_conn, schema: str) -> dict[str, dict[str, Any]]:
    """The reflection ``EXPECTED_0249_SHAPES`` was captured with (a sync connection; read-only)."""
    insp = inspect(sync_conn)
    out: dict[str, dict[str, Any]] = {}
    for table in DEAD_TABLES:
        pk = insp.get_pk_constraint(table, schema=schema)
        out[table] = {
            "columns": [
                (c["name"], c["type"].compile(dialect=postgresql.dialect()), bool(c["nullable"]), c.get("default"))
                for c in insp.get_columns(table, schema=schema)
            ],
            "pk": (pk["name"], tuple(pk["constrained_columns"])),
            "indexes": sorted(
                (i["name"], tuple(i["column_names"]), bool(i["unique"]))
                for i in insp.get_indexes(table, schema=schema)
            ),
            "foreign_keys": sorted(
                (
                    f["name"],
                    tuple(f["constrained_columns"]),
                    f["referred_table"],
                    tuple(f["referred_columns"]),
                    (f.get("options") or {}).get("ondelete"),
                )
                for f in insp.get_foreign_keys(table, schema=schema)
            ),
        }
    return out


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _migration():
    spec = importlib.util.spec_from_file_location("m_0250_pg", VERSIONS / M0250)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def conn():
    """A connection whose work COMMITS inside a scratch schema; the scratch schema is dropped afterwards."""
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
    """A scratch schema holding only the stub FK targets, ALONE on the search path."""
    scratch = f"w25dgb_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    for table in STUB_TABLES:
        await conn.execute(text(f'CREATE TABLE "{scratch}".{table} (id uuid PRIMARY KEY)'))
    await conn.execute(text(f'SET search_path TO "{scratch}"'))
    await conn.commit()
    return scratch


class _Refused(Exception):
    def __init__(self, message: str, present_inside: dict[str, bool]) -> None:
        super().__init__(message)
        self.message = message
        self.present_inside = present_inside


def _present_sync(sync_conn) -> dict[str, bool]:
    return {
        name: bool(sync_conn.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name}).scalar())
        for name in DEAD_TABLES
    }


async def _migrate(conn, step: str) -> None:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    module = _migration()

    def _run(sync_conn):
        context = MigrationContext.configure(sync_conn)
        module.op = Operations(context)
        # As a real run: alembic owns the transaction (PostgreSQL DDL is transactional).
        with context.begin_transaction():
            try:
                getattr(module, step)()
            except RuntimeError as exc:
                # Read the tables INSIDE the refused migration's transaction, before the rollback.
                raise _Refused(str(exc), _present_sync(sync_conn)) from exc

    await conn.commit()
    try:
        await conn.run_sync(_run)
    finally:
        await conn.rollback()


async def _present(conn) -> dict[str, bool]:
    out = await conn.run_sync(_present_sync)
    await conn.commit()
    return out


async def _shapes(conn, schema: str) -> dict[str, dict[str, Any]]:
    out = await conn.run_sync(reflect_dead_table_shapes, schema)
    await conn.commit()
    return out


async def _stub_tables(conn, schema: str) -> list[str]:
    rows = (await conn.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = :s ORDER BY tablename"), {"s": schema}
    )).scalars().all()
    await conn.commit()
    return list(rows)


async def _count(conn, table: str) -> int:
    value = (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
    await conn.commit()
    return int(value)


# ── the shapes and the round trip ────────────────────────────────────────────


async def test_the_downgrade_recreates_the_0249_shapes_and_the_upgrade_drops_them(conn):
    scratch = await _scratch(conn)
    assert await _present(conn) == {name: False for name in DEAD_TABLES}

    await _migrate(conn, "downgrade")
    assert await _present(conn) == {name: True for name in DEAD_TABLES}
    assert await _shapes(conn, scratch) == EXPECTED_0249_SHAPES
    for table in DEAD_TABLES:
        assert await _count(conn, table) == 0, "the downgrade recreates EMPTY tables"

    await _migrate(conn, "upgrade")
    assert await _present(conn) == {name: False for name in DEAD_TABLES}
    assert await _stub_tables(conn, scratch) == sorted(STUB_TABLES), "only the four dead tables were dropped"

    # The round trip: the downgrade runs again on the upgraded schema.
    await _migrate(conn, "downgrade")
    assert await _shapes(conn, scratch) == EXPECTED_0249_SHAPES
    await _migrate(conn, "upgrade")
    assert await _stub_tables(conn, scratch) == sorted(STUB_TABLES)


# ── the refusal ──────────────────────────────────────────────────────────────


async def test_a_row_refuses_the_upgrade_naming_every_non_empty_table_and_nothing_is_dropped(conn):
    scratch = await _scratch(conn)
    await _migrate(conn, "downgrade")
    await conn.execute(text("INSERT INTO quality_gates (name) VALUES ('zz-gb-retired-gate')"))
    await conn.commit()

    with pytest.raises(_Refused) as refused:
        await _migrate(conn, "upgrade")
    assert refused.value.message == (
        "0250_drop_dead_tables refused: quality_gates has 1 row — nothing was dropped. "
        "Export or delete those rows deliberately, then upgrade again."
    )
    assert refused.value.present_inside == {name: True for name in DEAD_TABLES}, (
        "a table was dropped before the probe refused"
    )
    assert await _present(conn) == {name: True for name in DEAD_TABLES}
    assert await _count(conn, "quality_gates") == 1, "the refused upgrade touched no row"

    # A second non-empty table: both are named, in probe order, each with its own count.
    project_id = (await conn.execute(text("INSERT INTO projects (id) VALUES (gen_random_uuid()) RETURNING id"))).scalar_one()
    for _ in range(2):
        await conn.execute(
            text("INSERT INTO tenant_metric_snapshots (id, project_id) VALUES (gen_random_uuid(), :p)"),
            {"p": project_id},
        )
    await conn.commit()
    with pytest.raises(_Refused) as refused:
        await _migrate(conn, "upgrade")
    assert refused.value.message == (
        "0250_drop_dead_tables refused: tenant_metric_snapshots has 2 rows, quality_gates has 1 row "
        "— nothing was dropped. Export or delete those rows deliberately, then upgrade again."
    )
    assert refused.value.present_inside == {name: True for name in DEAD_TABLES}
    assert await _present(conn) == {name: True for name in DEAD_TABLES}
    assert await _count(conn, "tenant_metric_snapshots") == 2
    assert await _shapes(conn, scratch) == EXPECTED_0249_SHAPES

    # Rows deleted deliberately -> the upgrade drops the four.
    await conn.execute(text("DELETE FROM tenant_metric_snapshots"))
    await conn.execute(text("DELETE FROM quality_gates"))
    await conn.commit()
    await _migrate(conn, "upgrade")
    assert await _present(conn) == {name: False for name in DEAD_TABLES}
    assert await _stub_tables(conn, scratch) == sorted(STUB_TABLES)


# ── the shared database, at head ─────────────────────────────────────────────


async def test_the_shared_database_at_head_holds_none_of_the_four_tables(conn):
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    head = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    rows = (await conn.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = :s AND tablename = ANY(:names)"),
        {"s": base, "names": list(DEAD_TABLES)},
    )).scalars().all()
    await conn.commit()
    assert head >= "0250"
    assert list(rows) == []
