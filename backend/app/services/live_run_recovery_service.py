"""Auto-recovery for completed live-stream runs missing per-test rows.

Belt-and-braces for the close_session → persist_live_session handoff.
When that handoff fails — silently swallowed apply_async, queue
backpressure, worker restart during dispatch — the TestRun row carries
final aggregates (``total_tests``, ``passed_tests``, ``failed_tests``)
but ``test_cases`` is empty. The hourly ``backfill_placeholder_test_cases``
task eventually inserts marker rows (``[ingestion gap …] #1``) but the
*real* per-test detail is still recoverable from ``TestRun.event_archive``
(written by ``close_session`` before the worker handoff) for 15 days.

This service finds those runs and re-queues ``persist_live_session``
with the archived events staged back into Redis — the same code path
the ``POST /api/v1/runs/{id}/recover-live`` endpoint uses. Runs hourly
out of band of the placeholder backfill so users see real names within
a minute of close_session firing, not "[ingestion gap …]" rows.

Idempotent: candidates are filtered by ``COUNT(test_cases) = 0``, and
a second run after a successful first one finds zero rows to recover.
"""
from __future__ import annotations

import json as _json
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import String, cast, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.redis_client import get_redis
from app.models.postgres import LiveSession, TestCase, TestRun
from app.streams import LIVE_TESTCASES_KEY

logger = structlog.get_logger(__name__)


async def _stage_archive_to_redis(run_id_str: str, events: list[dict]) -> int:
    """RPUSH archived events back into the Redis live-buffer list so
    ``persist_live_session`` can read them from its usual location.
    Mirrors the staging block in ``routers.runs.recover_live_run_from_buffer``.
    """
    redis = get_redis()
    list_key = LIVE_TESTCASES_KEY.format(run_id=run_id_str)
    for ev in events:
        await redis.rpush(list_key, _json.dumps(ev))
    # 1h TTL — plenty for the worker to drain. The task LRANGEs the list
    # but does not delete it; the TTL keeps the staging key from leaking.
    await redis.expire(list_key, 3600)
    return len(events)


async def auto_recover_completed_runs(
    db: AsyncSession,
    *,
    lookback_hours: int = 24,
    max_runs: int = 100,
) -> dict:
    """Re-enqueue persistence for completed live_stream runs whose
    ``test_cases`` table is empty but ``event_archive`` is still
    populated and within the 15-day archive window.

    Caller owns the transaction. The function does not commit; it
    only stages Redis state and dispatches Celery tasks. The candidate
    SELECT is read-only.

    Returns ``{candidates: int, recovered: int, errors: int}`` for
    observability.
    """
    # Local imports keep the service free of the Celery import cycle and
    # let tests stub them cleanly.
    from app.worker.ingestion_routing import queue_for_project
    from app.worker.tasks import persist_live_session

    counts = {"candidates": 0, "recovered": 0, "errors": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    archive_cutoff = datetime.now(timezone.utc) - timedelta(days=15)

    candidates_stmt = (
        select(TestRun)
        .where(
            TestRun.trigger_source == "live_stream",
            TestRun.event_archive_at.is_not(None),
            TestRun.event_archive_at >= archive_cutoff,
            TestRun.updated_at >= cutoff,
        )
        .where(
            ~select(TestCase.id)
            .where(TestCase.test_run_id == TestRun.id)
            .exists()
        )
        .order_by(TestRun.updated_at.desc())
        .limit(max_runs)
    )

    candidates = list((await db.execute(candidates_stmt)).scalars().all())
    counts["candidates"] = len(candidates)

    redis = get_redis()
    for run in candidates:
        archive = list(run.event_archive or [])
        if not archive:
            # Archive column present but empty — no real names to recover.
            # Leave the run to the placeholder backfill.
            continue

        try:
            run_id_str = str(run.id)
            # If the live Redis buffer still holds events, prefer those
            # — persist_live_session reads from the same list and would
            # double the rows if we RPUSH-ed the archive on top.
            # Mirrors the ``buffer_len`` check in
            # ``routers.runs.recover_live_run_from_buffer``.
            list_key = LIVE_TESTCASES_KEY.format(run_id=run_id_str)
            buffer_len = await redis.llen(list_key) or 0
            if buffer_len > 0:
                staged = int(buffer_len)
                source = "redis"
            else:
                staged = await _stage_archive_to_redis(run_id_str, archive)
                source = "archive"

            persist_live_session.apply_async(
                kwargs={
                    "run_id": run_id_str,
                    "project_id": str(run.project_id),
                    "build_number": run.build_number or run_id_str,
                    "branch": run.branch or "",
                    "commit_hash": run.commit_hash or "",
                    "final_state": {
                        "passed": run.passed_tests or 0,
                        "failed": run.failed_tests or 0,
                        "skipped": run.skipped_tests or 0,
                        "broken": run.broken_tests or 0,
                        "total": run.total_tests or 0,
                    },
                    "suite_name": run.primary_suite_name or None,
                },
                queue=queue_for_project(str(run.project_id)),
                priority=7,
            )
            counts["recovered"] += 1
            logger.info(
                "auto_recover_queued",
                run_id=run_id_str,
                project_id=str(run.project_id),
                events=staged,
                source=source,
            )
        except Exception as exc:
            counts["errors"] += 1
            logger.warning(
                "auto_recover_failed",
                run_id=str(run.id),
                error=str(exc),
            )

    if counts["candidates"] or counts["recovered"]:
        logger.info("auto_recover_sweep_done", **counts)
    return counts


async def repair_clobbered_primary_suite_names(
    db: AsyncSession,
    *,
    lookback_hours: int = 24,
    max_runs: int = 200,
) -> dict:
    """Reset ``TestRun.primary_suite_name`` to the SDK-supplied
    ``LiveSession.suite_name`` for any live_stream run where the two
    disagree.

    Before ingestion.py's ``_update_run_aggregates`` learned to respect
    a session-supplied ``primary_suite_name``, finalize_run recomputed
    it from the dominant per-event ``suite_name`` — which, combined
    with the TestNG-listener bug that stamped per-event suite_name to
    the test class name, made the run show up under
    ``com.example.OrderApiRegressionTests`` instead of the user's
    chosen "API Regression Multi-Class" label. This sweep heals
    affected rows by re-stamping ``primary_suite_name`` from the
    authoritative ``LiveSession.suite_name``. Idempotent: when the
    two already match, the row is left alone.

    Caller owns the transaction.

    Returns ``{candidates: int, repaired: int}`` for observability.
    """
    counts = {"candidates": 0, "repaired": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    # Join TestRun to LiveSession on the canonical UUID. ``LiveSession.run_id``
    # is a ``String(100)`` that holds the SDK-supplied slug — in the
    # current SDK that's the same UUID as ``TestRun.id`` (just stringified),
    # so a CAST-equality join finds the right session. Older legacy slugs
    # that aren't UUIDs simply won't match and are left alone.
    candidates_stmt = (
        select(
            TestRun.id,
            TestRun.primary_suite_name,
            LiveSession.suite_name,
        )
        .join(LiveSession, LiveSession.run_id == cast(TestRun.id, String))
        .where(
            TestRun.trigger_source == "live_stream",
            TestRun.updated_at >= cutoff,
            LiveSession.suite_name.is_not(None),
            LiveSession.suite_name != "",
        )
        .limit(max_runs)
    )

    try:
        rows = list((await db.execute(candidates_stmt)).all())
    except Exception as exc:
        # Cast may differ across dialects (sqlite vs postgres); never
        # fail the beat task here — log and bail.
        logger.warning("repair_primary_suite_join_failed", error=str(exc))
        return counts

    counts["candidates"] = len(rows)

    for run_id, current_psn, session_suite in rows:
        current = (current_psn or "").strip() or None
        target = (session_suite or "").strip() or None
        if not target or current == target:
            continue
        try:
            await db.execute(
                update(TestRun)
                .where(TestRun.id == run_id)
                .values(primary_suite_name=target)
            )
            counts["repaired"] += 1
            logger.info(
                "primary_suite_repaired",
                run_id=str(run_id), from_=current, to=target,
            )
        except Exception as exc:
            logger.warning(
                "primary_suite_repair_failed",
                run_id=str(run_id), error=str(exc),
            )

    if counts["repaired"]:
        logger.info("primary_suite_repair_sweep_done", **counts)
    return counts
