"""W08 TRND-07 -- the daily ``take_coverage_snapshot`` task.

The task runs through Celery's own ``apply`` (the real task, its argument
check included -- the W07 ``rebuild_run_snapshot`` pattern), with the database
replaced by a journal of sessions and the service's four calls replaced by
recorders. Proved: it is its own task on the ``default`` queue inside its own
limits and on the 00:05 UTC beat; the project ids are read in one session that
is closed before any project's work; each project has ITS OWN session and one
commit; the days are the missing ones plus yesterday, never today; one
project's failure is logged and skipped.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date
from types import SimpleNamespace

import pytest

TODAY = date(2026, 9, 21)
P1, P2, P3 = (uuid.UUID(int=n) for n in (1, 2, 3))


class _Session:
    def __init__(self, journal: list, project_ids: list):
        self.journal = journal
        self.project_ids = project_ids
        self.index = None

    def __call__(self):
        session = _Session(self.journal, self.project_ids)
        session.index = sum(1 for step in self.journal if step == "open")
        return session

    async def __aenter__(self):
        self.journal.append("open")
        return self

    async def __aexit__(self, *exc):
        self.journal.append("close")
        return False

    async def execute(self, statement, params=None):
        self.journal.append(("execute", self.index))
        ids = self.project_ids
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(ids)))

    async def commit(self):
        self.journal.append(("commit", self.index))


@pytest.fixture
def task(monkeypatch):
    import app.db.postgres as pg
    from app.services import coverage_snapshot_service as snapshots
    from app.worker import tasks

    journal: list = []
    state = {"missing": {P1: [date(2026, 9, 1)], P2: [], P3: []}, "fail": set()}
    monkeypatch.setattr(pg, "AsyncSessionLocal", _Session(journal, [P1, P2, P3]), raising=False)
    monkeypatch.setattr(snapshots, "utc_today", lambda now=None: TODAY)

    async def missing(db, project_id, *, today, back=30):
        journal.append(("missing", db.index, project_id, today))
        if project_id in state["fail"]:
            raise RuntimeError("db down: secret")
        return state["missing"][project_id]

    async def values(db, project_id, day):
        journal.append(("values", db.index, project_id, day))
        return {"catalogue_tests": 1, "exercised_tests": 1, "suites": 1, "suite_coverage": []}

    async def stage(db, project_id, day, vals):
        journal.append(("stage", db.index, project_id, day))

    monkeypatch.setattr(snapshots, "missing_days", missing)
    monkeypatch.setattr(snapshots, "snapshot_values", values)
    monkeypatch.setattr(snapshots, "stage_snapshot", stage)
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)
    return SimpleNamespace(tasks=tasks, journal=journal, state=state)


def _run(task) -> dict:
    # ``apply`` runs the real task eagerly, Celery's argument check included.
    return task.tasks.take_coverage_snapshot.apply().get()


def test_it_is_its_own_task_on_the_default_queue_inside_its_own_limits():
    from app.worker import tasks
    from app.worker.celery_app import celery_app

    snapshot = tasks.take_coverage_snapshot
    assert snapshot.name == "app.worker.tasks.take_coverage_snapshot"
    assert snapshot.name in celery_app.tasks
    assert (snapshot.soft_time_limit, snapshot.time_limit) == (1500, 1800)
    options = celery_app.amqp.router.route(dict(snapshot._get_exec_options()), snapshot.name)
    assert options["queue"].name == "default"
    # The beat payload (no arguments) binds to the real signature.
    snapshot.__header__()
    beat = celery_app.conf.beat_schedule["daily-coverage-snapshot"]
    assert beat["task"] == snapshot.name
    assert (beat["schedule"].hour, beat["schedule"].minute) == ({0}, {5})


def test_each_project_has_its_own_session_and_one_commit(task):
    assert _run(task) == {"projects": 3, "rows": 4, "failed": 0}
    journal = task.journal
    # Session 0 lists the projects and is closed before any project's work.
    assert journal[:3] == ["open", ("execute", 0), "close"]
    for index, project in ((1, P1), (2, P2), (3, P3)):
        steps = [step for step in journal if isinstance(step, tuple) and step[1] == index]
        assert steps[0] == ("missing", index, project, TODAY)
        assert steps[-1] == ("commit", index)
        assert [s for s in steps if s[0] == "commit"] == [("commit", index)]
    stages = [(step[2], step[3]) for step in journal if step[0] == "stage"]
    assert stages == [
        (P1, date(2026, 9, 1)), (P1, date(2026, 9, 20)), (P2, date(2026, 9, 20)), (P3, date(2026, 9, 20)),
    ]
    assert all(day != TODAY for _project, day in stages)


class _Log:
    """Records ``_slog`` calls. ``structlog.testing.capture_logs`` cannot see a
    logger another test already bound under a cached configuration."""

    def __init__(self):
        self.entries: list[dict] = []

    def warning(self, event, **kw):
        self.entries.append({"event": event, "log_level": "warning", **kw})

    def info(self, event, **kw):
        self.entries.append({"event": event, "log_level": "info", **kw})


def test_one_project_s_failure_is_logged_and_the_sweep_goes_on(task, monkeypatch):
    task.state["fail"] = {P2}
    log = _Log()
    monkeypatch.setattr(task.tasks, "_slog", log)
    assert _run(task) == {"projects": 3, "rows": 3, "failed": 1}
    logs = log.entries
    failed = [entry for entry in logs if entry["event"] == "coverage_snapshot_failed"]
    assert failed == [{
        "event": "coverage_snapshot_failed", "log_level": "warning",
        "project_id": str(P2), "error": "RuntimeError",
    }]
    assert "secret" not in repr(logs)
    done = [entry for entry in logs if entry["event"] == "coverage_snapshot_done"]
    assert done and (done[0]["projects"], done[0]["rows"], done[0]["failed"]) == (3, 3, 1)
    # P2 committed nothing; P1 and P3 did.
    commits = [step[1] for step in task.journal if isinstance(step, tuple) and step[0] == "commit"]
    assert commits == [1, 3]
