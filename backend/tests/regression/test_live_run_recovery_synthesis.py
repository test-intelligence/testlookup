"""Regression: live runs that reported tests but have zero per-test rows
must either populate via persist_live_session synthesis OR be
manually recoverable into placeholder rows.

Bug pinned (2026-05-19): user reported a live run at
``/runs/b336eb33...`` showing aggregates of 100 reported tests (90
passed, 10 failed) but the run-detail page rendered "per-test
details aren't loaded yet" with the Recover-from-buffer affordance.
After refresh the placeholders never appeared.

Two root causes addressed by this regression test pair:

  1. ``persist_live_session`` placeholder synthesis only covered
     failures + broken — passed and skipped tests had no rows
     synthesised. A run with 100 passed → 100 missing rows; a run
     with 90 passed + 10 failed → 10 placeholder rows for the
     failures only, page still half-empty.

  2. ``POST /runs/{id}/recover-live`` 422'd when both the Redis
     buffer and the durable archive were empty/expired — leaving
     the user with no path forward when the SDK never sent
     per-event ``test_result`` events.

Fix:

  * Synthesis covers all four buckets (passed/failed/broken/skipped)
    so the per-test view materialises every reported test.

  * The recovery endpoint falls back to synthesis (source=``synthesis``)
    when buffer + archive are both empty, queueing the persist task
    with no events so its synthesis branch fires.

What this file pins:

  * Synthesis branch's source contains the four-bucket sequence and
    counts ``passed`` + ``failed`` + ``broken`` + ``skipped`` total.
  * Recovery endpoint returns ``source="synthesis"`` when nothing
    is buffered AND nothing is archived AND aggregates are non-zero.
  * Recovery endpoint still 422s when there's truly nothing to
    recover (no buffer, no archive, zero aggregates).
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── persist_live_session synthesis branch (source inspection) ──────────────


def _synthesis_source() -> str:
    from app.worker.tasks import persist_live_session
    fn = getattr(persist_live_session, "__wrapped__", persist_live_session)
    return inspect.getsource(fn)


def test_synthesis_covers_all_four_status_buckets():
    """Pre-fix the synthesis only iterated failed + broken. Post-fix
    it iterates passed + failed + broken + skipped so the per-test
    view shows every reported test."""
    src = _synthesis_source()
    assert "int(passed)" in src and "int(skipped)" in src, (
        "Synthesis branch must reference all four buckets so a run "
        "with passed-only or skipped-only tests still materialises "
        "per-test rows."
    )
    # The placeholder_count must sum all four — not just failed+broken.
    # Look for "passed" + "skipped" in the same context as
    # "placeholder_count".
    needle_window = src[src.find("placeholder_count") : src.find("placeholder_count") + 500]
    assert "passed" in needle_window and "skipped" in needle_window, (
        "placeholder_count formula must sum passed + failed + broken + "
        "skipped — not just failed + broken."
    )


def test_synthesis_emits_passed_placeholders_when_only_passes_reported():
    """Indirect pin: when ``failed`` and ``broken`` are both zero but
    ``passed > 0``, the synthesis must NOT short-circuit. The pre-fix
    behaviour was ``if (failed + broken) > 0`` — which silently
    skipped 100% passing runs."""
    src = _synthesis_source()
    # Find the synthesis-gating ``if``. It must not gate on
    # ``failed + broken > 0`` alone.
    forbidden = "placeholder_count = int(failed) + int(broken)"
    assert forbidden not in src, (
        "Synthesis must not gate on failed+broken only — that drops "
        "passing/skipped placeholders."
    )


# ── /recover-live synthesis fallback (router) ──────────────────────────────


def _make_run(
    run_id: uuid.UUID,
    *,
    passed=0, failed=0, broken=0, skipped=0,
    trigger="live_stream",
    event_archive=None, event_archive_at=None,
):
    return SimpleNamespace(
        id=run_id,
        project_id=uuid.uuid4(),
        trigger_source=trigger,
        passed_tests=passed,
        failed_tests=failed,
        broken_tests=broken,
        skipped_tests=skipped,
        total_tests=passed + failed + broken + skipped,
        build_number="b1",
        branch="main",
        commit_hash=None,
        primary_suite_name="Auth",
        event_archive=event_archive,
        event_archive_at=event_archive_at,
    )


def _scalar_result(value):
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=value)
    res.scalar = MagicMock(return_value=value)
    return res


@pytest.mark.asyncio
async def test_recover_live_falls_back_to_synthesis_when_no_buffer_no_archive():
    """The user's 2026-05-19 bug: 100 reported, buffer empty, no
    archive — endpoint must queue the persist task in synthesis
    mode (NOT 422)."""
    from app.routers.runs import recover_live_run_from_buffer

    run_id = uuid.uuid4()
    run = _make_run(run_id, passed=90, failed=10)
    db = AsyncMock()
    # First execute: TestRun lookup. Second: test_case COUNT (zero).
    db.execute = AsyncMock(side_effect=[
        _scalar_result(run),
        _scalar_result(0),
    ])

    fake_redis = AsyncMock()
    fake_redis.llen = AsyncMock(return_value=0)
    fake_redis.rpush = AsyncMock()
    fake_redis.expire = AsyncMock()

    queued = {}
    def _apply_async(**kwargs):
        queued["fired"] = True
        queued["kwargs"] = kwargs
        return SimpleNamespace(id="task-1")

    with patch("app.db.redis_client.get_redis", return_value=fake_redis), \
         patch("app.worker.tasks.persist_live_session") as task, \
         patch("app.worker.ingestion_routing.queue_for_project",
               return_value="ingestion.shard.0"):
        task.apply_async = _apply_async
        result = await recover_live_run_from_buffer(run_id=run_id, db=db, _=None)

    assert queued.get("fired") is True, (
        "Recovery must queue the persist task even when buffer + "
        "archive are both empty — the task's synthesis branch covers "
        "the remaining gap."
    )
    assert result["source"] == "synthesis"
    assert result["buffered_events"] == 0
    # The task is called with the run's aggregates so synthesis can use them.
    final_state = queued["kwargs"]["kwargs"]["final_state"]
    assert final_state["passed"] == 90
    assert final_state["failed"] == 10


@pytest.mark.asyncio
async def test_recover_live_still_422s_when_aggregates_are_zero():
    """If buffer + archive + aggregates are ALL empty, there's
    genuinely nothing to recover. The 422 stays."""
    from app.routers.runs import recover_live_run_from_buffer
    from fastapi import HTTPException

    run_id = uuid.uuid4()
    run = _make_run(run_id, passed=0, failed=0, broken=0, skipped=0)
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result(run),
        _scalar_result(0),
    ])

    fake_redis = AsyncMock()
    fake_redis.llen = AsyncMock(return_value=0)

    with patch("app.db.redis_client.get_redis", return_value=fake_redis):
        with pytest.raises(HTTPException) as exc:
            await recover_live_run_from_buffer(run_id=run_id, db=db, _=None)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_recover_live_still_422s_for_already_populated_run():
    """Pre-existing contract: a run that already has test_case rows
    can't be "recovered" — that's destructive."""
    from app.routers.runs import recover_live_run_from_buffer
    from fastapi import HTTPException

    run_id = uuid.uuid4()
    run = _make_run(run_id, passed=10)
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result(run),
        _scalar_result(5),  # tc_count > 0
    ])

    with pytest.raises(HTTPException) as exc:
        await recover_live_run_from_buffer(run_id=run_id, db=db, _=None)
    assert exc.value.status_code == 422
    assert "already has" in exc.value.detail.lower()
