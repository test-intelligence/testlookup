"""E7.3: the reaper reclaims by lease expiry, rotates the token, and reschedules.

The old reaper declared a run dead at a fixed 30-minute age. These tests pin
the replacement: lease expiry is the signal, the fencing token is rotated so a
still-alive holder is locked out, and the run is rescheduled under its own id
unless the attempt ceiling is reached.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _row(**kw):
    now = datetime.now(timezone.utc)
    base = dict(
        id=uuid.uuid4(),
        status="running",
        attempt=1,
        max_attempts=5,
        next_retry_at=None,
        started_at=now - timedelta(minutes=2),
        completed_at=None,
        error=None,
        execution_metadata={},
        lease_owner="host:1",
        fencing_token="old-token",
        lease_expires_at=now - timedelta(seconds=1),  # lapsed
        heartbeat_at=now - timedelta(seconds=90),
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)


class _DB:
    def __init__(self, rows):
        self.rows = rows
        self.committed = False

    async def execute(self, _stmt):
        return _Result(self.rows)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        pass


def _wire(monkeypatch, rows):
    from app.worker import tasks

    db = _DB(rows)

    @asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _session)

    async def _no_investigations():
        return {"checked": 0, "reaped": 0}

    monkeypatch.setattr(
        "app.agents.investigator.workflow.reap_stale_investigations", _no_investigations
    )
    apply_async = MagicMock()
    monkeypatch.setattr(tasks.resume_agent_pipeline, "apply_async", apply_async)
    return db, apply_async


def _run(monkeypatch, rows):
    from app.worker import tasks

    db, apply_async = _wire(monkeypatch, rows)
    result = tasks.reap_stuck_agent_pipelines.run(stale_minutes=30)
    return result, db, apply_async


def test_a_lapsed_lease_is_rescheduled_under_the_same_id(monkeypatch):
    row = _row(attempt=1, max_attempts=5)
    result, db, apply_async = _run(monkeypatch, [row])

    assert result["reaped_to_retry"] == 1
    assert result["reaped_to_failed"] == 0
    assert row.status == "retry_wait"
    assert row.next_retry_at is not None
    assert db.committed is True
    kwargs = apply_async.call_args.kwargs
    assert kwargs["kwargs"]["pipeline_run_id"] == str(row.id)
    assert kwargs["kwargs"]["expected_attempt"] == 2
    assert kwargs["countdown"] >= 1


def test_the_fencing_token_is_rotated_so_the_old_holder_is_locked_out(monkeypatch):
    row = _row(fencing_token="old-token")
    _run(monkeypatch, [row])
    assert row.fencing_token not in (None, "old-token"), (
        "the reaper must mint a NEW token: a still-alive holder's next write "
        "has to be refused, not land underneath the next attempt"
    )
    assert row.lease_owner is None
    assert row.lease_expires_at is None


def test_at_the_attempt_ceiling_the_run_is_failed_not_rescheduled(monkeypatch):
    row = _row(attempt=5, max_attempts=5)
    result, _db, apply_async = _run(monkeypatch, [row])

    assert result["reaped_to_failed"] == 1
    assert result["reaped_to_retry"] == 0
    assert row.status == "failed"
    assert row.error and "heartbeat" in row.error.lower()
    assert row.completed_at is not None
    apply_async.assert_not_called()


def test_the_error_names_the_lapsed_lease(monkeypatch):
    row = _row(attempt=5, max_attempts=5)
    _run(monkeypatch, [row])
    assert "lease lapsed at" in row.error


def test_a_legacy_row_with_no_lease_reports_the_age_fallback(monkeypatch):
    row = _row(attempt=5, max_attempts=5, lease_expires_at=None)
    _run(monkeypatch, [row])
    assert "no heartbeat within 30m" in row.error


def test_nothing_expired_is_a_clean_no_op(monkeypatch):
    result, db, apply_async = _run(monkeypatch, [])
    assert result["reaped_to_retry"] == 0 and result["reaped_to_failed"] == 0
    assert result["errors"] == 0
    apply_async.assert_not_called()


def test_the_predicate_reads_lease_expiry_not_a_fixed_age():
    """A source assertion: the query must select on lease_expires_at.

    Behavioural coverage above uses a fake session that returns whatever rows
    it is given, so it cannot see the WHERE clause. This is the half that can.
    """
    import inspect

    from app.worker import tasks

    src = inspect.getsource(tasks.reap_stuck_agent_pipelines)
    assert "lease_expires_at < now" in src
    assert "RUNNING_STALE_THRESHOLD" not in src
    # the age fallback survives only for rows that never had a lease
    assert "lease_expires_at.is_(None)" in src


def test_the_router_no_longer_derives_status():
    """The read path must not second-guess the stored status (E7.3)."""
    from app.routers import agents as router

    src = open(router.__file__, encoding="utf-8").read()
    for gone in (
        "RUNNING_STALE_THRESHOLD",
        "def _apply_effective_status",
        "def _is_stale_running",
        "def _failed_stage_pipeline_ids",
    ):
        assert gone not in src, f"{gone} should have been deleted in E7.3"
    assert "_attach_public_status" in src


@pytest.mark.parametrize("status", ["completed", "passed", "failed", "retry_wait", "pending"])
def test_only_running_rows_are_candidates(status):
    """The reaper's predicate pins status='running'; a terminal or waiting row
    has no holder to reclaim from."""
    import inspect

    from app.worker import tasks

    src = inspect.getsource(tasks.reap_stuck_agent_pipelines)
    assert "PipelineRunStatus.RUNNING.value" in src
    assert f'== "{status}"' not in src.split("expired = (")[1].split(")\n")[0]
