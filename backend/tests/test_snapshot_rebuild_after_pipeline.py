"""The worker keeps a freshly analysed run's snapshot warm (W07 FAIL-04, contract §2.2).

The Runs list reads stored snapshots only, and the worker DELETES a run's
snapshot at the end of every analysis pipeline (``invalidate``: a completed
pipeline replaces the analysis wholesale). Right after that delete the pipeline
task QUEUES ``rebuild_run_snapshot`` -- it never rebuilds, or waits, itself:
the rebuild may wait minutes for the run's defect-candidate build, and a
pipeline worker slot is not where to wait. The rebuild task is idempotent (a
servable snapshot already there means nothing to do), runs on the
``default`` queue (not ``ai_analysis``, where pipelines run) with its own
time limits, and never raises.

A run's first intelligence read is ``defect_candidates_pending`` whenever it
has failure clusters and no persisted candidates (the read schedules their
build in the background), and such a payload is never cached. The build lives
on the rebuild task's loop, and ``rebuild_snapshot`` waits for it, bounded, and
reads once more.

The enqueue is checked against the REAL task signature (a mocked
``apply_async`` would accept any arguments; Celery's own check is the task's
``__header__``, which ``apply_async`` calls before sending).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import intelligence_snapshot_service as snapshots

RUN = str(uuid.UUID("33333333-3333-4333-8333-333333333333"))
PROJECT = str(uuid.UUID("11111111-1111-4111-8111-111111111111"))


class _Session:
    def __init__(self, journal):
        self.journal = journal

    def __call__(self):
        return self

    async def __aenter__(self):
        self.journal.append("open")
        return self

    async def __aexit__(self, *exc):
        self.journal.append("close")
        return False


def _forbidden(name):
    async def _raise(*args, **kwargs):
        raise AssertionError(f"the pipeline task reached {name}")
    return _raise


# ── The wiring in run_agent_pipeline ─────────────────────────────────────────


@pytest.fixture
def pipeline(monkeypatch):
    import app.db.postgres as pg
    from app.worker import tasks

    journal: list = []
    state: dict = {}
    session = _Session(journal)
    monkeypatch.setattr(pg, "AsyncSessionLocal", session, raising=False)

    async def invalidate(db, run_id):
        journal.append(("invalidate", db, run_id))
        return True

    monkeypatch.setattr(snapshots, "invalidate", invalidate)
    monkeypatch.setattr(snapshots, "rebuild_snapshot", _forbidden("rebuild_snapshot"))
    enqueue = MagicMock(side_effect=lambda *a, **k: journal.append("enqueue"))
    monkeypatch.setattr(tasks.rebuild_run_snapshot, "apply_async", enqueue)

    def fake_run_async(coro):
        name = getattr(coro, "__qualname__", "")
        if name.endswith("._do_invalidate"):
            return asyncio.run(coro)
        coro.close()
        if name.endswith("._run"):
            return state["final"]
        return None

    monkeypatch.setattr(tasks, "_run_async", fake_run_async)
    monkeypatch.setattr(tasks, "_capture_ai_history_after_pipeline", MagicMock())
    return SimpleNamespace(tasks=tasks, journal=journal, state=state, session=session, enqueue=enqueue)


def _run_pipeline(pipeline, final):
    pipeline.state["final"] = final
    return pipeline.tasks.run_agent_pipeline.apply(
        kwargs={"test_run_id": RUN, "project_id": PROJECT, "build_number": "101"},
    ).get()


def test_the_pipeline_invalidates_then_enqueues_the_rebuild_exactly_once(pipeline):
    result = _run_pipeline(pipeline, {"completed_stages": ["summary"], "errors": []})
    assert result == {"completed_stages": ["summary"], "error_count": 0}
    assert pipeline.journal == ["open", ("invalidate", pipeline.session, uuid.UUID(RUN)), "close", "enqueue"]
    pipeline.enqueue.assert_called_once_with(args=[RUN])


def test_the_enqueued_payload_binds_to_the_real_task_signature(pipeline):
    _run_pipeline(pipeline, {"completed_stages": ["summary"], "errors": []})
    (call,) = pipeline.enqueue.call_args_list
    args, kwargs = call.kwargs.get("args", ()), call.kwargs.get("kwargs") or {}
    task = pipeline.tasks.rebuild_run_snapshot
    # Celery's own argument check (what apply_async runs before sending), and the function's.
    task.__header__(*args, **kwargs)
    bound = inspect.signature(task.run).bind(*args, **kwargs)
    assert bound.arguments == {"run_id": RUN}


def test_a_broker_failure_never_fails_the_pipeline(pipeline, caplog):
    pipeline.enqueue.side_effect = ConnectionError("broker down")
    with caplog.at_level(logging.WARNING):
        result = _run_pipeline(pipeline, {"completed_stages": ["summary"], "errors": []})
    assert result == {"completed_stages": ["summary"], "error_count": 0}
    assert "Snapshot rebuild not queued (non-blocking, ConnectionError)" in caplog.text


def test_the_delete_primitive_is_still_the_invalidate_import():
    # Pinned by tests/test_bl03_snapshot_freshness.py too: the enqueue is ADDED after the delete.
    from app.worker import tasks

    src = inspect.getsource(tasks.run_agent_pipeline)
    assert "import invalidate as _invalidate_snapshot" in src
    assert src.index("_invalidate_snapshot(_db") < src.index("_enqueue_snapshot_rebuild(")
    assert "rebuild_snapshot(" not in src.replace("_enqueue_snapshot_rebuild(", ""), "the pipeline never rebuilds inline"


# ── The rebuild task ─────────────────────────────────────────────────────────


@pytest.fixture
def task(monkeypatch):
    import app.db.mongo as app_mongo
    import app.db.postgres as pg
    from app.worker import tasks

    journal: list = []
    state = {"exists": False, "saved": True}
    session = _Session(journal)
    mongo = object()
    monkeypatch.setattr(pg, "AsyncSessionLocal", session, raising=False)
    monkeypatch.setattr(app_mongo, "get_mongo_db", lambda: mongo)

    async def exists(db, run_id):
        journal.append(("exists?", db, run_id))
        if isinstance(state["exists"], Exception):
            raise state["exists"]
        return state["exists"]

    async def rebuild(db, run_id, mongo_db, *, build_wait_seconds=0.0):
        journal.append(("rebuild", db, run_id, mongo_db, build_wait_seconds))
        return state["saved"]

    monkeypatch.setattr(snapshots, "has_servable_snapshot", exists)
    monkeypatch.setattr(snapshots, "rebuild_snapshot", rebuild)
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)
    return SimpleNamespace(tasks=tasks, journal=journal, state=state, session=session, mongo=mongo)


def _run_task(task):
    # ``apply`` runs the real task eagerly, argument check included.
    return task.tasks.rebuild_run_snapshot.apply(args=[RUN]).get()


def test_the_task_rebuilds_on_its_own_session_with_the_wait_budget(task):
    assert _run_task(task) == {"status": "rebuilt"}
    run_id = uuid.UUID(RUN)
    assert task.journal == [
        "open",
        ("exists?", task.session, run_id),
        ("rebuild", task.session, run_id, task.mongo, task.tasks.SNAPSHOT_REBUILD_BUILD_WAIT_SECONDS),
        "close",
    ]
    assert task.tasks.SNAPSHOT_REBUILD_BUILD_WAIT_SECONDS == 300.0


def test_a_duplicate_enqueue_does_nothing(task):
    task.state["exists"] = True
    assert _run_task(task) == {"status": "skipped", "reason": "snapshot_exists"}
    assert [step[0] for step in task.journal if isinstance(step, tuple)] == ["exists?"]


def test_a_rebuild_that_saves_nothing_says_so(task):
    task.state["saved"] = False
    assert _run_task(task) == {"status": "not_saved"}


def test_a_failure_is_logged_with_its_class_and_never_raises(task, caplog):
    task.state["exists"] = RuntimeError("db down: secret")
    with caplog.at_level(logging.WARNING):
        assert _run_task(task) == {"status": "failed", "reason": "RuntimeError"}
    assert "Snapshot rebuild failed (non-blocking, RuntimeError)" in caplog.text
    assert "secret" not in caplog.text


def test_the_task_runs_on_the_default_queue_inside_its_own_limits():
    from app.worker import tasks
    from app.worker.celery_app import celery_app

    rebuild = tasks.rebuild_run_snapshot
    assert rebuild.name == "app.worker.tasks.rebuild_run_snapshot"
    assert rebuild.name in celery_app.tasks
    assert (rebuild.soft_time_limit, rebuild.time_limit) == (360, 420)
    assert rebuild.max_retries == 0
    assert tasks.SNAPSHOT_REBUILD_BUILD_WAIT_SECONDS < rebuild.soft_time_limit
    # The route Celery resolves for a send. NOT ai_analysis: the rebuild may wait
    # minutes for a defect-candidate build, and the ai_analysis pool runs pipelines.
    options = celery_app.amqp.router.route(dict(rebuild._get_exec_options()), rebuild.name)
    assert options["queue"].name == "default"


# ── has_servable_snapshot ────────────────────────────────────────────────────


async def test_has_servable_snapshot_asks_for_a_current_schema_version():
    statements: list = []

    class _Db:
        async def execute(self, statement):
            statements.append(statement)
            return SimpleNamespace(scalar_one_or_none=lambda: None)

    assert await snapshots.has_servable_snapshot(_Db(), uuid.UUID(RUN)) is False
    from sqlalchemy.dialects import postgresql

    compiled = statements[0].compile(dialect=postgresql.dialect())
    assert "run_intelligence_snapshots.schema_version >= " in str(compiled)
    assert snapshots.CURRENT_SCHEMA_VERSION in compiled.params.values()


# ── rebuild_snapshot ─────────────────────────────────────────────────────────


class _Db:
    new: list = []
    dirty: list = []
    deleted: list = []

    def __init__(self):
        self.close = AsyncMock()


@pytest.fixture
def rebuild(monkeypatch):
    from app.services import run_intelligence_service as ris

    journal: list = []
    payloads: list = []
    saved: list = []

    async def compute(run_id, db, mongo):
        journal.append(("compute", db))
        value = payloads.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def save(db, run_id, payload, fallback_used=False):
        journal.append(("save", db))
        saved.append((payload, fallback_used))

    monkeypatch.setattr(ris, "get_run_intelligence", compute)
    monkeypatch.setattr(snapshots, "save_snapshot", save)
    monkeypatch.setattr(ris, "_DEFECT_BUILD_TASKS", set())
    return SimpleNamespace(ris=ris, journal=journal, payloads=payloads, saved=saved)


async def test_compute_then_save_on_the_session_it_is_handed(rebuild):
    db = _Db()
    rebuild.payloads.append({"release_decision": {"recommendation": "GO"}, "provenance": {"fallback_used": True}})
    assert await snapshots.rebuild_snapshot(db, uuid.UUID(RUN), object()) is True
    assert rebuild.journal == [("compute", db), ("save", db)]
    assert rebuild.saved == [({"release_decision": {"recommendation": "GO"}, "provenance": {"fallback_used": True}},
                              True)]


async def test_a_compute_error_leaves_no_snapshot_and_does_not_raise(rebuild, caplog):
    rebuild.payloads.append(RuntimeError("mongo down: secret detail"))
    with caplog.at_level(logging.WARNING, logger="services.intelligence_snapshot"):
        assert await snapshots.rebuild_snapshot(_Db(), uuid.UUID(RUN), object()) is False
    assert rebuild.saved == []
    (record,) = [r for r in caplog.records if r.getMessage() == "snapshot_rebuild_failed"]
    assert record.error_type == "RuntimeError" and record.run_id == RUN
    assert "secret detail" not in caplog.text, "the exception CLASS only"


async def test_a_pending_payload_is_not_saved_without_a_wait_budget(rebuild):
    rebuild.payloads.append({"defect_candidates_pending": True})
    assert await snapshots.rebuild_snapshot(_Db(), uuid.UUID(RUN), object()) is False
    assert rebuild.journal == [("compute", rebuild.journal[0][1])]
    assert rebuild.saved == []


async def test_a_pending_payload_waits_for_the_build_then_reads_again(rebuild):
    db = _Db()
    done = asyncio.Event()

    async def build():
        await done.wait()

    task = asyncio.get_running_loop().create_task(build())
    rebuild.ris._DEFECT_BUILD_TASKS.add(task)
    rebuild.payloads.extend([{"defect_candidates_pending": True}, {"release_decision": {"recommendation": "NO_GO"}}])
    asyncio.get_running_loop().call_later(0.05, done.set)
    assert await snapshots.rebuild_snapshot(db, uuid.UUID(RUN), object(), build_wait_seconds=5) is True
    assert [step for step, _ in rebuild.journal] == ["compute", "compute", "save"]
    db.close.assert_awaited_once()  # the connection is handed back while the build runs
    assert rebuild.saved[0][0] == {"release_decision": {"recommendation": "NO_GO"}}


async def test_a_build_that_outlives_the_budget_leaves_no_snapshot(rebuild):
    never = asyncio.get_running_loop().create_future()
    task = asyncio.ensure_future(never)
    rebuild.ris._DEFECT_BUILD_TASKS.add(task)
    rebuild.payloads.append({"defect_candidates_pending": True})
    try:
        assert await snapshots.rebuild_snapshot(_Db(), uuid.UUID(RUN), object(), build_wait_seconds=0.05) is False
    finally:
        task.cancel()
    assert [step for step, _ in rebuild.journal] == ["compute"]
    assert rebuild.saved == []
