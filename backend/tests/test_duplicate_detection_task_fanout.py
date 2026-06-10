"""Phase 4 — nightly duplicate-detection beat task fan-out (review finding).

The sweep entry (``project_id is None``) must NOT scan every project inside ONE
task under the 900s hard ``time_limit`` (a global timeout SIGKILLs the worker
mid-loop and silently skips the tail of the project list every night). It now
fans out one ``run_duplicate_detection.delay(project_id=…)`` sub-task per
project, so each project gets its own time budget. The single-project path runs
the detection service (commit-owner) and leaves semantic OFF unless explicitly
enabled.

Invoked via ``.apply()`` (eager local execution) so Celery supplies the bound
``self.request`` context.
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

import pytest

pytest.importorskip("celery")

from app.worker import tasks as worker_tasks  # noqa: E402


def _run_coro_sync(coro):
    """Drive a coroutine to completion on a throwaway loop (no real DB)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeSession:
    def __init__(self, sink):
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def commit(self):
        self._sink["committed"] = True

    async def rollback(self):
        self._sink["rolledback"] = True


def test_sweep_fans_out_one_subtask_per_project():
    """No project_id → enqueue one .delay(project_id=…) per project; the parent
    task itself runs NO per-project detection."""
    pids = [uuid.uuid4() for _ in range(4)]

    # Fan-out path calls _run_async(_list_project_ids()); short-circuit it to
    # return the ids directly (and close the un-awaited coroutine cleanly).
    def _fake_run_async(coro):
        coro.close()
        return list(pids)

    with patch.object(worker_tasks, "_run_async", _fake_run_async), \
         patch.object(worker_tasks.run_duplicate_detection, "delay") as delay:
        result = worker_tasks.run_duplicate_detection.apply(
            kwargs={"project_id": None}
        ).get()

    assert result["mode"] == "fan_out"
    assert result["projects_enqueued"] == 4
    assert delay.call_count == 4
    enqueued = {c.kwargs.get("project_id") for c in delay.call_args_list}
    assert enqueued == {str(p) for p in pids}


def test_single_project_runs_detection_semantic_off_by_default():
    """A single-project invocation runs the detection unit-of-work, commits, and
    defaults semantic OFF (sweep sub-tasks are cheap + deterministic)."""
    pid = uuid.uuid4()
    captured: dict = {}

    async def _fake_detect(db, project_id, *, enable_semantic):
        captured["enable_semantic"] = enable_semantic
        captured["project_id"] = project_id
        return {"candidates_created": 2, "cases_scanned": 5, "sampled": False}

    with patch.object(worker_tasks, "_run_async", _run_coro_sync), \
         patch("app.db.postgres.AsyncSessionLocal", lambda: _FakeSession(captured)), \
         patch(
             "app.services.duplicate_detection_service.detect_duplicates_for_project",
             _fake_detect,
         ):
        result = worker_tasks.run_duplicate_detection.apply(
            kwargs={"project_id": str(pid)}
        ).get()

    assert captured["enable_semantic"] is False
    assert captured["project_id"] == pid
    assert captured.get("committed") is True
    assert result["projects_scanned"] == 1
    assert result["candidates_created"] == 2


def test_single_project_semantic_opt_in():
    """An explicit enable_semantic=True (ad-hoc) is honoured."""
    pid = uuid.uuid4()
    captured: dict = {}

    async def _fake_detect(db, project_id, *, enable_semantic):
        captured["enable_semantic"] = enable_semantic
        return {"candidates_created": 0, "cases_scanned": 1, "sampled": False}

    with patch.object(worker_tasks, "_run_async", _run_coro_sync), \
         patch("app.db.postgres.AsyncSessionLocal", lambda: _FakeSession(captured)), \
         patch(
             "app.services.duplicate_detection_service.detect_duplicates_for_project",
             _fake_detect,
         ):
        worker_tasks.run_duplicate_detection.apply(
            kwargs={"project_id": str(pid), "enable_semantic": True}
        ).get()

    assert captured["enable_semantic"] is True
