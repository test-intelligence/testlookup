"""W25e E-1 T-A3 — every live close publishes its staged persist right after its commit.

Before W25e a closed run's ``persist_live_session`` waited for the next relay
tick (0-60 s). The close paths now call ``publish_staged_now`` AFTER the
close's commit and its Redis finalize: the SDK ``DELETE`` close, the API-key
``/ingest`` with ``run_complete``, and the stale-session reaper (the
``/ws/events`` close is G-B's, tested in ``tests/test_live_ws_pacing.py``).
A commit that fails must publish nothing.

The reaper case also pins deviation D-A1: ``publish_staged_now`` can end with a
rollback, and a rollback expires every ORM row of the sweep's session — the
loop must not read an expired row afterwards.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.regression

_USER = (SimpleNamespace(role="QA_ENGINEER"), None)


def _recording_publish(order: list[str], calls: list[tuple]):
    async def _publish(db, *, run_id, operations=("persist_live_session",)):
        order.append("publish_now")
        calls.append((db, run_id))
        return "published"

    return _publish


# ── SDK close: DELETE /api/v1/stream/sessions/{session_id} ────────────────────


@pytest.mark.asyncio
async def test_sdk_close_publishes_after_commit_and_redis_finalize(monkeypatch):
    from app.routers import stream as stream_router
    from app.worker import downstream_dispatch

    session_id = "22222222-2222-2222-2222-222222222222"
    order: list[str] = []
    calls: list[tuple] = []
    db = type("DB", (), {})()
    db.commit = AsyncMock(side_effect=lambda: order.append("commit"))
    monkeypatch.setattr(
        stream_router.stream_service,
        "close_session",
        AsyncMock(side_effect=lambda *_a, **_k: order.append("close")),
    )
    monkeypatch.setattr(
        stream_router.stream_service,
        "finalize_closed_session_redis",
        AsyncMock(side_effect=lambda *_a: order.append("finalize")),
    )
    monkeypatch.setattr(
        downstream_dispatch, "publish_staged_now", _recording_publish(order, calls)
    )

    await stream_router.close_session(session_id, db, _USER)

    assert order == ["close", "commit", "finalize", "publish_now"]
    assert calls == [(db, session_id)]


@pytest.mark.asyncio
async def test_sdk_close_whose_commit_fails_publishes_nothing(monkeypatch):
    from app.routers import stream as stream_router
    from app.worker import downstream_dispatch

    db = type("DB", (), {})()
    db.commit = AsyncMock(side_effect=RuntimeError("commit failed"))
    finalize = AsyncMock()
    publish = AsyncMock(return_value="published")
    monkeypatch.setattr(
        stream_router.stream_service, "close_session", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        stream_router.stream_service, "finalize_closed_session_redis", finalize
    )
    monkeypatch.setattr(downstream_dispatch, "publish_staged_now", publish)

    with pytest.raises(RuntimeError, match="commit failed"):
        await stream_router.close_session(
            "22222222-2222-2222-2222-222222222222", db, _USER
        )

    finalize.assert_not_awaited()
    publish.assert_not_awaited()


# ── API-key close: POST /api/v1/stream/ingest with run_complete ───────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type, publishes", [("run_complete", True), ("test_result", False)])
async def test_api_key_ingest_publishes_only_when_it_closed_the_run(
    monkeypatch, event_type, publishes
):
    from app.routers import stream as stream_router
    from app.services import cache_service
    from app.worker import downstream_dispatch

    order: list[str] = []
    calls: list[tuple] = []
    db = type("DB", (), {})()
    db.commit = AsyncMock(side_effect=lambda: order.append("commit"))
    session_id = "33333333-3333-3333-3333-333333333333"
    monkeypatch.setattr(stream_router, "enforce_redis_memory_backpressure", AsyncMock())
    monkeypatch.setattr(stream_router, "enforce_ingest_rate_limit", AsyncMock())
    monkeypatch.setattr(
        stream_router.stream_service,
        "ingest_via_api_key",
        AsyncMock(return_value=SimpleNamespace(session_id=session_id)),
    )
    monkeypatch.setattr(
        stream_router.stream_service,
        "finalize_closed_session_redis",
        AsyncMock(side_effect=lambda *_a: order.append("finalize")),
    )
    monkeypatch.setattr(
        cache_service,
        "bump_analytics_epoch",
        AsyncMock(side_effect=lambda *_a: order.append("bump")),
    )
    monkeypatch.setattr(
        downstream_dispatch, "publish_staged_now", _recording_publish(order, calls)
    )

    response = await stream_router.ingest_via_api_key(
        payload=SimpleNamespace(events=[{"event_type": event_type}]),
        db=db,
        auth=SimpleNamespace(project_id=uuid.uuid4(), api_key_name="zz-key-q"),
    )

    assert response.session_id == session_id
    if publishes:
        assert order == ["commit", "bump", "finalize", "publish_now"]
        assert calls == [(db, session_id)]
    else:
        assert order == ["commit"]
        assert calls == []


# ── The stale-session reaper ───────────────────────────────────────────────────


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


class _ReaperDB:
    """The sweep's session: commit / rollback journaled; one close can fail."""

    def __init__(self, sessions, order):
        self.sessions = sessions
        self.order = order
        self.fail_commit = False
        self.expired = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, *_a, **_k):
        return _Rows(self.sessions)

    async def commit(self):
        if self.fail_commit:
            raise RuntimeError("zz-commit-failed-q")
        self.order.append("commit")

    async def rollback(self):
        # A SQLAlchemy rollback expires every instance in the identity map,
        # whatever expire_on_commit says (commit does not: expire_on_commit=False).
        self.expired = True
        self.order.append("rollback")


class _OrmLikeSession:
    """A LiveSession row that, once its session rolled back, cannot be read
    outside the greenlet — what an expired AsyncSession instance does."""

    def __init__(self, db_ref: dict, **fields):
        self.__dict__["_db_ref"] = db_ref
        self.__dict__["_fields"] = fields

    def __getattr__(self, name):
        db = self.__dict__["_db_ref"]["db"]
        if db is not None and db.expired:
            raise RuntimeError("zz-expired-row-q (MissingGreenlet)")
        try:
            return self.__dict__["_fields"][name]
        except KeyError:
            raise AttributeError(name) from None


def _run_reaper(monkeypatch, db, *, close, publish=None):
    import app.db.postgres as pg
    from app.services import cache_service, stream_service
    from app.worker import downstream_dispatch, tasks

    monkeypatch.setattr(tasks, "_run_async", asyncio.run)
    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: db)
    monkeypatch.setattr(
        "app.streams.live_run_state.RedisLiveRunState.get", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(stream_service, "close_session", close)
    monkeypatch.setattr(
        stream_service,
        "finalize_closed_session_redis",
        AsyncMock(side_effect=lambda *_a: db.order.append("finalize")),
    )
    monkeypatch.setattr(cache_service, "bump_analytics_epochs", AsyncMock())
    if publish is not None:
        monkeypatch.setattr(downstream_dispatch, "publish_staged_now", publish)
    return tasks.close_stale_live_sessions.run(idle_minutes=5)


def test_reaper_publishes_once_per_committed_close_and_never_for_a_failed_one(monkeypatch):
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    sessions = [
        SimpleNamespace(id=uuid.uuid4(), status="active", started_at=old, project_id=uuid.uuid4())
        for _ in range(3)
    ]
    order: list[str] = []
    calls: list[tuple] = []
    db = _ReaperDB(sessions, order)
    fail_on = sessions[1].id

    async def _close(session_db, session_id, **_k):
        assert session_db is db
        order.append("close")
        db.fail_commit = session_id == str(fail_on)
        return uuid.uuid4()

    result = _run_reaper(
        monkeypatch, db, close=_close, publish=_recording_publish(order, calls)
    )

    assert result["closed"] == 2 and result["errors"] == 1
    assert calls == [(db, sessions[0].id), (db, sessions[2].id)], (
        "one publish per COMMITTED close, on the sweep's own session, keyed by "
        "the session id (= the canonical run id the persist row is staged under)"
    )
    assert order == [
        "close", "commit", "finalize", "publish_now",
        "close", "rollback",
        "close", "commit", "finalize", "publish_now",
    ]


def test_reaper_survives_the_publish_rollback_expiring_its_rows(monkeypatch):
    """D-A1: the REAL publish ends with a rollback here (nothing claimable), which
    expires every LiveSession the sweep loaded. The next iteration must not read
    one; before the fix it raised out of the except branch's log line."""
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    db_ref: dict = {"db": None}
    sessions = [
        _OrmLikeSession(db_ref, id=uuid.uuid4(), status="active", started_at=old)
        for _ in range(3)
    ]
    order: list[str] = []
    db = _ReaperDB(sessions, order)
    db_ref["db"] = db
    closed_ids: list[str] = []

    async def _close(session_db, session_id, **_k):
        closed_ids.append(session_id)
        order.append("close")
        return None

    result = _run_reaper(monkeypatch, db, close=_close)

    assert result == {
        "checked": 3, "closed": 3, "skipped_recent": 0, "errors": 0, "idle_minutes": 5,
    }
    assert closed_ids == [str(s._fields["id"]) for s in sessions]
    assert order.count("rollback") == 3, "each publish found nothing and rolled back"
