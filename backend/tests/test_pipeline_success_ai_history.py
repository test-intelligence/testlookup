"""W03 G-B: the pipeline success hook (contract §5.7) and the one-shot backfill task (§5.6).

The hook keeps every finished pipeline's analysis as a version and re-enqueues
the AI compare report against the D42 previous run -- exactly once per NEW
digest version: not for a replay of the same pipeline, not without a primary
suite or a previous run, and it never fails the pipeline that already
succeeded. The backfill walks every run in chunks behind a Redis cursor,
resumes from it, stops on its budgets, and is a single Redis read once done
(its real-database run, idempotency and the "already versioned" skip included,
is ``tests/integration/test_ai_history_routes_postgres.py``).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import ai_history_read_service as reads
from app.services import ai_history_service as ahs

RUN = str(uuid.UUID("33333333-3333-4333-8333-333333333333"))
PROJECT = str(uuid.UUID("11111111-1111-4111-8111-111111111111"))
PIPELINE = str(uuid.UUID("66666666-6666-4666-8666-666666666666"))
BASELINE = uuid.UUID("55555555-5555-4555-8555-555555555555")


def _recorded(created=True, version=2):
    return ahs.RecordedVersion(version_id=str(uuid.uuid4()), version=version, created=created, doc={})


def _capture(version):
    return ahs.CaptureResult(version=version, previous_verdict=None, verdict_changed=False,
                             skipped_reason=None if version is None or version.created else "replay")


class _Factory:
    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def hook(monkeypatch):
    import app.db.mongo as app_mongo
    import app.db.postgres as pg
    from app.worker import tasks

    journal: list = []
    run = SimpleNamespace(id=uuid.UUID(RUN), primary_suite_name="checkout", project_id=uuid.UUID(PROJECT))
    session = MagicMock()
    session.commit = AsyncMock(side_effect=lambda: journal.append("commit"))
    session.get = AsyncMock(side_effect=lambda *a: journal.append("get") or run)
    monkeypatch.setattr(pg, "AsyncSessionLocal", _Factory(session), raising=False)
    mongo = object()
    monkeypatch.setattr(app_mongo, "get_mongo_db", lambda: mongo)
    capture = AsyncMock(side_effect=lambda *a, **k: journal.append("capture") or _capture(_recorded()))
    monkeypatch.setattr(ahs, "capture_analysis_digest", capture)
    previous = AsyncMock(return_value=reads.PreviousRun(SimpleNamespace(id=BASELINE), None, None, "checkout"))
    monkeypatch.setattr(reads, "resolve_previous_run", previous)
    delay = MagicMock(side_effect=lambda **k: journal.append("enqueue"))
    monkeypatch.setattr(tasks.generate_run_compare_report, "delay", delay)
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)
    return SimpleNamespace(tasks=tasks, run=run, session=session, mongo=mongo, capture=capture, previous=previous,
                           delay=delay, journal=journal)


def _fire(hook, state=None):
    return hook.tasks._capture_ai_history_after_pipeline(
        "task-1", RUN, PROJECT, state if state is not None else {"pipeline_run_id": PIPELINE, "stage_quality": "normal"},
    )


def test_a_new_version_is_committed_then_the_compare_report_is_regenerated_once(hook):
    result = _fire(hook)
    assert result == {"status": "captured", "version": 2, "compare": "enqueued"}
    hook.capture.assert_awaited_once()
    args, kwargs = hook.capture.await_args
    assert args == (hook.session, hook.mongo)
    assert kwargs == {"run_id": uuid.UUID(RUN), "pipeline_run_id": uuid.UUID(PIPELINE), "stage_quality": "normal"}
    assert hook.journal == ["capture", "commit", "get", "enqueue"], "the activity rows commit before the enqueue"
    hook.previous.assert_awaited_once_with(hook.session, run=hook.run)
    hook.delay.assert_called_once_with(project_id=PROJECT, left_run_id=str(BASELINE), right_run_id=RUN,
                                       suite_name="checkout")


def test_a_replayed_pipeline_records_nothing_and_enqueues_nothing(hook):
    hook.capture.side_effect = lambda *a, **k: _capture(_recorded(created=False))
    assert _fire(hook) == {"status": "not_new", "reason": "replay"}
    hook.delay.assert_not_called()
    hook.previous.assert_not_awaited()


def test_a_run_deleted_meanwhile_enqueues_nothing(hook):
    hook.capture.side_effect = lambda *a, **k: ahs.CaptureResult(None, None, False, "run_deleted")
    assert _fire(hook) == {"status": "not_new", "reason": "run_deleted"}
    hook.delay.assert_not_called()


def test_no_pipeline_id_skips_without_guessing(hook):
    assert _fire(hook, {"completed_stages": ["summary"]}) == {"status": "skipped", "reason": "no_pipeline_run_id"}
    hook.capture.assert_not_awaited()


def test_no_suite_or_no_previous_run_means_no_compare_report(hook):
    hook.run.primary_suite_name = None
    assert _fire(hook)["compare"] == "no_primary_suite"
    hook.previous.assert_not_awaited()
    hook.run.primary_suite_name = "checkout"
    hook.previous.return_value = reads.PreviousRun(None, "no_previous_run", "none", "checkout")
    assert _fire(hook)["compare"] == "no_previous_run"
    hook.delay.assert_not_called()


def test_a_broker_failure_is_logged_not_raised(hook, caplog):
    hook.delay.side_effect = ConnectionError("broker down")
    assert _fire(hook)["compare"] == "enqueue_failed"


def test_a_history_failure_never_fails_the_pipeline(hook, caplog):
    hook.capture.side_effect = RuntimeError("mongo down")
    with caplog.at_level("WARNING"):
        assert _fire(hook) is None
    assert "AI history capture failed (non-blocking, RuntimeError)" in caplog.text
    hook.delay.assert_not_called()


# ── The wiring in run_agent_pipeline ─────────────────────────────────────────


@pytest.fixture
def pipeline(monkeypatch):
    from app.worker import tasks

    journal: list = []
    state: dict = {}
    # W07 FAIL-04: the pipeline queues a snapshot rebuild; no broker here
    # (covered by tests/test_snapshot_rebuild_after_pipeline.py).
    monkeypatch.setattr(tasks, "_enqueue_snapshot_rebuild", lambda *a, **k: True)

    def fake_run_async(coro):
        name = getattr(coro, "__qualname__", "")
        coro.close()
        if name.endswith("_run"):
            journal.append("pipeline")
            return state["final"]
        if name == "_release_requested_admission":
            journal.append("release")
            return None
        journal.append("invalidate")
        return None

    monkeypatch.setattr(tasks, "_run_async", fake_run_async)
    spy = MagicMock(side_effect=lambda *a: journal.append("ai_history"))
    monkeypatch.setattr(tasks, "_capture_ai_history_after_pipeline", spy)
    return SimpleNamespace(tasks=tasks, journal=journal, state=state, spy=spy)


def _run_pipeline(pipeline, final):
    pipeline.state["final"] = final
    return pipeline.tasks.run_agent_pipeline.apply(
        kwargs={"test_run_id": RUN, "project_id": PROJECT, "build_number": "101"},
    ).get()


def test_a_finished_pipeline_captures_its_history_before_the_snapshot_invalidation(pipeline):
    final = {"completed_stages": ["summary"], "errors": [], "pipeline_run_id": PIPELINE}
    _run_pipeline(pipeline, final)
    pipeline.spy.assert_called_once()
    assert pipeline.spy.call_args.args[1:] == (RUN, PROJECT, final)
    assert pipeline.journal == ["pipeline", "ai_history", "invalidate", "release"]


def test_a_duplicate_short_circuit_captures_nothing(pipeline):
    _run_pipeline(pipeline, {"completed_stages": [], "error_count": 0, "duplicate": True})
    pipeline.spy.assert_not_called()


# ── The one-shot backfill task ───────────────────────────────────────────────

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def backfill(monkeypatch):
    import app.db.mongo as app_mongo
    import app.db.postgres as pg
    import app.db.redis_client as redis_client
    from app.worker import tasks

    journal: list = []
    store: dict = {}
    redis = SimpleNamespace(
        get=AsyncMock(side_effect=lambda key: store.get(key)),
        set=AsyncMock(side_effect=lambda key, value: store.__setitem__(key, value)),
    )
    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)
    lock = {"held": False}
    monkeypatch.setattr(tasks, "_is_duplicate", AsyncMock(side_effect=lambda *a, **k: lock["held"]))
    release = AsyncMock()
    monkeypatch.setattr(tasks, "_release_duplicate_lock", release)
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)
    session = MagicMock()
    session.commit = AsyncMock(side_effect=lambda: journal.append("commit"))
    session.rollback = AsyncMock(side_effect=lambda: journal.append("rollback"))
    monkeypatch.setattr(pg, "AsyncSessionLocal", _Factory(session), raising=False)
    mongo = object()
    monkeypatch.setattr(app_mongo, "get_mongo_db", lambda: mongo)
    script: list = []
    cursors: list = []

    async def fake_chunk(db, mongo_db, cursor, limit):
        assert db is session and mongo_db is mongo
        cursors.append((cursor, limit))
        return script.pop(0)

    monkeypatch.setattr(ahs, "backfill_chunk", fake_chunk)
    return SimpleNamespace(tasks=tasks, store=store, lock=lock, release=release, session=session, script=script,
                           cursors=cursors, journal=journal)


def _chunk(scanned, written, skipped, at, last_id, done):
    return ahs.BackfillChunk(scanned=scanned, written=written, skipped=skipped, done=done, last_created_at=at,
                             last_id=last_id)


def _task(env, **kwargs):
    return env.tasks.backfill_ai_run_versions.apply(kwargs=kwargs).get()


KEY = "ai_history.backfill_cursor"


def test_the_backfill_walks_chunks_until_a_short_one_and_marks_the_cursor_done(backfill):
    a, b = uuid.uuid4(), uuid.uuid4()
    backfill.script.extend([_chunk(2, 2, 0, T0, a, False), _chunk(1, 0, 1, T0 + timedelta(hours=1), b, True)])
    result = _task(backfill, chunk_size=2, max_chunks=10)
    assert result == {"status": "done", "chunks": 2, "scanned": 3, "written": 2, "skipped": 1}
    assert backfill.cursors == [(None, 2), ((T0, a), 2)], "the second chunk resumed after the first's last run"
    assert backfill.journal == ["commit", "commit"], "each chunk commits on its own session"
    assert '"done": true' in backfill.store[KEY]
    backfill.release.assert_awaited_once()


def test_the_backfill_resumes_from_its_cursor_on_the_next_tick(backfill):
    a, b = uuid.uuid4(), uuid.uuid4()
    backfill.script.append(_chunk(2, 2, 0, T0, a, False))
    assert _task(backfill, chunk_size=2, max_chunks=1)["status"] == "max_chunks"
    backfill.script.append(_chunk(1, 1, 0, T0 + timedelta(minutes=5), b, True))
    assert _task(backfill, chunk_size=2, max_chunks=1) == {
        "status": "done", "chunks": 1, "scanned": 3, "written": 3, "skipped": 0,
    }
    assert [c for c, _ in backfill.cursors] == [None, (T0, a)]


def test_a_done_backfill_is_one_redis_read(backfill):
    backfill.store[KEY] = '{"done": true, "scanned": 7, "written": 5, "skipped": 2}'
    assert _task(backfill) == {"status": "done", "chunks": 0, "scanned": 7, "written": 5, "skipped": 2}
    assert backfill.cursors == []


def test_a_held_lock_skips_the_pass(backfill):
    backfill.lock["held"] = True
    assert _task(backfill) == {"status": "locked"}
    assert backfill.cursors == []


def test_a_failed_chunk_rolls_back_and_keeps_the_cursor(backfill, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("mongo down")

    monkeypatch.setattr(ahs, "backfill_chunk", boom)
    with pytest.raises(RuntimeError):
        _task(backfill)
    assert backfill.journal == ["rollback"] and KEY not in backfill.store
    backfill.release.assert_awaited_once()


def test_the_backfill_stops_on_its_time_budget(backfill):
    backfill.script.extend([_chunk(1, 1, 0, T0, uuid.uuid4(), False) for _ in range(3)])
    assert _task(backfill, chunk_size=1, max_chunks=3, time_budget_s=-1)["status"] == "time_budget"
    assert len(backfill.cursors) == 1


def test_the_backfill_is_scheduled_on_the_default_queue_offset_from_the_suite_backfill():
    from app.worker.celery_app import celery_app
    from app.worker.tasks import backfill_ai_run_versions

    entries = [e for e in celery_app.conf.beat_schedule.values() if e["task"] == backfill_ai_run_versions.name]
    assert len(entries) == 1
    assert backfill_ai_run_versions.queue == "default"
    assert sorted(entries[0]["schedule"].minute) == [5, 15, 25, 35, 45, 55]
    assert backfill_ai_run_versions.max_retries == 0
    assert (backfill_ai_run_versions.soft_time_limit, backfill_ai_run_versions.time_limit) == (540, 600)
