"""W22b ACT-06 W-3 — the hourly sweep's wiring: the selector, the lock, the beat.

``retention_service.due_project_ids`` (G-C) is the ONLY thing that selects the
beat's projects; an explicit project (the manual "Purge now") ignores the
window and the pause but still takes the per-project lock; the lock fails
CLOSED (Redis down -> the project is skipped, no job, no purge).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from celery.schedules import crontab

from app.services import deletion_job_service, retention_service
from app.services.retention_vocab import PURGE_LOCK_KEY, PURGE_LOCK_TTL_SECONDS
from app.worker import tasks


class _NoSelectSession:
    """A session the sweep may open but must not query for its targets itself."""

    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, *_a, **_kw):
        raise AssertionError("the sweep selected projects itself instead of due_project_ids")

    def add(self, obj):
        self.log.append(("add", getattr(obj, "action", None)))

    async def commit(self):
        self.log.append(("commit", None))


@pytest.fixture
def sweep(monkeypatch):
    import app.db.postgres as pg

    s = SimpleNamespace(log=[], purged=[], due=[], lock="acquired", purge_error=None)
    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: _NoSelectSession(s.log))
    s.due_calls = AsyncMock(side_effect=lambda db, *, now: list(s.due))
    monkeypatch.setattr(retention_service, "due_project_ids", s.due_calls)

    async def _purge(db, *, project_id, mode):
        if s.purge_error:
            raise s.purge_error
        s.purged.append((project_id, mode))
        return {"counts": {"postgres": {"runs": 1}}, "cutoffs": {}}

    monkeypatch.setattr(retention_service, "run_purge", _purge)
    s.open = AsyncMock(return_value=uuid.uuid4())
    s.close = AsyncMock(return_value=True)
    monkeypatch.setattr(deletion_job_service, "open_job", s.open)
    monkeypatch.setattr(deletion_job_service, "close_job", s.close)
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", AsyncMock())
    monkeypatch.setattr(tasks, "_record_retention_purge", AsyncMock())
    s.claim = AsyncMock(side_effect=lambda pid, owner: s.lock)
    s.release = AsyncMock()
    monkeypatch.setattr(tasks, "_claim_retention_purge_lock", s.claim)
    monkeypatch.setattr(tasks, "_release_retention_purge_lock", s.release)
    return s


@pytest.mark.asyncio
async def test_the_beat_path_selects_through_due_project_ids_only(sweep):
    a, b = uuid.uuid4(), uuid.uuid4()
    sweep.due = [a, b]
    summary = await tasks._retention_purge_sweep(None, owner="beat-1")
    assert sweep.due_calls.await_count == 1
    assert sweep.due_calls.await_args.kwargs["now"].tzinfo is not None
    assert [p for p, _m in sweep.purged] == [a, b] and summary["projects"] == 2
    assert summary["skipped"] == {}
    assert [c.args for c in sweep.claim.await_args_list] == [(a, "beat-1"), (b, "beat-1")]


@pytest.mark.asyncio
async def test_the_explicit_path_ignores_the_window_and_the_pause(sweep):
    pid = uuid.uuid4()
    sweep.due = []          # not due this hour (window elsewhere, or paused)
    summary = await tasks._retention_purge_sweep(str(pid))
    sweep.due_calls.assert_not_awaited()
    assert sweep.purged == [(pid, "execute")] and summary["projects"] == 1
    owner = sweep.claim.await_args.args[1]
    assert isinstance(owner, str) and owner, "a fresh owner when the caller gives none"


@pytest.mark.parametrize("lock, reason", [("held", "already_running"), ("unavailable", "lock_unavailable")])
@pytest.mark.asyncio
async def test_a_project_without_the_lock_is_skipped_with_no_job_and_no_purge(lock, reason, sweep):
    pid = uuid.uuid4()
    sweep.lock = lock
    summary = await tasks._retention_purge_sweep(str(pid))
    assert summary["skipped"] == {str(pid): reason}
    assert summary["projects"] == 1 and summary["results"] == {}
    sweep.open.assert_not_awaited()
    sweep.close.assert_not_awaited()
    assert sweep.purged == [] and ("add", "purge") not in sweep.log
    sweep.release.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_lock_is_released_after_a_failing_purge(sweep):
    pid = uuid.uuid4()
    sweep.purge_error = RuntimeError("mongo unreachable")
    summary = await tasks._retention_purge_sweep(str(pid), owner="manual-7")
    assert summary["errors"] == 1
    assert sweep.release.await_args.args == (pid, "manual-7")
    assert sweep.close.await_args.kwargs["status"] == deletion_job_service.FAILED


def test_the_lock_comes_before_the_job_and_the_purge():
    import inspect

    source = inspect.getsource(tasks._retention_purge_sweep)
    lock = source.find("_claim_retention_purge_lock(")
    opened = source.find("deletion_job_service.open_job(")
    purged = source.find("await retention_service.run_purge(")
    released = source.find("_release_retention_purge_lock(")
    assert -1 not in (lock, opened, purged, released)
    assert lock < opened < purged < released
    assert "finally:" in source[purged:released]


# ── the lock helper itself (module-level, over the dedup lock) ───────────────


@pytest.mark.parametrize("duplicate, state", [(False, "acquired"), (True, "held")])
@pytest.mark.asyncio
async def test_the_lock_maps_the_dedup_answer(duplicate, state, monkeypatch):
    seen = {}

    async def _dup(key, ttl=3600, owner=None):
        seen.update(key=key, ttl=ttl, owner=owner)
        return duplicate

    monkeypatch.setattr(tasks, "_is_duplicate", _dup)
    pid = uuid.uuid4()
    assert await tasks._claim_retention_purge_lock(pid, "owner-1") == state
    assert seen == {"key": PURGE_LOCK_KEY.format(project_id=pid), "ttl": PURGE_LOCK_TTL_SECONDS, "owner": "owner-1"}
    assert seen["key"] == f"testlookup:lock:retention-purge:{pid}"


@pytest.mark.asyncio
async def test_an_unreachable_redis_is_unavailable_never_acquired(monkeypatch):
    monkeypatch.setattr(tasks, "_is_duplicate", AsyncMock(side_effect=ConnectionError("redis down")))
    assert await tasks._claim_retention_purge_lock(uuid.uuid4(), "o") == "unavailable"
    monkeypatch.setattr(tasks, "_release_duplicate_lock", AsyncMock(side_effect=ConnectionError("down")))
    await tasks._release_retention_purge_lock(uuid.uuid4(), "o")   # never raises over the purge


# ── the beat ─────────────────────────────────────────────────────────────────


def test_the_purge_beat_is_hourly_and_the_janitor_runs_every_fifteen_minutes():
    from app.worker.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    assert "nightly-retention-purge" not in schedule
    hourly = schedule["hourly-retention-purge"]
    assert hourly["task"] == "app.worker.tasks.run_retention_purges"
    assert hourly["schedule"] == crontab(minute=0)
    janitor = schedule["sweep-deletion-exports"]
    assert janitor["task"] == "app.worker.tasks.sweep_deletion_exports"
    assert janitor["schedule"] == crontab(minute="*/15")
    assert janitor["options"]["queue"] == "default"
    assert "app.worker.tasks.sweep_deletion_exports" in celery_app.tasks
