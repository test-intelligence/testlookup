"""Regression: ``ingest_via_api_key`` 500'd on a concurrent active-run_id create.

Bug pinned (review/stream-service, 2026-06-01):

``LiveSession`` has a partial unique index ``ix_live_sessions_active_run`` on
``(project_id, run_id) WHERE status='active'`` — its model comment says it
"Protects against duplicate event streams when two runners race on the same
run_id." But ``ingest_via_api_key``'s auto-create did ``db.add(session);
await db.flush()`` with no race handling: when two first-batches for the same
run_id arrived concurrently (parallel CI shards / SDK retries sharing a
build-number-derived run_id), both saw ``existing is None``, both inserted an
``active`` session, and the loser's flush raised ``IntegrityError`` — which
propagated uncaught through the router's ``await db.commit()`` as a 500,
dropping that runner's batch.

Fix: the insert is wrapped in a SAVEPOINT (``db.begin_nested()``); on
``IntegrityError`` the service re-selects and reuses the winning session
(``created_session=False``) and skips the post-create Redis/release block,
then still persists the batch onto the shared run.

This file pins:
  * race → reuse the winner, no exception, ``created_session=False``, and the
    Redis token / run-state are NOT re-registered by the loser;
  * happy path (no race) still creates the session and registers Redis state.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.services import stream_service  # noqa: E402


def _result(*, scalar_one_or_none=..., scalar_one=...):
    res = MagicMock()
    if scalar_one_or_none is not ...:
        res.scalar_one_or_none = MagicMock(return_value=scalar_one_or_none)
    if scalar_one is not ...:
        res.scalar_one = MagicMock(return_value=scalar_one)
    return res


def _noop_savepoint_db():
    """An AsyncMock session whose begin_nested() is a no-op async CM that does
    NOT suppress exceptions (so an IntegrityError raised in the body
    propagates to the service's `except`, mirroring a real SAVEPOINT)."""
    db = AsyncMock()
    db.add = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=None)
    cm.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=cm)
    return db


def _request(run_id: str):
    # events=[] → no run_complete, so close_session isn't triggered (keeps the
    # test focused on the create/race path). meta=None exercises the fallbacks.
    return SimpleNamespace(run_id=run_id, events=[], meta=None)


@pytest.mark.asyncio
async def test_ingest_via_api_key_reuses_winner_on_active_runid_race():
    project_id = uuid.uuid4()
    run_id = "ci-build-4711"
    winner = SimpleNamespace(id=uuid.uuid4(), status="active", run_id=run_id, project_id=project_id)

    db = _noop_savepoint_db()
    db.get = AsyncMock(return_value=SimpleNamespace(id=project_id))  # project lookup
    db.execute = AsyncMock(side_effect=[
        _result(scalar_one_or_none=None),  # existing-session lookup → none
        _result(scalar_one=winner),        # re-select after the race → the winner
    ])
    # The insert flush loses the partial-unique race.
    db.flush = AsyncMock(side_effect=IntegrityError("INSERT", {}, Exception("dup active run")))

    fake_redis = AsyncMock()
    with patch.object(stream_service, "get_redis", return_value=fake_redis), \
         patch.object(stream_service, "_persist_event_batch", AsyncMock(return_value=3)):
        resp = await stream_service.ingest_via_api_key(
            db, project_id, "ci-key", _request(run_id),
        )

    # No 500 — the race degraded to reuse.
    assert resp.created_session is False
    assert resp.session_id == str(winner.id)
    assert resp.run_id == run_id
    assert resp.accepted == 3
    # The loser must NOT re-register the Redis token (the winner already did).
    fake_redis.setex.assert_not_called()
    # It tried the insert (and it raced), then re-selected the winner.
    db.add.assert_called_once()
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_ingest_via_api_key_happy_create_still_registers_state():
    """Guard: the no-race path still creates the session + registers Redis."""
    project_id = uuid.uuid4()
    run_id = "ci-build-solo"

    db = _noop_savepoint_db()
    db.get = AsyncMock(return_value=SimpleNamespace(id=project_id))
    db.execute = AsyncMock(side_effect=[
        _result(scalar_one_or_none=None),  # no existing session
    ])
    db.flush = AsyncMock()  # insert succeeds — no race

    fake_redis = AsyncMock()
    start_mock = AsyncMock()
    with patch.object(stream_service, "get_redis", return_value=fake_redis), \
         patch.object(stream_service, "_persist_event_batch", AsyncMock(return_value=1)), \
         patch("app.streams.live_run_state.RedisLiveRunState.start", start_mock):
        resp = await stream_service.ingest_via_api_key(
            db, project_id, "ci-key", _request(run_id),
        )

    assert resp.created_session is True
    assert resp.run_id == run_id
    # The creator registered the session token + live run-state.
    fake_redis.setex.assert_awaited_once()
    start_mock.assert_awaited_once()
    # No re-select happened (only the existing-session lookup ran).
    assert db.execute.await_count == 1
