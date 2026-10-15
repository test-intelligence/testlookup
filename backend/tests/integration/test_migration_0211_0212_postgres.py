"""Migrations 0211 and 0212 on real PostgreSQL.

* 0211 (REL-02): ``releases.owner_user_id`` is added with its FK and partial
  index; the backfill copies ``created_by_id`` only for a real person (not a
  service account, not a synthetic account, not deactivated), keeps an owner
  already set, and leaves ``updated_at`` alone; a deleted owner user becomes
  ``NULL`` (``SET NULL``); the downgrade drops the index and the column.
* 0212 (REL-10 + REL-04): on an unused database (no user, no project) both
  ``release_phase_gate_enforcement`` and ``release_compliance_pack`` turn ON;
  with a user or a project they are untouched; a row already ON stays ON; the
  downgrade mirrors this and leaves a used database alone.

The migrations run through a real Alembic ``Operations`` context against a
SCRATCH schema (``releases`` as ``LIKE ... INCLUDING DEFAULTS`` without the new
column; ``users`` with the three flag columns; ``projects`` a one-column
stand-in; ``feature_flags`` as ``LIKE ... INCLUDING ALL``). Everything happens in
one transaction that is rolled back: the shared tables are never written.

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
M0211 = "0211_release_owner.py"
M0212 = "0212_release_flags_on_for_new_installs.py"
KEYS = ("release_phase_gate_enforcement", "release_compliance_pack")


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _migration(name: str):
    spec = importlib.util.spec_from_file_location(f"m_{name[:4]}_pg", VERSIONS / name)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def conn():
    """One connection inside one transaction that is always rolled back."""
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as connection:
            tx = await connection.begin()
            try:
                yield connection
            finally:
                await tx.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
async def committed_conn():
    """A connection whose work COMMITS: 0211 builds its index CONCURRENTLY, which no
    transaction can hold. Every scratch schema it creates is dropped afterwards."""
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


async def _scratch(conn, *, local: bool = True) -> str:
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    scratch = f"w05a_{uuid.uuid4().hex[:10]}"
    if not local:
        conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    await conn.execute(text(
        f'CREATE TABLE "{scratch}".releases (LIKE "{base}".releases INCLUDING DEFAULTS)'
    ))
    # The table as 0210 left it: no owner column yet.
    await conn.execute(text(f'ALTER TABLE "{scratch}".releases DROP COLUMN owner_user_id'))
    await conn.execute(text(f'ALTER TABLE "{scratch}".releases ADD PRIMARY KEY (id)'))
    await conn.execute(text(
        f'CREATE TABLE "{scratch}".users (id uuid PRIMARY KEY, is_service_account boolean '
        "NOT NULL DEFAULT false, is_synthetic boolean NOT NULL DEFAULT false, "
        "is_active boolean DEFAULT true)"
    ))
    await conn.execute(text(f'CREATE TABLE "{scratch}".projects (id uuid PRIMARY KEY)'))
    await conn.execute(text(
        f'CREATE TABLE "{scratch}".feature_flags (LIKE "{base}".feature_flags INCLUDING ALL)'
    ))
    scope = "LOCAL " if local else ""
    await conn.execute(text(f'SET {scope}search_path TO "{scratch}", "{base}"'))
    for key in KEYS:
        # The rows as 0066 / 0158 seeded them.
        await conn.execute(text(
            "INSERT INTO feature_flags (id, key, description, enabled_global, rollout_percent, "
            "created_at, updated_at) VALUES (gen_random_uuid(), :k, 'seed', false, 100, now(), now())"
        ), {"k": key})
    return scratch


async def _migrate(conn, step: str, names: tuple[str, ...], *, own_transaction: bool = False) -> None:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    modules = [_migration(name) for name in names]

    def _run(sync_conn):
        context = MigrationContext.configure(sync_conn)
        for module in modules:
            module.op = Operations(context)
            if own_transaction:
                # As a real run: alembic owns the transaction, so autocommit_block() can commit it.
                with context.begin_transaction():
                    getattr(module, step)()
            else:
                getattr(module, step)()

    if own_transaction:
        await conn.commit()
    await conn.run_sync(_run)


async def _flags(conn) -> dict[str, bool]:
    rows = (await conn.execute(text("SELECT key, enabled_global FROM feature_flags"))).all()
    return {key: bool(enabled) for key, enabled in rows}


async def _user(conn, **flags) -> uuid.UUID:
    uid = uuid.uuid4()
    await conn.execute(text(
        "INSERT INTO users (id, is_service_account, is_synthetic, is_active) VALUES (:id, :s, :y, :a)"
    ), {"id": uid, "s": flags.get("service", False), "y": flags.get("synthetic", False),
        "a": flags.get("active", True)})
    return uid


async def _release(conn, created_by) -> uuid.UUID:
    rid = uuid.uuid4()
    await conn.execute(text(
        "INSERT INTO releases (id, project_id, name, status, created_by_id, updated_at) "
        "VALUES (:id, gen_random_uuid(), :n, 'planning', :c, "
        "CAST('2026-01-01T00:00:00+00:00' AS timestamptz))"
    ), {"id": rid, "n": f"r-{rid.hex[:6]}", "c": created_by})
    return rid


async def _owners(conn) -> dict[uuid.UUID, uuid.UUID | None]:
    rows = (await conn.execute(text("SELECT id, owner_user_id FROM releases"))).all()
    return {rid: owner for rid, owner in rows}


async def test_0211_backfills_real_people_only_and_round_trips(committed_conn):
    conn = committed_conn
    await _scratch(conn, local=False)
    human = await _user(conn)
    other_human = await _user(conn)
    service = await _user(conn, service=True)
    synthetic = await _user(conn, synthetic=True)
    inactive = await _user(conn, active=False)
    by_human = await _release(conn, human)
    by_service = await _release(conn, service)
    by_synthetic = await _release(conn, synthetic)
    by_inactive = await _release(conn, inactive)
    by_nobody = await _release(conn, None)
    kept = await _release(conn, human)

    await _migrate(conn, "upgrade", (M0211,), own_transaction=True)
    owners = await _owners(conn)
    assert owners[by_human] == human
    assert owners[by_service] is None and owners[by_synthetic] is None
    assert owners[by_inactive] is None and owners[by_nobody] is None
    stamps = {row[0] for row in (await conn.execute(text("SELECT DISTINCT updated_at FROM releases"))).all()}
    assert len(stamps) == 1, "the backfill is not an edit: updated_at is untouched"

    # An owner already set is never overwritten by the backfill.
    await conn.execute(text("UPDATE releases SET owner_user_id = :o WHERE id = :r"), {"o": other_human, "r": kept})
    await conn.execute(text(_migration(M0211).BACKFILL_SQL))
    assert (await _owners(conn))[kept] == other_human

    index = (await conn.execute(text(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() AND indexname = 'ix_releases_owner'"
    ))).scalar_one()
    assert "WHERE (owner_user_id IS NOT NULL)" in index

    # FK ON DELETE SET NULL: deleting the owner leaves the release unowned.
    await conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": human})
    assert (await _owners(conn))[by_human] is None

    await _migrate(conn, "downgrade", (M0211,), own_transaction=True)
    columns = {row[0] for row in (await conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'releases'"
    ))).all()}
    assert "owner_user_id" not in columns
    assert (await conn.execute(text(
        "SELECT count(*) FROM pg_indexes WHERE schemaname = current_schema() AND indexname = 'ix_releases_owner'"
    ))).scalar_one() == 0


async def test_0212_a_new_install_gets_both_flags_on(conn):
    await _scratch(conn)
    await _migrate(conn, "upgrade", (M0212,))
    assert await _flags(conn) == dict.fromkeys(KEYS, True)
    await _migrate(conn, "downgrade", (M0212,))
    assert await _flags(conn) == dict.fromkeys(KEYS, False), "an untouched database goes back to the seed"


@pytest.mark.parametrize("used_by", ["user", "project"])
async def test_0212_a_used_database_keeps_its_values(conn, used_by):
    await _scratch(conn)
    table = "users" if used_by == "user" else "projects"
    await conn.execute(text(f"INSERT INTO {table} (id) VALUES (gen_random_uuid())"))
    await _migrate(conn, "upgrade", (M0212,))
    assert await _flags(conn) == dict.fromkeys(KEYS, False)


async def test_0212_a_flag_already_on_stays_on_in_both_directions(conn):
    await _scratch(conn)
    await conn.execute(text("INSERT INTO users (id) VALUES (gen_random_uuid())"))
    await conn.execute(text(
        "UPDATE feature_flags SET enabled_global = true WHERE key = 'release_compliance_pack'"
    ))
    await _migrate(conn, "upgrade", (M0212,))
    assert await _flags(conn) == {"release_phase_gate_enforcement": False, "release_compliance_pack": True}
    await _migrate(conn, "downgrade", (M0212,))
    assert await _flags(conn) == {"release_phase_gate_enforcement": False, "release_compliance_pack": True}
