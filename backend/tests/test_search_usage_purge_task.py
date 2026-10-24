"""W12 SRCH-05 §2.5 test 5: the nightly search-usage purge keeps a FIXED 30-day window.

The beat entry exists and binds to the task's REAL signature; the task loops
``purge_expired_batch`` (one staged DELETE each), committing every batch,
until a batch deletes nothing, at most 100 batches a run.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

from sqlalchemy.dialects import postgresql

from app.services import search_usage_service
from app.worker import tasks
from app.worker.celery_app import celery_app


class _Session:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        self.log.append("commit")


def _patch(monkeypatch, counts):
    log: list = []
    remaining = list(counts)

    async def batch(db, *, now):
        assert now.tzinfo is not None
        log.append(("batch", now))
        return remaining.pop(0) if remaining else 5000

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", lambda: _Session(log), raising=False)
    monkeypatch.setattr(search_usage_service, "purge_expired_batch", batch)
    return log


def test_the_beat_entry_runs_the_task_nightly_on_the_default_queue():
    entry = celery_app.conf.beat_schedule["nightly-search-usage-purge"]
    assert entry["task"] == "app.worker.tasks.purge_search_usage_events"
    assert entry["task"] in celery_app.tasks
    schedule = entry["schedule"]
    assert (schedule.hour, schedule.minute) == ({2}, {40})
    assert entry["options"]["queue"] == "default"
    # What beat sends must bind to the task's real signature.
    inspect.signature(tasks.purge_search_usage_events.run).bind(
        *entry.get("args", ()), **entry.get("kwargs", {})
    )


def test_the_task_commits_each_batch_until_one_deletes_nothing(monkeypatch):
    log = _patch(monkeypatch, [5000, 5000, 12, 0])

    outcome = tasks.purge_search_usage_events.run()

    assert outcome == {"deleted": 10012, "batches": 4}
    kinds = [item if isinstance(item, str) else item[0] for item in log]
    assert kinds == ["batch", "commit"] * 4
    nows = {item[1] for item in log if isinstance(item, tuple)}
    assert len(nows) == 1                                   # one cutoff for the whole run


def test_a_run_stops_after_one_hundred_batches(monkeypatch):
    log = _patch(monkeypatch, [])                           # every batch is full

    outcome = tasks.purge_search_usage_events.run()

    assert outcome == {"deleted": 100 * 5000, "batches": 100}
    assert log.count("commit") == 100


def test_the_batch_deletes_only_rows_older_than_thirty_days():
    now = datetime(2026, 10, 24, 2, 40, tzinfo=timezone.utc)
    assert search_usage_service.usage_cutoff(now) == now - timedelta(days=30)
    captured: list = []

    class _Recorder:
        async def execute(self, statement):
            captured.append(statement)

            class _Result:
                rowcount = 7
            return _Result()

    import asyncio

    deleted = asyncio.run(search_usage_service.purge_expired_batch(_Recorder(), now=now))

    assert deleted == 7 and len(captured) == 1
    compiled = captured[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert sql.startswith("DELETE FROM search_query_events WHERE search_query_events.id IN (SELECT")
    assert "project_id" not in sql                          # every project and the NULL rows alike
    assert now - timedelta(days=30) in compiled.params.values()
    assert search_usage_service.USAGE_PURGE_BATCH in compiled.params.values()
