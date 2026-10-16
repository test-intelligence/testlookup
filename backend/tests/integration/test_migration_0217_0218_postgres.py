"""Migrations 0217 and 0218 (W08 TRND-07 / TRND-04) on real PostgreSQL.

0217: a pre-0217 row with a timestamptz ``snapshot_date`` gets its UTC
``snapshot_day`` (a row without one gets today's); two rows of one project
and UTC day collapse to one (the larger id kept); the column is NOT NULL; the
old ``uq_coverage_project_date`` is gone; the CONCURRENT unique index is VALID
and refuses a second row of a day; re-running the build is a no-op; an
INVALID leftover of a failed build (made for real: a concurrent unique build
over duplicates) is dropped and rebuilt VALID. The downgrade re-stamps rows
that share a ``snapshot_date`` (the beat writes a project's days in one
transaction), restores ``uq_coverage_project_date`` as a constraint and drops
the column; the upgrade runs again on the downgraded table.

0218: ``timeline_notes`` refuses ``ends_on < starts_on``, an empty text and a
281-character one, keeps a valid note; the downgrade drops it.

The migrations run through a real Alembic ``Operations`` context against a
SCRATCH schema (``coverage_snapshots`` as ``LIKE ... INCLUDING DEFAULTS``
without the new column and with the 0001 constraint; ``projects`` / ``users``
one-column stand-ins). A CONCURRENT build cannot run inside a transaction, so
this work commits; the scratch schema is dropped afterwards and the shared
tables are never written.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
INDEX = "uq_coverage_snapshots_project_day"
P1, P2 = uuid.UUID(int=0xA1), uuid.UUID(int=0xA2)
R1, R2, R3, R4 = (uuid.UUID(int=i) for i in (1, 2, 3, 4))


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


M0217 = "0217_coverage_snapshot_day.py"
M0218 = "0218_timeline_notes.py"


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
    scratch = f"w08a_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    await conn.execute(text(
        f'CREATE TABLE "{scratch}".coverage_snapshots (LIKE "{base}".coverage_snapshots INCLUDING DEFAULTS)'
    ))
    # The table as 0216 left it: no snapshot_day, the 0001 unique constraint.
    await conn.execute(text(f'ALTER TABLE "{scratch}".coverage_snapshots DROP COLUMN IF EXISTS snapshot_day'))
    await conn.execute(text(f'ALTER TABLE "{scratch}".coverage_snapshots ADD PRIMARY KEY (id)'))
    await conn.execute(text(
        f'ALTER TABLE "{scratch}".coverage_snapshots '
        "ADD CONSTRAINT uq_coverage_project_date UNIQUE (project_id, snapshot_date)"
    ))
    await conn.execute(text(f'CREATE TABLE "{scratch}".projects (id uuid PRIMARY KEY)'))
    await conn.execute(text(f'CREATE TABLE "{scratch}".users (id uuid PRIMARY KEY)'))
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
        # As a real run: alembic owns the transaction, so autocommit_block() can commit it.
        with context.begin_transaction():
            getattr(module, step)()

    await conn.commit()
    await conn.run_sync(_go)


async def _index_state(conn, scratch: str, name: str = INDEX) -> list[tuple[bool, bool]]:
    rows = (await conn.execute(text(
        "SELECT i.indisvalid, i.indisunique FROM pg_index i "
        "JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname = :name AND n.nspname = :s"
    ), {"name": name, "s": scratch})).all()
    return [(bool(valid), bool(unique)) for valid, unique in rows]


async def _constraint_kind(conn, scratch: str, name: str):
    return (await conn.execute(text(
        "SELECT CAST(c.contype AS text) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = :s AND c.conname = :name"
    ), {"s": scratch, "name": name})).scalar_one_or_none()


async def _snapshot(conn, sid, pid, taken, day=None):
    columns = "id, project_id, snapshot_date" + (", snapshot_day" if day is not None else "")
    values = ":id, :p, :t" + (", :d" if day is not None else "")
    await conn.execute(text(f"INSERT INTO coverage_snapshots ({columns}) VALUES ({values})"),
                       {"id": sid, "p": pid, "t": taken, "d": day})


async def _days(conn) -> dict:
    rows = (await conn.execute(text("SELECT id, project_id, snapshot_day FROM coverage_snapshots ORDER BY id"))).all()
    return {sid: (pid, day) for sid, pid, day in rows}


async def test_0217_gives_each_row_its_utc_day_collapses_a_day_and_builds_a_valid_unique_index(conn):
    scratch = await _scratch(conn)
    await _snapshot(conn, R1, P1, datetime(2026, 9, 10, 23, 30, tzinfo=timezone.utc))
    # 00:30 at +02:00 is 22:30 UTC on 09-10: the same UTC day as R1 -> one of them goes (R1, the smaller id).
    await _snapshot(conn, R2, P1, datetime.fromisoformat("2026-09-11T00:30:00+02:00"))
    await _snapshot(conn, R3, P1, datetime(2026, 9, 11, 0, 30, tzinfo=timezone.utc))
    await _snapshot(conn, R4, P2, None)
    await conn.commit()

    await _run(conn, M0217, "upgrade")

    today = (await conn.execute(text("SELECT (now() AT TIME ZONE 'UTC')::date"))).scalar_one()
    assert await _days(conn) == {R2: (P1, date(2026, 9, 10)), R3: (P1, date(2026, 9, 11)), R4: (P2, today)}
    nullable = (await conn.execute(text(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_schema = :s AND table_name = 'coverage_snapshots' AND column_name = 'snapshot_day'"
    ), {"s": scratch})).scalar_one()
    assert nullable == "NO"
    assert await _constraint_kind(conn, scratch, "uq_coverage_project_date") is None
    assert await _index_state(conn, scratch) == [(True, True)], "the CONCURRENT build left a VALID unique index"
    await conn.commit()

    # A second row of a project's day is refused.
    with pytest.raises(Exception) as caught:
        await _snapshot(conn, uuid.uuid4(), P1, datetime(2026, 9, 10, 1, tzinfo=timezone.utc), day=date(2026, 9, 10))
    assert INDEX in str(caught.value)
    await conn.rollback()

    # Re-running the build is a no-op (IF NOT EXISTS): still one valid index.
    await _run(conn, M0217, "_build_unique_index")
    assert await _index_state(conn, scratch) == [(True, True)]


async def test_0217_drops_an_invalid_leftover_and_rebuilds_it_valid(conn):
    scratch = await _scratch(conn)
    await _run(conn, M0217, "upgrade")
    # A failed CONCURRENT unique build leaves an INVALID index behind: make one.
    await conn.execute(text(f"DROP INDEX {INDEX}"))
    for sid in (R1, R2):
        await _snapshot(conn, sid, P1, datetime.now(timezone.utc), day=date(2026, 9, 20))
    await conn.commit()
    auto = await conn.execution_options(isolation_level="AUTOCOMMIT")
    with pytest.raises(Exception):
        await auto.execute(text(
            f"CREATE UNIQUE INDEX CONCURRENTLY {INDEX} ON coverage_snapshots (project_id, snapshot_day)"
        ))
    await auto.rollback()
    await auto.execution_options(isolation_level="READ COMMITTED")
    assert await _index_state(conn, scratch) == [(False, True)], "the failed build left an INVALID index"
    await conn.execute(text("DELETE FROM coverage_snapshots WHERE id = :id"), {"id": R1})
    await conn.commit()

    await _run(conn, M0217, "_build_unique_index")
    assert await _index_state(conn, scratch) == [(True, True)], "the leftover was dropped and rebuilt"


async def test_0218_refuses_bad_notes_and_both_round_trip(conn):
    scratch = await _scratch(conn)
    await _snapshot(conn, R1, P1, datetime(2026, 9, 10, 8, tzinfo=timezone.utc))
    await _snapshot(conn, R2, P1, datetime(2026, 9, 11, 8, tzinfo=timezone.utc))
    await conn.commit()
    await _run(conn, M0217, "upgrade")
    await _run(conn, M0218, "upgrade")

    await conn.execute(text("INSERT INTO projects (id) VALUES (:p)"), {"p": P1})
    insert = text("INSERT INTO timeline_notes (id, project_id, starts_on, ends_on, text) VALUES (:id, :p, :s, :e, :t)")
    await conn.execute(insert, {"id": uuid.uuid4(), "p": P1, "s": date(2026, 9, 10), "e": date(2026, 9, 12),
                                "t": "release freeze"})
    await conn.commit()
    for values, needle in (
        ({"s": date(2026, 9, 10), "e": date(2026, 9, 9), "t": "backwards"}, "ck_timeline_notes_range"),
        ({"s": date(2026, 9, 10), "e": date(2026, 9, 10), "t": ""}, "ck_timeline_notes_text"),
        ({"s": date(2026, 9, 10), "e": date(2026, 9, 10), "t": "x" * 281}, "280"),
    ):
        with pytest.raises(Exception) as caught:
            await conn.execute(insert, {"id": uuid.uuid4(), "p": P1, **values})
        assert needle in str(caught.value)
        await conn.rollback()
    count = (await conn.execute(text("SELECT count(*) FROM timeline_notes"))).scalar_one()
    assert count == 1
    await conn.commit()

    await _run(conn, M0218, "downgrade")
    assert (await conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{scratch}.timeline_notes"})).scalar_one() is None
    await conn.commit()

    # The beat stages a project's days in one transaction: they share now().
    await conn.execute(text("UPDATE coverage_snapshots SET snapshot_date = '2026-09-12T03:00:00+00:00'"))
    await conn.commit()
    await _run(conn, M0217, "downgrade")
    assert await _constraint_kind(conn, scratch, "uq_coverage_project_date") == "u"
    assert await _index_state(conn, scratch) == []
    taken = dict((await conn.execute(text("SELECT id, snapshot_date FROM coverage_snapshots"))).all())
    assert taken == {R1: datetime(2026, 9, 10, tzinfo=timezone.utc), R2: datetime(2026, 9, 11, tzinfo=timezone.utc)}
    columns = (await conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = :s AND table_name = 'coverage_snapshots'"
    ), {"s": scratch})).scalars().all()
    assert "snapshot_day" not in columns
    await conn.commit()

    # The round trip: the upgrade runs again on the downgraded table.
    await _run(conn, M0217, "upgrade")
    assert await _index_state(conn, scratch) == [(True, True)]
    assert {day for _p, day in (await _days(conn)).values()} == {date(2026, 9, 10), date(2026, 9, 11)}
