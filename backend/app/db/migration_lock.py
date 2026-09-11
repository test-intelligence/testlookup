"""The singleton lock every online ``alembic upgrade`` holds (re-audit N24).

All API replicas share one image, so a rolling deployment can start several
``alembic upgrade head`` processes at once. They must not interleave.

The first version took ``pg_advisory_xact_lock`` inside the migration
transaction. A transaction-scoped lock is released on COMMIT, and every
``autocommit_block()`` commits: from the first ``CREATE INDEX CONCURRENTLY``
(0082, 0152, 0153, 0166, 0167) to the end of the upgrade, two runs were not
serialised at all.

This holds a SESSION-level advisory lock on a dedicated AUTOCOMMIT connection
for the whole upgrade. A session lock survives every commit on every other
connection; it is released by ``pg_advisory_unlock`` on exit, or by the server
when the connection closes or the process dies, so a crashed migrator cannot
wedge the next one.

Why the waiter POLLS instead of blocking in ``pg_advisory_lock``
----------------------------------------------------------------
A statement blocked in ``SELECT pg_advisory_lock(...)`` holds a snapshot for
as long as it waits. ``CREATE INDEX CONCURRENTLY`` waits for every transaction
with an older snapshot to finish -- including that waiter, which is waiting
for the lock the building migrator's session holds. The two backends belong
to different clients as far as PostgreSQL can tell, so its deadlock detector
never fires: both migrators hang for ever. Proven on PostgreSQL 16 while
fixing N24 (the second upgrade's blocked lock call stalled 0166's build).

So the waiter asks ``pg_try_advisory_lock`` -- a statement that returns at
once -- and sleeps between tries holding no snapshot. The wait is bounded
(``MIGRATION_LOCK_WAIT_SECONDS``, default 30 minutes): a migrator that cannot
get the lock in that time fails loudly instead of hanging a rollout.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger("alembic.env")

# A stable, application-specific signed bigint. Changing it lets an old and a
# new image migrate concurrently during the rollout that ships the change.
ALEMBIC_ADVISORY_LOCK_ID = 6075990748104101441


class MigrationLockTimeout(RuntimeError):
    """Another migrator held the lock for longer than we were willing to wait."""


def _wait_seconds_from_env() -> float:
    try:
        return max(float(os.getenv("MIGRATION_LOCK_WAIT_SECONDS", "1800")), 0.0)
    except ValueError:
        return 1800.0


@asynccontextmanager
async def migration_singleton_lock(
    connect: Callable[[], Awaitable[AsyncConnection]],
    *,
    lock_id: int = ALEMBIC_ADVISORY_LOCK_ID,
    wait_seconds: float | None = None,
    poll_seconds: float = 0.5,
) -> AsyncIterator[AsyncConnection]:
    """Hold the migration lock, on a connection of its own, for the body."""
    budget = _wait_seconds_from_env() if wait_seconds is None else wait_seconds
    connection = await connect()
    try:
        # AUTOCOMMIT: no transaction is left open on the lock connection, so
        # between tries it holds no snapshot a CONCURRENTLY build would wait on.
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        deadline = time.monotonic() + budget
        announced = False
        while not await connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": lock_id}
        ):
            if time.monotonic() >= deadline:
                raise MigrationLockTimeout(
                    f"another migration held the lock for over {budget:.0f}s"
                )
            if not announced:
                logger.info("Waiting for the migration lock held by another migrator")
                announced = True
            await asyncio.sleep(poll_seconds)
        try:
            yield connection
        finally:
            try:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id}
                )
            except Exception as exc:  # noqa: BLE001 - close() below releases it too
                logger.warning("Could not unlock the migration lock explicitly: %s", exc)
    finally:
        await connection.close()
