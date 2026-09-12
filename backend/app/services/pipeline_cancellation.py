"""Cancelling a pipeline run, and the race it has to win (architecture E7.4).

Why cancel is not just "set status = failed"
--------------------------------------------
A run that is ``pending`` or ``retry_wait`` has no live worker, so cancelling it
is a straight terminal transition. A ``running`` run does: something is
mid-model-call in another process, holding a lease. Writing its row terminal
from the API would leave that worker to finish and write results into a run the
operator believes is dead -- exactly the double-execution problem E7.3 exists to
prevent.

So ``running`` is a *request*: the flag is set, and the worker stops itself at
its next stage boundary (the same shape the Investigator already uses --
``app/agents/investigator/persistence.py::is_cancel_requested``). The lease
guarantees the request is honoured or the run is reaped; it cannot sit forever.

The race that matters
---------------------
Cancel and a retryable failure can happen at the same instant: a stage raises
``model_unavailable`` while the operator clicks cancel. The failure path
(``_schedule_pipeline_retry``) wants ``retry_wait``; the cancel wants terminal.
If the failure path wins, the run comes back to life minutes later under a
cancellation the operator was told had been accepted.

The resolution is that **cancellation is sticky and terminal always wins**:

* the failure path takes the row ``FOR UPDATE`` and re-reads ``cancel_requested``
  *inside* that lock, so it can never schedule a retry for a cancelled run;
* the cancel path takes the same lock, so the two serialise in either order and
  both orderings end ``failed``:
  - cancel first  -> the retry scheduler sees the flag and terminalises instead;
  - retry first   -> the row is ``retry_wait`` when cancel arrives, and
    ``retry_wait -> failed`` is a legal edge, so cancel terminalises it and the
    scheduled resume's ``expected_attempt`` check (E7.2) makes the queued task
    a no-op when it fires.

This mirrors the Investigator's ``_terminal_status``, where an error alongside a
cancellation still reports ``failed``: a run that broke did not stop because it
was asked to.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

import structlog

from app.services.workflow_run_state import (
    CANCELLED_ERROR_PREFIX,
    IllegalTransition,
    PipelineRunStatus,
    TransitionLost,
    apply_transition,
    is_terminal,
    normalize_status,
    public_status,
)

logger = structlog.get_logger("services.pipeline_cancellation")

__all__ = [
    "CancelOutcome",
    "PipelineCancelled",
    "is_cancel_requested",
    "raise_if_cancelled",
    "request_cancel",
    "terminalize_cancelled",
]

_S = PipelineRunStatus
# States with no live worker: cancelling them is immediate and terminal.
_NO_LIVE_WORKER = frozenset({_S.PENDING, _S.RETRY_WAIT})


class PipelineCancelled(RuntimeError):
    """The run was cancelled; the worker must stop without scheduling a retry.

    Deliberately NOT in ``RetryPolicy.DEFAULT_RETRYABLE`` -- its error code is
    ``cancelled``, which ``retry_policy.NON_RETRYABLE`` lists. A cancelled run
    that retried would defeat the cancellation.
    """

    error_code = "cancelled"

    def __init__(self, pipeline_run_id: str):
        self.pipeline_run_id = str(pipeline_run_id)
        # Carries the state machine's ``cancelled: `` prefix so the reason
        # survives into ``agent_pipeline_runs.error`` whichever handler writes it.
        super().__init__(f"{CANCELLED_ERROR_PREFIX}pipeline {pipeline_run_id} stopped by request")


class CancelOutcome:
    """What a cancel request did.

    ``accepted`` distinguishes "stopped it now" (``terminal=True``) from
    "asked the running worker to stop" (``terminal=False``); ``already`` means
    the run had already finished and nothing was changed.
    """

    __slots__ = ("accepted", "terminal", "status", "reason")

    def __init__(self, *, accepted: bool, terminal: bool, status: str, reason: str) -> None:
        self.accepted = accepted
        self.terminal = terminal
        self.status = status
        self.reason = reason

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "terminal": self.terminal,
            "status": self.status,
            "public_status": public_status(self.status),
            "reason": self.reason,
        }


async def is_cancel_requested(db: Any, pipeline_run_id: str | uuid.UUID) -> bool:
    """True when someone asked for this run to stop."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.postgres import AgentPipelineRun  # noqa: PLC0415

    flag = (
        await db.execute(
            select(AgentPipelineRun.cancel_requested).where(
                AgentPipelineRun.id == _as_uuid(pipeline_run_id)
            )
        )
    ).scalar_one_or_none()
    return bool(flag)


async def raise_if_cancelled(db: Any, pipeline_run_id: str | uuid.UUID) -> None:
    """Stop the worker at a stage boundary if the run was cancelled.

    Called from the checkpointed-node wrapper, alongside the fencing check, so
    a cancellation costs at most one stage rather than the rest of the run.
    """
    if await is_cancel_requested(db, pipeline_run_id):
        raise PipelineCancelled(str(pipeline_run_id))


async def request_cancel(
    db: Any,
    pipeline_run_id: str | uuid.UUID,
    *,
    requested_by: Optional[str] = None,
) -> CancelOutcome:
    """Ask a run to stop, terminalising it immediately when nothing is running.

    The caller owns the commit. The row is taken ``FOR UPDATE`` so this
    serialises against ``_schedule_pipeline_retry``; see the module docstring
    for why both orderings end ``failed``.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.postgres import AgentPipelineRun  # noqa: PLC0415

    run_id = _as_uuid(pipeline_run_id)
    row = (
        await db.execute(
            select(AgentPipelineRun).where(AgentPipelineRun.id == run_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise LookupError(f"agent_pipeline_runs {run_id}: not found")

    current = normalize_status(row.status)
    if is_terminal(current):
        return CancelOutcome(
            accepted=False,
            terminal=True,
            status=current.value,
            reason="already_terminal",
        )

    # Sticky from here: every later reader (the retry scheduler, the stage
    # wrapper, a stale resume) sees the flag whichever way the race went.
    row.cancel_requested = True

    if current not in _NO_LIVE_WORKER:
        # ``running``: a worker holds the lease. Let it stop itself so its
        # in-flight writes land before the row goes terminal.
        logger.info(
            "pipeline_cancel_requested",
            pipeline_run_id=str(run_id),
            status=current.value,
            requested_by=requested_by or "unknown",
        )
        return CancelOutcome(
            accepted=True,
            terminal=False,
            status=current.value,
            reason="signalled_running_worker",
        )

    reason = f"by {requested_by}" if requested_by else "by request"
    try:
        apply_transition(row, "cancelled", error=reason)
    except IllegalTransition:  # pragma: no cover -- every _NO_LIVE_WORKER edge is legal
        raise
    # Clear the lease so nothing can be fenced back in, and drop the schedule so
    # the parked retry has no wake-up time to honour.
    row.next_retry_at = None
    row.lease_owner = None
    row.lease_expires_at = None
    logger.info(
        "pipeline_cancelled",
        pipeline_run_id=str(run_id),
        from_status=current.value,
        requested_by=requested_by or "unknown",
    )
    return CancelOutcome(
        accepted=True,
        terminal=True,
        status=_S.FAILED.value,
        reason="cancelled_before_start" if current is _S.PENDING else "cancelled_while_waiting",
    )


async def terminalize_cancelled(
    db: Any,
    pipeline_run_id: str | uuid.UUID,
    *,
    error: Optional[str] = None,
) -> bool:
    """Move a cancelled run to ``failed`` from wherever it is. False if it moved.

    Used by the worker's error path when a stage raised
    :class:`PipelineCancelled`, and by the retry scheduler when it finds the
    flag set on a row it was about to park in ``retry_wait``.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.postgres import AgentPipelineRun  # noqa: PLC0415

    run_id = _as_uuid(pipeline_run_id)
    row = (
        await db.execute(
            select(AgentPipelineRun).where(AgentPipelineRun.id == run_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    if is_terminal(row.status):
        return False
    try:
        apply_transition(
            row,
            PipelineRunStatus.FAILED,
            error=f"{CANCELLED_ERROR_PREFIX}{error or 'by request'}",
        )
    except (IllegalTransition, TransitionLost):
        return False
    row.next_retry_at = None
    row.lease_owner = None
    row.lease_expires_at = None
    return True


def _as_uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
