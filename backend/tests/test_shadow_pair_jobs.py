"""W16 AGT-13 §2.2 test 5: the two nightly shadow-pair jobs.

``golden_match_shadow_pairs`` (03:50 UTC) and ``expire_unlabelled_shadow_pairs``
(02:50 UTC) loop G-D's stage-only batches, committing each, until a batch
touches nothing (at most 20 / 100 batches a night). The beat entries bind to
the tasks' REAL signatures. Times are built inside each test.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pytest

from app.services import shadow_pair_service
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


def _patch(monkeypatch, name, counts, *, full):
    log: list = []
    remaining = list(counts)

    async def batch(db, *, now):
        assert now.tzinfo is not None
        log.append(("batch", now))
        return remaining.pop(0) if remaining else full

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", lambda: _Session(log), raising=False)
    monkeypatch.setattr(shadow_pair_service, name, batch)
    return log


@pytest.mark.parametrize(
    "key,task,hour,minute",
    [
        ("golden-match-shadow-pairs", "golden_match_shadow_pairs", 3, 50),
        ("expire-unlabelled-shadow-pairs", "expire_unlabelled_shadow_pairs", 2, 50),
    ],
)
def test_the_beat_entries_bind_to_the_tasks(key, task, hour, minute):
    entry = celery_app.conf.beat_schedule[key]
    assert entry["task"] == f"app.worker.tasks.{task}"
    assert entry["task"] in celery_app.tasks
    assert (entry["schedule"].hour, entry["schedule"].minute) == ({hour}, {minute})
    assert entry["options"]["queue"] == "default"
    inspect.signature(getattr(tasks, task).run).bind(*entry.get("args", ()), **entry.get("kwargs", {}))
    assert getattr(tasks, task).max_retries == 0


def test_golden_match_commits_each_batch_until_one_labels_nothing(monkeypatch):
    log = _patch(monkeypatch, "golden_match_batch", [500, 31, 0], full=500)

    assert tasks.golden_match_shadow_pairs.run() == {"labelled": 531, "batches": 3}
    assert [e if isinstance(e, str) else e[0] for e in log] == ["batch", "commit"] * 3
    assert len({e[1] for e in log if isinstance(e, tuple)}) == 1  # one ``now`` for the night


def test_golden_match_stops_after_twenty_batches(monkeypatch):
    log = _patch(monkeypatch, "golden_match_batch", [], full=500)

    assert tasks.golden_match_shadow_pairs.run() == {"labelled": 20 * 500, "batches": 20}
    assert log.count("commit") == 20


class _Log:
    """Records ``_slog`` calls. ``structlog.testing.capture_logs`` cannot see a
    logger another test already bound under a cached configuration."""

    def __init__(self):
        self.entries: list[dict] = []

    def info(self, event, **kw):
        self.entries.append({"event": event, "log_level": "info", **kw})

    def error(self, event, **kw):
        self.entries.append({"event": event, "log_level": "error", **kw})


def test_expiry_commits_each_batch_until_one_deletes_nothing(monkeypatch):
    log = _patch(monkeypatch, "expire_unlabelled_batch", [1000, 400, 0], full=1000)
    slog = _Log()
    monkeypatch.setattr(tasks, "_slog", slog)
    assert tasks.expire_unlabelled_shadow_pairs.run() == {"deleted": 1400, "batches": 3}

    assert log.count("commit") == 3
    assert {"event": "shadow_pairs_expired", "deleted": 1400, "batches": 3, "log_level": "info"} in slog.entries


def test_expiry_stops_after_one_hundred_batches(monkeypatch):
    log = _patch(monkeypatch, "expire_unlabelled_batch", [], full=1000)

    assert tasks.expire_unlabelled_shadow_pairs.run() == {"deleted": 100 * 1000, "batches": 100}
    assert log.count("commit") == 100


def test_the_body_takes_the_now_it_is_given(monkeypatch):
    import asyncio

    log = _patch(monkeypatch, "expire_unlabelled_batch", [0], full=0)
    now = datetime(2026, 11, 24, 2, 50, tzinfo=timezone.utc)

    assert asyncio.run(tasks._expire_unlabelled_shadow_pairs_async(now)) == {"deleted": 0, "batches": 1}
    assert log[0] == ("batch", now)


def test_a_failing_batch_surfaces_the_modules_handler_shape(monkeypatch):
    async def boom(db, *, now):
        raise ConnectionRefusedError(111, "Connect call failed")

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", lambda: _Session([]), raising=False)
    monkeypatch.setattr(shadow_pair_service, "golden_match_batch", boom)

    exc = tasks.golden_match_shadow_pairs.apply().result

    assert isinstance(exc, RuntimeError)
    assert str(exc) == "ConnectionRefusedError: shadow pair golden match failed"
    assert exc.__suppress_context__ is True
