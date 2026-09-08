"""Regression guard: ``persist_live_session`` must invoke ``finalize_run``.

The live-stream path historically committed TestCase rows and returned,
skipping the post-ingestion pipeline (test_suites, canonical_test_cases,
suite_memberships, auto-tagging, release link, AI pipeline trigger).
That meant ``/suites`` was empty even when test_cases had data — every
live run was missing its suite/canonical wiring.

If this test fails because someone removed the ``finalize_run`` call
from ``persist_live_session``, restore it before merging. The user-
visible symptom is "/suites and /search show no data from SDK runs".
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Lightweight fakes ────────────────────────────────────────────────────────


class _FakeExecResult:
    """Minimal stand-in for SQLAlchemy execute results.

    The persister calls .scalar() (for the dedup count) and
    .scalar_one_or_none() (for the existing TestRun lookup). One class
    covers both since each is single-use per call.
    """

    def __init__(self, *, scalar=None):
        self._scalar = scalar

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar


class _FakeSession:
    """Async-context manager session: returns canned execute results in order."""

    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, _stmt):
        if not self._results:
            return _FakeExecResult(scalar=None)
        return self._results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        # Synthesise a DB id for newly-added rows so downstream FKs are valid.
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                try:
                    obj.id = uuid.uuid4()
                except Exception:
                    pass

    async def commit(self):
        self.committed = True


class _FakeRedis:
    """Async redis stub: empty list buffer, swallow delete."""

    def __init__(self):
        self.deleted = []

    async def lrange(self, _key, _start, _end):
        return []

    async def delete(self, key):
        self.deleted.append(key)


# ── The actual regression test ───────────────────────────────────────────────


def test_persist_live_session_invokes_finalize_run():
    """After committing TestCase rows, persist_live_session must call
    finalize_run with the same run_id/project_id so the post-ingestion
    pipeline (suite_sync, canonical_sync, auto-tagging) runs.

    The test is synchronous: the task internally uses ``_run_async`` which
    spins its own event loop. Running this test under ``@pytest.mark.asyncio``
    nests loops and fails with "Cannot run the event loop while another
    loop is running".
    """
    pytest.importorskip("celery")
    pytest.importorskip("sqlalchemy")

    from app.worker import tasks as worker_tasks

    run_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    build_number = "build-test-1"

    # dedup count = 0 (proceed); existing TestRun lookup = None (create new)
    fake_session = _FakeSession(results=[
        _FakeExecResult(scalar=0),
        _FakeExecResult(scalar=None),
    ])
    fake_redis = _FakeRedis()
    finalize_calls = []
    redis_close_finalize = AsyncMock()

    async def _capturing_finalize(**kwargs):
        finalize_calls.append(kwargs)

    # The task's inner ``_run`` uses deferred imports — patch the module
    # attributes at their canonical locations so the inside-function
    # ``from X import Y`` resolves to our doubles.
    with (
        patch(
            "app.db.postgres.AsyncSessionLocal",
            return_value=fake_session,
        ),
        patch(
            "app.db.redis_client.get_redis",
            return_value=fake_redis,
        ),
        patch(
            "app.services.ingestion_pipeline.finalize_run",
            new=AsyncMock(side_effect=_capturing_finalize),
        ),
        patch(
            "app.services.stream_service.finalize_closed_session_redis",
            new=redis_close_finalize,
        ),
        patch.object(
            worker_tasks,
            "_drain_live_evidence_before_finalize",
            new=AsyncMock(return_value=0),
        ),
    ):
        # Invoke the task synchronously in-process. The bound task wrapper
        # handles ``self``; we provide an empty final_state so the buffer-
        # empty branch runs cleanly without inventing per-test counts.
        result = worker_tasks.persist_live_session.apply(kwargs={
            "run_id": run_id,
            "project_id": project_id,
            "build_number": build_number,
            "final_state": {},
        })

    # Surface task failures with their traceback instead of a bare
    # boolean — Celery's .apply() swallows them otherwise.
    if not result.successful():
        raise AssertionError(f"persist_live_session task failed: {result.traceback}")

    assert fake_session.committed, "Expected TestCase rows to be committed"
    redis_close_finalize.assert_awaited_once_with(run_id)
    assert len(finalize_calls) == 1, (
        f"Expected finalize_run to be called exactly once; got {len(finalize_calls)}. "
        "If this assertion failed, /suites will be empty for all live-stream runs — "
        "see the docstring at the top of this file for the regression history."
    )
    call = finalize_calls[0]
    assert call["run_id"]  # canonical UUID derived from the slug
    assert call["project_id"] == project_id
    assert call["build_number"] == build_number


def test_persist_live_session_resumes_finalize_when_already_persisted():
    """When test_cases already exist for the canonical run uuid, the
    persister resumes finalization. A prior attempt may have committed rows and
    crashed before finalization, so returning here would permanently lose the
    outbox-backed downstream work."""
    pytest.importorskip("celery")

    from app.worker import tasks as worker_tasks

    run_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    existing_run = SimpleNamespace(
        end_time=None,
        primary_suite_name=None,
        suite_names=None,
    )
    fake_session = _FakeSession(results=[
        _FakeExecResult(scalar=5),
        _FakeExecResult(scalar=existing_run),
    ])
    fake_redis = _FakeRedis()

    finalize_mock = AsyncMock()
    redis_close_finalize = AsyncMock()

    with (
        patch(
            "app.db.postgres.AsyncSessionLocal",
            return_value=fake_session,
        ),
        patch(
            "app.db.redis_client.get_redis",
            return_value=fake_redis,
        ),
        patch(
            "app.services.ingestion_pipeline.finalize_run",
            new=finalize_mock,
        ),
        patch(
            "app.services.stream_service.finalize_closed_session_redis",
            new=redis_close_finalize,
        ),
        patch.object(
            worker_tasks,
            "_drain_live_evidence_before_finalize",
            new=AsyncMock(return_value=5),
        ),
    ):
        result = worker_tasks.persist_live_session.apply(kwargs={
            "run_id": run_id,
            "project_id": project_id,
            "build_number": "b",
            "final_state": {"total": 5, "passed": 5},
        })

    if not result.successful():
        raise AssertionError(f"persist_live_session task failed: {result.traceback}")

    finalize_mock.assert_awaited_once_with(
        run_id=run_id,
        project_id=project_id,
        build_number="b",
    )
    redis_close_finalize.assert_awaited_once_with(run_id)
    assert fake_session.committed
    assert existing_run.total_tests == 5
    assert str(existing_run.status) in {"LaunchStatus.PASSED", "PASSED"}


def test_persist_live_session_retry_repairs_failed_postcommit_redis_finalize():
    """The committed outbox worker retries the post-commit Redis transition.

    A lost Redis connection after the API commit must leave the gate durable
    and TTL-less until this worker can idempotently mark it closed.
    """
    pytest.importorskip("celery")
    from celery.exceptions import Retry

    from app.worker import tasks as worker_tasks

    run_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    sessions: list[_FakeSession] = []

    def _session_factory():
        session = _FakeSession(results=[
            _FakeExecResult(scalar=0),
            _FakeExecResult(scalar=None),
        ])
        sessions.append(session)
        return session

    finalize_attempts: list[str] = []
    retry_requested = MagicMock(side_effect=Retry("retry requested"))

    async def _flaky_redis_finalize(candidate_run_id: str):
        assert sessions[-1].committed
        finalize_attempts.append(candidate_run_id)
        if len(finalize_attempts) == 1:
            raise RuntimeError("redis unavailable after PostgreSQL commit")

    with (
        patch("app.db.postgres.AsyncSessionLocal", side_effect=_session_factory),
        patch(
            "app.services.stream_service.finalize_closed_session_redis",
            new=AsyncMock(side_effect=_flaky_redis_finalize),
        ),
        patch(
            "app.services.ingestion_pipeline.finalize_run",
            new=AsyncMock(),
        ),
        patch.object(
            worker_tasks,
            "_drain_live_evidence_before_finalize",
            new=AsyncMock(return_value=0),
        ),
        patch.object(worker_tasks.persist_live_session, "retry", retry_requested),
    ):
        kwargs = {
            "run_id": run_id,
            "project_id": project_id,
            "build_number": "postcommit-repair",
            "final_state": {},
        }
        # Invoke the task body directly and replace Celery's version-dependent
        # eager retry runner with the Retry control-flow signal a worker uses.
        with pytest.raises(Retry):
            worker_tasks.persist_live_session.run(**kwargs)
        worker_tasks.persist_live_session.run(**kwargs)

    retry_requested.assert_called_once()
    assert finalize_attempts == [run_id, run_id]
    assert len(sessions) == 2
    assert all(session.committed for session in sessions)
