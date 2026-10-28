"""Migration 0238 (W21d ACT-02) on real PostgreSQL.

* The upgrade builds ``ix_pae_actor_time`` ``(actor_id, occurred_at DESC, id
  DESC) WHERE actor_type = 'user'`` and ``ix_mtc_assignee`` ``(assignee_id)
  WHERE assignee_id IS NOT NULL``, both VALID (``indisvalid``), with exactly
  those predicates.
* The downgrade drops both; upgrade -> downgrade -> upgrade works, and a
  second upgrade over a finished one is a no-op (``IF NOT EXISTS``).
* A failed concurrent build's INVALID ``ix_pae_actor_time`` (planted for real:
  a ``CREATE UNIQUE INDEX CONCURRENTLY`` that meets a duplicate leaves one
  behind, no superuser needed) is dropped and rebuilt valid.
* ``EXPLAIN`` of /activity/mine's part A (``actor_type = 'user' AND actor_id =
  :id ORDER BY occurred_at DESC, id DESC LIMIT 51``) names
  ``ix_pae_actor_time``, an API key's rows are outside its predicate, and
  part C's ``assignee_id = :id`` names ``ix_mtc_assignee``.

The migration runs through a real Alembic ``Operations`` context (so every
``autocommit_block()`` and CONCURRENTLY build really runs outside a
transaction) against a SCRATCH schema first on the search path, holding the
two tables with only the columns the indexes read; it is dropped afterwards.
The shared tables are never written, and the last test checks they carry
head's two indexes. The fixture awaits
``app.db.postgres.dispose_engine_for_loop()`` before building its engine and
after disposing it, and closes the app's Redis / Mongo clients, so the next
module never meets "Event loop is closed". Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
M0238 = "0238_activity_mine_indexes.py"
ACTOR_INDEX, ASSIGNEE_INDEX = "ix_pae_actor_time", "ix_mtc_assignee"
ACTOR_DEF = (
    "CREATE INDEX ix_pae_actor_time ON {s}.project_activity_events USING btree "
    "(actor_id, occurred_at DESC, id DESC) WHERE ((actor_type)::text = 'user'::text)"
)
ASSIGNEE_DEF = (
    "CREATE INDEX ix_mtc_assignee ON {s}.managed_test_cases USING btree "
    "(assignee_id) WHERE (assignee_id IS NOT NULL)"
)


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _migration():
    spec = importlib.util.spec_from_file_location("m_0238_pg", VERSIONS / M0238)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def conn():
    # Redis and Mongo are never bound here, so they are not closed here either: closing a client an earlier
    # module bound on its own (closed) loop raised "Event loop is closed" in this teardown on CI.
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
                await connection.execute(text("RESET enable_seqscan"))
                await connection.execute(text("RESET search_path"))
                for schema in connection.info["scratch_schemas"]:
                    await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                await connection.commit()
    finally:
        await engine.dispose()
        await dispose_engine_for_loop()


#: What 0238 reads of each table (the columns its two indexes name).
_SCRATCH_TABLES = (
    "CREATE TABLE {s}.project_activity_events (id uuid PRIMARY KEY, actor_id uuid, "
    "actor_type varchar(20) NOT NULL, occurred_at timestamptz NOT NULL DEFAULT now())",
    "CREATE TABLE {s}.managed_test_cases (id uuid PRIMARY KEY, assignee_id uuid)",
)


async def _scratch(conn) -> str:
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    scratch = f"w21da_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    conn.info["base_schema"] = base
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    for statement in _SCRATCH_TABLES:
        await conn.execute(text(statement.format(s=f'"{scratch}"')))
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


async def _indexes(conn, schema: str) -> dict[str, tuple[str, bool]]:
    """{index name: (its definition, indisvalid)} of the two 0238 indexes in ``schema``."""
    rows = await _rows(
        conn,
        "SELECT c.relname, pg_get_indexdef(c.oid), i.indisvalid FROM pg_class c "
        "JOIN pg_index i ON i.indexrelid = c.oid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = :s AND c.relname IN (:a, :b)",
        s=schema, a=ACTOR_INDEX, b=ASSIGNEE_INDEX,
    )
    return {r[0]: (r[1], bool(r[2])) for r in rows}


def _expected(schema: str) -> dict[str, tuple[str, bool]]:
    """Both indexes, valid, as the server renders them (a lower-case schema name renders unquoted)."""
    return {
        ACTOR_INDEX: (ACTOR_DEF.format(s=schema), True),
        ASSIGNEE_INDEX: (ASSIGNEE_DEF.format(s=schema), True),
    }


# ── upgrade, downgrade, idempotence ─────────────────────────────────────────


async def test_the_upgrade_builds_both_indexes_valid_with_their_predicates(conn):
    scratch = await _scratch(conn)
    assert await _indexes(conn, scratch) == {}
    await _run(conn, "upgrade")
    assert await _indexes(conn, scratch) == _expected(scratch)


async def test_the_downgrade_drops_both_and_the_upgrade_runs_again(conn):
    scratch = await _scratch(conn)
    await _run(conn, "upgrade")
    await _run(conn, "downgrade")
    assert await _indexes(conn, scratch) == {}
    await _run(conn, "upgrade")
    assert await _indexes(conn, scratch) == _expected(scratch)
    # the shared tables kept head's indexes through the scratch downgrade
    assert await _indexes(conn, conn.info["base_schema"]) == _expected(conn.info["base_schema"])


async def test_a_second_upgrade_is_a_no_op(conn):
    scratch = await _scratch(conn)
    await _run(conn, "upgrade")
    before = await _rows(
        conn,
        "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = :s AND c.relname IN (:a, :b) ORDER BY c.relname",
        s=scratch, a=ACTOR_INDEX, b=ASSIGNEE_INDEX,
    )
    await _run(conn, "upgrade")
    after = await _rows(
        conn,
        "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = :s AND c.relname IN (:a, :b) ORDER BY c.relname",
        s=scratch, a=ACTOR_INDEX, b=ASSIGNEE_INDEX,
    )
    assert len(before) == 2 and after == before          # the same two indexes, not rebuilt
    assert await _indexes(conn, scratch) == _expected(scratch)


# ── an INVALID leftover of a failed concurrent build ────────────────────────


async def _plant_invalid_actor_index(scratch: str) -> None:
    """A real failed CONCURRENTLY build: a unique index over two equal values.

    The build fails on the duplicate and leaves an INVALID index of that name
    behind (what an interrupted 0238 would leave); no superuser needed.
    """
    engine = create_async_engine(_dsn(), isolation_level="AUTOCOMMIT", pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as autocommit:
            await autocommit.execute(text(
                f'INSERT INTO "{scratch}".project_activity_events (id, actor_id, actor_type) '
                "VALUES (:a, NULL, 'system'), (:b, NULL, 'system')"
            ), {"a": uuid.uuid4(), "b": uuid.uuid4()})
            with pytest.raises(DBAPIError):
                await autocommit.execute(text(
                    f"CREATE UNIQUE INDEX CONCURRENTLY {ACTOR_INDEX} "
                    f'ON "{scratch}".project_activity_events ((1))'
                ))
    finally:
        await engine.dispose()


async def test_an_invalid_leftover_is_dropped_and_rebuilt_valid(conn):
    scratch = await _scratch(conn)
    await _plant_invalid_actor_index(scratch)
    planted = await _indexes(conn, scratch)
    assert set(planted) == {ACTOR_INDEX} and planted[ACTOR_INDEX][1] is False   # really INVALID
    await _run(conn, "upgrade")
    assert await _indexes(conn, scratch) == _expected(scratch)


# ── the reads the indexes exist for ─────────────────────────────────────────


async def _seed(conn, scratch: str, me: uuid.UUID) -> None:
    """Three people's signed-in rows plus one API key's rows on my id; cases, a few assigned."""
    now = datetime.now(timezone.utc)
    others = [uuid.uuid4(), uuid.uuid4()]
    rows = []
    for n in range(300):
        actor = (me, *others)[n % 3]
        rows.append({"id": uuid.uuid4(), "actor": actor, "kind": "user", "at": now - timedelta(minutes=n)})
    for n in range(100):   # a CI key carries its owner's actor_id
        rows.append({"id": uuid.uuid4(), "actor": me, "kind": "api_key", "at": now - timedelta(minutes=n)})
    await conn.execute(
        text(
            f'INSERT INTO "{scratch}".project_activity_events (id, actor_id, actor_type, occurred_at) '
            "VALUES (:id, :actor, :kind, :at)"
        ),
        rows,
    )
    cases = [
        {"id": uuid.uuid4(), "assignee": (me if n % 50 == 0 else (others[0] if n % 7 == 0 else None))}
        for n in range(300)
    ]
    await conn.execute(
        text(f'INSERT INTO "{scratch}".managed_test_cases (id, assignee_id) VALUES (:id, :assignee)'),
        cases,
    )
    await conn.execute(text(f'ANALYZE "{scratch}".project_activity_events'))
    await conn.execute(text(f'ANALYZE "{scratch}".managed_test_cases'))
    await conn.commit()


async def _plan(conn, sql: str, **params) -> str:
    # enable_seqscan off for THIS connection only (reset again here and by the
    # fixture): the question is whether the index CAN serve the read, not the
    # planner's cost choice on a few hundred rows.
    await conn.execute(text("SET enable_seqscan = off"))
    plan = "\n".join(r[0] for r in (await conn.execute(text(f"EXPLAIN {sql}"), params)).all())
    await conn.execute(text("RESET enable_seqscan"))
    await conn.commit()
    return plan


async def test_part_a_reads_through_the_actor_index(conn):
    scratch = await _scratch(conn)
    await _run(conn, "upgrade")
    me = uuid.uuid4()
    await _seed(conn, scratch, me)
    plan = await _plan(
        conn,
        "SELECT id FROM project_activity_events WHERE actor_type = 'user' AND actor_id = :me "
        "ORDER BY occurred_at DESC, id DESC LIMIT 51",
        me=me,
    )
    assert ACTOR_INDEX in plan, plan
    assert "Sort" not in plan, plan          # the index order IS the keyset order
    # an API key's rows are outside the partial index's predicate
    key_plan = await _plan(
        conn,
        "SELECT id FROM project_activity_events WHERE actor_type = 'api_key' AND actor_id = :me "
        "ORDER BY occurred_at DESC, id DESC LIMIT 51",
        me=me,
    )
    assert ACTOR_INDEX not in key_plan, key_plan


async def test_part_c_reads_through_the_assignee_index(conn):
    scratch = await _scratch(conn)
    await _run(conn, "upgrade")
    me = uuid.uuid4()
    await _seed(conn, scratch, me)
    plan = await _plan(conn, "SELECT id FROM managed_test_cases WHERE assignee_id = :me", me=me)
    assert ASSIGNEE_INDEX in plan, plan


async def test_the_shared_tables_carry_heads_two_indexes(conn):
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    await conn.commit()
    assert await _indexes(conn, base) == _expected(base)
