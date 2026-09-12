"""Leases and fencing tokens for agent pipeline runs (architecture E7.3).

What this replaces
------------------
A ``running`` row was declared dead by a fixed 30-minute age
(``RUNNING_STALE_THRESHOLD`` in the /agents router, and the same rule in the
reaper). That heuristic is wrong in both directions: a legitimately long deep
run is reported failed while it is still working, and a worker that dies in the
first minute keeps its row ``running`` for half an hour.

A lease makes staleness exact. The worker says "I am alive" on a schedule; the
row stops being anyone's the moment it stops saying so.

Why a fencing token and not just a lease
----------------------------------------
A lease alone prevents double *dispatch*, not double *execution*. A worker that
is merely slow -- a GC pause, a single long model call, a stalled socket -- can
have its lease expire while it is still running, be reaped, and then keep
writing stage rows and checkpoints underneath the new attempt. Both attempts
would also call mutating tools.

So every write a stage makes is fenced: it carries the token the holder
acquired, and the write is refused when the pipeline row no longer holds that
token. The reaper rotates the token when it reclaims a row, so the old attempt's
next write raises :class:`LeaseLost` and it stops. This is the standard fencing
-token rule (Kleppmann): the lock is advisory, the token is what the store
checks.

Timing discipline is borrowed from ``llm_cluster_semaphore`` (re-audit M12),
which solved the same problem for LLM slots:

* the deadline is timed from when the last successful renew was **sent**, never
  from when it returned, so the local view is never later than the database's;
* each renew is bounded by the time left before that deadline, less a margin: a
  renew that stalls is abandoned and the holder is stopped *before* its lease
  can lapse, rather than when the stalled call finally returns;
* a heartbeat that wakes after the deadline (a starved event loop) stops the
  holder at once without trying to renew.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Optional

import structlog

logger = structlog.get_logger("services.pipeline_lease")

__all__ = [
    "HEARTBEAT_SECONDS",
    "LEASE_SECONDS",
    "LeaseLost",
    "PipelineLease",
    "acquire_lease_fields",
    "fence_or_raise",
    "held_lease",
    "new_owner",
    "release_lease_fields",
    "renew_lease",
    "verify_lease",
]

# How often a holder renews. The lease is twice this, so one missed renew is
# survivable and two are not.
HEARTBEAT_SECONDS = 30.0
LEASE_SECONDS = HEARTBEAT_SECONDS * 2
# A renew must land this far before the deadline to be worth attempting.
RENEW_MARGIN_SECONDS = 5.0


class LeaseLost(RuntimeError):
    """This worker no longer holds the pipeline's lease.

    Raised by a fenced write whose token no longer matches, and by the
    heartbeat when it cannot keep the lease. The error code is
    ``lease_lost``, which :mod:`app.services.retry_policy` treats as
    retryable: another worker has almost certainly taken the row over, and if
    it has not, the reaper will.
    """

    def __init__(self, pipeline_run_id: str, token: str | None = None):
        self.pipeline_run_id = str(pipeline_run_id)
        self.token = token
        super().__init__(f"pipeline {pipeline_run_id}: lease lost")


@dataclass(frozen=True)
class PipelineLease:
    """One holder's claim on one pipeline row."""

    pipeline_run_id: str
    owner: str
    token: str

    def as_fields(self, *, now: Optional[datetime] = None) -> dict[str, Any]:
        stamp = now or datetime.now(timezone.utc)
        return {
            "lease_owner": self.owner,
            "fencing_token": self.token,
            "lease_expires_at": stamp + timedelta(seconds=LEASE_SECONDS),
            "heartbeat_at": stamp,
        }


def new_owner() -> str:
    """A human-readable holder id: host and process, enough to find the worker."""
    try:
        host = socket.gethostname()
    except Exception:  # noqa: BLE001 -- naming must never fail an acquire
        host = "unknown"
    return f"{host}:{os.getpid()}"[:255]


def acquire_lease_fields(*, now: Optional[datetime] = None) -> tuple[str, dict[str, Any]]:
    """Column values for taking a fresh lease. Returns ``(token, fields)``.

    The caller sets these on the row in the same transaction that moves it to
    ``running``, so a row can never be running without a lease.
    """
    token = uuid.uuid4().hex
    lease = PipelineLease(pipeline_run_id="", owner=new_owner(), token=token)
    return token, lease.as_fields(now=now)


def release_lease_fields() -> dict[str, Any]:
    """Column values for giving a lease up (the run reached a terminal state)."""
    return {"lease_owner": None, "fencing_token": None, "lease_expires_at": None}


async def verify_lease(db: Any, pipeline_run_id: str, token: str | None) -> bool:
    """True when the row still carries ``token``.

    ``token=None`` means the caller is not fenced (a legacy path, or a row that
    predates the lease columns) and is allowed through: this must not break
    pipelines that were already running when the code shipped.
    """
    if not token or not pipeline_run_id:
        return True
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.postgres import AgentPipelineRun  # noqa: PLC0415

    current = (
        await db.execute(
            select(AgentPipelineRun.fencing_token).where(
                AgentPipelineRun.id == _as_uuid(pipeline_run_id)
            )
        )
    ).scalar_one_or_none()
    # ``scalar_one_or_none`` is Any; compare explicitly so the declared bool holds.
    return bool(current is not None and str(current) == token)


async def fence_or_raise(db: Any, pipeline_run_id: str, token: str | None) -> None:
    """Raise :class:`LeaseLost` unless the row still carries ``token``."""
    if await verify_lease(db, pipeline_run_id, token):
        return
    logger.warning(
        "pipeline_write_fenced_out",
        pipeline_run_id=str(pipeline_run_id),
    )
    raise LeaseLost(pipeline_run_id, token)


async def renew_lease(db: Any, pipeline_run_id: str, token: str) -> bool:
    """Extend the lease if we still hold it. False when we do not.

    One guarded UPDATE: the ``fencing_token`` predicate is what makes a
    reclaimed row refuse its old holder's renew.
    """
    from sqlalchemy import update  # noqa: PLC0415

    from app.models.postgres import AgentPipelineRun  # noqa: PLC0415

    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(AgentPipelineRun)
        .where(
            AgentPipelineRun.id == _as_uuid(pipeline_run_id),
            AgentPipelineRun.fencing_token == token,
        )
        .values(
            lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
            heartbeat_at=now,
        )
        .returning(AgentPipelineRun.id)
    )
    return result.first() is not None


def _as_uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


@contextlib.asynccontextmanager
async def held_lease(
    pipeline_run_id: str,
    token: str | None,
    *,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    on_lost: Optional[asyncio.Event] = None,
) -> AsyncIterator[Optional[asyncio.Event]]:
    """Renew ``token`` for the body's duration; signal when the lease is lost.

    Yields an :class:`asyncio.Event` that is set if the lease goes away. The
    caller decides what to do -- the stage wrapper cancels the node, so a long
    model call does not run on for minutes after its row was reclaimed.

    Critically, this heartbeats from **inside** the stage, not at stage
    boundaries. The revision-1 design renewed in ``mark_stage_running`` /
    ``mark_stage_done``, so a single model call longer than the lease looked
    dead to the reaper while it was perfectly healthy -- and some stages never
    call those hooks at all (see the note in ``_make_checkpointed_node``).

    A ``token`` of ``None`` disables the heartbeat and yields ``None``: legacy
    rows keep working exactly as before.
    """
    if not token or not pipeline_run_id:
        yield None
        return

    lost = on_lost or asyncio.Event()
    stop = asyncio.Event()

    async def _beat() -> None:
        # Timed from the last SEND, so the local deadline is never later than
        # the database's own.
        deadline = asyncio.get_running_loop().time() + LEASE_SECONDS
        while not stop.is_set():
            wait = min(heartbeat_seconds, max(0.0, deadline - asyncio.get_running_loop().time()))
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=wait)
            if stop.is_set():
                return
            now = asyncio.get_running_loop().time()
            if now >= deadline:
                # A starved event loop woke us past the deadline. Do not renew:
                # the lease has lapsed and someone else may hold the row.
                logger.warning(
                    "pipeline_lease_deadline_passed_before_renew",
                    pipeline_run_id=str(pipeline_run_id),
                )
                lost.set()
                return
            budget = max(1.0, (deadline - now) - RENEW_MARGIN_SECONDS)
            sent_at = asyncio.get_running_loop().time()
            try:
                from app.db.postgres import AsyncSessionLocal  # noqa: PLC0415

                async with AsyncSessionLocal() as db:
                    ok = await asyncio.wait_for(
                        renew_lease(db, pipeline_run_id, token), timeout=budget
                    )
                    await db.commit()
            except asyncio.TimeoutError:
                # The renew stalled past its budget. Abandon it and stop the
                # holder rather than waiting for a call that may never return.
                logger.warning(
                    "pipeline_lease_renew_timed_out",
                    pipeline_run_id=str(pipeline_run_id),
                )
                lost.set()
                return
            except Exception as exc:  # noqa: BLE001
                # A transient database error is survivable while the deadline
                # has room; the next iteration retries.
                logger.warning(
                    "pipeline_lease_renew_failed",
                    pipeline_run_id=str(pipeline_run_id),
                    error_type=type(exc).__name__,
                )
                continue
            if not ok:
                logger.warning(
                    "pipeline_lease_reclaimed_by_another_worker",
                    pipeline_run_id=str(pipeline_run_id),
                )
                lost.set()
                return
            deadline = sent_at + LEASE_SECONDS

    beat = asyncio.create_task(_beat(), name=f"pipeline-lease-{pipeline_run_id}")
    try:
        yield lost
    finally:
        stop.set()
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await beat
