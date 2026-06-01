"""Regression: ``get_run_intelligence`` shared a single ``AsyncSession``
across ``asyncio.gather``ed queries.

Bug pinned (review/run-intelligence-service, 2026-06-01):

``get_run_intelligence`` launched eight coroutines through
``asyncio.gather``; six of them issued ``await db.execute(...)`` on the
*same* injected ``AsyncSession``.  An ``AsyncSession`` is not safe for
concurrent use — the moment the first query yields on I/O the event loop
starts the next, which finds the session mid-operation and raises::

    sqlalchemy.exc.InvalidRequestError: This session is provisioning a
    new connection; concurrent operations are not permitted

That error is not a ``ValueError``, so the router's ``except ValueError``
did not catch it → HTTP 500 on every live-compute path
(``/intelligence`` cache-miss, ``/intelligence/refresh``, ``/export``).
The snapshot cache (only written *after* a successful compute) could
therefore never warm, making the failure permanent.

Fix landed: the single MongoDB read still overlaps the PG work via
``gather`` (independent client), but the PostgreSQL queries now run
sequentially on the one shared session.

What this file pins:

  * ``get_run_intelligence`` never issues two overlapping ``execute``
    calls on its injected session (the guard below would trip otherwise).
  * The guard itself genuinely detects concurrency — so the first test's
    pass is meaningful, not vacuous.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _result(*, scalar=None, rows=None):
    """A fake ``Result`` supporting the three access patterns the service uses."""
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=scalar)
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=list(rows or []))
    res.scalars = MagicMock(return_value=scalars)
    res.all = MagicMock(return_value=list(rows or []))
    return res


class _GuardedSession:
    """Fake ``AsyncSession`` that raises if two ``execute`` calls overlap.

    Mirrors SQLAlchemy's real "concurrent operations are not permitted"
    guard.  Each ``execute`` flips ``_active`` and yields the event loop
    (``await asyncio.sleep(0)``); if a second call enters while the first
    is still active, the bug is present.
    """

    def __init__(self, results):
        self._results = list(results)
        self._i = 0
        self._active = False
        self.concurrent_detected = False
        self.execute_count = 0

    async def execute(self, *args, **kwargs):
        if self._active:
            self.concurrent_detected = True
            raise AssertionError(
                "concurrent operations are not permitted on a shared AsyncSession"
            )
        self._active = True
        try:
            await asyncio.sleep(0)  # yield — a gathered sibling would re-enter here
            res = self._results[self._i] if self._i < len(self._results) else _result()
            self._i += 1
            self.execute_count += 1
            return res
        finally:
            self._active = False


class _FakeMongoCollection:
    async def find_one(self, *args, **kwargs):
        await asyncio.sleep(0)  # genuine yield, so Mongo||PG overlap is exercised
        return None


class _FakeMongo:
    def __getitem__(self, _name):
        return _FakeMongoCollection()


def _make_run():
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        build_number="b-100",
        branch="main",
        status="COMPLETED",
        total_tests=10,
        passed_tests=10,
        failed_tests=0,
        skipped_tests=0,
        broken_tests=0,
        pass_rate=100.0,
        duration_ms=1234,
        start_time=None,
        end_time=None,
        ocp_namespace=None,
    )


@pytest.mark.asyncio
async def test_get_run_intelligence_no_overlapping_session_use():
    """The bug: gathered queries collided on one session. Now they don't."""
    from app.services.run_intelligence_service import get_run_intelligence

    run = _make_run()
    # First execute is the run fetch; the rest of the PG bundle returns empties.
    db = _GuardedSession(results=[_result(scalar=run)])

    with patch(
        "app.services.run_intelligence_service.get_baseline_diff",
        AsyncMock(return_value=None),
    ):
        result = await get_run_intelligence(run.id, db, _FakeMongo())

    assert db.concurrent_detected is False, (
        "two execute() calls overlapped on the shared session — the gather "
        "regression is back"
    )
    assert isinstance(result, dict)
    assert result["run"]["id"] == str(run.id)
    # With no clusters/summary/analyses, the payload still assembles cleanly.
    assert result["failure_clusters"] == []
    assert result["category_breakdown"] == {}
    assert result["all_green"] is True
    # More than one PG query ran — proving the bundle actually executed serially.
    assert db.execute_count >= 2


@pytest.mark.asyncio
async def test_guard_detects_real_concurrency():
    """Meta-test: the guard trips when two executes are genuinely gathered,
    so the test above is not vacuously green."""
    db = _GuardedSession(results=[_result(), _result()])

    async def _q():
        return await db.execute("SELECT 1")

    with pytest.raises(AssertionError, match="concurrent operations"):
        await asyncio.gather(_q(), _q())

    assert db.concurrent_detected is True
