"""E7.2: a failed pipeline attempt is parked in retry_wait and resumed under its
own id; the attempt ceiling parks it in the DLQ instead; a stale scheduled
resume does nothing; a trigger while a run is in progress returns that run.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.enums import PipelineRunStatus


def _row(status="failed", attempt=1, max_attempts=5):
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status,
        attempt=attempt,
        max_attempts=max_attempts,
        next_retry_at=None,
        completed_at=datetime.now(timezone.utc),
        error="boom",
        execution_metadata={},
    )


class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _DB:
    def __init__(self, row):
        self.row = row
        self.committed = False

    async def execute(self, _stmt):
        return _Result(self.row)

    async def commit(self):
        self.committed = True


def _wire(monkeypatch, row):
    from app.worker import tasks

    db = _DB(row)

    @asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _session)
    redis = SimpleNamespace(expire=AsyncMock(return_value=True))
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
    apply_async = MagicMock()
    monkeypatch.setattr(tasks.resume_agent_pipeline, "apply_async", apply_async)
    return db, redis, apply_async


@pytest.mark.asyncio
async def test_first_failure_parks_in_retry_wait_and_schedules_a_same_id_resume(monkeypatch):
    from app.worker import tasks

    row = _row(status="failed", attempt=1, max_attempts=5)
    db, redis, apply_async = _wire(monkeypatch, row)

    out = await tasks._schedule_pipeline_retry(
        test_run_id=str(uuid.uuid4()), workflow_type="offline", build_number="b1",
        pipeline_run_id=str(row.id), dedup_key="k", dedup_owner="o", error="RuntimeError: x",
    )

    assert out is not None
    assert row.status == PipelineRunStatus.RETRY_WAIT.value
    assert row.next_retry_at is not None
    assert db.committed is True
    # dedup lock is extended, never released, while the retry is pending
    redis.expire.assert_awaited_once()
    key, ttl = redis.expire.await_args.args
    assert key == "k" and ttl >= out["delay_seconds"] + 300
    # the resume carries the attempt it is meant for
    kwargs = apply_async.call_args.kwargs
    assert kwargs["kwargs"] == {"pipeline_run_id": str(row.id), "build_number": "b1", "expected_attempt": 2}
    assert kwargs["countdown"] == out["delay_seconds"]
    lo, hi = 24, 36  # 30s +/- 20%
    assert lo <= out["delay_seconds"] <= hi
    assert out["next_attempt"] == 2 and out["max_attempts"] == 5


@pytest.mark.asyncio
async def test_delay_grows_with_the_attempt_number(monkeypatch):
    from app.worker import tasks

    row = _row(status="failed", attempt=3, max_attempts=5)
    _wire(monkeypatch, row)
    out = await tasks._schedule_pipeline_retry(
        test_run_id="r", workflow_type="offline", build_number="b",
        pipeline_run_id=str(row.id), dedup_key="k", dedup_owner="o", error="e",
    )
    assert 96 <= out["delay_seconds"] <= 144  # 120s +/- 20%


@pytest.mark.asyncio
async def test_attempt_ceiling_returns_none_and_leaves_the_row_failed(monkeypatch):
    from app.worker import tasks

    row = _row(status="failed", attempt=5, max_attempts=5)
    db, redis, apply_async = _wire(monkeypatch, row)
    out = await tasks._schedule_pipeline_retry(
        test_run_id="r", workflow_type="offline", build_number="b",
        pipeline_run_id=str(row.id), dedup_key="k", dedup_owner="o", error="e",
    )
    assert out is None
    assert row.status == "failed"
    assert db.committed is False
    redis.expire.assert_not_awaited()
    apply_async.assert_not_called()


@pytest.mark.asyncio
async def test_a_row_that_is_not_failed_is_not_rescheduled(monkeypatch):
    from app.worker import tasks

    for status in ("running", "completed", "passed", "retry_wait"):
        row = _row(status=status, attempt=1)
        _, _, apply_async = _wire(monkeypatch, row)
        out = await tasks._schedule_pipeline_retry(
            test_run_id="r", workflow_type="offline", build_number="b",
            pipeline_run_id=str(row.id), dedup_key="k", dedup_owner="o", error="e",
        )
        assert out is None, status
        apply_async.assert_not_called()


@pytest.mark.asyncio
async def test_missing_row_returns_none(monkeypatch):
    from app.worker import tasks

    _wire(monkeypatch, None)
    out = await tasks._schedule_pipeline_retry(
        test_run_id=str(uuid.uuid4()), workflow_type="offline", build_number="b",
        pipeline_run_id=None, dedup_key="k", dedup_owner="o", error="e",
    )
    assert out is None


def test_pipeline_task_no_longer_uses_celery_retries():
    """The task decorator has max_retries=0 and the body never calls self.retry:
    retries are the state machine's, on the row, where a user can see them."""
    import inspect

    from app.worker import tasks

    src = inspect.getsource(tasks.run_agent_pipeline.run) if hasattr(tasks.run_agent_pipeline, "run") else ""
    text = open(tasks.__file__, encoding="utf-8").read()
    start = text.index("def run_agent_pipeline(")
    end = text.index("async def _schedule_pipeline_retry(")
    body = text[start:end]
    assert "self.retry(" not in body
    assert "_schedule_pipeline_retry(" in body
    assert "_release_duplicate_lock(dedup_key, dedup_owner)" in body  # exhausted path still releases
    assert body.count("requested_by=requested_by,") == 2
    assert tasks.run_agent_pipeline.max_retries == 0
    del src


def test_failure_exceptions_carry_the_pipeline_id():
    from app.agents import workflow

    exc = RuntimeError("x")
    workflow._tag_failure_with_pipeline(exc, "abc")
    assert exc.pipeline_run_id == "abc"
    text = open(workflow.__file__, encoding="utf-8").read()
    marks = text.count("success=False,")
    tags = text.count("_tag_failure_with_pipeline(exc, pipeline_run_id)")
    assert marks >= 2 and tags >= 2, (marks, tags)
    assert "fencing_token=initial_state.get(\"_fencing_token\")" in text
    assert "fencing_token=pipeline_setup.get(\"fencing_token\")" in text


# ── stale scheduled resume ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_refuses_a_stale_expected_attempt(monkeypatch):
    from app.agents import workflow

    pipeline = _row(status="retry_wait", attempt=2)
    db = _DB(pipeline)

    class _Locked:
        def __init__(self, r):
            self.r = r

        def scalar_one_or_none(self):
            return self.r

    async def _execute(stmt):
        return _Locked(pipeline)

    db.execute = _execute  # type: ignore[assignment]

    @asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr(workflow, "AsyncSessionLocal", _session)
    # the retry was queued for attempt 3; the row is already at attempt 2 -> expects 3 -> ok path
    # would continue into ownership lookups; a stale one (queued for attempt 2) must stop first.
    assert await workflow._claim_pipeline_resume(str(pipeline.id), expected_attempt=2) is None
    assert pipeline.status == "retry_wait", "a refused claim must not touch the row"


# ── trigger short-circuit ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_returns_the_in_progress_run_instead_of_queueing(monkeypatch):
    from app.routers import agents as router

    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), build_number="7")
    existing = _row(status="retry_wait", attempt=2)

    class _RunResult:
        def scalar_one_or_none(self):
            return run

    class _ExistingResult:
        def scalar_one_or_none(self):
            return existing

    calls = []

    async def _execute(stmt):
        calls.append(stmt)
        return _RunResult() if len(calls) == 1 else _ExistingResult()

    db = SimpleNamespace(execute=_execute)

    async def _scope(*a, **k):
        return None

    monkeypatch.setattr(router, "resolve_project_scope", _scope)
    delay = MagicMock()
    monkeypatch.setattr("app.worker.tasks.run_agent_pipeline.delay", delay)

    payload = SimpleNamespace(test_run_id=run.id)
    resp = await router.trigger_pipeline(
        payload, db=db, current_user=SimpleNamespace(id=uuid.uuid4()), _=None
    )
    assert resp.status_code == 200
    import json

    body = json.loads(resp.body)
    assert body["pipeline_run_id"] == str(existing.id)
    assert body["public_status"] == "in_progress"
    assert body["attempt"] == 2
    delay.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_queues_when_nothing_is_in_progress(monkeypatch):
    from app.routers import agents as router

    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), build_number="7")

    class _RunResult:
        def scalar_one_or_none(self):
            return run

    class _NoneResult:
        def scalar_one_or_none(self):
            return None

    calls = []

    async def _execute(stmt):
        calls.append(stmt)
        return _RunResult() if len(calls) == 1 else _NoneResult()

    db = SimpleNamespace(execute=_execute)

    async def _scope(*a, **k):
        return None

    monkeypatch.setattr(router, "resolve_project_scope", _scope)
    delay = MagicMock(return_value=SimpleNamespace(id="task-1"))
    monkeypatch.setattr("app.worker.tasks.run_agent_pipeline.delay", delay)

    requester = uuid.uuid4()
    resp = await router.trigger_pipeline(
        SimpleNamespace(test_run_id=run.id),
        db=db,
        current_user=SimpleNamespace(id=requester),
        _=None,
    )
    assert resp == {
        "message": "Pipeline queued",
        "task_id": "task-1",
        "run_id": str(run.id),
        "workflow_ref": "offline@1",
    }
    delay.assert_called_once()
    assert delay.call_args.kwargs["requested_by"] == str(requester)
