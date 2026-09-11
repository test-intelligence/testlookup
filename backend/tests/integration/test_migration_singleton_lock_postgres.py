"""N24: the migration lock outlives every commit, and waiting for it stalls nothing.

The first lock was ``pg_advisory_xact_lock`` inside the migration transaction.
Every ``autocommit_block()`` commits, which released it, so from the first
``CREATE INDEX CONCURRENTLY`` two ``alembic upgrade head`` runs interleaved.

The first replacement blocked in ``pg_advisory_lock``. That deadlocked: the
blocked statement holds a snapshot, the holder's CONCURRENTLY build waits for
every older snapshot, and PostgreSQL cannot see the cycle because the builder
and the lock holder are different backends. Waiters now poll.

These run on real PostgreSQL:

* the lock is still held after a COMMIT and during a real CONCURRENTLY build
  on a different connection (``pg_locks`` sampled while it builds);
* a second migrator WAITING for the lock does not stall that build;
* a real ``alembic upgrade head`` subprocess waits on a held lock and finishes
  once it is released (so env.py really takes it).

Nothing here changes the schema: builds are on scratch tables dropped at the
end, and the database is expected to be at head already.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN``.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import subprocess
import sys
import time
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migration_lock import (
    ALEMBIC_ADVISORY_LOCK_ID,
    MigrationLockTimeout,
    migration_singleton_lock,
)

pytestmark = pytest.mark.integration

BACKEND = pathlib.Path(__file__).resolve().parents[2]
# pg_locks splits a bigint advisory key into two 32-bit halves.
_KEY = ALEMBIC_ADVISORY_LOCK_ID & 0xFFFFFFFFFFFFFFFF
_CLASSID = _KEY >> 32
_OBJID = _KEY & 0xFFFFFFFF


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def _holder_pids(conn) -> list[int]:
    rows = await conn.execute(
        text(
            "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted "
            "AND classid = CAST(:classid AS oid) AND objid = CAST(:objid AS oid) "
            "AND objsubid = 1"
        ),
        {"classid": _CLASSID, "objid": _OBJID},
    )
    return [row[0] for row in rows]


async def _scratch_table(conn, table: str, rows: int) -> None:
    await conn.execute(text(f"CREATE TABLE {table} (n int)"))
    await conn.execute(text(f"INSERT INTO {table} SELECT generate_series(1, {rows})"))
    await conn.commit()


async def test_the_lock_survives_commits_and_a_concurrently_build():
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    table = f"n24_scratch_{uuid.uuid4().hex[:10]}"
    try:
        async with engine.connect() as observer_raw:
            observer = await observer_raw.execution_options(isolation_level="AUTOCOMMIT")
            async with migration_singleton_lock(engine.connect) as lock_conn:
                lock_pid = await lock_conn.scalar(text("SELECT pg_backend_pid()"))

                async with engine.connect() as migrator_raw:
                    # What alembic's migration connection does: a transaction
                    # that commits, then an autocommit CONCURRENTLY build.
                    await _scratch_table(migrator_raw, table, 400_000)
                    migrator = await migrator_raw.execution_options(
                        isolation_level="AUTOCOMMIT"
                    )
                    migrator_pid = await migrator.scalar(text("SELECT pg_backend_pid()"))

                    build = asyncio.create_task(
                        migrator.execute(
                            text(f"CREATE INDEX CONCURRENTLY {table}_n ON {table} (n)")
                        )
                    )
                    seen_during_build = False
                    while not build.done():
                        building = await observer.scalar(
                            text(
                                "SELECT count(*) FROM pg_stat_activity WHERE pid = :pid "
                                "AND query ILIKE 'CREATE INDEX CONCURRENTLY%' "
                                "AND state = 'active'"
                            ),
                            {"pid": migrator_pid},
                        )
                        if building:
                            assert await _holder_pids(observer) == [lock_pid]
                            seen_during_build = True
                        await asyncio.sleep(0.005)
                    await build
                    assert seen_during_build, "the build finished before it was sampled"

                # After the commit and the build, another migrator is still refused.
                assert await _holder_pids(observer) == [lock_pid]
                taken = await observer.scalar(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": ALEMBIC_ADVISORY_LOCK_ID}
                )
                assert taken is False

            assert await _holder_pids(observer) == []
            taken = await observer.scalar(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": ALEMBIC_ADVISORY_LOCK_ID}
            )
            assert taken is True
            await observer.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": ALEMBIC_ADVISORY_LOCK_ID}
            )
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
        await engine.dispose()


async def test_a_waiting_migrator_does_not_stall_the_holders_concurrently_build():
    """The deadlock the first session-lock version had: found by running two
    real upgrades from 0165, where the second one's blocked lock call held a
    snapshot that 0166's build waited on for ever."""
    engine = create_async_engine(_dsn(), pool_size=5, max_overflow=0)
    table = f"n24_wait_{uuid.uuid4().hex[:10]}"
    second_entered = asyncio.Event()
    try:
        async with migration_singleton_lock(engine.connect):

            async def _second_migrator():
                async with migration_singleton_lock(
                    engine.connect, wait_seconds=120, poll_seconds=0.05
                ):
                    second_entered.set()

            waiter = asyncio.create_task(_second_migrator())
            await asyncio.sleep(0.3)  # the waiter is now queued on the lock
            assert not waiter.done()

            async with engine.connect() as migrator_raw:
                await _scratch_table(migrator_raw, table, 50_000)
                migrator = await migrator_raw.execution_options(isolation_level="AUTOCOMMIT")
                await asyncio.wait_for(
                    migrator.execute(
                        text(f"CREATE INDEX CONCURRENTLY {table}_n ON {table} (n)")
                    ),
                    timeout=30,
                )
            assert not second_entered.is_set(), "the waiter ran while the lock was held"
        await asyncio.wait_for(waiter, timeout=30)
        assert second_entered.is_set()
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
        await engine.dispose()


async def test_a_migrator_that_cannot_get_the_lock_gives_up_loudly():
    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    try:
        async with migration_singleton_lock(engine.connect):
            started = time.monotonic()
            with pytest.raises(MigrationLockTimeout):
                async with migration_singleton_lock(
                    engine.connect, wait_seconds=0.5, poll_seconds=0.05
                ):
                    pytest.fail("entered the body without the lock")
            assert time.monotonic() - started < 10
    finally:
        await engine.dispose()


async def test_alembic_upgrade_waits_for_the_lock_and_then_finishes():
    dsn = _dsn()
    engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    env = dict(os.environ, DATABASE_URL=dsn, MIGRATION_LOCK_WAIT_SECONDS="120")
    try:
        async with engine.connect() as holder_raw:
            holder = await holder_raw.execution_options(isolation_level="AUTOCOMMIT")
            await holder.execute(
                text("SELECT pg_advisory_lock(:k)"), {"k": ALEMBIC_ADVISORY_LOCK_ID}
            )
            proc = subprocess.Popen(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=BACKEND,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            try:
                # At head, an unlocked upgrade exits in a couple of seconds.
                await asyncio.sleep(8)
                assert proc.poll() is None, (
                    "alembic finished while another session held the migration lock: "
                    + proc.communicate()[0].decode(errors="replace")
                )
            finally:
                await holder.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": ALEMBIC_ADVISORY_LOCK_ID}
                )
            output, _ = await asyncio.to_thread(proc.communicate, timeout=180)
            text_out = output.decode(errors="replace")
            assert proc.returncode == 0, text_out
            assert "Waiting for the migration lock" in text_out, text_out
    finally:
        await engine.dispose()
