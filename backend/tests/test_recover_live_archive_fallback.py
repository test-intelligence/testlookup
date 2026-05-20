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
  3. Empty Redis + EXPIRED archive (> 15 days) but non-zero
     aggregates ⇒ the stale archive is NOT replayed; the endpoint
     degrades to ``source == "synthesis"`` so the user still gets
     placeholder per-test rows. (Updated 2026-05-20: the recover
     endpoint gained a synthesis fallback — see
     ``tests/regression/test_live_run_recovery_synthesis.py`` and
     ``feedback_auto_recover_live_runs``. Previously this 422'd.)
  4. Empty Redis + no archive AND zero aggregates ⇒ 422, because
     there is genuinely nothing to recover or synthesise from. Pins
     the new "no aggregates to synthesise from" wording and that the
     stale "25-hour TTL" phrasing stays gone.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run_row(
    *,
    event_archive=None,
    event_archive_at=None,
    passed_tests=2,
    failed_tests=1,
    skipped_tests=0,
    broken_tests=0,
):
    # Aggregates are parametrised (2026-05-20): the recover endpoint now
    # only 422s when buffer + archive are empty AND every aggregate is
    # zero. A run with reported tests degrades to synthesis instead, so
    # tests that want to assert the 422 must pass all-zero aggregates.
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        trigger_source="live_stream",
        build_number="b1",
        branch=None,
        commit_hash=None,
        passed_tests=passed_tests,
        failed_tests=failed_tests,
        skipped_tests=skipped_tests,
        broken_tests=broken_tests,
        total_tests=passed_tests + failed_tests + skipped_tests + broken_tests,
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
async def test_recover_ignores_expired_archive_falls_back_to_synthesis():
    """Empty Redis + archive >15 days old + non-zero aggregates ⇒ the
    stale archive must NOT be replayed (it would resurrect 3-week-old
    per-test rows the SDK has long since superseded), and the endpoint
    must NOT 422 — it degrades to ``source == "synthesis"`` so the
    user still gets placeholder rows from the run's aggregates.

    Pins two things:
      * Staleness is enforced — ``rpush`` (the archive-replay staging
        write) is never called for an out-of-window archive.
      * The recover path stays usable for old runs: synthesis covers
        the gap rather than dead-ending the user on a 422."""
    from app.routers.runs import recover_live_run_from_buffer

    archived_at = datetime.now(timezone.utc) - timedelta(days=20)
    run = _run_row(
        event_archive=[{"test_name": "x", "status": "PASSED"}],
        event_archive_at=archived_at,
        passed_tests=2,
        failed_tests=1,
    )
    db = _mk_db(run)

    redis = AsyncMock()
    redis.llen = AsyncMock(return_value=0)
    redis.rpush = AsyncMock()
    redis.expire = AsyncMock()

    with patch("app.db.redis_client.get_redis", return_value=redis), \
         patch("app.worker.tasks.persist_live_session") as task_mock, \
         patch("app.worker.ingestion_routing.queue_for_project",
               return_value="ingestion.shard.0"):
        task_mock.apply_async = MagicMock()
        result = await recover_live_run_from_buffer(
            run_id=run.id, db=db, _=SimpleNamespace(id=uuid.uuid4()),
        )

    assert result["queued"] is True
    assert result["source"] == "synthesis"
    assert result["buffered_events"] == 0
    # The stale archive must not be staged back into Redis.
    redis.rpush.assert_not_called()
    # The persist task is still queued so its synthesis branch fires.
    task_mock.apply_async.assert_called_once()


@pytest.mark.asyncio
async def test_recover_rejects_when_no_redis_no_archive_and_zero_aggregates():
    """Empty Redis + no archive + ZERO aggregates ⇒ 422. With nothing
    buffered, nothing archived, and no reported tests, there is
    genuinely nothing to recover or synthesise from.

    Pins the new wording ("no aggregates to synthesise from") and that
    the stale "25-hour TTL" phrasing — which hid the durable-archive +
    synthesis recovery options — stays gone."""
    from fastapi import HTTPException
    from app.routers.runs import recover_live_run_from_buffer

    run = _run_row(
        event_archive=None,
        event_archive_at=None,
        passed_tests=0,
        failed_tests=0,
        skipped_tests=0,
        broken_tests=0,
    )
    db = _mk_db(run)

    redis = AsyncMock()
    redis.llen = AsyncMock(return_value=0)

    with patch("app.db.redis_client.get_redis", return_value=redis):
        with pytest.raises(HTTPException) as exc:
            await recover_live_run_from_buffer(
                run_id=run.id, db=db, _=SimpleNamespace(id=uuid.uuid4()),
            )

    assert exc.value.status_code == 422
    assert "no aggregates to synthesise from" in exc.value.detail.lower()
    assert "25-hour TTL" not in exc.value.detail
