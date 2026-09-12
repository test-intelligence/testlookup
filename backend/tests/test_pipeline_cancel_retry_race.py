"""E7.4: cancel racing a retryable failure always lands ``failed``.

The two writers are the operator's cancel and the worker's failure path. If the
failure path wins unconditionally, a cancelled run is parked in ``retry_wait``
and comes back to life minutes later -- after the API told the operator it had
stopped. These pin that neither ordering can produce that.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _row(status="failed", *, attempt=1, cancel_requested=False):
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status,
        attempt=attempt,
        max_attempts=5,
        next_retry_at=None,
        completed_at=datetime.now(timezone.utc),
        error="model_unavailable",
        execution_metadata={},
        cancel_requested=cancel_requested,
        lease_owner="host:1",
        fencing_token="tok",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )


class _DB:
    def __init__(self, row):
        self.row = row
        self.committed = False

    async def execute(self, stmt):
        if len(list(getattr(stmt, "selected_columns", []))) == 1:
            return SimpleNamespace(
                scalar_one_or_none=lambda: getattr(self.row, "cancel_requested", False)
            )
        return SimpleNamespace(scalar_one_or_none=lambda: self.row)

    async def commit(self):
        self.committed = True


def _wire(monkeypatch, row):
    from app.worker import tasks

    db = _DB(row)

    @asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _session)
    monkeypatch.setattr(
        "app.db.redis_client.get_redis",
        lambda: SimpleNamespace(expire=AsyncMock(return_value=True)),
    )
    apply_async = MagicMock()
    monkeypatch.setattr(tasks.resume_agent_pipeline, "apply_async", apply_async)
    return db, apply_async


async def _schedule(monkeypatch, row):
    from app.worker import tasks

    db, apply_async = _wire(monkeypatch, row)
    scheduled = await tasks._schedule_pipeline_retry(
        test_run_id=str(uuid.uuid4()),
        workflow_type="offline",
        build_number="b1",
        pipeline_run_id=str(row.id),
        dedup_key="k",
        dedup_owner="o",
        error="model_unavailable",
    )
    return scheduled, db, apply_async


# ── ordering 1: cancel commits first ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_first_means_the_failure_path_schedules_nothing(monkeypatch):
    row = _row(cancel_requested=True, attempt=1)
    scheduled, _db, apply_async = await _schedule(monkeypatch, row)

    assert scheduled is None, "a cancelled run must never be parked in retry_wait"
    assert row.status == "failed"
    apply_async.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_first_also_clears_any_wake_up_time(monkeypatch):
    row = _row(cancel_requested=True)
    row.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    await _schedule(monkeypatch, row)
    assert row.next_retry_at is None


@pytest.mark.asyncio
async def test_the_suppression_is_committed_not_just_in_memory(monkeypatch):
    row = _row(cancel_requested=True)
    _scheduled, db, _apply = await _schedule(monkeypatch, row)
    assert db.committed is True


# ── ordering 2: the failure path commits first ───────────────────────────────


@pytest.mark.asyncio
async def test_retry_first_then_cancel_still_ends_failed(monkeypatch):
    """The scheduler parks the row, then the cancel arrives. ``retry_wait ->
    failed`` is a legal edge, so cancel terminalises it."""
    from app.services import pipeline_cancellation as pc

    row = _row(cancel_requested=False, attempt=1)
    scheduled, _db, apply_async = await _schedule(monkeypatch, row)
    assert scheduled is not None and row.status == "retry_wait"
    apply_async.assert_called_once()

    outcome = await pc.request_cancel(_DB(row), row.id, requested_by="qa@example.com")
    assert outcome.accepted and outcome.terminal
    assert row.status == "failed"
    assert row.next_retry_at is None


@pytest.mark.asyncio
async def test_the_queued_resume_cannot_resurrect_the_cancelled_run(monkeypatch):
    """The scheduled task still fires. ``_claim_pipeline_resume`` must refuse
    it -- otherwise the cancellation is undone by a timer."""
    from app.agents import workflow

    row = _row(status="failed", cancel_requested=True)
    row.execution_metadata = {
        "initial_workflow_plan": {"stages": ["ingestion"]},
        "analysis_mode_resolution": {"resolved": "rules"},
    }
    db = _DB(row)

    @asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr(workflow, "AsyncSessionLocal", _session)
    claimed = await workflow._claim_pipeline_resume(str(row.id))
    assert claimed is None, "a cancelled run is not resumable by any path"


@pytest.mark.asyncio
async def test_an_uncancelled_failed_run_still_retries_normally(monkeypatch):
    """The control: the cancel check must not have broken ordinary retries."""
    row = _row(cancel_requested=False, attempt=2)
    scheduled, _db, apply_async = await _schedule(monkeypatch, row)
    assert scheduled is not None
    assert row.status == "retry_wait"
    assert apply_async.call_args.kwargs["kwargs"]["expected_attempt"] == 3
