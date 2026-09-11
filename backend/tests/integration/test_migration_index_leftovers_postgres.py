"""0166 and 0167 rebuild whatever holds their index's name, and keep their own index.

``CREATE INDEX CONCURRENTLY IF NOT EXISTS`` skips its build whenever the name is
taken, so each migration first asks the catalog (``_stale_index``) what holds
the name, and drops it CONCURRENTLY unless it is that very index, valid. "That
very index" is decided by having the server render the definition on an empty
copy of the table: Postgres stores an index as a parse tree and renders it back
in its own words, so a text comparison with the migration's source cannot work
(0167's ``DESC NULLS FIRST`` comes back as ``DESC``).

This runs the migrations' own ``_stale_index`` against real catalogs. Every case
plants its state inside a transaction that is rolled back, so the shared
database keeps its indexes. The INVALID case marks the real index invalid in
``pg_index``, which needs a superuser; it skips without one.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

VERSIONS = pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"


def _migration(filename: str):
    spec = importlib.util.spec_from_file_location(filename[:-3], VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M0166 = _migration("0166_test_case_tags_trgm_index.py")
M0167 = _migration("0167_test_runs_natural_build_index.py")


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def _stale_after(migration, *plant: str):
    """What ``migration._stale_index`` answers once ``plant`` has run, rolled back after."""
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)

    def run(conn):
        transaction = conn.begin()
        try:
            conn.execute(text("SET LOCAL lock_timeout = '5s'"))
            for statement in plant:
                conn.execute(text(statement))
            return migration._stale_index(conn)
        finally:
            transaction.rollback()

    try:
        async with engine.connect() as conn:
            return await conn.run_sync(run)
    finally:
        await engine.dispose()


async def test_0166_keeps_the_index_it_built():
    assert await _stale_after(M0166) is None


async def test_0167_keeps_the_index_it_built():
    """The hard case: an expression the server renders nothing like the source."""
    assert await _stale_after(M0167) is None


async def test_a_free_name_needs_no_drop():
    assert await _stale_after(M0166, f"DROP INDEX {M0166.INDEX}") is None


async def test_an_invalid_leftover_is_dropped():
    try:
        stale = await _stale_after(
            M0166,
            "UPDATE pg_index SET indisvalid = false "
            f"WHERE indexrelid = CAST('{M0166.INDEX}' AS regclass)",
        )
    except DBAPIError as exc:
        if "permission denied" in str(exc):
            pytest.skip("marking an index INVALID needs a superuser")
        raise
    assert stale == f"public.{M0166.INDEX}"


@pytest.mark.parametrize("migration, replacement", [
    (M0166, [f"CREATE INDEX {M0166.INDEX} ON test_cases (test_name)"]),
    (M0166, [f"CREATE INDEX {M0166.INDEX} ON test_cases USING gin ((CAST(tags AS VARCHAR)) gin_trgm_ops)"]),
    (M0167, [f"CREATE INDEX {M0167.INDEX} ON test_runs (project_id, build_number DESC)"]),
], ids=["0166 btree", "0166 varchar cast", "0167 raw build_number"])
async def test_a_valid_index_that_is_not_this_one_is_dropped(migration, replacement):
    stale = await _stale_after(migration, f"DROP INDEX {migration.INDEX}", *replacement)
    assert stale == f"public.{migration.INDEX}"


@pytest.mark.parametrize("migration, plant", [
    (M0166, [f"CREATE INDEX {M0166.INDEX} ON test_runs (build_number)"]),
    # Renders exactly like the real one after USING: only the table differs.
    (M0166, ["CREATE TABLE r3_other_tags (tags jsonb)",
             f"CREATE INDEX {M0166.INDEX} ON r3_other_tags {M0166.DEFINITION}"]),
    (M0167, [f"CREATE INDEX {M0167.INDEX} ON test_cases (test_name)"]),
], ids=["0166 another table", "0166 same definition, another table", "0167 another table"])
async def test_an_index_of_this_name_on_another_table_stops_the_migration(migration, plant):
    """It is not this migration's index, so not this migration's to drop: the
    first version dropped it CONCURRENTLY (code review round 3)."""
    with pytest.raises(RuntimeError, match="on another table"):
        await _stale_after(migration, f"DROP INDEX {migration.INDEX}", *plant)


async def test_a_table_holding_the_name_stops_the_migration():
    with pytest.raises(RuntimeError, match="is not an index"):
        await _stale_after(M0166, f"DROP INDEX {M0166.INDEX}", f"CREATE TABLE {M0166.INDEX} (id int)")


async def test_the_probe_leaves_nothing_behind():
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    try:
        await _stale_after(M0166)
        await _stale_after(M0167)
        async with engine.connect() as conn:
            left = (await conn.execute(text(
                "SELECT count(*) FROM pg_class WHERE relname LIKE '\\_alembic\\_016%'"
            ))).scalar_one()
        assert left == 0
    finally:
        await engine.dispose()
