"""Migration 0225 (W11 REL-11) on real PostgreSQL.

* An existing pack reads ``signature`` and ``signing_key_id`` as NULL (it
  verifies as ``unsigned``).
* ``uq_compliance_signing_keys_active`` refuses a second ACTIVE key and
  accepts a retired one beside the active one.
* upgrade -> downgrade 0224 -> upgrade again works, and the downgrade leaves
  the ``secret_refs`` rows alone (key material an operator may still want).

The migration runs through a real Alembic ``Operations`` context against a
SCRATCH schema: ``compliance_packs`` copied as 0224 left it (``LIKE ...
INCLUDING DEFAULTS`` minus the two new columns), one-column ``users`` (the
FK target) and ``secret_refs`` copies. The scratch schema is dropped
afterwards; the shared tables are never written, and the last test checks
they carry head's table, index and columns.

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
M0225 = "0225_compliance_pack_signing.py"
NEW_PACK_COLUMNS = ("signature", "signing_key_id")


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _migration():
    spec = importlib.util.spec_from_file_location("m_0225_pg", VERSIONS / M0225)
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
    scratch = f"w11a_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    await conn.execute(text(f'CREATE TABLE "{scratch}".compliance_packs (LIKE "{base}".compliance_packs INCLUDING DEFAULTS)'))
    for column in NEW_PACK_COLUMNS:
        await conn.execute(text(f'ALTER TABLE "{scratch}".compliance_packs DROP COLUMN {column}'))
    await conn.execute(text(f'CREATE TABLE "{scratch}".users (id uuid PRIMARY KEY)'))
    await conn.execute(text(f'CREATE TABLE "{scratch}".secret_refs (LIKE "{base}".secret_refs INCLUDING DEFAULTS)'))
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


async def _columns(conn, schema: str, table: str) -> set[str]:
    rows = await _rows(
        conn, "SELECT column_name FROM information_schema.columns WHERE table_schema = :s AND table_name = :t",
        s=schema, t=table,
    )
    return {r[0] for r in rows}


async def _tables(conn, schema: str) -> set[str]:
    rows = await _rows(conn, "SELECT table_name FROM information_schema.tables WHERE table_schema = :s", s=schema)
    return {r[0] for r in rows}


async def _index_def(conn, schema: str) -> str | None:
    rows = await _rows(
        conn, "SELECT indexdef FROM pg_indexes WHERE schemaname = :s AND indexname = 'uq_compliance_signing_keys_active'",
        s=schema,
    )
    return rows[0][0] if rows else None


async def _pack(conn) -> uuid.UUID:
    pack = uuid.uuid4()
    await conn.execute(text(
        "INSERT INTO compliance_packs (id, project_id, minio_key, manifest_sha256, file_count, bytes, "
        "retention_expires_at, generated_at) VALUES (:id, :p, 'compliance/x.zip', :sha, 13, 4096, "
        "now() + interval '7 years', now())"
    ), {"id": pack, "p": uuid.uuid4(), "sha": "ab" * 32})
    await conn.commit()
    return pack


async def _key(conn, key_id: str, *, retired: bool = False) -> None:
    await conn.execute(text(
        "INSERT INTO compliance_signing_keys (key_id, public_key_pem, retired_at) "
        "VALUES (:k, '-----BEGIN PUBLIC KEY-----', CASE WHEN :r THEN now() END)"
    ), {"k": key_id, "r": retired})
    await conn.commit()


async def test_existing_packs_read_unsigned_and_one_active_key_is_enforced(conn):
    scratch = await _scratch(conn)
    pack = await _pack(conn)

    await _run(conn, "upgrade")

    assert await _rows(conn, "SELECT signature, signing_key_id FROM compliance_packs WHERE id = :id", id=pack) == [
        (None, None),
    ]
    assert "compliance_signing_keys" in await _tables(conn, scratch)
    definition = await _index_def(conn, scratch)
    assert definition is not None and "UNIQUE" in definition and "WHERE (retired_at IS NULL)" in definition
    defaults = await _rows(conn, "SELECT algorithm, created_at IS NOT NULL FROM compliance_signing_keys")
    assert defaults == []

    await _key(conn, "k-aaaaaaaaaaaaaaaa")
    assert await _rows(conn, "SELECT algorithm, created_at IS NOT NULL FROM compliance_signing_keys") == [
        ("ed25519", True),
    ]
    with pytest.raises(IntegrityError):
        await _key(conn, "k-bbbbbbbbbbbbbbbb")                  # a second ACTIVE key
    await conn.rollback()
    await _key(conn, "k-cccccccccccccccc", retired=True)        # a retired one beside it
    assert await _rows(conn, "SELECT count(*) FROM compliance_signing_keys") == [(2,)]


async def test_upgrade_downgrade_upgrade_and_the_secrets_are_left(conn):
    scratch = await _scratch(conn)
    await _pack(conn)
    await _run(conn, "upgrade")
    await conn.execute(text(
        "INSERT INTO secret_refs (id, scope, provider, key_name, encrypted_value, rotation_status) "
        "VALUES (:id, 'compliance_signing', 'db', 'k-aaaaaaaaaaaaaaaa', 'ciphertext', 'active')"
    ), {"id": uuid.uuid4()})
    await conn.commit()

    await _run(conn, "downgrade")
    assert "compliance_signing_keys" not in await _tables(conn, scratch)
    assert not set(NEW_PACK_COLUMNS) & await _columns(conn, scratch, "compliance_packs")
    assert await _index_def(conn, scratch) is None
    assert await _rows(conn, "SELECT key_name FROM secret_refs WHERE scope = 'compliance_signing'") == [
        ("k-aaaaaaaaaaaaaaaa",),
    ], "a downgrade never destroys key material"

    await _run(conn, "upgrade")                                   # upgrade again
    assert set(NEW_PACK_COLUMNS) <= await _columns(conn, scratch, "compliance_packs")
    assert await _index_def(conn, scratch) is not None


async def test_the_shared_tables_carry_heads_table_index_and_columns(conn):
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    await conn.commit()
    assert {
        "key_id", "algorithm", "public_key_pem", "created_at", "created_by_user_id",
        "retired_at", "retired_by_user_id", "retired_reason",
    } == await _columns(conn, base, "compliance_signing_keys")
    assert set(NEW_PACK_COLUMNS) <= await _columns(conn, base, "compliance_packs")
    definition = await _index_def(conn, base)
    assert definition is not None and "WHERE (retired_at IS NULL)" in definition
