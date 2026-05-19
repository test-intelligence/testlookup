"""Tests for the ``close_stale_live_sessions`` Celery task + its beat config.

Two contracts to pin:

  1. The beat schedule kwargs (``idle_minutes``) — if someone bumps it
     back to 10 without updating the Live page's ~60s freshness window,
     the "active runs" KPI silently lies again for up to 15 minutes per
     stale session. Pin the value here.

  2. The reaper's staleness heuristic — sessions with a fresh
     ``last_event_at`` are KEPT, sessions past the cutoff are CLOSED.
     This is the safety net for SDK clients that crash mid-run.

DB / Redis access is fully mocked; the task body is the unit under test.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


# ── Beat schedule contract ────────────────────────────────────────────────


def test_beat_schedule_uses_5_minute_idle_threshold():
    """Worst-case staleness ≈ idle_minutes + sweep_interval. The Live page
    promises ~60s freshness on the client side; the server-side reaper
    must catch up within a few minutes so the DB doesn't drift.
    """
    from app.worker.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["close-stale-live-sessions"]
    assert entry["task"] == "app.worker.tasks.close_stale_live_sessions"
    assert entry["kwargs"] == {"idle_minutes": 5}, (
        "idle_minutes regression — see comment in celery_app.py. If you "
        "intentionally raised this (e.g. for slow load tests), update the "
        "Live page freshness comment too so the two surfaces agree."
    )


# ── Reaper staleness logic ────────────────────────────────────────────────


def _mock_session(run_id: str, started_at: datetime, status: str = "active"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        run_id=run_id,
        status=status,
        started_at=started_at,
    )


class _ExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        inner = self
        class _S:
            def all(self_inner):
                return inner._rows
        return _S()


def _make_db(sessions):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_ExecuteResult(sessions))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def test_reaper_keeps_session_with_recent_last_event(monkeypatch):
    """A session whose Redis ``last_event_at`` is within the cutoff must
    NOT be closed — it's still actively reporting."""
    from app.worker import tasks as worker_tasks

    now = datetime.now(timezone.utc)
    fresh = _mock_session("r1", now - timedelta(minutes=2))
    db = _make_db([fresh])

    close_mock = AsyncMock()

    class _FakeAsyncSessionLocal:
        async def __aenter__(self_inner):
            return db
        async def __aexit__(self_inner, *a):
            return False

    monkeypatch.setattr(
        worker_tasks, "_run_async", lambda coro: __import__("asyncio").get_event_loop().run_until_complete(coro)
        if False else __run(coro),
    )

    with patch("app.db.postgres.AsyncSessionLocal", return_value=_FakeAsyncSessionLocal()), \
         patch("app.streams.live_run_state.RedisLiveRunState.get",
               new=AsyncMock(return_value={"last_event_at": (now - timedelta(seconds=30)).isoformat()})), \
         patch("app.services.stream_service.close_session", new=close_mock):
        result = worker_tasks.close_stale_live_sessions.run(idle_minutes=5)

    assert result["closed"] == 0
    assert result["skipped_recent"] == 1
    close_mock.assert_not_called()


def test_reaper_closes_session_past_cutoff(monkeypatch):
    """A session whose ``last_event_at`` is older than the cutoff must be
    closed via the standard ``close_session`` path."""
    from app.worker import tasks as worker_tasks

    now = datetime.now(timezone.utc)
    stale = _mock_session("r2", now - timedelta(minutes=30))
    db = _make_db([stale])

    close_mock = AsyncMock()

    class _FakeAsyncSessionLocal:
        async def __aenter__(self_inner):
            return db
        async def __aexit__(self_inner, *a):
            return False

    with patch("app.db.postgres.AsyncSessionLocal", return_value=_FakeAsyncSessionLocal()), \
         patch("app.streams.live_run_state.RedisLiveRunState.get",
               new=AsyncMock(return_value={"last_event_at": (now - timedelta(minutes=15)).isoformat()})), \
         patch("app.services.stream_service.close_session", new=close_mock):
        result = worker_tasks.close_stale_live_sessions.run(idle_minutes=5)

    assert result["closed"] == 1
    assert result["skipped_recent"] == 0
    close_mock.assert_awaited_once()


def test_reaper_closes_session_with_no_redis_state_past_started_cutoff(monkeypatch):
    """When Redis has no state for a session (TTL evicted OR never
    received an event), the reaper compares ``started_at`` to the cutoff
    so DOA sessions get closed too. Without this branch they accumulate
    indefinitely. Regression repro: 5 sessions started simultaneously
    where 3 never emitted a single event afterward.
    """
    from app.worker import tasks as worker_tasks

    now = datetime.now(timezone.utc)
    doa = _mock_session("r3", now - timedelta(minutes=10))
    db = _make_db([doa])

    close_mock = AsyncMock()

    class _FakeAsyncSessionLocal:
        async def __aenter__(self_inner):
            return db
        async def __aexit__(self_inner, *a):
            return False

    with patch("app.db.postgres.AsyncSessionLocal", return_value=_FakeAsyncSessionLocal()), \
         patch("app.streams.live_run_state.RedisLiveRunState.get",
               new=AsyncMock(return_value=None)), \
         patch("app.services.stream_service.close_session", new=close_mock):
        result = worker_tasks.close_stale_live_sessions.run(idle_minutes=5)

    assert result["closed"] == 1
    close_mock.assert_awaited_once()


def test_reaper_keeps_brand_new_doa_session_within_cutoff(monkeypatch):
    """A session that has no Redis state YET but only started 30s ago
    must NOT be reaped — the first event simply hasn't landed."""
    from app.worker import tasks as worker_tasks

    now = datetime.now(timezone.utc)
    new = _mock_session("r4", now - timedelta(seconds=30))
    db = _make_db([new])

    close_mock = AsyncMock()

    class _FakeAsyncSessionLocal:
        async def __aenter__(self_inner):
            return db
        async def __aexit__(self_inner, *a):
            return False

    with patch("app.db.postgres.AsyncSessionLocal", return_value=_FakeAsyncSessionLocal()), \
         patch("app.streams.live_run_state.RedisLiveRunState.get",
               new=AsyncMock(return_value=None)), \
         patch("app.services.stream_service.close_session", new=close_mock):
        result = worker_tasks.close_stale_live_sessions.run(idle_minutes=5)

    assert result["closed"] == 0
    assert result["skipped_recent"] == 1
    close_mock.assert_not_called()


# ── helper ────────────────────────────────────────────────────────────────


def __run(coro):
    """Adapter for the ``_run_async`` private helper in worker.tasks so we
    don't need to import it directly (its name is intentionally private).
    """
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
