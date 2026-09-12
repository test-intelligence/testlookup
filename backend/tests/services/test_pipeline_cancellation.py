"""E7.4: cancelling a run, and the race against a retryable failure.

The requirement (architecture §7, E7.4) is that cancel racing a retryable
failure ALWAYS lands ``failed`` -- never ``retry_wait``, from which the run
would come back to life minutes after the operator was told it had stopped.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import pipeline_cancellation as pc
from app.services.workflow_run_state import CANCELLED_ERROR_PREFIX


def _row(status="running", **kw):
    base = dict(
        id=uuid.uuid4(),
        status=status,
        attempt=1,
        max_attempts=5,
        next_retry_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        completed_at=None,
        error=None,
        cancel_requested=False,
        execution_metadata={},
        lease_owner="host:1",
        fencing_token="tok",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _DB:
    """Serves one row for a SELECT, and a scalar for the cancel-flag read."""

    def __init__(self, row):
        self.row = row
        self.committed = False

    async def execute(self, stmt):
        # A one-column SELECT is the cancel-flag read; anything wider is the
        # whole-entity load. Matching on the text would misfire, since the full
        # row's column list also contains ``cancel_requested``.
        if len(list(getattr(stmt, "selected_columns", []))) == 1:
            return SimpleNamespace(
                scalar_one_or_none=lambda: getattr(self.row, "cancel_requested", False)
            )
        return SimpleNamespace(scalar_one_or_none=lambda: self.row)

    async def commit(self):
        self.committed = True


# ── request_cancel ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_pending_run_is_cancelled_outright():
    row = _row(status="pending")
    outcome = await pc.request_cancel(_DB(row), row.id, requested_by="qa@example.com")

    assert outcome.accepted and outcome.terminal
    assert row.status == "failed"
    assert row.cancel_requested is True
    assert row.error.startswith(CANCELLED_ERROR_PREFIX)
    assert "qa@example.com" in row.error
    assert row.completed_at is not None


@pytest.mark.asyncio
async def test_a_retry_wait_run_is_cancelled_and_its_schedule_dropped():
    row = _row(status="retry_wait")
    outcome = await pc.request_cancel(_DB(row), row.id)

    assert outcome.terminal
    assert row.status == "failed"
    assert row.next_retry_at is None, (
        "a cancelled run must not keep a wake-up time; the queued resume has to "
        "find nothing to do when it fires"
    )
    assert row.lease_owner is None and row.lease_expires_at is None


@pytest.mark.asyncio
async def test_a_running_run_is_asked_to_stop_not_terminalised():
    """Terminalising it from the API would orphan a worker that is still
    mid-model-call, holding the lease, and about to write results."""
    row = _row(status="running")
    outcome = await pc.request_cancel(_DB(row), row.id)

    assert outcome.accepted is True
    assert outcome.terminal is False
    assert row.cancel_requested is True
    assert row.status == "running", "the worker terminalises itself at its next stage"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "passed", "failed"])
async def test_an_already_finished_run_is_not_accepted(status):
    row = _row(status=status)
    outcome = await pc.request_cancel(_DB(row), row.id)
    assert outcome.accepted is False
    assert outcome.reason == "already_terminal"
    assert row.cancel_requested is False, "a finished run must not be marked cancelled"


@pytest.mark.asyncio
async def test_a_missing_row_raises_lookup_error():
    with pytest.raises(LookupError):
        await pc.request_cancel(_DB(None), uuid.uuid4())


@pytest.mark.asyncio
async def test_the_outcome_reports_the_public_projection():
    row = _row(status="pending")
    payload = (await pc.request_cancel(_DB(row), row.id)).as_dict()
    assert payload["public_status"] == "failed"


# ── the flag, as the worker sees it ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_cancel_requested_reads_the_flag():
    assert await pc.is_cancel_requested(_DB(_row(cancel_requested=True)), uuid.uuid4()) is True
    assert await pc.is_cancel_requested(_DB(_row(cancel_requested=False)), uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_raise_if_cancelled_stops_the_stage():
    run_id = uuid.uuid4()
    with pytest.raises(pc.PipelineCancelled) as exc:
        await pc.raise_if_cancelled(_DB(_row(cancel_requested=True)), run_id)
    assert exc.value.pipeline_run_id == str(run_id)


@pytest.mark.asyncio
async def test_raise_if_cancelled_is_silent_when_not_cancelled():
    await pc.raise_if_cancelled(_DB(_row(cancel_requested=False)), uuid.uuid4())


def test_cancelled_is_not_a_retryable_error_code():
    """If it were, the retry machinery would undo every cancellation."""
    from app.services.retry_policy import NON_RETRYABLE, RetryPolicy

    assert pc.PipelineCancelled.error_code in NON_RETRYABLE
    assert RetryPolicy().is_retryable(pc.PipelineCancelled.error_code) is False


def test_the_exception_message_carries_the_state_machine_prefix():
    assert str(pc.PipelineCancelled("abc")).startswith(CANCELLED_ERROR_PREFIX)


# ── terminalize_cancelled ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_terminalize_moves_a_running_row_to_failed():
    row = _row(status="running")
    assert await pc.terminalize_cancelled(_DB(row), row.id, error="stopped mid-stage") is True
    assert row.status == "failed"
    assert row.error.startswith(CANCELLED_ERROR_PREFIX)
    assert row.next_retry_at is None


@pytest.mark.asyncio
async def test_terminalize_is_a_no_op_on_an_already_terminal_row():
    row = _row(status="completed")
    assert await pc.terminalize_cancelled(_DB(row), row.id) is False
    assert row.status == "completed"


@pytest.mark.asyncio
async def test_terminalize_handles_a_vanished_row():
    assert await pc.terminalize_cancelled(_DB(None), uuid.uuid4()) is False
