"""Regression guard: a knowledge source cannot be stranded in SYNCING.

The defect
----------
``run_sync`` sets ``sync_status = SYNCING`` and **commits it** before a fetch
+ chunk + embed that can run for minutes. Every terminal write (SYNCED /
FAILED) lives in an ``except`` block -- and a process death runs no ``except``
block. An OOM kill, a pod eviction, a worker redeploy, or Celery's hard
``task_time_limit`` all leave the row on SYNCING.

Nothing rescued it. ``list_stale_sources`` -- the only re-sync sweep -- carries
``sync_status != SYNCING`` deliberately, to stop two syncs running at once. So
the one row that most needed re-syncing was the one row permanently excluded
from re-syncing: its RAG chunks froze at the last good sync, answers kept
citing content that had moved on, and the UI showed a sync still "in progress"
indefinitely. Silent in logs, because nothing failed.

Same shape as the re-quarantine dead end (#780) and as the stuck agent
pipelines that ``reap_stuck_agent_pipelines`` already ages out: **a live state
with no exit.**

What is guarded
---------------
Not "a reaper function exists" -- the three properties that make it work:

1. It only reaps what is genuinely dead. A sync younger than the cutoff is
   left running. Reaping a live sync would re-enqueue a source alongside
   itself and double-index it.
2. The cutoff stays above Celery's hard ``task_time_limit``. This is the
   invariant the constant depends on, and it is invisible by reading either
   file alone -- lower the limit in one place and this fails.
3. The status it writes is one ``list_stale_sources`` actually selects.
   Reaping into a status nothing sweeps would move the row from one dead end
   to another and still look like a fix.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.regression

pytest.importorskip("sqlalchemy")

from app.core.config import settings  # noqa: E402
from app.models.postgres import KnowledgeSyncStatus  # noqa: E402
from app.services import knowledge_sync_service as svc  # noqa: E402


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


class _FakeDB:
    """Captures the statement and any rows staged by the caller."""

    def __init__(self, rows):
        self._rows = rows
        self.statements = []
        self.added = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _ScalarsResult(self._rows)

    def add(self, obj):
        self.added.append(obj)


def _source(**kw):
    import uuid

    base = dict(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        sync_status=KnowledgeSyncStatus.SYNCING.value,
        sync_error=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _bound_values(stmt):
    """Every bound parameter, flattened -- an in_() binds a whole list as one."""
    out = []
    for value in stmt.compile().params.values():
        if isinstance(value, (list, tuple, set)):
            out.extend(value)
        else:
            out.append(value)
    return out


@pytest.mark.asyncio
async def test_a_stranded_syncing_source_is_failed_and_becomes_sweepable():
    row = _source()
    db = _FakeDB([row])

    reaped = await svc.reap_stuck_syncing_sources(db, stuck_minutes=45)

    assert reaped == [row]
    assert row.sync_status == KnowledgeSyncStatus.FAILED.value, (
        "the reaper selected the stranded row but left it on SYNCING, which is "
        "the exact status list_stale_sources excludes -- it is still stuck"
    )
    assert row.sync_error, "a reaped row must say why, or the UI shows a bare failure"
    assert db.added, "no KnowledgeSyncEvent recorded -- the recovery is unauditable"

    bound = [str(v) for v in _bound_values(db.statements[0])]
    assert KnowledgeSyncStatus.SYNCING.value in bound, (
        "the reaper does not filter on SYNCING; bound values: " + repr(bound)
    )


@pytest.mark.asyncio
async def test_the_cutoff_is_an_age_and_not_every_syncing_row():
    """A sync that is merely slow must survive. Reaping it would double-index."""
    db = _FakeDB([])
    before = datetime.now(timezone.utc)
    await svc.reap_stuck_syncing_sources(db, stuck_minutes=45)
    after = datetime.now(timezone.utc)

    cutoffs = [v for v in _bound_values(db.statements[0]) if isinstance(v, datetime)]
    assert cutoffs, (
        "the reaper binds no timestamp, so it matches EVERY SYNCING row "
        "including syncs that are still running"
    )
    cutoff = cutoffs[0]
    assert before - timedelta(minutes=45) - timedelta(seconds=5) <= cutoff
    assert cutoff <= after - timedelta(minutes=45) + timedelta(seconds=5)


def test_the_cutoff_outlives_celerys_hard_time_limit():
    """The invariant the constant rests on, and it spans two files.

    Celery SIGKILLs a task at ``task_time_limit``. A reaper cutoff below that
    would fail syncs that are still legitimately running.
    """
    from app.worker.celery_app import celery_app

    hard_limit_s = celery_app.conf.task_time_limit
    assert hard_limit_s, "no task_time_limit configured; this guard cannot compare"

    cutoff_s = settings.KNOWLEDGE_SYNC_STUCK_MINUTES * 60
    assert cutoff_s > hard_limit_s, (
        "KNOWLEDGE_SYNC_STUCK_MINUTES ("
        + str(settings.KNOWLEDGE_SYNC_STUCK_MINUTES)
        + "min = " + str(cutoff_s) + "s) is not above Celery's hard "
        "task_time_limit (" + str(hard_limit_s) + "s). The reaper would mark a "
        "sync dead while the worker is still running it, then re-enqueue the "
        "source alongside itself."
    )


@pytest.mark.asyncio
async def test_the_reaped_status_is_one_the_resync_sweep_selects():
    """Reaping into a status nothing sweeps swaps one dead end for another."""
    row = _source()
    await svc.reap_stuck_syncing_sources(_FakeDB([row]), stuck_minutes=45)
    reaped_status = row.sync_status

    db = _FakeDB([])
    await svc.list_stale_sources(db)

    selected = set()
    for stmt in db.statements:
        selected.update(str(v) for v in _bound_values(stmt))

    assert reaped_status in selected, (
        "the reaper writes " + reaped_status + " but list_stale_sources never "
        "selects it, so the row is still never re-synced. Statuses the sweep "
        "binds: " + repr(sorted(s for s in selected if s.islower() and "-" not in s))
    )
