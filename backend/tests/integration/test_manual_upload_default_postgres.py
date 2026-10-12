"""``manual_upload`` is ON for a new install and untouched on an existing one.

Migration 0201 flips the flag 0092 seeded OFF, but only on a database with no
user and no project — the state ``alembic upgrade head`` runs in on a new
install. Every check runs the migration's own SQL inside a transaction that is
rolled back, so nothing in the shared test database changes.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head;
skips otherwise. Runs in CI as part of the ``postgres-integration`` job's file
list in ``.github/workflows/ci.yml``.
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

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "0201_manual_upload_on_for_new_installs.py"
)


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0201_pg", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def _flag(conn) -> bool:
    return (
        await conn.execute(
            text("SELECT enabled_global FROM feature_flags WHERE key = 'manual_upload'")
        )
    ).scalar_one()


async def test_an_existing_install_keeps_its_stored_value():
    migration = _migration()
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            try:
                await conn.execute(
                    text("UPDATE feature_flags SET enabled_global = false WHERE key = 'manual_upload'")
                )
                token = uuid.uuid4().hex[:12]
                await conn.execute(
                    text(
                        "INSERT INTO users (id, email, username, hashed_password, role, "
                        "is_active, is_service_account, is_synthetic, must_change_password, "
                        "mfa_enabled, failed_login_attempts) "
                        "VALUES (gen_random_uuid(), :email, :username, 'x', 'VIEWER', "
                        "true, false, false, false, false, 0)"
                    ),
                    {"email": f"existing-{token}@example.com", "username": f"existing-{token}"},
                )
                await conn.execute(text(migration.UPGRADE_SQL))
                assert await _flag(conn) is False, "a used database must keep its OFF"
            finally:
                await tx.rollback()
    finally:
        await engine.dispose()


async def test_a_new_install_starts_with_upload_on():
    migration = _migration()
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            try:
                # Make this transaction look like a never-used database. Empty
                # temporary tables named users and projects shadow the real ones
                # (a session's temp schema is searched first), so the migration's
                # NOT EXISTS checks see no user and no project. Not TRUNCATE ...
                # CASCADE: feature_flags.updated_by references users, so the
                # cascade emptied feature_flags too and the flag row was gone.
                await conn.execute(text("CREATE TEMP TABLE users (id uuid)"))
                await conn.execute(text("CREATE TEMP TABLE projects (id uuid)"))
                await conn.execute(
                    text("UPDATE feature_flags SET enabled_global = false WHERE key = 'manual_upload'")
                )
                await conn.execute(text(migration.UPGRADE_SQL))
                assert await _flag(conn) is True
                await conn.execute(text(migration.DOWNGRADE_SQL))
                assert await _flag(conn) is False
            finally:
                await tx.rollback()
    finally:
        await engine.dispose()
