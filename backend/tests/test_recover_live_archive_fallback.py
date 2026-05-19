"""Regression tests for the 15-day event-archive fallback on
``POST /api/v1/runs/{run_id}/recover-live``.

Before this fix, the endpoint only consulted the Redis buffer
(``LIVE_TESTCASES_KEY``) which has a 25-hour TTL. Live runs older than
that lost their per-test recovery path entirely — the user-reported
symptom was "the SDK event buffer for this run is empty or has expired".

The fix: at ``close_session`` time we copy the buffer to
``TestRun.event_archive`` (migration 0086), retained for 15 days. The
recover endpoint now falls back to that archive when Redis is empty
and stages the events back into Redis so the existing persistence
task can keep its single read path.

These tests pin:

  1. Fresh Redis buffer ⇒ endpoint uses Redis (no archive read).
  2. Empty Redis + valid archive (< 15 days) ⇒ endpoint replays from
     archive, returns ``source == "archive"``.
  3. Empty Redis + expired archive (> 15 days) ⇒ 422 with the new
     "older than the 15-day recovery window" message.
  4. Empty Redis + no archive at all ⇒ 422 with the same message.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run_row(*, event_archive=None, event_archive_at=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        trigger_source="live_stream",
        build_number="b1",
        branch=None,
        commit_hash=None,
        passed_tests=2,
        failed_tests=1,
        skipped_tests=0,
        broken_tests=0,
        total_tests=3,
        event_archive=event_archive,
        event_archive_at=event_archive_at,
        # Added 2026-05-19: ``routers/runs.py::recover_live_run_from_buffer``
        # reads ``run.primary_suite_name`` when building the
        # ``persist_live_session.apply_async`` kwargs. Without this
        # attribute the call raises AttributeError on the SimpleNamespace
        # — surfacing as "test_recover_uses_redis_when_buffer_is_fresh"
        # failure even though the recovery path itself is unchanged.
        primary_suite_name=None,
    )


def _mk_db(run_row, tc_count: int = 0):
    """Stub ``db.execute`` to return run_row then a TC count then nothing."""
    db = AsyncMock()
    seq = [
        SimpleNamespace(scalar_one_or_none=MagicMock(return_value=run_row)),
        SimpleNamespace(scalar=MagicMock(return_value=tc_count)),
    ]
    db.execute = AsyncMock(side_effect=seq)
    return db


@pytest.mark.asyncio
async def test_recover_uses_redis_when_buffer_is_fresh():
    """Sanity: with a populated Redis buffer the endpoint replays from
    there and never touches the archive."""
    from app.routers.runs import recover_live_run_from_buffer

    run = _run_row(event_archive=None, event_archive_at=None)
    db = _mk_db(run)

    redis = AsyncMock()
    redis.llen = AsyncMock(return_value=12)
    redis.rpush = AsyncMock()
    redis.expire = AsyncMock()

    with patch("app.db.redis_client.get_redis", return_value=redis), \
         patch("app.worker.tasks.persist_live_session") as task_mock:
        task_mock.apply_async = MagicMock()
        result = await recover_live_run_from_buffer(
            run_id=run.id, db=db, _=SimpleNamespace(id=uuid.uuid4()),
        )

    assert result["queued"] is True
    assert result["source"] == "redis"
    assert result["buffered_events"] == 12
    # Archive path didn't fire — rpush only happens when staging the
    # archive back into Redis.
    redis.rpush.assert_not_called()
    task_mock.apply_async.assert_called_once()


@pytest.mark.asyncio
async def test_recover_falls_back_to_archive_within_15_days():
    """Empty Redis + a 5-day-old archive ⇒ events are staged back into
    Redis and the persistence task is queued. The response surfaces
    ``source: "archive"`` so the UI can show "replayed from archive"."""
    from fastapi import HTTPException  # noqa: F401 — ensure router import works
    from app.routers.runs import recover_live_run_from_buffer

    events = [
        {"test_name": "test_a", "status": "PASSED"},
        {"test_name": "test_b", "status": "FAILED"},
        {"test_name": "test_c", "status": "PASSED"},
    ]
    archived_at = datetime.now(timezone.utc) - timedelta(days=5)
    run = _run_row(event_archive=events, event_archive_at=archived_at)
    db = _mk_db(run)

    redis = AsyncMock()
    redis.llen = AsyncMock(return_value=0)  # buffer expired
    redis.rpush = AsyncMock()
    redis.expire = AsyncMock()

    with patch("app.db.redis_client.get_redis", return_value=redis), \
         patch("app.worker.tasks.persist_live_session") as task_mock:
        task_mock.apply_async = MagicMock()
        result = await recover_live_run_from_buffer(
            run_id=run.id, db=db, _=SimpleNamespace(id=uuid.uuid4()),
        )

    assert result["queued"] is True
    assert result["source"] == "archive"
    assert result["buffered_events"] == len(events)
    # Each event got pushed individually to preserve the same on-the-
    # wire JSON shape that the SDK originally writes.
    assert redis.rpush.await_count == len(events)
    # Short TTL on the staging key so it doesn't pile up.
    redis.expire.assert_awaited()
    task_mock.apply_async.assert_called_once()


@pytest.mark.asyncio
async def test_recover_rejects_archive_older_than_15_days():
    """Empty Redis + archive >15 days old ⇒ 422 with the new wording.
    Pins that we don't silently 'succeed' on stale data."""
    from fastapi import HTTPException
    from app.routers.runs import recover_live_run_from_buffer

    archived_at = datetime.now(timezone.utc) - timedelta(days=20)
    run = _run_row(
        event_archive=[{"test_name": "x", "status": "PASSED"}],
        event_archive_at=archived_at,
    )
    db = _mk_db(run)

    redis = AsyncMock()
    redis.llen = AsyncMock(return_value=0)
    redis.rpush = AsyncMock()
    redis.expire = AsyncMock()

    with patch("app.db.redis_client.get_redis", return_value=redis):
        with pytest.raises(HTTPException) as exc:
            await recover_live_run_from_buffer(
                run_id=run.id, db=db, _=SimpleNamespace(id=uuid.uuid4()),
            )

    assert exc.value.status_code == 422
    # The new wording must mention the 15-day window so the user knows
    # WHY recovery failed (not just "buffer expired").
    assert "15-day recovery window" in exc.value.detail


@pytest.mark.asyncio
async def test_recover_rejects_when_no_redis_and_no_archive():
    """Empty Redis + no archive at all ⇒ 422 with the same wording.
    Mirrors the behaviour of an older live run that pre-dates the
    archive feature, but with a clearer error than the previous
    'buffer expired' phrasing."""
    from fastapi import HTTPException
    from app.routers.runs import recover_live_run_from_buffer

    run = _run_row(event_archive=None, event_archive_at=None)
    db = _mk_db(run)

    redis = AsyncMock()
    redis.llen = AsyncMock(return_value=0)

    with patch("app.db.redis_client.get_redis", return_value=redis):
        with pytest.raises(HTTPException) as exc:
            await recover_live_run_from_buffer(
                run_id=run.id, db=db, _=SimpleNamespace(id=uuid.uuid4()),
            )

    assert exc.value.status_code == 422
    assert "15-day recovery window" in exc.value.detail
    # The previous "buffer is empty or has expired" wording is gone —
    # pin that so a future revert can't re-introduce the old message
    # that hid the durable-archive recovery option.
    assert "25-hour TTL" not in exc.value.detail
