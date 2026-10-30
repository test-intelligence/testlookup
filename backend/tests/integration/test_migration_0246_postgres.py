"""Migration 0246 (W23c AGT-11) on real PostgreSQL.

* The upgrade adds ``agent_invocations.input_payload`` (``jsonb``) and
  ``input_sha256`` (``character varying(64)``), both nullable with no default;
  rows that existed before read NULL in both (every earlier invocation is a
  stored-subject one).
* Both CHECKs exist and are VALIDATED (``pg_constraint.convalidated``) after
  the ``NOT VALID`` + ``VALIDATE`` pair, with the ORM's texts.
* An insert with a payload but no hash (or a hash but no payload) fails
  ``ck_agent_invocations_input_pair``; an upper-case or short hash fails
  ``ck_agent_invocations_input_sha256``; a payload with its lower-case hash is
  stored and round-trips as JSONB.
* Downgrade removes all four objects; upgrading again restores them (the
  round trip).
* The shared database (migrated to head) carries both columns and both
  validated CHECKs, at a head >= 0246.

The migration runs through a real Alembic ``Operations`` context against a
SCRATCH schema first on the search path (``agent_invocations`` as ``LIKE …
INCLUDING DEFAULTS`` without the new columns, as 0245 left it). The work
commits; the scratch schema is dropped afterwards and the shared tables are
only read. The fixture awaits ``app.db.postgres.dispose_engine_for_loop()``
before building its engine and after disposing it; it binds no Redis / Mongo
client and closes none. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a
database migrated to head.
"""
from __future__ import annotations

import importlib.util
import json
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
M0246 = "0246_agent_invocation_inline_input.py"
PAIR = "ck_agent_invocations_input_pair"
SHA = "ck_agent_invocations_input_sha256"
EXPECTED_DEFS = {
    PAIR: "CHECK (((input_payload IS NULL) = (input_sha256 IS NULL)))",
    SHA: "CHECK (((input_sha256 IS NULL) OR ((input_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
}


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _migration():
    spec = importlib.util.spec_from_file_location("m_0246_pg", VERSIONS / M0246)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def conn():
    """A connection whose work COMMITS; the scratch schema is dropped afterwards."""
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
    scratch = f"w23ca_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    await conn.execute(text(
        f'CREATE TABLE "{scratch}".agent_invocations (LIKE "{base}".agent_invocations INCLUDING DEFAULTS)'
    ))
    # The table as 0245 left it: no inline-input columns yet (LIKE copies no CHECK constraint).
    await conn.execute(text(f'ALTER TABLE "{scratch}".agent_invocations DROP COLUMN IF EXISTS input_sha256'))
    await conn.execute(text(f'ALTER TABLE "{scratch}".agent_invocations DROP COLUMN IF EXISTS input_payload'))
    await conn.execute(text(f'ALTER TABLE "{scratch}".agent_invocations ADD PRIMARY KEY (id)'))
    await conn.execute(text(f'SET search_path TO "{scratch}", "{base}"'))
    await conn.commit()
    return scratch


async def _migrate(conn, step: str) -> None:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    module = _migration()

    def _run(sync_conn):
        context = MigrationContext.configure(sync_conn)
        module.op = Operations(context)
        with context.begin_transaction():
            getattr(module, step)()

    await conn.commit()
    await conn.run_sync(_run)
    await conn.commit()


async def _columns(conn, schema: str) -> dict[str, tuple]:
    rows = (await conn.execute(text(
        "SELECT column_name, data_type, character_maximum_length, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema = :s AND table_name = 'agent_invocations' "
        "AND column_name IN ('input_payload', 'input_sha256')"
    ), {"s": schema})).all()
    await conn.commit()
    return {name: (data_type, length, nullable, default) for name, data_type, length, nullable, default in rows}


async def _checks(conn, schema: str) -> dict[str, tuple[bool, str]]:
    rows = (await conn.execute(text(
        "SELECT con.conname, con.convalidated, pg_get_constraintdef(con.oid) FROM pg_constraint con "
        "JOIN pg_class c ON c.oid = con.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = :s AND c.relname = 'agent_invocations' AND con.contype = 'c' "
        "AND con.conname IN (:pair, :sha)"
    ), {"s": schema, "pair": PAIR, "sha": SHA})).all()
    await conn.commit()
    return {name: (bool(valid), definition) for name, valid, definition in rows}


_INSERT = (
    "INSERT INTO agent_invocations (id, project_id, agent_id, stage_name, test_run_id, pipeline_run_id, "
    "workflow_type{extra_cols}) VALUES (:id, :p, 'agent.summary.v1', 'summary', :run, :pipe, 'offline'{extra_vals})"
)


async def _insert(conn, *, payload=None, sha=None, with_inline_columns: bool = True) -> uuid.UUID:
    row_id = uuid.uuid4()
    params = {"id": row_id, "p": uuid.uuid4(), "run": uuid.uuid4(), "pipe": uuid.uuid4()}
    if with_inline_columns:
        sql = _INSERT.format(
            extra_cols=", input_payload, input_sha256", extra_vals=", CAST(:payload AS jsonb), :sha"
        )
        params.update(payload=None if payload is None else json.dumps(payload), sha=sha)
    else:
        sql = _INSERT.format(extra_cols="", extra_vals="")
    await conn.execute(text(sql), params)
    await conn.commit()
    return row_id


async def _refused(conn, constraint: str, **kwargs) -> None:
    with pytest.raises(DBAPIError) as raised:
        await _insert(conn, **kwargs)
    await conn.rollback()
    assert constraint in str(raised.value), str(raised.value)


# ── the upgrade, the CHECKs, the round trip ──────────────────────────────────


async def test_0246_adds_two_nullable_columns_two_validated_checks_and_round_trips(conn):
    scratch = await _scratch(conn)
    before = [await _insert(conn, with_inline_columns=False) for _ in range(3)]
    assert await _columns(conn, scratch) == {} and await _checks(conn, scratch) == {}

    await _migrate(conn, "upgrade")
    assert await _columns(conn, scratch) == {
        "input_payload": ("jsonb", None, "YES", None),
        "input_sha256": ("character varying", 64, "YES", None),
    }
    checks = await _checks(conn, scratch)
    assert {name: valid for name, (valid, _) in checks.items()} == {PAIR: True, SHA: True}
    assert {name: " ".join(d.split()) for name, (_, d) in checks.items()} == EXPECTED_DEFS

    # Rows that existed before: a stored-subject invocation, NULL in both.
    rows = (await conn.execute(text(
        "SELECT count(*) FROM agent_invocations WHERE id = ANY(:ids) AND input_payload IS NULL AND input_sha256 IS NULL"
    ), {"ids": before})).scalar_one()
    await conn.commit()
    assert rows == 3

    payload = {"schema_version": 1, "failures": [{"test_name": "checkout pays sentinel", "confidence_score": 70}]}
    stored = await _insert(conn, payload=payload, sha="a1" * 32)
    value = (await conn.execute(
        text("SELECT input_payload FROM agent_invocations WHERE id = :id"), {"id": stored}
    )).scalar_one()
    await conn.commit()
    assert value == payload

    await _refused(conn, PAIR, payload=payload, sha=None)
    await _refused(conn, PAIR, payload=None, sha="b2" * 32)
    await _refused(conn, SHA, payload=payload, sha="A1" * 32)
    await _refused(conn, SHA, payload=payload, sha="a1" * 31)

    await _migrate(conn, "downgrade")
    assert await _columns(conn, scratch) == {}
    assert await _checks(conn, scratch) == {}

    await _migrate(conn, "upgrade")
    assert set(await _columns(conn, scratch)) == {"input_payload", "input_sha256"}
    assert {name: valid for name, (valid, _) in (await _checks(conn, scratch)).items()} == {PAIR: True, SHA: True}


# ── the shared database, at head ─────────────────────────────────────────────


async def test_the_shared_database_at_head_carries_both_columns_and_both_validated_checks(conn):
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    await conn.commit()
    assert version >= "0246", version
    assert set(await _columns(conn, base)) == {"input_payload", "input_sha256"}
    assert {name: valid for name, (valid, _) in (await _checks(conn, base)).items()} == {PAIR: True, SHA: True}
