"""Celery background tasks for ingestion and AI analysis."""
import logging
from datetime import datetime, timezone
import time
import uuid
from typing import Any, cast

import structlog
from celery import Task
from celery.exceptions import Retry
from structlog.contextvars import bind_contextvars, clear_contextvars

from app.worker.celery_app import celery_app

#: Digest schedules driven by a run landing, rather than by the clock.
#:
#: Derived from nothing — spelled here and asserted against ``DigestSchedule``
#: by ``tests/regression/test_digest_schedule_vocab.py``, which now checks that
#: every enum member is DISPATCHED somewhere and not merely ACCEPTED by the API.
#: That distinction is the whole finding: the previous test derived its cases
#: from the enum and asserted only that each one was accepted, so three
#: members could be stored and never delivered.
EVENT_DRIVEN_SCHEDULES = ("PER_RUN", "PER_SUITE", "PER_RELEASE")


def run_matches_digest_scope(sub, run) -> bool:
    """Whether this run falls inside an event-driven subscription's scope.

    ``PER_RUN`` is unscoped by design — every run of the project. The other two
    narrow it, and an UNSCOPED narrowing subscription is refused rather than
    widened: a ``PER_SUITE`` row with no ``scope_value`` would deliver every
    run in the project, which is what ``PER_RUN`` already is, and silently
    turning one subscription into another is worse than delivering nothing.
    """
    if sub.schedule == "PER_SUITE":
        wanted = (getattr(sub, "scope_value", None) or "").strip().lower()
        actual = (getattr(run, "primary_suite_name", None) or "").strip().lower()
        return bool(wanted) and wanted == actual
    if sub.schedule == "PER_RELEASE":
        wanted = (getattr(sub, "scope_value", None) or "").strip()
        actual = str(getattr(run, "primary_release_id", None) or "")
        return bool(wanted) and wanted == actual
    return True

logger = logging.getLogger(__name__)
_slog = structlog.get_logger("worker.tasks")


def _run_async(coro):
    """Run a task's coroutine on this worker child's event loop.

    One loop per worker child, reused from task to task (re-audit M1). The
    history -- why it used to be a fresh loop per task, and the teardown that
    implied (BUG-003) -- is in ``worker/loop_runner.py``.
    """
    from app.worker.loop_runner import run_async

    return run_async(coro)


class DownstreamTrackedTask(Task):
    """Tie outbox publication to the consumer's durable business outcome.

    Direct callers have no tracking headers and retain the task's historical
    Celery retry behaviour.  Outbox deliveries let PostgreSQL own retries so a
    Celery retry and the relay cannot race one another.
    """

    abstract = True

    def __call__(self, *args, **kwargs):
        headers = getattr(self.request, "headers", None) or {}
        raw_outbox_id = headers.get("downstream_outbox_id")
        raw_token = headers.get("downstream_dispatch_token")
        if not raw_outbox_id or not raw_token:
            return super().__call__(*args, **kwargs)

        from app.services.run_downstream_outbox import (
            begin_downstream_execution,
            complete_downstream_execution,
            defer_downstream_execution,
            fail_downstream_execution,
        )

        outbox_id = uuid.UUID(str(raw_outbox_id))
        dispatch_token = uuid.UUID(str(raw_token))
        claimed = _run_async(
            begin_downstream_execution(
                outbox_id=outbox_id,
                dispatch_token=dispatch_token,
                task_id=str(self.request.id or ""),
            )
        )
        if not claimed:
            return {"duplicate": True, "downstream_outbox_id": str(outbox_id)}

        try:
            result = super().__call__(*args, **kwargs)
        except Retry as exc:
            retry_exc = exc.exc
            if getattr(retry_exc, "defer_downstream_without_failure", False):
                _run_async(
                    defer_downstream_execution(
                        outbox_id=outbox_id,
                        dispatch_token=dispatch_token,
                        reason=f"task_{type(retry_exc).__name__}",
                    )
                )
            else:
                _run_async(
                    fail_downstream_execution(
                        outbox_id=outbox_id,
                        dispatch_token=dispatch_token,
                        error_code=(
                            f"task_{type(retry_exc).__name__}"
                            if retry_exc
                            else "task_retry"
                        ),
                    )
                )
            return {"deferred": True, "downstream_outbox_id": str(outbox_id)}
        except Exception as exc:  # noqa: BLE001
            _run_async(
                fail_downstream_execution(
                    outbox_id=outbox_id,
                    dispatch_token=dispatch_token,
                    error_code=f"task_{type(exc).__name__}",
                )
            )
            return {"deferred": True, "downstream_outbox_id": str(outbox_id)}

        completed = _run_async(
            complete_downstream_execution(
                outbox_id=outbox_id,
                dispatch_token=dispatch_token,
            )
        )
        if not completed:
            raise RuntimeError("downstream_completion_fence_lost")
        return result


def _bind_task_context(task, **extra):
    """Bind structured logging context for a Celery task (observability improvement)."""
    clear_contextvars()
    bind_contextvars(
        celery_task_id=task.request.id,
        celery_task_name=task.name,
        celery_retry=task.request.retries,
        **extra,
    )


def _exponential_backoff(attempt: int, base: int = 30, cap: int = 600) -> int:
    """Jittered exponential backoff seconds for a Celery retry (E7.2).

    ``attempt`` here is Celery's 0-based ``self.request.retries``; the shared
    :class:`app.services.retry_policy.RetryPolicy` is 1-based, so ``attempt + 1``.
    Delegating keeps one implementation for every task; the jitter is now
    symmetric (the old helper only ever added 0..20%).
    """
    from app.services.retry_policy import RetryPolicy  # noqa: PLC0415

    policy = RetryPolicy(base_seconds=float(base), cap_seconds=float(cap))
    return max(1, int(round(policy.delay(int(attempt) + 1))))


def _live_run_completion_time(
    completed_at: str | None,
    *,
    existing_end_time: datetime | None,
    worker_time: datetime,
) -> datetime:
    """Resolve the immutable live-session completion time for persistence.

    New durable payloads carry the timestamp captured by ``close_session``.
    Legacy payloads preserve a terminal run's existing value; only a genuinely
    missing timestamp falls back to worker execution time.
    """
    if completed_at:
        try:
            parsed = datetime.fromisoformat(str(completed_at).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid_live_session_completed_at") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    if existing_end_time is not None:
        return existing_end_time
    return worker_time


def _beat_span(task_name: str):
    """Return a context manager that produces an OTEL span for the named
    Celery beat task.

    Naming convention (Phase E-3): ``celery.beat.<task_name>``. All Tier
    0-2 beat tasks use this helper so Jaeger surfaces them in a single
    swim lane. The context yields a span-like object that supports
    ``set_attribute`` + ``set_status`` — callers can enrich the span
    with result counts, flag state, or error category without worrying
    about whether OpenTelemetry is actually wired up in the current
    environment (tests, offline-mode, missing SDK).
    """
    try:
        from app.core.tracing import get_tracer
        tracer = get_tracer("celery.beat")
        return tracer.start_as_current_span(f"celery.beat.{task_name}")
    except Exception:
        from contextlib import nullcontext

        class _NullSpan:
            def set_attribute(self, *args, **kwargs) -> None:  # noqa: D401
                pass

            def set_status(self, *args, **kwargs) -> None:  # noqa: D401
                pass

        return nullcontext(_NullSpan())


# ── Deduplication helper ──────────────────────────────────────────────────────

async def _is_duplicate(key: str, ttl: int = 3600, owner: str | None = None) -> bool:
    """
    Return True if `key` already exists in Redis (task already running/done).
    Otherwise, set the key with TTL and return False.

    When owner is provided, the same owner can reacquire the lock. Celery
    retries keep the task id stable, so a real retry should not be treated as
    a duplicate of its own failed attempt.
    """
    from app.db.redis_client import get_redis
    redis = get_redis()
    # SET NX — only sets if key does not exist; returns True on first write
    lock_value = owner or "1"
    was_set = await redis.set(key, lock_value, ex=ttl, nx=True)
    if was_set:
        return False
    if not owner:
        return True
    existing = await redis.get(key)
    if isinstance(existing, bytes):
        existing = existing.decode("utf-8", errors="ignore")
    if existing == owner:
        await redis.expire(key, ttl)
        return False
    return True


async def _release_duplicate_lock(key: str, owner: str) -> None:
    """Release a dedup lock only if it is still owned by this task."""
    from app.db.redis_client import get_redis
    redis = get_redis()
    existing = await redis.get(key)
    if isinstance(existing, bytes):
        existing = existing.decode("utf-8", errors="ignore")
    if existing == owner:
        await redis.delete(key)


def _count_ingestion_run(status: str, elapsed: float) -> None:
    """Record one run-ingestion outcome and its wall-clock time. Never raises.

    ``ingestion_runs_total`` and ``ingestion_duration_seconds`` were both
    declared and never emitted, so there was no way to see ingestion failing or
    slowing down except by reading task logs.

    A duplicate-suppressed attempt still counts as a success: it did what it was
    asked to do (ensure the prefix is ingested) and the alternative -- counting
    it as a failure -- would make ordinary webhook redelivery look like an
    outage.
    """
    try:
        from app.core.metrics import (
            ingestion_duration_seconds,
            ingestion_runs_total,
        )

        ingestion_runs_total.labels(status=status).inc()
        ingestion_duration_seconds.observe(elapsed)
    except Exception:  # noqa: BLE001 -- metrics must never break the task
        pass


def _observe_pipeline_duration(workflow_type: str, elapsed: float) -> None:
    """Record end-to-end AI pipeline wall-clock time. Never raises.

    ``ai_analysis_duration_seconds`` was declared and never emitted. It is not
    a duplicate of ``pipeline_stage_duration_seconds``: that one is per stage,
    and summing stages misses queue waits and orchestration between them, which
    is exactly where a slow pipeline usually is.
    """
    try:
        from app.core.metrics import ai_analysis_duration_seconds

        ai_analysis_duration_seconds.labels(
            workflow_type=workflow_type or "unknown"
        ).observe(elapsed)
    except Exception:  # noqa: BLE001 -- metrics must never break the task
        pass


def _count_pipeline(workflow_type: str, status: str) -> None:
    """Record one AI pipeline outcome. Never raises."""
    try:
        from app.core.metrics import ai_analyses_total

        ai_analyses_total.labels(
            workflow_type=workflow_type or "unknown", status=status
        ).inc()
    except Exception:  # noqa: BLE001 — metrics must never break the task
        pass


def _release_dedup_for_retry(dedup_key: str, dedup_owner: str, task_id: str) -> None:
    """Drop this attempt's dedup lock so Celery's retry can actually do the work.

    A task that takes a ``SET NX`` lock to suppress concurrent duplicates and
    then calls ``self.retry()`` will, on the retry, meet its own surviving
    lock, conclude it is a duplicate, and return **success** having done
    nothing. The retry policy goes inert and the work is dropped silently.

    Never raises: failing to release must not replace the original error.
    """
    try:
        _run_async(_release_duplicate_lock(dedup_key, dedup_owner))
    except Exception as release_exc:
        logger.warning(
            "[Task %s] Failed to release dedup lock %s after error (%s)",
            task_id, dedup_key, type(release_exc).__name__,
        )


# ── Tasks ─────────────────────────────────────────────────────────────────────

async def _drain_live_evidence_before_finalize(
    *,
    run_id: str,
    project_id: str,
    build_number: str,
    suite_name: str | None,
) -> int:
    """Project every durable live event before the terminal finalizer runs.

    Current producers append to a per-run Redis Stream and mirror test results
    to the legacy LIST during the rolling upgrade.  The drainer owns the only
    safe acknowledgement boundary: it commits PostgreSQL first, then ACKs and
    trims only through that committed watermark.  This close-time loop keeps
    calling that boundary until both sources are empty.

    A legacy-only run can encounter an empty Stream created by the consumer
    group.  In that case the drainer correctly prefers the Stream but cannot
    see the LIST, so migrate the frozen LIST into the Stream and resume.  The
    migration is checkpointed and WATCH-protected; cleanup is attempted only
    through its acknowledged-drain verifier.
    """
    from app.db.redis_client import get_redis
    from app.services.live_persistence_scrub import (
        migrate_redis_list_to_evidence_stream,
        remove_drained_legacy_redis_list,
    )
    from app.services.live_session_drainer import drain_run_buffer
    from app.streams import (
        LIVE_EVIDENCE_GROUP,
        LIVE_EVIDENCE_STREAM_KEY,
        LIVE_TESTCASES_KEY,
    )

    redis = get_redis()
    stream_key = LIVE_EVIDENCE_STREAM_KEY.format(run_id=run_id)
    list_key = LIVE_TESTCASES_KEY.format(run_id=run_id)
    total_drained = 0

    while True:
        outcome = await drain_run_buffer(
            run_id=run_id,
            project_id=project_id,
            build_number=build_number,
            suite_name=suite_name,
        )
        drained = int(outcome.get("drained", 0) or 0)
        total_drained += drained
        if drained:
            continue

        stream_length = int(await redis.xlen(stream_key) or 0)
        legacy_length = int(await redis.llen(list_key) or 0)
        if stream_length:
            # A pending entry from a crashed consumer is not claimable until
            # its idle timeout elapses.  Finalizing now would strand it; make
            # Celery retry instead.
            raise RuntimeError(
                f"live evidence stream still contains {stream_length} "
                "uncommitted entry(s)"
            )
        if not legacy_length:
            return total_drained

        migrated = await migrate_redis_list_to_evidence_stream(
            redis,
            list_key,
            stream_key,
            run_id=run_id,
        )
        if migrated:
            continue

        # A completed checkpoint with an empty Stream means all migrated rows
        # were committed and acknowledged.  This verifier refuses deletion if
        # lag/pending/topology changed, so it cannot erase later evidence.
        removed = await remove_drained_legacy_redis_list(
            redis,
            list_key,
            stream_key,
            group_name=LIVE_EVIDENCE_GROUP,
        )
        if removed:
            continue
        raise RuntimeError("legacy live evidence remains after migration")


@celery_app.task(
    name="app.worker.tasks.dispatch_run_completed_webhook",
    base=DownstreamTrackedTask,
    bind=True,
    max_retries=2,
    default_retry_delay=15,
    queue="default",
)
def dispatch_run_completed_webhook(
    self,
    project_id: str,
    payload: dict,
):
    """Persist and publish an idempotent run.completed webhook emission."""
    from app.services.webhook_service import emit_event

    headers = getattr(self.request, "headers", None) or {}
    outbox_id = headers.get("downstream_outbox_id")
    if not outbox_id:
        raise RuntimeError("run_completed_webhook_requires_outbox_identity")
    try:
        return _run_async(
            emit_event(
                "run.completed",
                project_id=project_id,
                payload=payload,
                delivery_scope=(
                    f"run-completed:{project_id}:{payload.get('run_id')}:v1"
                ),
                raise_on_persistence_error=True,
            )
        )
    except Exception as exc:
        logger.error(
            "[Task %s] run.completed webhook emission failed: %s",
            self.request.id,
            exc,
            exc_info=True,
        )
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.worker.tasks.persist_live_session",
    base=DownstreamTrackedTask,
    bind=True,
    max_retries=3,
    queue="ingestion",
)
def persist_live_session(
    self,
    run_id: str,
    project_id: str,
    build_number: str,
    client_name: str = "",
    framework: str = "",
    branch: str = "",
    commit_hash: str = "",
    final_state: dict | None = None,
    suite_name: str | None = None,
    completed_at: str | None = None,
):
    """
    Persist a completed live execution session to PostgreSQL.

    Drains the per-run durable evidence Stream (with legacy LIST migration)
    and creates:
      - One TestRun row (with aggregated counts from final_state / Redis data)
      - One TestCase row per event

    Called by DELETE /api/v1/stream/sessions/{session_id} after run_complete.
    Deduplicates by run_id so retries are safe.
    """
    import hashlib
    import uuid as _uuid_mod
    from datetime import datetime, timezone

    final_state = final_state or {}

    async def _run():
        # Keep the row-count/reconciliation queries on the same session as the
        # terminal aggregate update. A previous version opened a separate
        # session and raced asyncpg's connection pool during retries.
        from sqlalchemy import select as _sel, func as _func

        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import (
            TestCase, TestRun, TestStatus,
        )
        from app.services.stream_service import (
            canonical_test_run_uuid,
            finalize_closed_session_redis,
        )
        from app.services.run_status import terminal_run_status
        from app.services.ingestion_sanitization import sanitize_test_result_payload

        # Project through the shared durable drainer before terminal state is
        # materialised. Only that service may ACK or trim evidence because it
        # commits PostgreSQL before advancing the Redis watermark.
        drained = await _drain_live_evidence_before_finalize(
            run_id=run_id,
            project_id=project_id,
            build_number=build_number,
            suite_name=suite_name,
        )
        events: list[dict] = []

        logger.info(
            "[Task %s] Persisting live session: run=%s drained=%d",
            self.request.id, run_id, drained,
        )

        # ── Compute aggregate counts ──────────────────────────────────────────
        # ``final_state`` comes from the authoritative HINCRBY counters
        # (LIVE_STATE_KEY hash). Prefer it whenever a total was reported.
        # Legacy recovery without those counters is reconciled from the rows
        # the durable drainer committed below.
        fs_total = final_state.get("total")
        if fs_total is not None and int(fs_total) > 0:
            passed  = int(final_state.get("passed",  0) or 0)
            failed  = int(final_state.get("failed",  0) or 0)
            skipped = int(final_state.get("skipped", 0) or 0)
            broken  = int(final_state.get("broken",  0) or 0)
            unknown = int(final_state.get("unknown", 0) or 0)
            total   = int(fs_total)
        else:
            passed  = sum(1 for e in events if (e.get("status") or "").upper() == "PASSED")
            failed  = sum(1 for e in events if (e.get("status") or "").upper() == "FAILED")
            skipped = sum(1 for e in events if (e.get("status") or "").upper() == "SKIPPED")
            broken  = sum(1 for e in events if (e.get("status") or "").upper() == "BROKEN")
            # Everything the vocabulary does not cover. ``total`` is len(events)
            # here, so without this the remainder was inside the total with no
            # bucket accounting for it.
            unknown = sum(
                1 for e in events
                if (e.get("status") or "").upper()
                not in ("PASSED", "FAILED", "SKIPPED", "BROKEN")
            )
            total   = len(events) or final_state.get("total", 0)

        # Empty buffer at close — surface loudly so the empty Run
        # Detail table on the UI is traceable to a real root cause
        # (buffer TTL, dedup-skipped retry, or rpush failure in
        # publish_event_batch). Aggregates above already reflect the
        # truth regardless.
        if not events and (passed + failed + skipped + broken) > 0:
            logger.warning(
                "[Task %s] Live persist: event buffer empty for run=%s but "
                "final_state reports passed=%d failed=%d skipped=%d broken=%d. "
                "TestRun aggregates will be written; per-test rows will be "
                "synthesised as labelled placeholders (real per-test detail "
                "is unavailable without the buffered events).",
                self.request.id, run_id, passed, failed, skipped, broken,
            )

        # If total wasn't tracked explicitly, derive it from component counts
        total = total or (passed + failed + skipped + broken + unknown)

        pass_rate = round(passed / (passed + failed + broken) * 100, 2) if (passed + failed + broken) > 0 else None
        # Use the shared grader rather than an inline ternary: this path used to
        # grade PASSED whenever failed+broken == 0, which meant a run whose only
        # non-passing result was uninterpretable went out green.
        run_status = terminal_run_status(passed + failed + broken, failed, broken, unknown)

        # ── Resolve project UUID ──────────────────────────────────────────────
        try:
            proj_uuid = _uuid_mod.UUID(project_id)
        except ValueError:
            logger.error("[Task %s] Invalid project_id %s — aborting", self.request.id, project_id)
            return

        # ── Resolve run UUID via the shared helper ────────────────────────────
        # Keeps slug→UUID derivation in lockstep with stream_service.upsert_test_run
        # and the LiveSessionState response, so the frontend's /runs/<id> link
        # always resolves to the same row this task writes.
        run_uuid = canonical_test_run_uuid(run_id)

        now = datetime.now(timezone.utc)

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            # The drainer may have created rows in this task or on an earlier
            # tick. Count them before deciding whether placeholder recovery is
            # needed; terminal aggregates and finalization still run on retry.
            existing_tc_count = (
                await db.execute(
                    _sel(_func.count(TestCase.id)).where(TestCase.test_run_id == run_uuid)
                )
            ).scalar() or 0
            if total <= 0 and existing_tc_count > 0:
                # Legacy/manual recovery may not have a Redis counter hash or
                # final_state.  The drainer has already committed the durable
                # evidence, so reconstruct terminal aggregates from those rows
                # instead of overwriting the run with zeroes.
                aggregate_rows = (
                    await db.execute(
                        _sel(TestCase.status, _func.count(TestCase.id))
                        .where(TestCase.test_run_id == run_uuid)
                        .group_by(TestCase.status)
                    )
                ).all()
                status_counts = {
                    (
                        status.value
                        if hasattr(status, "value")
                        else str(status)
                    ).upper(): int(count or 0)
                    for status, count in aggregate_rows
                }
                passed = status_counts.get(TestStatus.PASSED.value, 0)
                failed = status_counts.get(TestStatus.FAILED.value, 0)
                skipped = status_counts.get(TestStatus.SKIPPED.value, 0)
                broken = status_counts.get(TestStatus.BROKEN.value, 0)
                unknown = status_counts.get(TestStatus.UNKNOWN.value, 0)
                total = passed + failed + skipped + broken + unknown
                pass_rate = (
                    round(passed / (passed + failed + broken) * 100, 2)
                    if (passed + failed + broken) > 0
                    else None
                )
                run_status = terminal_run_status(
                    passed + failed + broken,
                    failed,
                    broken,
                    unknown,
                )
            # Upsert TestRun — skip if already exists (idempotent)
            existing = await db.execute(select(TestRun).where(TestRun.id == run_uuid))
            run = existing.scalar_one_or_none()

            session_suite = (suite_name or "").strip() or None
            from app.services import run_tombstone_service as run_tombstones

            if run is None and await run_tombstones.run_is_tombstoned(db, run_uuid):
                # Deliberately deleted — do not bring it back headless.
                logger.info("[live] skipping tombstoned run %s", run_uuid)
                return
            if run is None:
                run_completed_at = _live_run_completion_time(
                    completed_at,
                    existing_end_time=None,
                    worker_time=now,
                )
                run = TestRun(
                    id=run_uuid,
                    project_id=proj_uuid,
                    build_number=build_number,
                    trigger_source="live_stream",
                    ingestion_source="live",
                    branch=branch or None,
                    commit_hash=commit_hash or None,
                    status=run_status,
                    total_tests=total,
                    passed_tests=passed,
                    failed_tests=failed,
                    skipped_tests=skipped,
                    broken_tests=broken,
                    unknown_tests=unknown,
                    pass_rate=pass_rate,
                    primary_suite_name=session_suite,
                    suite_names=[session_suite] if session_suite else None,
                    start_time=run_completed_at,
                    end_time=run_completed_at,
                )
                db.add(run)
                await db.flush()   # assigns DB id before we reference it in TestCase FKs
            else:
                run_completed_at = _live_run_completion_time(
                    completed_at,
                    existing_end_time=run.end_time,
                    worker_time=now,
                )
                # Update aggregates on the existing row
                run.status       = run_status
                run.total_tests  = total
                run.passed_tests = passed
                run.failed_tests = failed
                run.skipped_tests = skipped
                run.broken_tests  = broken
                run.unknown_tests = unknown
                run.pass_rate     = pass_rate
                run.end_time      = run_completed_at
                # Stamp primary_suite_name if upsert_test_run never ran
                # for this session (race window: session opens + closes
                # without the periodic upsert firing).
                if session_suite and not run.primary_suite_name:
                    run.primary_suite_name = session_suite
                    run.suite_names = [session_suite]

            # ── Insert TestCase rows ──────────────────────────────────────────
            # Phase 2.2 — bulk-insert via SQLAlchemy Core ``insert(...)``
            # with chunked ``execute_many``. Replaces a per-row ``db.add()``
            # loop that issued one INSERT per event (= one round-trip per
            # event). For a 5K-event run that's 5K round-trips serial on
            # one connection; here it's 5 chunked round-trips. Memory
            # footprint stays bounded by ``PERSIST_LIVE_BULK_INSERT_CHUNK``
            # (default 1000 rows × ~500 B/row ≈ 500 KB per chunk).
            from app.core.config import settings as _settings
            chunk_size = max(1, _settings.PERSIST_LIVE_BULK_INSERT_CHUNK)
            # Phase 4.2 — high-volume sampling. When the detector has
            # flagged this project, drop to 1-of-N persistence so the
            # bulk-insert stays well under the round-trip budget at
            # 500-concurrent-run scale. Aggregates remain accurate
            # because they come from ``test_runs.passed_tests`` /
            # ``failed_tests`` (HINCRBY-sourced), not from a count of
            # persisted ``test_cases`` rows. Logged so support can
            # spot the sampling effect when comparing live-state
            # counts against on-disk row counts.
            sampled_events = events
            sample_n = max(1, _settings.HIGH_VOLUME_SAMPLE_EVERY_N)
            if sample_n > 1:
                try:
                    from app.services.high_volume_detector import is_high_volume
                    if await is_high_volume(project_id):
                        sampled_events = events[::sample_n]
                        if len(sampled_events) < len(events):
                            logger.info(
                                "[Task %s] high_volume sampling run=%s "
                                "kept=%d of %d (1-of-%d)",
                                self.request.id, run_id,
                                len(sampled_events), len(events), sample_n,
                            )
                except Exception as exc:  # pragma: no cover - fail-OPEN
                    logger.warning(
                        "[Task %s] high_volume sampling check failed: %s",
                        self.request.id, exc,
                    )
            # Fall back to the session-level suite_name when the per-event
            # field is missing. SDKs send testlookup.suite once at session
            # create (stamped on TestRun.primary_suite_name) and typically
            # don't repeat it on every event — so persisted TestCase rows
            # ended up with NULL suite_name, invisible to every page that
            # groups by tc.suite_name (test-management Test Suites tab,
            # /reports/summary's cases_agg path, /coverage/suite).
            session_suite_default = (suite_name or "").strip() or None
            run_suite_default = getattr(run, "primary_suite_name", None) or None
            default_suite = session_suite_default or run_suite_default
            rows: list[dict] = []
            for event in sampled_events:
                safe_event = sanitize_test_result_payload(event)
                test_name  = safe_event.get("test_name") or ""
                class_name = safe_event.get("class_name") or ""
                raw_status = (safe_event.get("status") or "UNKNOWN").upper()

                try:
                    tc_status = TestStatus(raw_status)
                except ValueError:
                    tc_status = TestStatus.UNKNOWN

                fingerprint = hashlib.md5(
                    f"{test_name}:{class_name}".encode()
                ).hexdigest()

                event_suite = (safe_event.get("suite_name") or "").strip()
                resolved_suite = (event_suite or default_suite or "")[:500] or None

                rows.append({
                    "id": _uuid_mod.uuid4(),
                    "test_run_id": run.id,
                    "test_fingerprint": fingerprint,
                    "test_name": test_name[:1000],
                    "suite_name": resolved_suite,
                    "class_name": class_name[:500] or None,
                    "status": tc_status.value if hasattr(tc_status, "value") else tc_status,
                    "duration_ms": safe_event.get("duration_ms"),
                    "error_message": safe_event.get("error_message"),
                    "stack_trace": safe_event.get("stack_trace"),
                    "tags": safe_event.get("tags"),
                })
            if rows:
                from sqlalchemy.dialects.postgresql import insert as _pg_insert
                # on_conflict_do_nothing: a task retry after a partial commit
                # re-presents the same (test_run_id, test_fingerprint) rows
                # (the Redis buffer is only deleted post-finalize, so on retry
                # `events` is still non-empty and the dedup skip doesn't fire).
                # The uq_test_cases_run_fingerprint constraint turns the
                # re-insert into a no-op instead of a duplicate per-test row.
                stmt = _pg_insert(TestCase).on_conflict_do_nothing(
                    index_elements=["test_run_id", "test_fingerprint"]
                )
                for offset in range(0, len(rows), chunk_size):
                    chunk = rows[offset:offset + chunk_size]
                    await db.execute(stmt, chunk)
            elif existing_tc_count == 0:
                # Buffer was empty but final_state reports tests ran.
                # This happens when the SDK only sends a ``run_complete``
                # event without per-test ``test_result`` events, or when
                # the Phase 4.5 drain couldn't fire because the session
                # lifetime was shorter than its 30s tick. Without a
                # placeholder, the aggregates surface on /runs +
                # /coverage but the action queue (/my-failures), the
                # per-suite case table, and the run-detail per-test
                # view all stay empty.
                #
                # Synthesize ONE placeholder TestCase per reported
                # test so EVERY bucket (passed / failed / broken /
                # skipped) materialises. Earlier versions only
                # synthesized failures + broken — leaving the user
                # with the "100 reported, 0 visible" confusion the
                # 2026-05-19 bug captured. The row is clearly
                # labelled "[ingestion gap]" so an operator
                # immediately sees synthesised rows. Fingerprint
                # seeds with the run id so re-running the task is
                # idempotent (same hash = unique-constraint conflict
                # = no duplicates).
                placeholder_count = (
                    int(passed) + int(failed) + int(skipped) + int(broken)
                    + int(unknown)
                )
                if placeholder_count > 0:
                    from sqlalchemy.dialects.postgresql import insert as _pg_insert
                    placeholder_rows = []
                    bucket_sequence = (
                        (int(passed),  TestStatus.PASSED.value),
                        (int(failed),  TestStatus.FAILED.value),
                        (int(broken),  TestStatus.BROKEN.value),
                        (int(skipped), TestStatus.SKIPPED.value),
                        (int(unknown), TestStatus.UNKNOWN.value),
                    )
                    i = 0
                    for count, status_value in bucket_sequence:
                        for _ in range(count):
                            ph_fp = hashlib.md5(
                                f"placeholder:{run.id}:{i}".encode()
                            ).hexdigest()
                            placeholder_rows.append({
                                "id": _uuid_mod.uuid4(),
                                "test_run_id": run.id,
                                "test_fingerprint": ph_fp,
                                "test_name": (
                                    f"[ingestion gap — per-test detail "
                                    f"unavailable] #{i + 1}"
                                ),
                                "suite_name": default_suite,
                                "class_name": None,
                                "status": status_value,
                                "duration_ms": None,
                                "error_message": (
                                    "Per-test events were lost during "
                                    "ingestion. Run reported "
                                    f"{int(passed)} passed / "
                                    f"{int(failed)} failed / "
                                    f"{int(broken)} broken / "
                                    f"{int(skipped)} skipped; re-run "
                                    "the suite to capture per-test "
                                    "detail."
                                ) if status_value != TestStatus.PASSED.value else None,
                                "tags": None,
                            })
                            i += 1
                    # Idempotent on retry — the placeholder fingerprint seeds
                    # with the run id, so on_conflict_do_nothing keeps re-runs
                    # from duplicating the synthesised rows (matches the
                    # comment above that assumed this constraint existed).
                    stmt = _pg_insert(TestCase).on_conflict_do_nothing(
                        index_elements=["test_run_id", "test_fingerprint"]
                    )
                    await db.execute(stmt, placeholder_rows)
                    logger.warning(
                        "[Task %s] Synthesized %d placeholder TestCase "
                        "row(s) for run=%s (passed=%d failed=%d "
                        "broken=%d skipped=%d) because the event buffer "
                        "was empty but final_state reported tests ran.",
                        self.request.id, placeholder_count, run_id,
                        int(passed), int(failed), int(broken), int(skipped),
                    )

            await db.commit()
            # The close transaction and its durable outbox intent are now
            # committed. Finalize the Redis close only at this point. This
            # outbox-backed task is retryable, so it also repairs a process
            # crash or Redis failure between the API commit and its immediate
            # best-effort finalizer call.
            await finalize_closed_session_redis(str(run_uuid))
            logger.info(
                "[Task %s] Persisted run=%s tests=%d passed=%d failed=%d",
                self.request.id, run_id, total, passed, failed,
            )

        # ── Post-ingestion pipeline ──────────────────────────────────────────
        # Live-stream ingestion has historically stopped here, after the
        # TestCase rows committed. The API-ingest paths (ingest_uploaded_*)
        # call finalize_run at this point to materialise test_suites,
        # canonical_test_cases, suite_memberships, auto-tags, release link,
        # and notifications. Skipping finalize_run for live-stream runs is
        # why /suites was empty even with test_cases populated. Mirror the
        # API path here so live runs participate in the full pipeline.
        from app.services.ingestion_pipeline import finalize_run

        await finalize_run(
            run_id=str(run_uuid),
            project_id=str(proj_uuid),
            build_number=build_number,
        )


    try:
        _run_async(_run())
    except Exception as exc:
        logger.error("[Task %s] persist_live_session failed: %s", self.request.id, exc)
        # Phase 4.3 — when retries are exhausted, write a structured
        # dead-letter record so operators can inspect what blew up
        # without grepping logs. ``self.retry`` raises ``MaxRetriesExceededError``
        # when the retry budget is exhausted; we catch that to write
        # the DLQ entry, then re-raise so Celery marks the task FAILED.
        try:
            raise self.retry(exc=exc, countdown=_exponential_backoff(self.request.retries))
        except Exception as final_exc:
            from celery.exceptions import MaxRetriesExceededError
            if isinstance(final_exc, MaxRetriesExceededError):
                try:
                    from app.services.ingestion_dlq import record_persist_failure
                    _run_async(record_persist_failure(
                        run_id=run_id,
                        project_id=project_id,
                        task_id=self.request.id,
                        retry_count=self.request.retries,
                        error=str(exc),
                    ))
                except Exception as dlq_exc:  # pragma: no cover - DLQ is best-effort
                    logger.error(
                        "[Task %s] DLQ write failed for run=%s: %s",
                        self.request.id, run_id, dlq_exc,
                    )
            raise


@celery_app.task(
    name="app.worker.tasks.ingest_test_run",
    bind=True,
    max_retries=3,
    queue="ingestion",
)
def ingest_test_run(
    self,
    sentinel_dict: dict | None = None,
    minio_prefix: str = "",
    sentinel_key: str | None = None,
):
    """
    Background task: parse Allure JSON + TestNG XML from MinIO and
    upsert structured data into PostgreSQL + MongoDB.
    Deduplicates by minio_prefix so concurrent webhooks don't double-ingest.

    The MinIO webhook queues the sentinel's object KEY, and this task reads the
    sentinel (services/minio_sentinel.py): off the API process, and inside this
    task's retries for a storage hiccup (code review of re-audit N10). A
    sentinel that is definitively unusable -- missing, oversized, not a JSON
    object, invalid -- is refused without a retry. ``sentinel_dict`` is the
    shape an API from before that change queued; it is still accepted.
    """
    from app.models.schemas import SentinelFile
    from app.services.ingestion import process_sentinel
    from app.services.minio_sentinel import SentinelRefused, read_sentinel

    dedup_key = f"testlookup:dedup:ingest:{minio_prefix}"
    dedup_owner = str(self.request.id)

    async def _run() -> str:
        if await _is_duplicate(dedup_key, owner=dedup_owner):
            logger.info("[Task %s] Skipping duplicate ingestion for %s", self.request.id, minio_prefix)
            return "duplicate"
        if sentinel_key:
            try:
                sentinel = await read_sentinel(sentinel_key)
            except SentinelRefused as exc:
                logger.warning(
                    "[Task %s] Sentinel refused, not retrying: %s (%s)",
                    self.request.id, sentinel_key, exc,
                )
                return "refused"
        else:
            sentinel = SentinelFile(**(sentinel_dict or {}))
        await process_sentinel(sentinel, minio_prefix)
        return "ingested"

    logger.info("[Task %s] Starting ingestion: %s", self.request.id, minio_prefix)
    _ingest_started = time.perf_counter()
    try:
        outcome = _run_async(_run())
        if outcome == "refused":
            # Nothing was ingested, and nothing a retry could change. Free the
            # prefix, so a real upload notified later is not refused as a
            # duplicate of this one for the lock's hour.
            _count_ingestion_run("failure", time.perf_counter() - _ingest_started)
            _release_dedup_for_retry(dedup_key, dedup_owner, self.request.id)
            return
        logger.info("[Task %s] Ingestion complete", self.request.id)
        _count_ingestion_run("success", time.perf_counter() - _ingest_started)

        # ROI-04: Trigger incremental search indexing after ingestion
        try:
            reindex_search.apply_async(kwargs={"full": False}, countdown=5)
        except Exception:
            pass  # Non-blocking — indexing will catch up on the next hourly beat
    except Exception as exc:
        logger.error("[Task %s] Ingestion failed: %s", self.request.id, exc, exc_info=True)
        _count_ingestion_run("failure", time.perf_counter() - _ingest_started)
        _release_dedup_for_retry(dedup_key, dedup_owner, self.request.id)
        if self.request.retries >= self.max_retries:
            # The retries are spent (code review of re-audit N10). MinIO had
            # its 200 from the webhook and will not notify again, so without
            # a record an object-store outage longer than the backoff loses
            # the build. Record the call exactly as this task takes it, where
            # an admin reads dead letters (GET /api/v1/admin/maintenance/dlq),
            # so it can be replayed. _send_to_dlq never raises.
            replay: dict[str, Any] = {"sentinel_key": sentinel_key, "minio_prefix": minio_prefix}
            if sentinel_dict is not None:
                replay["sentinel_dict"] = sentinel_dict
            _run_async(_send_to_dlq(
                task_name=self.name,
                task_id=self.request.id,
                kwargs=replay,
                error=f"{type(exc).__name__}: {exc}",
                # Already validated by sentinel_key_problem; replayed as is.
                verbatim=("sentinel_key", "minio_prefix"),
            ))
        countdown = _exponential_backoff(self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)


# ── Unified Ingest Tasks (POST /api/v1/ingest) ──────────────────────────────


@celery_app.task(
    name="app.worker.tasks.ingest_uploaded_results",
    bind=True,
    max_retries=3,
    queue="ingestion",
)
def ingest_uploaded_results(
    self, run_id: str, payload: dict = None, user_id: str = None,
    payload_storage_key: str = None,
):
    """
    Process a JSON batch of test results from POST /api/v1/ingest.
    Creates a TestRun, upserts test cases, runs post-ingestion pipeline.
    """
    from app.services.ingestion_pipeline import (
        create_run_from_payload,
        finalize_run,
        ingest_test_results,
    )

    async def _run():
        from app.db.postgres import AsyncSessionLocal
        nonlocal payload
        if payload_storage_key:
            import json
            from app.db.storage import get_storage_provider
            raw_payload = await get_storage_provider().get_object_content(payload_storage_key)
            payload = json.loads(raw_payload)
        if not payload:
            raise ValueError("Uploaded batch payload is missing")

        async with AsyncSessionLocal() as db:
            try:
                run = await create_run_from_payload(
                    db,
                    project_id=payload["project_id"],
                    build_number=payload["build_number"],
                    run_id=run_id,
                    branch=payload.get("branch"),
                    commit_hash=payload.get("commit_hash"),
                    framework=payload.get("framework"),
                    trigger_source=payload.get("trigger_source", "api"),
                    release_name=payload.get("release_name"),
                    ingestion_source="sdk",
                    ci_provider=payload.get("ci_provider"),
                    ci_repo=payload.get("ci_repo"),
                    pr_number=payload.get("pr_number"),
                    ci_actor=payload.get("ci_actor"),
                    ci_run_url=payload.get("ci_run_url"),
                    jenkins_job=payload.get("jenkins_job"),
                    environment=payload.get("environment"),
                    commit_range=payload.get("commit_range"),
                )
                count = await ingest_test_results(db, run, payload["results"])
                await db.commit()
                logger.info(
                    "[Task %s] Uploaded results ingested: %d cases",
                    self.request.id, count,
                )
            except Exception:
                await db.rollback()
                raise

        await finalize_run(
            # Use the canonical row returned after identity conflict handling.
            run_id=str(run.id),
            project_id=payload["project_id"],
            build_number=payload["build_number"],
            release_name=payload.get("release_name"),
        )
        if payload_storage_key:
            try:
                from app.db.storage import get_storage_provider
                await get_storage_provider().delete_object(payload_storage_key)
            except Exception as exc:  # noqa: BLE001 — cleanup is best effort
                logger.warning("queued_batch_cleanup_failed key=%s error=%s", payload_storage_key, exc)

    logger.info("[Task %s] Processing uploaded batch: run=%s", self.request.id, run_id)
    try:
        _run_async(_run())
        try:
            reindex_search.apply_async(kwargs={"full": False}, countdown=5)
        except Exception:
            pass
    except Exception as exc:
        logger.error("[Task %s] Batch ingest failed: %s", self.request.id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=_exponential_backoff(self.request.retries))


@celery_app.task(
    name="app.worker.tasks.ingest_uploaded_file",
    bind=True,
    max_retries=3,
    queue="ingestion",
)
def ingest_uploaded_file(
    self,
    run_id: str,
    file_name: str,
    file_format: str,
    project_id: str,
    build_number: str,
    file_storage_key: str = None,
    file_content: str = None,  # compatibility for tasks queued before M02
    branch: str = None,
    commit_hash: str = None,
    release_name: str = None,
    user_id: str = None,
    disabled_formats: list = None,
    run_ai: bool = True,
    ci_provider: str = None,
    ci_repo: str = None,
    pr_number: int = None,
    ci_actor: str = None,
    ci_run_url: str = None,
    jenkins_job: str = None,
    environment: str = None,
    # ISO-8601 string; defaulted so tasks queued before this shipped still
    # deserialize. None means "not supplied" and ingest time is used.
    executed_at: str = None,
    commit_range=None,  # bare list OR {base, head, commits}; both JSON-safe
):
    """
    Parse an uploaded test result file and ingest.
    Supports JUnit XML, TestNG XML, and Allure JSON.
    """
    from app.services.ingestion_pipeline import (
        create_run_from_payload,
        finalize_run,
        ingest_test_results,
    )
    from app.services import upload_status

    task_id = self.request.id

    async def _run() -> bool:
        """Returns True on success, False on a non-retryable parse/empty error
        (which is recorded as a failed status, not raised, so Celery doesn't
        retry a file that will never parse)."""
        import time as _time

        from app.core import metrics as _m
        from app.db.postgres import AsyncSessionLocal

        nonlocal file_content
        if file_storage_key:
            from app.db.storage import get_storage_provider
            raw_content = await get_storage_provider().get_object_content(file_storage_key)
            if file_format == "archive":
                import base64
                file_content = base64.b64encode(raw_content).decode("ascii")
            else:
                file_content = raw_content.decode("utf-8", errors="replace")
        if not file_content:
            raise ValueError("Uploaded file content is missing")

        _t0 = _time.monotonic()

        def _emit_failed(code: str) -> None:
            _m.uploads_total.labels(state="failed", format=file_format).inc()
            _m.upload_failures_total.labels(code=code).inc()

        await upload_status.set_status(
            task_id, run_id=run_id, project_id=project_id,
            state=upload_status.STATE_PARSING,
        )

        # Archive the raw upload for audit/replay (MRU-9) — off the request path,
        # before parsing, so even a parse failure leaves the original recoverable.
        archive_prefix = await _archive_raw_upload(
            file_content, file_format, file_name, project_id, run_id,
        )

        # Parse — any parser exception or an empty result is a user-fixable
        # problem, surfaced as a failed status rather than a silent empty run.
        from app.services.safe_archive import UnsafeZipError
        from app.services.upload_limits import TooManyResults, enforce_result_limit
        try:
            results = _parse_file_to_results(
                file_content, file_format, file_name, run_id, disabled_formats=disabled_formats,
            )
            # Re-audit M5: the exact cap, on what was actually parsed.
            enforce_result_limit(len(results))
        except UnsafeZipError as exc:
            # Archive safety violation — surface the specific code (zip_bomb,
            # unsafe_path, …) so the UI explains exactly what was rejected.
            logger.warning("upload_unsafe_archive task=%s file=%s code=%s", task_id, file_name, exc.code)
            await upload_status.set_status(
                task_id, run_id=run_id, project_id=project_id,
                state=upload_status.STATE_FAILED,
                error={"code": exc.code, "message": exc.message},
            )
            _emit_failed(exc.code)
            return False
        except TooManyResults as exc:
            # Refused whole, before the run row is created -- like a parse error.
            logger.warning("upload_too_many_results task=%s file=%s", task_id, file_name)
            await upload_status.set_status(
                task_id, run_id=run_id, project_id=project_id,
                state=upload_status.STATE_FAILED,
                error={"code": exc.code, "message": exc.message},
            )
            _emit_failed(exc.code)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("upload_parse_error task=%s file=%s error=%s", task_id, file_name, exc)
            await upload_status.set_status(
                task_id, run_id=run_id, project_id=project_id,
                state=upload_status.STATE_FAILED,
                error={"code": "parse_error",
                       "message": f"Could not parse the {file_format} report: {str(exc)[:300]}"},
            )
            _emit_failed("parse_error")
            return False

        if not results:
            await upload_status.set_status(
                task_id, run_id=run_id, project_id=project_id,
                state=upload_status.STATE_FAILED,
                error={"code": "empty_report",
                       "message": "No test results were found in the file. "
                                  "Check that the format matches the file contents."},
            )
            _emit_failed("empty_report")
            return False

        await upload_status.set_status(
            task_id, run_id=run_id, project_id=project_id,
            state=upload_status.STATE_INGESTING, progress={"total": len(results)},
        )

        async with AsyncSessionLocal() as db:
            try:
                run = await create_run_from_payload(
                    db,
                    project_id=project_id,
                    build_number=build_number,
                    run_id=run_id,
                    branch=branch,
                    commit_hash=commit_hash,
                    release_name=release_name,
                    ingestion_source="upload",
                    # Manual uploads must never merge into an unrelated run on a
                    # build-label collision — always create a fresh run so the
                    # 202 run_id is authoritative and aggregates aren't blended.
                    reuse_existing=False,
                    ci_provider=ci_provider,
                    ci_repo=ci_repo,
                    pr_number=pr_number,
                    ci_actor=ci_actor,
                    ci_run_url=ci_run_url,
                    jenkins_job=jenkins_job,
                    environment=environment,
                    executed_at=(
                        datetime.fromisoformat(executed_at) if executed_at else None
                    ),
                    commit_range=commit_range,
                )
                if archive_prefix:
                    run.minio_prefix = archive_prefix  # link the archived raw upload (dir prefix)
                count = await ingest_test_results(db, run, results)
                await db.commit()
                logger.info(
                    "[Task %s] File ingested: %d cases from %s (%s)",
                    task_id, count, file_name, file_format,
                )
            except Exception:
                await db.rollback()
                raise

        # Finalize even an all-rejected run so aggregates stamp end_time and
        # release linking makes its incomplete evidence visible to the gate.
        # AI analysis has no stored test row to inspect in that case.
        await finalize_run(
            run_id=run_id,
            project_id=project_id,
            build_number=build_number,
            release_name=release_name,
            run_ai=run_ai if count > 0 else False,
        )

        # All rows failed to upsert despite a non-empty parse — surface as a
        # failure rather than a misleading "succeeded, 0 tests".
        if count == 0:
            await upload_status.set_status(
                task_id, run_id=run_id, project_id=project_id,
                state=upload_status.STATE_FAILED,
                error={"code": "ingest_error",
                       "message": "Parsed the report but no test results could be stored."},
            )
            _emit_failed("ingest_error")
            return False

        # Counts reflect accepted rows only; attempted/rejected make a partial
        # result explicit instead of letting the status breakdown exceed total.
        summary = _summarize_upload(
            ingested=count,
            attempted=run.ingestion_attempted_tests,
            rejected=run.ingestion_rejected_tests,
            status_counts=getattr(run, "_ingestion_accepted_status_counts", {}),
        )
        await upload_status.set_status(
            task_id, run_id=run_id, project_id=project_id,
            state=upload_status.STATE_SUCCEEDED, result=summary,
        )
        _m.uploads_total.labels(state="succeeded", format=file_format).inc()
        _m.upload_processing_seconds.observe(_time.monotonic() - _t0)
        if file_storage_key:
            try:
                from app.db.storage import get_storage_provider
                await get_storage_provider().delete_object(file_storage_key)
            except Exception as exc:  # noqa: BLE001 — cleanup is best effort
                logger.warning("queued_upload_cleanup_failed key=%s error=%s", file_storage_key, exc)
        return True

    logger.info("[Task %s] Processing uploaded file: %s (%s)", task_id, file_name, file_format)
    try:
        ok = _run_async(_run())
        if ok:
            try:
                reindex_search.apply_async(kwargs={"full": False}, countdown=5)
            except Exception:
                pass
    except Exception as exc:
        # A transient/infra failure (DB, Redis) — retry, and only surface a
        # failed status once retries are exhausted so the UI doesn't flap.
        logger.error("[Task %s] File ingest failed: %s", task_id, exc, exc_info=True)
        if self.request.retries >= self.max_retries:
            try:
                _run_async(upload_status.set_status(
                    task_id, run_id=run_id, project_id=project_id,
                    state=upload_status.STATE_FAILED,
                    error={"code": "infra_error",
                           "message": "Ingestion failed after retries. Please try again."},
                ))
            except Exception:
                pass
            # This is a real terminal — count it so failure metrics balance the
            # status writes (the inner _emit_failed closure isn't in scope here).
            try:
                from app.core import metrics as _m
                _m.uploads_total.labels(state="failed", format=file_format).inc()
                _m.upload_failures_total.labels(code="infra_error").inc()
            except Exception:
                pass
        raise self.retry(exc=exc, countdown=_exponential_backoff(self.request.retries))


def _parse_archive_to_results(
    b64_content: str, filename: str, run_id: str, disabled_formats: list = None,
) -> list[dict]:
    """Parse an uploaded report ZIP (base64 string) into normalized results.

    Safety-extracts in memory (raises UnsafeZipError on any limit violation),
    then dispatches:
      * tier 1 — Allure results dir (any ``*-result.json``) → parse_allure_zip
      * tier 2 — heterogeneous archive (e.g. N JUnit XMLs) → per-entry detect +
        the existing single-file parsers, concatenated.

    ``disabled_formats`` (resolved at the router from the cypress/playwright
    feature flags) are skipped in tier 2 so zipping a gated report can't bypass
    the admin's flag.
    """
    import base64
    import posixpath

    from app.core.config import settings
    from app.services.allure_parser import parse_allure_zip
    from app.services.safe_archive import safe_extract_zip
    from app.services.upload_limits import TooManyResults, enforce_result_limit

    def _basename(n: str) -> str:
        return posixpath.basename(n)

    def _is_noise(n: str) -> bool:
        # macOS resource-fork / dotfile noise — skipped before decompression.
        return "__MACOSX" in n or _basename(n).startswith(".")

    raw = base64.b64decode(b64_content)
    files = safe_extract_zip(
        raw,
        max_total=settings.MAX_ARCHIVE_UNCOMPRESSED_BYTES,
        max_entries=settings.MAX_ARCHIVE_ENTRIES,
        max_entry=settings.MAX_ARCHIVE_ENTRY_BYTES,
        max_ratio=settings.MAX_ARCHIVE_RATIO,
        skip_name=_is_noise,
    )
    del raw  # free the compressed copy; the decompressed map is bounded by limits

    gated_hit: set = set()  # formats skipped purely because their flag is off

    # Tier 1 — Allure results directory.
    if any(_basename(n).lower().endswith("-result.json") for n in files):
        results = parse_allure_zip(files, run_id, s3_prefix=f"uploads/{run_id}")
    else:
        # Tier 2 — heterogeneous archive: detect + parse each entry.
        from app.routers.ingest import _detect_format  # lazy: avoid import cycle

        disabled = set(disabled_formats or [])
        results = []
        for name, data in files.items():
            base = _basename(name)
            try:
                entry_fmt = _detect_format(base, data)
                if entry_fmt in disabled:
                    # Admin disabled this format — don't let a zip bypass the gate.
                    gated_hit.add(entry_fmt)
                    logger.warning("archive_entry_format_disabled entry=%s fmt=%s", base, entry_fmt)
                    continue
                text = data.decode("utf-8", errors="replace")
                results.extend(_parse_file_to_results(text, entry_fmt, base, run_id))
                # Re-audit M5: the cap covers the whole upload, not each entry.
                enforce_result_limit(len(results))
            except TooManyResults:
                # Not 'one bad entry': the upload as a whole is refused.
                raise
            except Exception as exc:  # noqa: BLE001 — one bad entry must not fail all
                logger.warning("archive_entry_parse_failed entry=%s error=%s", base, exc)
                continue

    if files and not results:
        # A zip whose only candidates were admin-disabled formats gets a message
        # matching the single-file 503, not a misleading "unparseable".
        if gated_hit:
            raise ValueError(
                f"Ingestion is disabled for: {', '.join(sorted(gated_hit))}. "
                "Ask an admin to enable the feature flag."
            )
        # Otherwise: had candidate files but none parsed (distinct from a truly
        # empty/noise-only archive, which returns [] → empty_report).
        raise ValueError(
            f"Archive contained {len(files)} file(s) but none could be parsed as a "
            "supported report (JUnit/TestNG XML, or Allure/Playwright/Cypress JSON)."
        )
    return results


async def _archive_raw_upload(
    file_content: str, file_format: str, file_name: str, project_id: str, run_id: str,
):
    """Archive the byte-exact raw upload to storage for audit/replay (MRU-9).

    Best-effort: a storage failure logs and returns None (the ingest proceeds;
    run.minio_prefix stays NULL). Returns the DIRECTORY prefix (trailing slash)
    — matching the sentinel-path convention for ``minio_prefix`` — under which
    the object is stored.
    """
    import base64

    from app.db.storage import get_storage_provider

    try:
        safe_name = (file_name or "report").replace("/", "_").replace("\\", "_").lstrip(".") or "report"
        prefix = f"uploads/{project_id}/{run_id}/"
        raw = (
            base64.b64decode(file_content)
            if file_format == "archive"
            else file_content.encode("utf-8", errors="replace")
        )
        await get_storage_provider().put_object(
            prefix + safe_name, raw, content_type="application/octet-stream",
        )
        return prefix
    except Exception as exc:  # noqa: BLE001 — archival is advisory
        logger.warning("upload_raw_archive_failed run=%s error=%s", run_id, exc)
        return None


def _summarize_upload(
    *,
    ingested: int,
    attempted: int,
    rejected: int,
    status_counts: dict[str, int],
) -> dict:
    """Build the SUCCEEDED-status result summary.

    Every count describes accepted rows. The former implementation counted
    statuses across parsed rows while reporting an accepted-only total, so one
    rejected result could make the breakdown add up to more than ``total``.
    """
    return {
        "total": ingested,
        "attempted": attempted,
        "rejected": rejected,
        "complete": rejected == 0,
        "passed": status_counts.get("PASSED", 0),
        "failed": status_counts.get("FAILED", 0),
        "skipped": status_counts.get("SKIPPED", 0),
        "broken": status_counts.get("BROKEN", 0),
        "unknown": status_counts.get("UNKNOWN", 0),
    }


def _parse_file_to_results(
    content: str, fmt: str, filename: str, run_id: str, disabled_formats: list = None,
) -> list[dict]:
    """Parse a test result file into normalized result dicts.

    Dispatch table — each parser returns ``list[dict]`` matching the
    TestLookup ingestion contract. New frameworks register here and add
    their content-sniff rules in ``routers/ingest._detect_format``.

    For ``fmt == "archive"`` ``content`` is a base64-encoded zip (Celery's JSON
    serializer can't carry raw bytes); see ``_parse_archive_to_results``.
    """
    import json as _json

    if fmt == "archive":
        return _parse_archive_to_results(content, filename, run_id, disabled_formats=disabled_formats)

    # Re-audit M5: a report so far over the result cap that parsing it would
    # itself exhaust the worker is refused on a cheap count, unparsed.
    from app.services.upload_limits import refuse_before_parsing

    refuse_before_parsing(content, fmt)

    if fmt == "allure":
        from app.services.allure_parser import parse_allure_result
        try:
            raw = _json.loads(content)
        except _json.JSONDecodeError as exc:
            # Surface as a parse error (the upload task turns this into a
            # 'parse_error' status) instead of a silent empty run.
            raise ValueError(f"invalid JSON in Allure file '{filename}': {exc}") from exc
        items = raw if isinstance(raw, list) else [raw]
        results = []
        for item in items:
            parsed = parse_allure_result(item, run_id, filename)
            if parsed:
                results.append(parsed)
        return results

    if fmt == "testng":
        from app.services.testng_parser import parse_testng_xml
        return parse_testng_xml(content, run_id)

    if fmt == "cypress":
        from app.services.cypress_parser import parse_cypress_json
        return parse_cypress_json(content, run_id)

    if fmt == "playwright":
        from app.services.playwright_parser import parse_playwright_json
        return parse_playwright_json(content, run_id)

    if fmt == "pytest":
        from app.services.pytest_parser import parse_pytest_json
        return parse_pytest_json(content, run_id)

    if fmt == "robot":
        from app.services.robot_parser import parse_robot_xml
        return parse_robot_xml(content, run_id)

    if fmt == "cucumber":
        from app.services.cucumber_parser import parse_cucumber_json
        return parse_cucumber_json(content, run_id)

    if fmt == "nunit":
        from app.services.nunit_parser import parse_nunit_xml
        return parse_nunit_xml(content, run_id)

    if fmt == "trx":
        from app.services.trx_parser import parse_trx_xml
        return parse_trx_xml(content, run_id)

    if fmt == "xunit":
        from app.services.xunit_parser import parse_xunit_xml
        return parse_xunit_xml(content, run_id)

    # junit (default) — reuse testng_parser which handles standard JUnit XML too
    from app.services.testng_parser import parse_testng_xml
    return parse_testng_xml(content, run_id)


@celery_app.task(
    name="app.worker.tasks.run_live_test_analysis",
    bind=True,
    max_retries=2,
    queue="critical",
    time_limit=120,
    priority=9,
)
def run_live_test_analysis(
    self,
    test_case_id: str,
    test_name: str,
    run_id: str,
    project_id: str,
):
    """
    Immediate root-cause analysis for a single test that failed during live execution.
    Runs on the critical queue (priority=9) so results appear in the dashboard fast.
    Protected by the LLM circuit breaker — skips silently if the provider is down.
    """
    from app.services.analysis_router import classify_test
    from app.streams.circuit_breaker import LLMCircuitBreaker

    async def _run():
        if not await LLMCircuitBreaker.is_available():
            retry_after = await LLMCircuitBreaker.retry_after_seconds()
            logger.info(
                "[Task %s] Circuit open — skipping live analysis for %s (retry in %ds)",
                self.request.id, test_name, retry_after,
            )
            return None

        try:
            # Load the failure text. This task is handed only ids, but the
            # failure text IS the input to classification — passing just a name
            # left every tier with nothing to reason about, so live analysis
            # returned UNKNOWN for every test no matter how obvious the error.
            # Best-effort: a failed read degrades to the old id-only payload
            # rather than dropping the analysis entirely.
            failure: dict[str, Any] = {}
            try:
                from sqlalchemy import select as _select  # noqa: PLC0415

                from app.db.postgres import AsyncSessionLocal  # noqa: PLC0415
                from app.models.postgres import TestCase  # noqa: PLC0415

                async with AsyncSessionLocal() as _db:
                    row = (
                        await _db.execute(
                            _select(TestCase).where(TestCase.id == uuid.UUID(test_case_id))
                        )
                    ).scalar_one_or_none()
                    if row is not None:
                        failure = {
                            "error_message": row.error_message,
                            "stack_trace": row.stack_trace,
                            "suite_name": row.suite_name,
                            "duration_ms": row.duration_ms,
                            "severity": row.severity,
                        }
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[Task %s] could not load failure text for %s: %s",
                    self.request.id, test_case_id, exc,
                )

            # Spelled out rather than splatted in: the regression guard checks
            # that each call site's literal payload names the failure text, and
            # a `**failure` splat would hide the contract from it — leaving the
            # very call this fix repaired unprotected.
            result = await classify_test(
                test_case={
                    "test_case_id": test_case_id,
                    "test_name": test_name,
                    "run_id": run_id,
                    "project_id": project_id,
                    "error_message": failure.get("error_message"),
                    "stack_trace": failure.get("stack_trace"),
                    "suite_name": failure.get("suite_name"),
                    "duration_ms": failure.get("duration_ms"),
                    "severity": failure.get("severity"),
                },
                run_context={"run_id": run_id, "project_id": project_id},
            )
            await LLMCircuitBreaker.record_success()
            return result
        except Exception:
            await LLMCircuitBreaker.record_failure()
            raise

    logger.info("[Task %s] Live analysis for test=%s run=%s", self.request.id, test_name, run_id)
    try:
        result = _run_async(_run())
        if result:
            logger.info(
                "[Task %s] Live analysis complete. confidence=%s",
                self.request.id, result.get("confidence_score"),
            )
        return result
    except Exception as exc:
        logger.error("[Task %s] Live analysis failed: %s", self.request.id, exc, exc_info=True)
        countdown = _exponential_backoff(self.request.retries, base=10, cap=60)
        raise self.retry(exc=exc, countdown=countdown)


@celery_app.task(
    name="app.worker.tasks.run_ai_analysis",
    bind=True,
    max_retries=2,
    queue="ai_analysis",
    time_limit=180,
)
def run_ai_analysis(self, test_case_id: str, test_name: str, **kwargs):
    """
    Background task: run the LangChain ReAct agent for a single test case.
    Used by the offline auto-analyzer. Protected by the LLM circuit breaker.
    """
    from app.services.analysis_router import classify_test
    from app.streams.circuit_breaker import LLMCircuitBreaker

    async def _run():
        if not await LLMCircuitBreaker.is_available():
            retry_after = await LLMCircuitBreaker.retry_after_seconds()
            raise RuntimeError(f"LLM circuit open — retry in {retry_after}s")

        try:
            test_case = {
                "test_case_id": test_case_id,
                "test_name": test_name,
                **kwargs,
            }
            run_context = test_case.pop("run_context", None) or {
                "run_id": test_case.get("run_id"),
                "project_id": test_case.get("project_id"),
            }
            result = await classify_test(
                test_case=test_case,
                history=test_case.get("history"),
                run_context=run_context,
                mode=test_case.get("mode"),
            )
            await LLMCircuitBreaker.record_success()
            return result
        except Exception:
            await LLMCircuitBreaker.record_failure()
            raise

    logger.info("[Task %s] AI analysis for: %s", self.request.id, test_name)
    try:
        result = _run_async(_run())
        logger.info("[Task %s] Analysis complete. confidence=%s", self.request.id, result.get("confidence_score"))
        return result
    except Exception as exc:
        logger.error("[Task %s] AI analysis failed: %s", self.request.id, exc, exc_info=True)
        countdown = _exponential_backoff(self.request.retries, base=60, cap=300)
        raise self.retry(exc=exc, countdown=countdown)


@celery_app.task(
    name="app.worker.tasks.dispatch_run_notifications",
    base=DownstreamTrackedTask,
    bind=True,
    max_retries=2,
    default_retry_delay=15,
    queue="default",
)
def dispatch_run_notifications(
    self,
    project_id: str,
    run_id: str,
    build_number: str,
    pass_rate: float,
    total_tests: int,
    failed_tests: int,
    project_name: str,
    dashboard_url: str = "",
):
    """Background task: fan-out run-completion notifications to all subscribed users.

    When ``dashboard_url`` is empty, builds an absolute link from
    ``settings.public_base_url`` so notifications rendered in Slack/Teams/email
    contain a clickable link regardless of where the cluster is deployed.
    """
    import uuid as _uuid
    from app.core.config import settings
    from app.services.notification.manager import dispatch_run_notifications as _dispatch
    request_headers = getattr(self.request, "headers", None) or {}
    downstream_outbox_id = request_headers.get("downstream_outbox_id")

    if not dashboard_url or dashboard_url == "#":
        dashboard_url = f"{settings.public_base_url}/runs/{run_id}"

    logger.info("[Task %s] Dispatching run notifications for build=%s", self.request.id, build_number)
    try:
        _run_async(_dispatch(
            project_id=_uuid.UUID(project_id),
            run_id=_uuid.UUID(run_id),
            build_number=build_number,
            pass_rate=pass_rate,
            total_tests=total_tests,
            failed_tests=failed_tests,
            project_name=project_name,
            dashboard_url=dashboard_url,
            delivery_scope=(
                f"run-outbox:{downstream_outbox_id}"
                if downstream_outbox_id
                else None
            ),
        ))
    except Exception as exc:
        logger.error("[Task %s] Notification dispatch failed: %s", self.request.id, exc, exc_info=True)
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.worker.tasks.dispatch_transition_notifications",
    base=DownstreamTrackedTask,
    bind=True,
    max_retries=2,
    default_retry_delay=15,
    queue="default",
)
def dispatch_transition_notifications(self, run_id: str):
    """Background task: evaluate transition events (PMF US-7.1/US-7.2) for a
    finalized run and send the batched, cluster-deduped notification.

    Idempotent per run — the state store stamps each fingerprint with the
    run id, so a retry (or a re-finalized run) advances nothing and sends
    nothing.
    """
    import uuid as _uuid
    from app.services.notification_transitions import (
        TransitionOrderPending,
        evaluate_run_transitions,
    )

    logger.info("[Task %s] Evaluating transition notifications for run=%s", self.request.id, run_id)

    # PMF US-5.5 — quarantine stability tracking rides the same finalization
    # hook. Own try/except: a stability fault must neither block nor retry
    # the transition dispatch, and the tracker is idempotent per (request,
    # run) via last_stability_run_id, so a retry of THIS task (triggered by
    # the transition evaluation below) advances nothing twice.
    try:
        from app.services.flaky_quarantine_service import update_quarantine_stability
        stability = _run_async(update_quarantine_stability(_uuid.UUID(run_id)))
        if stability.get("tracked"):
            logger.info(
                "[Task %s] Quarantine stability update: %s", self.request.id, stability,
            )
    except Exception as exc:
        logger.warning(
            "[Task %s] Quarantine stability update failed: %s", self.request.id, exc,
        )

    try:
        result = _run_async(evaluate_run_transitions(_uuid.UUID(run_id)))
        logger.info("[Task %s] Transition evaluation done: %s", self.request.id, result)
        return result
    except TransitionOrderPending as exc:
        logger.info(
            "[Task %s] Transition evaluation waiting for an older run: %s",
            self.request.id,
            exc,
        )
        raise self.retry(exc=exc, countdown=5, max_retries=100)
    except Exception as exc:
        logger.error("[Task %s] Transition notification dispatch failed: %s", self.request.id, exc, exc_info=True)
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.worker.tasks.run_agent_pipeline",
    base=DownstreamTrackedTask,
    bind=True,
    # E7.2: Celery does not retry this task. A retryable failure moves the
    # pipeline row to ``retry_wait`` and schedules a same-id resume under the
    # shared RetryPolicy (see _schedule_pipeline_retry), so the attempt count
    # and the next retry time live on the row a user can see. Outbox-driven
    # deliveries keep PostgreSQL-owned retries (DownstreamTrackedTask).
    max_retries=0,
    queue="ai_analysis",
    time_limit=1800,
)
def run_agent_pipeline(
    self,
    test_run_id: str,
    project_id: str,
    build_number: str,
    workflow_type: str = "offline",
):
    """
    Background task: run the full multi-agent LangGraph pipeline for a completed test run.
    Stages: ingestion → anomaly detection → root-cause analysis → summary → triage
    Deduplicates by test_run_id so multiple triggers for the same run don't stack up.
    Moves to DLQ after max retries.
    """
    _bind_task_context(self, run_id=test_run_id, project_id=project_id, workflow_type=workflow_type)
    from app.agents.workflow import run_offline_pipeline, run_deep_pipeline

    # Include workflow_type in dedup key so a deep run isn't blocked by a prior offline run
    dedup_key = f"testlookup:dedup:pipeline:{test_run_id}:{workflow_type}"
    dedup_owner = str(self.request.id)
    request_headers = getattr(self.request, "headers", None) or {}
    source_outbox_id = request_headers.get("downstream_outbox_id")
    cost_budget_mode_override = request_headers.get("ai_mode_override")
    durable_pipeline_id = (
        str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"testlookup:agent-pipeline:{source_outbox_id}",
            )
        )
        if source_outbox_id
        else None
    )

    async def _run():
        if durable_pipeline_id:
            from app.db.postgres import AsyncSessionLocal
            from app.services.run_downstream_outbox import (
                repair_terminal_ai_summary_operation,
            )

            async with AsyncSessionLocal() as db:
                repaired = await repair_terminal_ai_summary_operation(
                    db,
                    pipeline_run_id=uuid.UUID(durable_pipeline_id),
                    run_id=uuid.UUID(test_run_id),
                    project_id=uuid.UUID(project_id),
                    build_number=build_number,
                )
                if repaired is not None:
                    await db.commit()
                    return {
                        "completed_stages": (
                            ["summary"] if repaired["summary_completed"] else []
                        ),
                        "errors": [],
                        "duplicate": True,
                        "terminal_replay": True,
                        **repaired,
                    }

        # A durable outbox delivery must establish/check its deterministic SQL
        # pipeline identity. A Redis key from a concurrent manual/debounced
        # request is not proof that this requested operation completed, and
        # treating it as such would let DownstreamTrackedTask close the outbox
        # without any matching AgentPipelineRun. Direct callers retain the
        # short-lived Redis admission guard.
        if not source_outbox_id and await _is_duplicate(dedup_key, ttl=7200, owner=dedup_owner):
            logger.info(
                "[Task %s] Skipping duplicate pipeline for run=%s type=%s",
                self.request.id, test_run_id, workflow_type,
            )
            return {"completed_stages": [], "error_count": 0, "duplicate": True}

        if workflow_type == "deep" and not source_outbox_id:
            return await run_deep_pipeline(
                test_run_id=test_run_id,
                project_id=project_id,
                build_number=build_number,
                cost_budget_mode_override=cost_budget_mode_override,
            )
        return await run_offline_pipeline(
            test_run_id=test_run_id,
            project_id=project_id,
            build_number=build_number,
            workflow_type=workflow_type,
            pipeline_run_id=durable_pipeline_id,
            create_if_missing=durable_pipeline_id is not None,
            cost_budget_mode_override=cost_budget_mode_override,
        )

    logger.info(
        "[Task %s] Starting agent pipeline run=%s build=%s type=%s",
        self.request.id, test_run_id, build_number, workflow_type,
    )
    _pipeline_started = time.perf_counter()
    try:
        final_state = _run_async(_run())
        stages_done = final_state.get("completed_stages", [])
        errors = final_state.get("errors", [])
        # TestLookupAIPipelineFailures alerts on this counter and nothing
        # incremented it, so the alert could never fire (verified live, 0
        # samples). A dedup short-circuit is not a completion — counting it
        # would inflate the success rate with runs that never executed.
        if not final_state.get("duplicate"):
            _count_pipeline(workflow_type, "success")
            # Same reasoning as the counter above: a dedup short-circuit did no
            # work, and timing it would drag the latency distribution toward
            # zero with runs that never executed a stage.
            _observe_pipeline_duration(
                workflow_type, time.perf_counter() - _pipeline_started
            )
        logger.info(
            "[Task %s] Pipeline complete. stages=%s errors=%d",
            self.request.id, stages_done, len(errors),
        )

        # BL-03: Invalidate cached intelligence snapshot so next read recomputes
        try:
            import uuid as _uuid
            from app.services.intelligence_snapshot_service import invalidate as _invalidate_snapshot
            from app.db.postgres import AsyncSessionLocal
            async def _do_invalidate():
                async with AsyncSessionLocal() as _db:
                    await _invalidate_snapshot(_db, _uuid.UUID(test_run_id))
            _run_async(_do_invalidate())
            logger.debug("[Task %s] Intelligence snapshot invalidated for run %s", self.request.id, test_run_id)
        except Exception as inv_exc:
            logger.warning(
                "[Task %s] Snapshot invalidation failed (non-blocking, %s)",
                self.request.id,
                type(inv_exc).__name__,
            )

        return {"completed_stages": stages_done, "error_count": len(errors)}
    except Exception as exc:
        safe_error = f"{type(exc).__name__}: agent pipeline failed"
        _count_pipeline(workflow_type, "failure")
        _observe_pipeline_duration(
            workflow_type, time.perf_counter() - _pipeline_started
        )
        logger.error(
            "[Task %s] Pipeline failed (%s)",
            self.request.id,
            type(exc).__name__,
        )
        if source_outbox_id:
            # Outbox deliveries: PostgreSQL owns the retry (DownstreamTrackedTask
            # defers the row); a Celery-side reschedule would race the relay.
            raise RuntimeError(safe_error) from None

        scheduled = _run_async(
            _schedule_pipeline_retry(
                test_run_id=test_run_id,
                workflow_type=workflow_type,
                build_number=build_number,
                pipeline_run_id=getattr(exc, "pipeline_run_id", None),
                dedup_key=dedup_key,
                dedup_owner=dedup_owner,
                error=safe_error,
            )
        )
        if scheduled is not None:
            # The row is in retry_wait with next_retry_at set; the resume task
            # is queued. This attempt is over, but the RUN is not failed, so the
            # task returns instead of raising (a raise would count as a second
            # failure and mislead Celery's result store).
            return {
                "completed_stages": [],
                "error_count": 1,
                "retry_scheduled": True,
                **scheduled,
            }
        # Retries exhausted (or nothing to retry): release the admission lock so
        # a manual re-trigger can start a fresh run, park the failure in the
        # DLQ, and let the exception propagate as a real task failure.
        try:
            _run_async(_release_duplicate_lock(dedup_key, dedup_owner))
        except Exception as release_exc:
            logger.warning(
                "[Task %s] Failed to release pipeline dedup lock after error (%s)",
                self.request.id,
                type(release_exc).__name__,
            )
        _run_async(_send_to_dlq(
            task_name=self.name,
            task_id=self.request.id,
            kwargs={"test_run_id": test_run_id, "build_number": build_number},
            error=safe_error,
        ))
        raise RuntimeError(safe_error) from None


async def _schedule_pipeline_retry(
    *,
    test_run_id: str,
    workflow_type: str,
    build_number: str,
    pipeline_run_id: str | None,
    dedup_key: str,
    dedup_owner: str,
    error: str,
) -> dict | None:
    """Move a just-failed pipeline to ``retry_wait`` and queue its same-id resume.

    Returns the scheduling facts (``pipeline_run_id``, ``attempt``,
    ``next_retry_at``, ``delay_seconds``) or ``None`` when no further attempt is
    allowed (attempt ceiling reached, row not found, or already moved on by
    another writer). The caller then treats the run as failed.

    The dedup lock is EXTENDED to cover the wait, not released: releasing it
    would let a manual trigger or a webhook redelivery start a second
    concurrent run in the gap before the scheduled resume fires (the mirror
    image of the "dedup lock silences its own retry" defect). The state
    machine, not the lock, is what refuses a new trigger while a run is
    in progress; the lock is a belt for the case where that check races.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.db.postgres import AsyncSessionLocal
    from app.db.redis_client import get_redis
    from app.models.postgres import AgentPipelineRun
    from app.services.retry_policy import pipeline_retry_policy
    from app.services.workflow_run_state import (
        IllegalTransition,
        PipelineRunStatus,
        apply_transition,
        normalize_status,
    )

    async with AsyncSessionLocal() as db:
        if pipeline_run_id:
            stmt = select(AgentPipelineRun).where(
                AgentPipelineRun.id == uuid.UUID(str(pipeline_run_id))
            )
        else:
            stmt = (
                select(AgentPipelineRun)
                .where(
                    AgentPipelineRun.test_run_id == uuid.UUID(str(test_run_id)),
                    AgentPipelineRun.workflow_type == workflow_type,
                    AgentPipelineRun.status == PipelineRunStatus.FAILED.value,
                )
                .order_by(AgentPipelineRun.created_at.desc())
                .limit(1)
            )
        row = (await db.execute(stmt.with_for_update())).scalar_one_or_none()
        if row is None or normalize_status(row.status) is not PipelineRunStatus.FAILED:
            return None

        attempt = int(row.attempt or 1)
        policy = pipeline_retry_policy(max_attempts=row.max_attempts)
        if not policy.can_retry(attempt):
            return None

        delay = policy.delay(attempt)
        next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        try:
            apply_transition(row, PipelineRunStatus.RETRY_WAIT, error=error)
        except IllegalTransition:
            return None
        row.next_retry_at = next_retry_at
        await db.commit()
        row_id = str(row.id)

    # Keep the admission lock alive until the resume has had its chance.
    try:
        redis = get_redis()
        await redis.expire(dedup_key, int(delay) + 300)
    except Exception as exc:  # noqa: BLE001 -- the schedule is the truth, not the lock
        logger.warning(
            "[pipeline %s] dedup lock extend failed (%s)", row_id, type(exc).__name__
        )

    resume_agent_pipeline.apply_async(
        kwargs={
            "pipeline_run_id": row_id,
            "build_number": build_number,
            "expected_attempt": attempt + 1,
        },
        countdown=int(delay),
        queue="ai_analysis",
    )
    logger.info(
        "[pipeline %s] attempt %d failed; retry %d/%d scheduled in %ds",
        row_id, attempt, attempt + 1, policy.max_attempts, int(delay),
    )
    return {
        "pipeline_run_id": row_id,
        "attempt": attempt,
        "next_attempt": attempt + 1,
        "max_attempts": policy.max_attempts,
        "delay_seconds": int(delay),
        "next_retry_at": next_retry_at.isoformat(),
    }


@celery_app.task(
    name="app.worker.tasks.resume_agent_pipeline",
    bind=True,
    max_retries=0,
    queue="ai_analysis",
    time_limit=1800,
)
def resume_agent_pipeline(
    self,
    pipeline_run_id: str,
    build_number: str = "resume",
    expected_attempt: int | None = None,
):
    """Resume a failed / retry_wait / degraded pipeline under its existing id.

    The workflow atomically claims the row, preserves the immutable initial
    plan, and replays only checksum-authorized completed checkpoints.
    Duplicate deliveries return ``pipeline_not_resumable`` without spending
    another model call.

    ``expected_attempt`` (E7.2) is set by the scheduled retry: the claim
    succeeds only if the row's next attempt number is exactly this one, so a
    stale scheduled resume (the run was cancelled, or a manual retry already
    ran) exits without side effects instead of resurrecting the run.
    """
    _bind_task_context(self, pipeline_run_id=pipeline_run_id)
    from app.agents.workflow import resume_pipeline

    try:
        return _run_async(
            resume_pipeline(pipeline_run_id, build_number, expected_attempt=expected_attempt)
        )
    except Exception as exc:
        logger.error(
            "[Task %s] Pipeline resume failed (%s)",
            self.request.id,
            type(exc).__name__,
        )
        raise RuntimeError(f"{type(exc).__name__}: pipeline resume failed") from None
@celery_app.task(
    name="app.worker.tasks.run_agent_investigation",
    bind=True,
    max_retries=0,
    queue="ai_analysis",
    time_limit=1800,
)
def run_agent_investigation(self, investigation_id: str):
    """Background task: execute one hypothesis-loop investigation (AI-1).

    Dispatched by the manual endpoint and the auto-trigger hooks
    (``test.newly_failing`` transitions / gate NO_GO). No retries by design:
    the workflow's own error path marks the investigation row ``failed``
    and writes the AgentRun ledger entry, and a blind retry could double-run
    LLM budgets. Duplicate protection is the one-active-per-run partial
    unique index at trigger time plus the runner's queued-status check.
    """
    _bind_task_context(self, investigation_id=investigation_id)
    from app.agents.investigator.workflow import run_investigation

    logger.info(
        "[Task %s] Starting investigation %s", self.request.id, investigation_id,
    )
    try:
        final_state = _run_async(run_investigation(investigation_id))
        verdict = (final_state or {}).get("verdict") or {}
        logger.info(
            "[Task %s] Investigation %s finished: primary_cause=%s",
            self.request.id, investigation_id, verdict.get("primary_cause"),
        )
        return {
            "investigation_id": investigation_id,
            "primary_cause": verdict.get("primary_cause"),
        }
    except Exception as exc:
        # The workflow already finalized the row as failed + wrote the
        # ledger entry; surface the failure to Celery without retrying.
        logger.error(
            "[Task %s] Investigation %s failed (%s)",
            self.request.id, investigation_id, type(exc).__name__,
        )
        raise


@celery_app.task(
    name="app.worker.tasks.run_agent_child_investigation",
    bind=True,
    max_retries=0,
    queue="agent_children",
    time_limit=900,
)
def run_agent_child_investigation(self, investigation_id: str):
    """Execute one ID-only, cluster-scoped child on the isolated queue."""
    _bind_task_context(self, investigation_id=investigation_id)
    from app.agents.investigator.workflow import run_investigation

    logger.info(
        "[Task %s] Starting cluster child investigation %s",
        self.request.id,
        investigation_id,
    )
    try:
        final_state = _run_async(run_investigation(investigation_id))
        verdict = (final_state or {}).get("verdict") or {}
        return {
            "investigation_id": investigation_id,
            "primary_cause": verdict.get("primary_cause"),
        }
    except Exception as exc:
        logger.error(
            "[Task %s] Cluster child investigation %s failed (%s)",
            self.request.id,
            investigation_id,
            type(exc).__name__,
        )
        raise



@celery_app.task(
    name="app.worker.tasks.resume_agent_child_investigation",
    bind=True,
    max_retries=0,
    queue="agent_children",
    time_limit=900,
)
def resume_agent_child_investigation(self, investigation_id: str):
    """Resume a failed cluster child under its stable investigation identity."""
    _bind_task_context(self, investigation_id=investigation_id)
    from app.agents.investigator.workflow import resume_investigation

    try:
        return _run_async(resume_investigation(investigation_id))
    except Exception as exc:
        _slog.error(
            "cluster_child_resume_failed",
            investigation_id=investigation_id,
            error_type=type(exc).__name__,
        )
        raise RuntimeError(
            f"{type(exc).__name__}: child resume failed"
        ) from None


@celery_app.task(
    name="app.worker.tasks.relay_agent_child_dispatch_outbox",
    bind=True,
    max_retries=0,
    queue="default",
    time_limit=120,
)
def relay_agent_child_dispatch_outbox(self):
    """Recover pending/stale cluster-child dispatches from the PG outbox."""
    _bind_task_context(self)
    from app.services.cluster_investigation_orchestrator import relay_child_dispatch_outbox

    try:
        return _run_async(relay_child_dispatch_outbox())
    except Exception as exc:
        logger.error(
            "[Task %s] Cluster child outbox relay failed (%s)",
            self.request.id,
            type(exc).__name__,
        )
        raise


@celery_app.task(
    name="app.worker.tasks.process_decision_report_supersessions",
    bind=True,
    max_retries=0,
    queue="default",
    time_limit=120,
)
def process_decision_report_supersessions(self):
    """Publish terminal child-enriched report versions from durable requests."""
    _bind_task_context(self)
    from app.services.decision_report_supersession_service import (
        process_pending_decision_report_supersessions,
    )

    try:
        return _run_async(process_pending_decision_report_supersessions())
    except Exception as exc:  # noqa: BLE001
        _slog.error(
            "decision_report_supersession_failed",
            error_type=type(exc).__name__,
        )
        raise RuntimeError(
            f"{type(exc).__name__}: report supersession failed"
        ) from None


@celery_app.task(
    name="app.worker.tasks.relay_agent_action_dispatch_outbox",
    bind=True,
    max_retries=0,
    queue="default",
    time_limit=120,
)
def relay_agent_action_dispatch_outbox(self):
    """Publish approved action IDs to the guarded action executor."""
    _bind_task_context(self)
    from app.services.agent_action_ledger_service import relay_action_dispatch_outbox

    try:
        return _run_async(relay_action_dispatch_outbox())
    except Exception as exc:
        _slog.error(
            "agent_action_dispatch_relay_failed",
            error_type=type(exc).__name__,
        )
        raise RuntimeError(
            f"{type(exc).__name__}: action dispatch relay failed"
        ) from None


@celery_app.task(
    name="app.worker.tasks.relay_run_downstream_outbox",
    bind=True,
    max_retries=0,
    queue="default",
    time_limit=120,
)
def relay_run_downstream_outbox(self):
    """Publish durable post-ingestion intents whose retry time has arrived."""
    _bind_task_context(self)
    from app.services.run_downstream_outbox import relay_downstream_outbox

    try:
        return _run_async(relay_downstream_outbox())
    except Exception as exc:  # noqa: BLE001
        _slog.error(
            "run_downstream_outbox_relay_failed",
            error_type=type(exc).__name__,
        )
        raise RuntimeError(
            f"{type(exc).__name__}: run downstream outbox relay failed"
        ) from None


@celery_app.task(
    name="app.worker.tasks.recover_waiting_run_finalizations",
    bind=True,
    max_retries=0,
    queue="default",
    time_limit=1860,
)
def recover_waiting_run_finalizations(self):
    """Resume finalization after a worker died before opening its child gate."""
    _bind_task_context(self)
    from app.services.run_downstream_outbox import recover_waiting_finalizations

    try:
        return _run_async(recover_waiting_finalizations())
    except Exception as exc:  # noqa: BLE001
        _slog.error(
            "waiting_run_finalization_recovery_failed",
            error_type=type(exc).__name__,
        )
        raise RuntimeError(
            f"{type(exc).__name__}: waiting run finalization recovery failed"
        ) from None


@celery_app.task(
    name="app.worker.tasks.relay_pending_webhook_deliveries",
    bind=True,
    max_retries=0,
    queue="default",
    time_limit=120,
)
def relay_pending_webhook_deliveries(self):
    """Republish webhook rows stranded before broker acceptance."""
    _bind_task_context(self)
    from app.services.webhook_service import relay_pending_webhook_deliveries as _relay

    try:
        return _run_async(_relay())
    except Exception as exc:  # noqa: BLE001
        _slog.error(
            "webhook_delivery_relay_failed",
            error_type=type(exc).__name__,
        )
        raise RuntimeError(
            f"{type(exc).__name__}: webhook delivery relay failed"
        ) from None


@celery_app.task(
    name="app.worker.tasks.relay_pending_notification_deliveries",
    bind=True,
    max_retries=0,
    queue="default",
    time_limit=180,
)
def relay_pending_notification_deliveries(self):
    """Deliver and retry durable per-channel notification children."""
    _bind_task_context(self)
    from app.services.notification.manager import (
        relay_pending_notification_deliveries as _relay,
    )

    try:
        return _run_async(_relay())
    except Exception as exc:  # noqa: BLE001
        _slog.error(
            "notification_delivery_relay_failed",
            error_type=type(exc).__name__,
        )
        raise RuntimeError(
            f"{type(exc).__name__}: notification delivery relay failed"
        ) from None


@celery_app.task(
    name="app.worker.tasks.execute_agent_action",
    bind=True,
    max_retries=0,
    queue="default",
    time_limit=120,
)
def execute_agent_action(self, project_id: str, action_id: str):
    """Resolve and consume one action; unregistered effects fail closed."""
    _bind_task_context(self, project_id=project_id, action_id=action_id)
    from app.services.agent_action_ledger_service import execute_agent_action as _execute

    try:
        return _run_async(
            _execute(
                project_id=uuid.UUID(str(project_id)),
                action_id=uuid.UUID(str(action_id)),
            )
        )
    except Exception as exc:
        _slog.error(
            "agent_action_execution_failed",
            error_type=type(exc).__name__,
        )
        raise RuntimeError(
            f"{type(exc).__name__}: action execution failed"
        ) from None


@celery_app.task(
    name="app.worker.tasks.run_fixer_run_task",
    bind=True,
    max_retries=0,
    queue="ai_analysis",
    time_limit=3600,
)
def run_fixer_run_task(self, project_id: str, fixer_run_id: str, triggered_by: str = "scheduled"):
    """Background task: execute one budgeted Fixer run (AI-2).

    Dispatched by the manual endpoint and the scheduler beats. No retries by
    design: a blind retry could double-run validation budgets / re-open PRs.
    The workflow writes the ``agent_runs`` ledger entry on completion.
    """
    _bind_task_context(self, project_id=project_id, fixer_run_id=fixer_run_id)
    from app.agents.fixer.workflow import run_fixer_run

    logger.info(
        "[Task %s] Starting fixer run %s (project %s)",
        self.request.id, fixer_run_id, project_id,
    )
    try:
        result = _run_async(run_fixer_run(project_id, fixer_run_id, triggered_by))
        logger.info(
            "[Task %s] Fixer run %s finished: %s",
            self.request.id, fixer_run_id, (result or {}).get("counters"),
        )
        return result
    except Exception as exc:
        logger.error(
            "[Task %s] Fixer run %s failed: %s",
            self.request.id, fixer_run_id, exc, exc_info=True,
        )
        raise


@celery_app.task(
    name="app.worker.tasks.dispatch_scheduled_fixer_runs",
    bind=True,
    max_retries=0,
    queue="default",
    time_limit=300,
)
def dispatch_scheduled_fixer_runs(self, schedule: str):
    """Beat: enqueue a Fixer run for every project whose fixer policy is
    enabled with ``schedule == <schedule>`` (daily|weekly). Each run re-checks
    its own gate; already-running projects are skipped. The gate also takes
    the cross-process dispatch lock (released in workflow._finalize), closing
    the race with a concurrent manual POST."""
    import uuid as _uuid

    from sqlalchemy import select

    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import AgentPolicy
    from app.services import fixer_service

    _bind_task_context(self, schedule=schedule)

    async def _run() -> int:
        dispatched = 0
        async with AsyncSessionLocal() as db:
            # Schedule filter pushed into SQL (budgets JSONB ->> 'schedule')
            # instead of deserializing every enabled fixer policy in Python.
            project_ids = (
                await db.execute(
                    select(AgentPolicy.project_id).where(
                        AgentPolicy.agent_id == "fixer",
                        AgentPolicy.enabled.is_(True),
                        AgentPolicy.budgets["schedule"].astext == schedule,
                    )
                )
            ).scalars().all()
        for project_id in project_ids:
            try:
                async with AsyncSessionLocal() as db:
                    await fixer_service.gate_fixer_run(db, project_id)
            except (
                fixer_service.FixerDisabled,
                fixer_service.FixerAlreadyRunning,
                fixer_service.FixerRunnerRequiredForSuggest,
            ):
                continue  # expected gate outcomes — skip quietly
            except Exception as exc:  # noqa: BLE001 — a FAULT is not a gate decision
                logger.error(
                    "Fixer beat gate check failed for project %s: %s", project_id, exc,
                )
                continue
            fixer_run_id = _uuid.uuid4()
            if fixer_service.enqueue_fixer_run(project_id, fixer_run_id, f"scheduled:{schedule}"):
                dispatched += 1
            else:
                # The scheduled slot is lost until the next beat — say so,
                # and free the dispatch lock the gate just took.
                logger.warning(
                    "Fixer enqueue failed for project %s — %s slot skipped this cycle",
                    project_id, schedule,
                )
                await fixer_service.release_fixer_run_lock(project_id)
        return dispatched

    count = _run_async(_run())
    logger.info("[Task %s] dispatched %s scheduled fixer runs (%s)", self.request.id, count, schedule)
    return {"schedule": schedule, "dispatched": count}


@celery_app.task(
    name="app.worker.tasks.poll_fixer_pr_outcomes",
    bind=True,
    max_retries=0,
    queue="default",
    time_limit=600,
)
def poll_fixer_pr_outcomes(self):
    """Beat: poll open fixer-created PRs; merged → record_fix_outcome(fixed),
    closed-unmerged → not_fixed (AI-5 feedback loop). No-op offline.

    The sweep owns its sessions/commits internally (bounded batches; no DB
    session held across the GitHub GETs) — see pipeline.poll_open_fixer_prs.
    """
    from app.agents.fixer.pipeline import poll_open_fixer_prs

    _bind_task_context(self)

    summary = _run_async(poll_open_fixer_prs())
    logger.info("[Task %s] fixer PR outcome poll: %s", self.request.id, summary)
    return summary


@celery_app.task(
    name="app.worker.tasks.generate_run_compare_report",
    bind=True,
    max_retries=1,
    queue="ai_analysis",
    time_limit=900,
)
def generate_run_compare_report(
    self,
    project_id: str,
    left_run_id: str,
    right_run_id: str,
    suite_name: str | None = None,
):
    """Generate and cache the AI report for a deterministic run comparison."""
    _bind_task_context(
        self,
        project_id=project_id,
        left_run_id=left_run_id,
        right_run_id=right_run_id,
        suite_name=suite_name,
    )

    async def _run():
        import uuid as _uuid
        from app.db.postgres import AsyncSessionLocal
        from app.services import run_compare_ai_service, run_compare_service

        pid = _uuid.UUID(project_id)
        left = _uuid.UUID(left_run_id)
        right = _uuid.UUID(right_run_id)
        async with AsyncSessionLocal() as db:
            selection = {
                "mode": "explicit",
                "scope": "suite" if suite_name else "run",
                "suite_name": suite_name,
                "selection_reason": "AI report generated asynchronously for a saved comparison.",
                "project_id": pid,
                "branch": None,
                "branch_mismatch": False,
                "release_name": None,
            } if suite_name else None
            compare_payload = await run_compare_service.compare_runs(
                db,
                left,
                right,
                suite_name=suite_name,
                selection=selection,
            )
            try:
                report = await run_compare_ai_service.generate_and_save_report(
                    db,
                    project_id=pid,
                    left_run_id=left,
                    right_run_id=right,
                    suite_name=suite_name,
                    compare_payload=compare_payload,
                )
                # Worker owns the transaction boundary here — the service was
                # converted to stage-only (flush, not commit) to satisfy the
                # architectural test, so the worker has to commit explicitly.
                await db.commit()
                return report
            except Exception as exc:
                await db.rollback()
                async with AsyncSessionLocal() as failure_db:
                    await run_compare_ai_service.mark_failed(
                        failure_db,
                        project_id=pid,
                        left_run_id=left,
                        right_run_id=right,
                        suite_name=suite_name,
                        compare_payload=compare_payload,
                        error_message=str(exc),
                    )
                    await failure_db.commit()
                raise

    try:
        return _run_async(_run())
    except Exception as exc:
        logger.error("[Task %s] Run compare report failed: %s", self.request.id, exc, exc_info=True)
        if self.request.retries >= self.max_retries:
            _run_async(_send_to_dlq(
                task_name=self.name,
                task_id=self.request.id,
                kwargs={
                    "project_id": project_id,
                    "left_run_id": left_run_id,
                    "right_run_id": right_run_id,
                    "suite_name": suite_name,
                },
                error=str(exc),
            ))
        raise self.retry(exc=exc, countdown=_exponential_backoff(self.request.retries))


@celery_app.task(
    name="app.worker.tasks.precompute_suite_comparisons_for_run",
    base=DownstreamTrackedTask,
    bind=True,
    max_retries=1,
    queue="ai_analysis",
    time_limit=1800,
)
def precompute_suite_comparisons_for_run(self, test_run_id: str, project_id: str):
    """Precompute default latest-vs-previous suite comparison reports after nightly runs."""
    _bind_task_context(self, run_id=test_run_id, project_id=project_id)

    async def _run():
        import uuid as _uuid
        from sqlalchemy import func, select
        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import TestCase, TestRun
        from app.services import (
            llm_cost_budget,
            run_compare_ai_service,
            run_compare_service,
        )

        rid = _uuid.UUID(test_run_id)
        pid = _uuid.UUID(project_id)
        generated = 0
        already_ready = 0
        skipped_no_baseline = 0
        budget_actions: list[str] = []
        deterministic_fallbacks = 0
        last_cost_budget_mode_override = None
        failures: list[tuple[str, Exception]] = []
        async with AsyncSessionLocal() as db:
            suites_result = await db.execute(
                select(TestCase.suite_name)
                .where(
                    TestCase.test_run_id == rid,
                    TestCase.suite_name.is_not(None),
                    func.trim(TestCase.suite_name) != "",
                )
                .distinct()
            )
            primary_suite = (
                await db.execute(
                    select(TestRun.primary_suite_name).where(
                        TestRun.id == rid,
                        TestRun.project_id == pid,
                    )
                )
            ).scalar_one_or_none()
            suites_by_key: dict[str, str] = {}
            for raw_suite in [
                *(row.suite_name for row in suites_result.all()),
                primary_suite,
            ]:
                display_name = str(raw_suite or "").strip()
                suite_key = run_compare_service.normalize_suite_name(display_name)
                if suite_key:
                    suites_by_key.setdefault(suite_key, display_name)
            suites = sorted(
                suites_by_key.values(),
                key=lambda value: (value.casefold(), value),
            )
            for suite in suites:
                try:
                    previous, latest = await run_compare_service.resolve_suite_pair_for_run(
                        db,
                        project_id=pid,
                        suite_name=suite,
                        right_run_id=rid,
                    )
                except LookupError:
                    await db.rollback()
                    skipped_no_baseline += 1
                    continue

                compare_payload = None
                try:
                    if await run_compare_ai_service.is_report_ready(
                        db,
                        project_id=pid,
                        left_run_id=previous.id,
                        right_run_id=latest.id,
                        suite_name=suite,
                    ):
                        already_ready += 1
                        continue
                    selection = {
                        "mode": "latest_vs_previous",
                        "scope": "suite",
                        "suite_name": suite,
                        "selection_reason": (
                            "Precomputed for the dispatched suite run and its "
                            "preceding run on the same branch."
                        ),
                        "project_id": pid,
                        "branch": latest.branch,
                        "branch_mismatch": previous.branch != latest.branch,
                        "release_name": None,
                    }
                    compare_payload = await run_compare_service.compare_runs(
                        db,
                        previous.id,
                        latest.id,
                        suite_name=suite,
                        selection=selection,
                    )
                    # Re-evaluate immediately before each possible LLM call.
                    # A run may contain many suites, and usage from this or a
                    # concurrent worker can cross the cap while the loop is in
                    # progress. Any capped decision, including HARD_BLOCK,
                    # still materialises the zero-LLM deterministic report so
                    # the durable suite-comparison operation remains truthful.
                    budget_decision = await llm_cost_budget.check_and_apply_cap(pid)
                    budget_actions.append(str(budget_decision.action))
                    last_cost_budget_mode_override = budget_decision.mode_override
                    deterministic_only = budget_decision.is_capped()
                    await run_compare_ai_service.generate_and_save_report(
                        db,
                        project_id=pid,
                        left_run_id=previous.id,
                        right_run_id=latest.id,
                        suite_name=suite,
                        compare_payload=compare_payload,
                        deterministic_only=deterministic_only,
                        cost_budget_prechecked=True,
                    )
                    deterministic_fallbacks += int(deterministic_only)
                    if not await run_compare_ai_service.is_report_ready(
                        db,
                        project_id=pid,
                        left_run_id=previous.id,
                        right_run_id=latest.id,
                        suite_name=suite,
                    ):
                        raise RuntimeError(
                            "suite comparison generator returned without a ready report"
                        )
                    await db.commit()
                    generated += 1
                except Exception as exc:
                    await db.rollback()
                    if compare_payload is not None:
                        try:
                            await run_compare_ai_service.mark_failed(
                                db,
                                project_id=pid,
                                left_run_id=previous.id,
                                right_run_id=latest.id,
                                suite_name=suite,
                                compare_payload=compare_payload,
                                error_message=(
                                    f"{type(exc).__name__}: {exc}"
                                )[:1000],
                            )
                            await db.commit()
                        except Exception as persist_exc:
                            await db.rollback()
                            logger.warning(
                                "[Task %s] Could not persist failed suite comparison %s: %s",
                                self.request.id,
                                suite,
                                persist_exc,
                            )
                    failures.append((suite, exc))
                    logger.warning("[Task %s] Suite comparison precompute failed for %s: %s", self.request.id, suite, exc)
        if failures:
            failed_suites = ", ".join(suite for suite, _exc in failures[:10])
            raise RuntimeError(
                f"Suite comparison reports remain incomplete for: {failed_suites}"
            )
        return {
            "generated": generated,
            "already_ready": already_ready,
            "skipped_no_baseline": skipped_no_baseline,
            "budget_action": budget_actions[-1] if budget_actions else "NOT_EVALUATED",
            "budget_actions": budget_actions,
            "deterministic_fallbacks": deterministic_fallbacks,
            "cost_budget_mode_override": last_cost_budget_mode_override,
        }

    try:
        return _run_async(_run())
    except Exception as exc:
        logger.error("[Task %s] Suite comparison precompute failed: %s", self.request.id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=_exponential_backoff(self.request.retries))


@celery_app.task(
    name="app.worker.tasks.dispatch_ai_summary_email",
    base=DownstreamTrackedTask,
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    queue="default",
)
def dispatch_ai_summary_email(
    self,
    test_run_id: str,
    project_id: str,
    build_number: str,
):
    """
    EM-1: Send AI executive-summary email after the pipeline completes.

    Loads the run summary (MongoDB or fallback) and dispatches to users
    subscribed to AI_ANALYSIS_COMPLETE notifications. Durable child delivery
    keys deduplicate retries per run.
    """
    _bind_task_context(self, run_id=test_run_id, project_id=project_id)
    import uuid as _uuid

    async def _dispatch():
        from app.db.postgres import AsyncSessionLocal
        from app.db.mongo import get_mongo_db, Collections
        from app.models.postgres import Project as _Project, TestRun as _TestRun
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            # Load run and project
            run = (await db.execute(select(_TestRun).where(_TestRun.id == _uuid.UUID(test_run_id)))).scalar_one_or_none()
            if not run:
                raise RuntimeError(f"AI summary run {test_run_id} not found")

            project = (await db.execute(select(_Project).where(_Project.id == run.project_id))).scalar_one_or_none()
            project_name = project.name if project else str(run.project_id)

        # Load summary from MongoDB
        mongo = get_mongo_db()
        doc = await mongo[Collections.RUN_SUMMARIES].find_one({"test_run_id": test_run_id})
        if not doc:
            try:
                doc = await mongo[Collections.RUN_SUMMARIES].find_one({"test_run_id": _uuid.UUID(test_run_id)})
            except (ValueError, TypeError):
                doc = None

        executive_summary = ""
        executive_panel = None
        if doc:
            executive_summary = doc.get("executive_summary") or doc.get("layer1_executive_summary") or ""
            executive_panel = doc.get("executive_panel")

        if not executive_summary:
            # Fallback to deterministic summary
            from app.services.run_summary_service import build_fallback_summary
            async with AsyncSessionLocal() as db:
                fallback = await build_fallback_summary(db, test_run_id)
                if fallback:
                    executive_summary = fallback.executive_summary
                    executive_panel = fallback.executive_panel

        from app.core.config import settings
        from app.services.notification.manager import (
            dispatch_ai_summary_notifications,
            stage_explicit_notification_deliveries,
        )

        delivery_scope = f"ai-summary:{test_run_id}:v1"

        _pass_rate = float(run.pass_rate or 0) if run else 0.0
        _total_tests = int(run.total_tests or 0) if run else 0
        _failed_tests = int(run.failed_tests or 0) if run else 0

        # 1. Dispatch to notification-preference subscribers (AI_ANALYSIS_COMPLETE event)
        await dispatch_ai_summary_notifications(
            project_id=_uuid.UUID(project_id),
            run_id=_uuid.UUID(test_run_id),
            build_number=build_number,
            project_name=project_name,
            executive_summary=executive_summary,
            executive_panel=executive_panel,
            pass_rate=_pass_rate,
            total_tests=_total_tests,
            failed_tests=_failed_tests,
            dashboard_url=f"{settings.public_base_url}/runs/{test_run_id}/intelligence",
            delivery_scope=f"{delivery_scope}:preferences",
        )

        # 2. EM-4 + F9: dispatch every EVENT-DRIVEN digest subscription.
        #
        # `PER_SUITE` and `PER_RELEASE` were offered in the UI (DigestsPage
        # renders both, and PER_SUITE has its own scope input), accepted by the
        # API, and stored -- and no dispatcher ever read them. Those
        # subscriptions were silently undeliverable forever: a user subscribed,
        # got a success confirmation, and never received anything.
        #
        # They belong here rather than in a schedule of their own because they
        # are SCOPE FILTERS on this same run-completion event. The model said
        # so all along: `scope_type` has documented "project | release | suite |
        # global" since the column was added, and `scope_value` "suite name,
        # release id, etc." -- the schema anticipated this and the dispatcher
        # never used it.
        from app.models.postgres import DigestSubscription, User as _User

        async with AsyncSessionLocal() as db:
            per_run_result = await db.execute(
                select(DigestSubscription, _User.email)
                .join(_User, _User.id == DigestSubscription.user_id)
                .where(
                    DigestSubscription.schedule.in_(EVENT_DRIVEN_SCHEDULES),
                    DigestSubscription.is_active == True,  # noqa: E712
                    DigestSubscription.is_paused == False,  # noqa: E712
                )
            )
            digest_deliveries = []

            for sub, user_email in per_run_result.all():
                # Scope check: global or matching project
                if sub.project_id and str(sub.project_id) != project_id:
                    continue
                if not run_matches_digest_scope(sub, run):
                    continue
                # Trigger filter: failed_only skips all-green runs
                if sub.trigger_filter == "failed_only" and _failed_tests == 0:
                    continue
                if sub.trigger_filter == "degraded_only" and _pass_rate >= 90:
                    continue

                if not user_email:
                    continue

                title = f"🤖 AI Summary — Build {build_number} ({project_name})"
                digest_deliveries.append(
                    {
                        "route_id": str(sub.id),
                        "user_id": sub.user_id,
                        "channel_type": "email",
                        "target": user_email,
                        "event_type": "ai_analysis_complete",
                        "title": title,
                        "body": executive_summary,
                        "digest_subscription_id": str(sub.id),
                        "metadata": {
                            "project_name": project_name,
                            "build_number": build_number,
                            "pass_rate": _pass_rate,
                            "total_tests": _total_tests,
                            "failed_tests": _failed_tests,
                            "dashboard_url": f"{settings.public_base_url}/runs/{test_run_id}/intelligence",
                            "executive_panel": executive_panel,
                        },
                    }
                )
            await stage_explicit_notification_deliveries(
                db,
                project_id=_uuid.UUID(project_id),
                run_id=_uuid.UUID(test_run_id),
                delivery_scope=f"{delivery_scope}:digests",
                deliveries=digest_deliveries,
            )
            await db.commit()

        # TG-5/6: Apply AI-derived signal tags after analysis
        try:
            from app.services.auto_tagging_service import auto_tag_after_analysis
            async with AsyncSessionLocal() as tag_db:
                await auto_tag_after_analysis(tag_db, _uuid.UUID(test_run_id))
                await tag_db.commit()
        except Exception as tag_exc:
            logger.warning("[AI Email] Post-analysis auto-tagging failed (non-blocking): %s", tag_exc)

        logger.info("[AI Email] Summary notification intents staged for run %s (build %s)", test_run_id, build_number)

    try:
        _run_async(_dispatch())
    except Exception as exc:
        logger.error("[AI Email] Failed for run %s: %s", test_run_id, exc)
        countdown = _exponential_backoff(self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)


@celery_app.task(
    name="app.worker.tasks.generate_ai_test_cases_task",
    bind=True,
    max_retries=1,
    queue="ai_analysis",
    time_limit=300,
)
def generate_ai_test_cases_task(self, requirements: str, project_id: str, author_id: str) -> dict:
    """Background task: run LLM test-case generation and persist results to DB.
    Enqueued by POST /cases/ai-generate/async — fires immediately and returns,
    so the HTTP request never times out."""
    import json
    import uuid as _uuid
    from app.services.test_case_ai_agent import generate_test_cases_tool, run_tool_for_project

    async def _persist(result: dict) -> int:
        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import ManagedTestCase, User
        from app.services.test_case_lifecycle_service import stage_test_case_snapshot
        from app.services.test_management_audit_service import audit_event
        from app.services.test_management_metrics_service import (
            emit_staged_test_management_metrics,
        )

        saved = 0
        async with AsyncSessionLocal() as db:
            actor = await db.get(User, _uuid.UUID(author_id))
            if actor is None:
                raise ValueError("AI generation author no longer exists")
            for tc_data in result.get("test_cases", []):
                tc = ManagedTestCase(
                    project_id=_uuid.UUID(project_id),
                    title=tc_data.get("title", "AI Generated Test Case"),
                    description=tc_data.get("objective"),
                    objective=tc_data.get("objective"),
                    preconditions=tc_data.get("preconditions"),
                    steps=tc_data.get("steps"),
                    expected_result=tc_data.get("expected_result"),
                    test_data=tc_data.get("test_data"),
                    test_type=tc_data.get("test_type", "functional"),
                    priority=tc_data.get("priority", "medium"),
                    severity=tc_data.get("severity", "major"),
                    feature_area=tc_data.get("feature_area"),
                    tags=tc_data.get("tags", []),
                    estimated_duration_minutes=tc_data.get("estimated_duration_minutes"),
                    ai_generated=True,
                    ai_generation_prompt=requirements,
                    author_id=_uuid.UUID(author_id),
                    status="draft",
                    version=1,
                )
                db.add(tc)
                await db.flush()
                stage_test_case_snapshot(
                    db,
                    tc,
                    actor_id=_uuid.UUID(author_id),
                    change_summary="AI generated",
                    change_type="created",
                    changed_fields=[
                        "title", "description", "objective", "preconditions",
                        "steps", "expected_result", "test_data", "test_type",
                        "priority", "severity", "feature_area", "tags",
                        "estimated_duration_minutes", "status",
                    ],
                )
                await audit_event(
                    db,
                    "test_case",
                    tc.id,
                    tc.project_id,
                    "ai_generated",
                    actor,
                    details=f"AI generated from requirements: {requirements[:100]}",
                )
                saved += 1
            await db.commit()
            await emit_staged_test_management_metrics(db)
        return saved

    logger.info("[Task %s] AI generate test cases project=%s", self.request.id, project_id)
    try:
        raw = _run_async(run_tool_for_project(
            generate_test_cases_tool, {"requirements": requirements}, project_id,
        ))
        result = json.loads(raw) if isinstance(raw, str) else raw
        saved = _run_async(_persist(result))
        logger.info("[Task %s] AI generation complete, saved %d cases", self.request.id, saved)
        return {"saved": saved}
    except json.JSONDecodeError:
        logger.error("[Task %s] Failed to parse AI response", self.request.id)
        return {"saved": 0, "error": "Failed to parse AI response"}
    except Exception as exc:
        logger.error("[Task %s] AI generation failed: %s", self.request.id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(
    name="app.worker.tasks.create_ai_test_plan_task",
    bind=True,
    max_retries=1,
    queue="ai_analysis",
    time_limit=300,
)
def create_ai_test_plan_task(
    self,
    project_id: str,
    author_id: str,
    plan_name: str | None = None,
    constraints: str | None = None,
) -> dict:
    """Background task: run LLM plan optimisation and persist the test plan to DB."""
    import json
    import uuid as _uuid
    from datetime import datetime, timezone
    from app.services.test_case_ai_agent import optimize_test_plan_tool, run_tool_for_project

    async def _build_and_save() -> dict:
        from sqlalchemy import select
        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import ManagedTestCase, TestPlan, TestPlanItem

        # R-B45-R2-5: no DB transaction rides across the LLM call (the rule
        # fixer/workflow.py states): a pooled connection would sit idle in
        # transaction for up to the task's 300 s. Read and snapshot, close the
        # session, call the model, then write in a new short transaction.
        async with AsyncSessionLocal() as db:
            q = select(ManagedTestCase).where(
                ManagedTestCase.project_id == _uuid.UUID(project_id),
                ManagedTestCase.status.in_(["approved", "active"]),
            )
            result = await db.execute(q)
            cases = [
                {
                    "id": c.id,
                    "title": c.title,
                    "priority": c.priority,
                    "test_type": c.test_type,
                    "estimated_duration_minutes": c.estimated_duration_minutes or 5,
                }
                for c in result.scalars().all()
            ]
        if not cases:
            return {"error": "No approved test cases found for this project"}

        tc_json = json.dumps([{k: v for k, v in c.items() if k != "id"} for c in cases], indent=2)
        constraints_text = constraints or "No specific constraints. Optimize for maximum risk coverage."

        raw = await run_tool_for_project(optimize_test_plan_tool, {
            "test_cases_json": tc_json,
            "constraints": constraints_text,
        }, project_id)
        optimization = json.loads(raw) if isinstance(raw, str) else raw

        async with AsyncSessionLocal() as db:
            plan = TestPlan(
                project_id=_uuid.UUID(project_id),
                name=plan_name or f"AI Test Plan — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
                description=optimization.get("optimization_notes"),
                ai_generated=True,
                ai_generation_context=constraints,
                created_by_id=_uuid.UUID(author_id),
                total_cases=len(cases),
            )
            db.add(plan)
            await db.flush()

            order_map: dict[str, int] = {
                entry.get("title", ""): entry.get("execution_order", 999)
                for entry in optimization.get("optimized_order", [])
            }
            for tc in cases:
                db.add(TestPlanItem(
                    plan_id=plan.id,
                    test_case_id=tc["id"],
                    order_index=order_map.get(tc["title"], 999),
                ))
            await db.commit()
            return {"plan_id": str(plan.id), "total_cases": len(cases)}

    logger.info("[Task %s] AI create test plan project=%s", self.request.id, project_id)
    try:
        result = cast(dict[str, Any], _run_async(_build_and_save()))
        logger.info("[Task %s] AI plan creation complete: %s", self.request.id, result)
        return result
    except Exception as exc:
        logger.error("[Task %s] AI plan creation failed: %s", self.request.id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(
    name="app.worker.tasks.generate_ai_strategy_task",
    bind=True,
    max_retries=1,
    queue="ai_analysis",
    time_limit=300,
)
def generate_ai_strategy_task(
    self,
    project_id: str,
    author_id: str,
    project_context: str,
    strategy_name: str | None = None,
) -> dict:
    """Background task: run LLM strategy generation and persist to DB."""
    import json
    import uuid as _uuid
    from datetime import datetime, timezone
    from app.services.test_case_ai_agent import generate_test_strategy_tool, run_tool_for_project

    async def _build_and_save() -> dict:
        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import TestStrategy
        from app.core.config import settings

        raw = await run_tool_for_project(
            generate_test_strategy_tool, {"project_context": project_context}, project_id,
        )
        result = json.loads(raw) if isinstance(raw, str) else raw

        async with AsyncSessionLocal() as db:
            strategy = TestStrategy(
                project_id=_uuid.UUID(project_id),
                name=strategy_name or f"Test Strategy — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
                version_label="v1.0",
                status="draft",
                objective=result.get("objective"),
                scope=result.get("scope"),
                out_of_scope=result.get("out_of_scope"),
                test_approach=result.get("test_approach"),
                risk_assessment=result.get("risk_assessment"),
                test_types=result.get("test_types"),
                entry_criteria=result.get("entry_criteria"),
                exit_criteria=result.get("exit_criteria"),
                environments=result.get("environments"),
                automation_approach=result.get("automation_approach"),
                defect_management=result.get("defect_management"),
                ai_generated=True,
                generation_context=project_context,
                ai_model_used=settings.LLM_MODEL,
                created_by_id=_uuid.UUID(author_id),
            )
            db.add(strategy)
            await db.commit()
            return {"strategy_id": str(strategy.id)}

    logger.info("[Task %s] AI generate strategy project=%s", self.request.id, project_id)
    try:
        result = cast(dict[str, Any], _run_async(_build_and_save()))
        logger.info("[Task %s] AI strategy generation complete: %s", self.request.id, result)
        return result
    except Exception as exc:
        logger.error("[Task %s] AI strategy generation failed: %s", self.request.id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(
    name="app.worker.tasks.take_coverage_snapshot",
    queue="default",
)
def take_coverage_snapshot():
    """Scheduled task: capture daily coverage snapshot for all active projects."""
    from sqlalchemy import select
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import Project

    async def _run():
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Project).where(Project.is_active.is_(True)))
            projects = result.scalars().all()
            logger.info("Taking coverage snapshot for %d projects", len(projects))
            for project in projects:
                logger.info("  Snapshot: %s", project.name)

    logger.info("Running daily coverage snapshot task")
    _run_async(_run())


@celery_app.task(
    name="app.worker.tasks.reindex_search",
    bind=True,
    max_retries=2,
    soft_time_limit=540,
    time_limit=600,
)
def reindex_search(self, project_id: str | None = None, full: bool = False) -> dict:
    """Background task: reindex test cases into ChromaDB for semantic search.
    Uses incremental indexing by default; pass full=True for complete rebuild."""
    async def _run():
        from app.db.postgres import AsyncSessionLocal
        from app.services.semantic_search import index_test_cases, index_incremental
        async with AsyncSessionLocal() as db:
            if full:
                count = await index_test_cases(db, project_id=project_id)
            else:
                count = await index_incremental(db, project_id=project_id)
            # A full rebuild returning None has retained its durable checkpoint
            # and released (or will expire) its lease. Raise so Celery retries
            # that checkpoint instead of recording a successful incomplete job.
            if full and count is None:
                raise RuntimeError("full semantic reindex did not complete")
        # `count is None` means the indexer could not report a number — the
        # vector store was unreachable, or the embedder failed partway. It is
        # NOT "indexed nothing", and this result is what whoever triggered the
        # reindex reads. Reporting 0 here made a failed run indistinguishable
        # from a successful no-op.
        #
        # For the mid-run embedder failure, earlier batches may have landed and
        # the Redis cursor holds the real progress — the count is unreportable,
        # not zero.
        return {
            "indexed_count": count,
            "indexing_measured": count is not None,
            "project_id": project_id,
            "mode": "full" if full else "incremental",
        }

    try:
        result = cast(dict[str, Any], _run_async(_run()))
        return result
    except Exception as exc:
        logger.error("reindex_search failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc, countdown=60)


# ── Knowledge source sync (RAG-4) ────────────────────────────────────────────


@celery_app.task(queue="ai_analysis", bind=True, max_retries=3)
def sync_knowledge_source(self, source_id: str, trigger: str = "manual") -> dict:
    """Fetch content for a KnowledgeSource, chunk, index in ChromaDB, and update sync state."""
    async def _run():
        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import KnowledgeSource
        from app.services.knowledge_sync_service import run_sync
        import uuid as _uuid

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(KnowledgeSource).where(KnowledgeSource.id == _uuid.UUID(source_id))
            )
            source = result.scalar_one_or_none()
            if not source:
                return {"status": "not_found", "source_id": source_id}
            return await run_sync(db, source, trigger=trigger)

    try:
        return cast(dict[str, Any], _run_async(_run()))
    except Exception as exc:
        logger.error("sync_knowledge_source failed: %s (source=%s)", exc, source_id)
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


# ── Knowledge source scheduled re-sync (RAG-6) ──────────────────────────────


@celery_app.task(queue="default", bind=True, max_retries=0)
def resync_stale_knowledge_sources(self) -> dict:
    """Periodic task: find stale/failed sources and enqueue individual sync tasks.

    Reaps sources stranded in SYNCING first. ``run_sync`` commits SYNCING
    before a fetch+chunk+embed that can outlive the process, and every terminal
    write sits in an ``except`` block that a kill never runs -- so an OOM,
    eviction or Celery hard time limit strands the row. ``list_stale_sources``
    excludes SYNCING to avoid concurrent syncs, so nothing would ever pick it
    up again. Reaping flips it to FAILED, which that same sweep already
    selects, so recovery happens in this very run.
    """
    async def _find_and_enqueue():
        from app.db.postgres import AsyncSessionLocal
        from app.services.knowledge_sync_service import (
            list_stale_sources,
            reap_stuck_syncing_sources,
        )

        async with AsyncSessionLocal() as db:
            reaped = await reap_stuck_syncing_sources(db)
            if reaped:
                await db.commit()
            stale = await list_stale_sources(db)

        from app.core.config import settings
        cap = getattr(settings, "KNOWLEDGE_RESYNC_BATCH_CAP", 50)
        enqueued = 0
        for source in stale[:cap]:
            sync_knowledge_source.apply_async(
                kwargs={"source_id": str(source.id), "trigger": "scheduled"},
                countdown=enqueued * 2,  # stagger to avoid burst
            )
            enqueued += 1

        logger.info(
            "Knowledge resync scheduled: enqueued=%d, total_stale=%d, reaped=%d",
            enqueued, len(stale), len(reaped),
        )
        return {
            "enqueued": enqueued,
            "total_stale": len(stale),
            "reaped_stuck_syncing": len(reaped),
        }

    return cast(dict[str, Any], _run_async(_find_and_enqueue()))


# ── Performance baseline refresh (Tier 2 item 10) ──────────────────────────


@celery_app.task(
    name="app.worker.tasks.refresh_perf_baselines",
    queue="default",
    bind=True,
    max_retries=0,
)
def refresh_perf_baselines(self) -> dict:
    """Nightly sweep that extends each per-test duration baseline with
    the newest observations from the TestCase table.

    No-op until the ``perf_regression_detection`` feature flag is on.
    """
    async def _run():
        from app.services.perf_regression_service import refresh_baselines
        with _beat_span("refresh_perf_baselines") as span:
            out = await refresh_baselines()
            span.set_attribute("result.observed", int(out.get("observed", 0)))
            span.set_attribute("result.baselines", int(out.get("baselines", 0)))
            span.set_attribute("flag_enabled", not bool(out.get("skipped", 0)))
            logger.info(
                "[Task %s] perf baselines refresh: observed=%d baselines=%d",
                self.request.id, out.get("observed", 0), out.get("baselines", 0),
            )
            return out

    return cast(dict, _run_async(_run()))


# ── Outbound webhook delivery (Tier 2 item 6) ──────────────────────────────


@celery_app.task(
    name="app.worker.tasks.deliver_webhook",
    queue="default",
    bind=True,
    max_retries=5,
    default_retry_delay=30,
)
def deliver_webhook(
    self,
    delivery_id: str,
    dispatch_token: str | None = None,
) -> dict:
    """Deliver a single webhook subscription event.

    Delegates the actual HTTP work to ``webhook_service.deliver`` which
    holds the DB row as the authoritative outcome. When that function
    signals a retryable failure, we schedule an exponential retry via
    ``self.retry`` so Celery's own backoff policy drives the cadence.
    """
    import uuid as _uuid_mod

    async def _run():
        from app.services.webhook_service import deliver
        try:
            return await deliver(
                _uuid_mod.UUID(delivery_id),
                dispatch_token=(
                    _uuid_mod.UUID(dispatch_token) if dispatch_token else None
                ),
            )
        except Exception as exc:
            logger.warning(
                "[Task %s] deliver_webhook unhandled error: %s",
                self.request.id, exc,
            )
            # The delivery row may still be PENDING when a transient DB,
            # secret-store, or setup failure occurs before ``deliver`` can
            # persist an outcome. Route through the task's bounded retry path
            # instead of acknowledging and permanently stranding the row.
            return {"error": str(exc), "retry": True}

    result = cast(dict, _run_async(_run()))

    if result.get("retry"):
        if dispatch_token:
            # SQL relay owns retries for leased deliveries. Reusing this
            # token through Celery.retry would be rejected after deliver()
            # returns the row to PENDING and would race the periodic relay.
            return {**result, "deferred_to_relay": True}
        # Exponential backoff — 30s, 60s, 120s, 240s, 480s. The webhook
        # service already knows whether the subscription has retries left;
        # we only reach this branch when it signals retry=True.
        delay = min(30 * (2 ** self.request.retries), 480)
        raise self.retry(countdown=delay, max_retries=5)

    return result


# ── Flaky quarantine maintenance (Tier 1 item 3) ────────────────────────────


@celery_app.task(
    name="app.worker.tasks.run_flaky_quarantine_maintenance",
    queue="default",
    bind=True,
    max_retries=0,
)
def run_flaky_quarantine_maintenance(self) -> dict:
    """Nightly housekeeping for the flaky auto-quarantine workflow.

    Runs four passes in order:

      1. ``expire_stale_proposals`` — PROPOSED rows older than 7 days
         flip to EXPIRED so the UI stays readable.
      2. ``schedule_pending_rechecks`` — windowed quarantines whose
         ``recheck_at`` has passed move to RECHECK_SCHEDULED. Covers both
         QUARANTINED (the first window) and RE_QUARANTINED (every window
         after that); missing the latter stranded re-quarantined tests in
         an active state permanently.
      3. ``run_recheck_cycle`` — evaluates RECHECK_SCHEDULED rows against
         recent TestCase history and either releases or re-quarantines
         the test.
      4. ``mark_stale_quarantines`` (PMF US-5.4) — active quarantines past
         their SLA window get a once-per-entry ``test.quarantine_stale``
         notification (anchored on ``stale_notified_at``).

    Every pass is a no-op when the ``flaky_auto_quarantine`` feature flag
    is off, so enabling this beat entry is safe on existing deployments.
    """
    async def _run():
        from app.services.flaky_quarantine_service import (
            expire_stale_proposals,
            mark_stale_quarantines,
            run_recheck_cycle,
            schedule_pending_rechecks,
        )
        with _beat_span("run_flaky_quarantine_maintenance") as span:
            expired = await expire_stale_proposals()
            rechecks_scheduled = await schedule_pending_rechecks()
            outcomes = await run_recheck_cycle()
            stale_flagged = await mark_stale_quarantines()
            span.set_attribute("result.expired", int(expired))
            span.set_attribute("result.rechecks_scheduled", int(rechecks_scheduled))
            span.set_attribute("result.released", int(outcomes["released"]))
            span.set_attribute("result.re_quarantined", int(outcomes["re_quarantined"]))
            span.set_attribute("result.insufficient_data", int(outcomes["insufficient_data"]))
            span.set_attribute("result.stale_flagged", int(stale_flagged))
            logger.info(
                "[Task %s] flaky quarantine maintenance: expired=%d scheduled=%d "
                "released=%d re_quarantined=%d insufficient_data=%d stale_flagged=%d",
                self.request.id,
                expired,
                rechecks_scheduled,
                outcomes["released"],
                outcomes["re_quarantined"],
                outcomes["insufficient_data"],
                stale_flagged,
            )
            return {
                "expired": expired,
                "rechecks_scheduled": rechecks_scheduled,
                "stale_flagged": stale_flagged,
                **outcomes,
            }

    return cast(dict[str, Any], _run_async(_run()))


# ── FLK-P3: flaky-confidence model retraining ───────────────────────────────


@celery_app.task(
    name="app.worker.tasks.train_flaky_confidence_model",
    queue="default",
    bind=True,
    max_retries=0,
)
def train_flaky_confidence_model(self) -> dict:
    """Nightly retrain of the FLK-P3 flaky-confidence model from human
    quarantine approve/reject decisions.

    A no-op-safe degrade chain: returns ``insufficient_data`` /
    ``insufficient_class_diversity`` / ``error`` status strings (never raises)
    when scikit-learn is absent or there aren't yet enough labeled decisions,
    so enabling this beat entry is safe on a fresh deployment. The model is a
    LOCAL scikit-learn artifact — no outbound calls — so it is unaffected by
    ``AI_OFFLINE_MODE``.
    """
    async def _run():
        from app.services.ml.flaky_confidence import train_flaky_confidence_model as _train
        with _beat_span("train_flaky_confidence_model") as span:
            result = await _train()
            span.set_attribute("result.status", str(result.get("status")))
            if result.get("auc") is not None:
                span.set_attribute("result.auc", float(result["auc"]))
            span.set_attribute("result.sample_count", int(result.get("sample_count", 0)))
            logger.info(
                "[Task %s] flaky-confidence training: status=%s auc=%s samples=%s",
                self.request.id,
                result.get("status"),
                result.get("auc"),
                result.get("sample_count", 0),
            )
            return result

    return cast(dict[str, Any], _run_async(_run()))


# ── DLQ helper ────────────────────────────────────────────────────────────────

async def _send_to_dlq(
    task_name: str,
    task_id: str,
    kwargs: dict,
    error: str,
    *,
    verbatim: tuple[str, ...] = (),
) -> None:
    """Write a failed task to the Redis DLQ stream for manual inspection and replay.

    ``verbatim`` names kwargs recorded exactly as given: storage locators the
    caller has already validated, which sanitizing would rewrite -- a build
    segment like ``1.0.0.123`` reads as an IP address -- so the replay named a
    key that does not exist (QA of the N10 follow-up).
    """
    try:
        import json
        from app.db.redis_client import get_redis
        from app.services.ingestion_sanitization import sanitize_test_result_payload
        from app.streams import DLQ_STREAM
        safe_kwargs = sanitize_test_result_payload(kwargs)
        for name in verbatim:
            if name in kwargs:
                safe_kwargs[name] = kwargs[name]
        safe_error = sanitize_test_result_payload({"error_message": error})[
            "error_message"
        ]
        redis = get_redis()
        await redis.xadd(
            DLQ_STREAM,
            {
                "source": "celery",
                "task_name": task_name,
                "task_id": task_id,
                "kwargs": json.dumps(safe_kwargs),
                "error": safe_error[:500],
            },
            maxlen=5000,
            approximate=True,
        )
        logger.error(
            "Moved failed task to DLQ: task=%s id=%s error=%s",
            task_name,
            task_id,
            safe_error,
        )
    except Exception as dlq_exc:
        logger.error("Failed to write to DLQ: %s", dlq_exc)


# ── OPS-01: Integration Health Probes ────────────────────────────────────────


@celery_app.task(
    name="app.worker.tasks.run_integration_health_probes",
    bind=True,
    max_retries=0,
    queue="default",
)
def run_integration_health_probes(self):
    """Periodic task: probe all configured integrations and record health status."""
    logger.info("[Task %s] Running integration health probes", self.request.id)

    async def _probe():
        from app.services.integration_probe_service import persist_probe_results, run_all_probes

        results = await run_all_probes()
        await persist_probe_results(results)
        healthy = sum(1 for r in results if r.status == "healthy")
        skipped = sum(1 for r in results if r.status == "skipped")
        logger.info(
            "Integration probes complete: %d healthy, %d degraded/down, %d skipped",
            healthy, len(results) - healthy - skipped, skipped,
        )

    _run_async(_probe())


# ── PMF US-6.2: Jira defect status sync-back ─────────────────────────────────


@celery_app.task(
    name="app.worker.tasks.sync_jira_defect_statuses",
    bind=True,
    max_retries=0,
    queue="default",
)
def sync_jira_defect_statuses(self):
    """Periodic task: mirror Jira issue status onto linked OPEN defects.

    The task owns the transaction (no request session exists here); the
    service stages the ``jira_status`` / ``external_status_at`` /
    ``external_status_conflict`` mutations and this commit makes them
    durable. Capped inside the service (~50 issues/cycle) so a big backlog
    never hammers Jira — the 15-minute beat catches up over cycles.
    """
    logger.info("[Task %s] Syncing Jira defect statuses", self.request.id)

    async def _sync():
        from app.db.postgres import AsyncSessionLocal
        from app.services.defect_jira_service import sync_external_statuses

        async with AsyncSessionLocal() as db:
            outcome = await sync_external_statuses(db)
            await db.commit()
        logger.info("Jira defect status sync outcome: %s", outcome)

    _run_async(_sync())


# ── ENT-05: Scheduled Digest Dispatch ────────────────────────────────────────


@celery_app.task(
    name="app.worker.tasks.dispatch_scheduled_digests",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
    queue="default",
)
def dispatch_scheduled_digests(self):
    """
    Periodic task: find all digest subscriptions due for delivery and dispatch.

    Runs daily at 07:00 UTC. For each active, non-paused subscription whose
    next_delivery_at <= now, generate digest content and deliver via the
    configured channel.
    """
    from datetime import datetime, timedelta, timezone

    logger.info("[Task %s] Dispatching scheduled digests", self.request.id)

    async def _dispatch():
        from sqlalchemy import select, update

        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import DigestSubscription, NotificationLog, User
        from app.services.digest_content_service import generate_digest, render_digest_html

        now = datetime.now(timezone.utc)

        # Step 1: discover due subscriptions. We only read IDs here; the actual
        # claim happens per-row via an atomic UPDATE so concurrent invocations
        # of this task (beat hiccup, worker retry, manual trigger) cannot
        # double-dispatch the same email. ``last_delivered_at`` is captured
        # NOW (pre-claim) because it is the delta-window watermark (US-7.4)
        # and the claim UPDATE advances it.
        async with AsyncSessionLocal() as db:
            discovery = await db.execute(
                select(
                    DigestSubscription.id,
                    DigestSubscription.schedule,
                    DigestSubscription.last_delivered_at,
                    DigestSubscription.send_when_unchanged,
                    DigestSubscription.report_attachment,
                ).where(
                    DigestSubscription.is_active.is_(True),
                    DigestSubscription.is_paused.is_(False),
                    DigestSubscription.next_delivery_at <= now,
                    # Tier 2 item 12: ``WEEKLY_RETRO`` subscriptions use
                    # the same dispatcher but are routed to the retro
                    # renderer below. The extra schedule type is
                    # feature-flag gated inside ``generate_weekly_retro``.
                    DigestSubscription.schedule.in_(["DAILY", "WEEKLY", "WEEKLY_RETRO"]),
                )
            )
            due = discovery.all()
        logger.info("Found %d digest subscriptions due for delivery", len(due))

        # Step 2: for each candidate, try to atomically CLAIM it by advancing
        # next_delivery_at in the same UPDATE that still sees it as due. A
        # concurrent worker that already claimed the row will find its WHERE
        # clause false and get rowcount=0 — we skip those.
        #
        # We deliberately advance next_delivery_at BEFORE sending the email.
        # If the send fails later we log the failure but do NOT revert the
        # claim: missing a digest (which the user can manually re-trigger) is
        # always better than spamming users with duplicates because a crash
        # between send and commit left the row "still due".
        for sub_id, schedule, last_delivered_at, send_when_unchanged, report_attachment in due:
            delta = timedelta(days=1) if schedule == "DAILY" else timedelta(weeks=1)
            period = "daily" if schedule == "DAILY" else "weekly"
            is_retro = schedule == "WEEKLY_RETRO"
            # Delta window (US-7.4): since the previous successful send,
            # falling back to one schedule period for first-ever deliveries.
            window_start = last_delivered_at or (now - delta)

            # Each claim runs in its own short transaction so the UPDATE is
            # visible to sibling workers immediately.
            async with AsyncSessionLocal() as claim_db:
                claim = await claim_db.execute(
                    update(DigestSubscription)
                    .where(
                        DigestSubscription.id == sub_id,
                        DigestSubscription.is_active.is_(True),
                        DigestSubscription.is_paused.is_(False),
                        DigestSubscription.next_delivery_at <= now,
                    )
                    .values(
                        last_delivered_at=now,
                        next_delivery_at=now + delta,
                        delivery_count=DigestSubscription.delivery_count + 1,
                    )
                    .returning(
                        DigestSubscription.user_id,
                        DigestSubscription.project_id,
                        DigestSubscription.channel,
                    )
                )
                claimed = claim.first()
                await claim_db.commit()

            if claimed is None:
                # Another worker won the race for this subscription.
                continue
            user_id, project_id, channel = claimed

            # Step 3: actually deliver. A failure here only affects the log
            # row — the claim is already persisted so we will not retry at
            # the next beat tick.
            status = "sent"
            error_detail = None
            try:
                async with AsyncSessionLocal() as db:
                    if is_retro:
                        # Tier 2 item 12 — route WEEKLY_RETRO through the
                        # retro-specific renderer. Falls back to the
                        # plain weekly digest when the feature flag is off
                        # so paused-but-not-deleted subscriptions still
                        # deliver something useful.
                        from app.services.retro_digest_service import (
                            generate_weekly_retro,
                        )
                        digest = await generate_weekly_retro(db, project_id)
                        if digest is None:
                            digest = await generate_digest(db, project_id, "weekly")
                    else:
                        # US-7.4: delta digest — content is structured
                        # around changes since the last successful send.
                        digest = await generate_digest(
                            db, project_id, period, since=window_start,
                        )

                    user_result = await db.execute(
                        select(User).where(User.id == user_id)
                    )
                    user = user_result.scalar_one_or_none()
                    if not user:
                        logger.warning(
                            "Claimed subscription %s references missing user %s",
                            sub_id, user_id,
                        )
                        continue

                    # US-7.4: zero-change window + send_when_unchanged=False
                    # → skip delivery entirely (the claim already advanced,
                    # so the next digest covers the whole span since this
                    # skipped one — nothing is lost).
                    skip_unchanged = (
                        bool(digest.get("is_zero_change"))
                        and not bool(send_when_unchanged)
                    )

                    # ``generate_digest`` applies NO project filter when
                    # ``project_id`` is None -- runs, cluster labels, blocking
                    # issues and risk scores are aggregated across EVERY
                    # project on the install. The create endpoint only checked
                    # the project_id it was *given*, so a non-admin could store
                    # a null-project row and be mailed the whole workspace on a
                    # schedule. The router now refuses to create one; this is
                    # the matching send-time guard, because nothing else
                    # re-checks membership at delivery and rows created before
                    # the fix are still on disk.
                    #
                    # Re-audit N33: a project-scoped digest is checked too. Its
                    # owner must still be active and an ADMIN or a member of the
                    # project; one who left kept receiving it on every send.
                    from app.services.notification.manager import (
                        digest_owner_block_reason,
                    )

                    block_reason = await digest_owner_block_reason(db, user, project_id)

                    if block_reason:
                        status = "skipped"
                        error_detail = block_reason
                        logger.warning(
                            "Digest for subscription %s skipped — %s",
                            sub_id,
                            block_reason,
                        )
                    elif skip_unchanged:
                        status = "skipped"
                        logger.info(
                            "Digest for subscription %s skipped — zero-change window",
                            sub_id,
                        )
                    elif channel == "email":
                        try:
                            from app.services.digest_content_service import (
                                REPORT_BUILD_FAILED_NOTE,
                                append_digest_html_note,
                            )
                            from app.services.notification.email_service import (
                                send_html_email_with_attachments,
                            )

                            html_body = render_digest_html(digest)
                            attachments = None
                            if report_attachment:
                                # US-7.5: attach the self-contained HTML
                                # analysis report. NEVER blocks the digest —
                                # a failed build (or a non-project-scoped
                                # subscription) sends the digest with an
                                # apologetic note instead.
                                from app.services.analysis_report_service import (
                                    build_digest_report_attachment,
                                )
                                attachment = await build_digest_report_attachment(
                                    db, project_id, period,
                                )
                                if attachment is not None:
                                    filename, report_html = attachment
                                    attachments = [
                                        (filename, report_html, "text/html"),
                                    ]
                                else:
                                    html_body = append_digest_html_note(
                                        html_body, REPORT_BUILD_FAILED_NOTE,
                                    )
                            await send_html_email_with_attachments(
                                to_email=user.email,
                                subject=f"TestLookup — {period.title()} Quality Digest",
                                html_body=html_body,
                                attachments=attachments,
                            )
                        except Exception as e:
                            status = "failed"
                            error_detail = str(e)
                            # The address is PII — redact before it reaches
                            # a log sink. Stays stdlib-positional because
                            # ``logger`` here is logging.getLogger, not structlog.
                            from app.services.privacy_service import sanitize_for_logging

                            logger.warning(
                                "Digest email failed for %s: %s",
                                sanitize_for_logging(user.email or ""), e,
                            )
                    elif channel in ("slack", "teams"):
                        # Resolve the user's webhook for this channel
                        # (project-scoped preference first, then global,
                        # then the instance-wide settings default).
                        from app.models.postgres import NotificationPreference
                        from app.services.integration_config_service import (
                            resolve_global_notification_webhooks,
                        )
                        from app.services.digest_content_service import (
                            digest_text_with_attachment_note,
                            render_digest_text,
                        )
                        from sqlalchemy import or_ as _or
                        pref_result = await db.execute(
                            select(NotificationPreference)
                            .where(
                                NotificationPreference.user_id == user_id,
                                NotificationPreference.channel == channel,
                                NotificationPreference.enabled.is_(True),
                                _or(
                                    NotificationPreference.project_id == project_id,
                                    NotificationPreference.project_id.is_(None),
                                ),
                            )
                            # Project-scoped preference wins over global
                            # (Postgres DESC defaults to NULLS FIRST, so be
                            # explicit).
                            .order_by(NotificationPreference.project_id.desc().nullslast())
                        )
                        prefs = pref_result.scalars().all()
                        from app.services.notification.manager import preference_webhook

                        field = f"{channel}_webhook_url"
                        override = next(
                            (getattr(p, field) for p in prefs if getattr(p, field)), None
                        )
                        # A user's own webhook is never deployment-wide, so the
                        # operator's allow-list does not cover it; only the
                        # global webhook is (code review of H10).
                        webhook_url, deployment_wide = preference_webhook(
                            channel,
                            override,
                            None if override else await resolve_global_notification_webhooks(db),
                        )
                        if not webhook_url:
                            status = "failed"
                            error_detail = f"No {channel} webhook URL configured"
                        else:
                            try:
                                title = (
                                    f"📰 TestLookup — {period.title()} Quality Digest"
                                    f" — {digest.get('project_name') or 'All Projects'}"
                                )
                                # US-7.5: content is unchanged for Slack/Teams;
                                # when the attachment is enabled, one appended
                                # line points at the email digest carrying it.
                                digest_body = digest_text_with_attachment_note(
                                    render_digest_text(digest),
                                    bool(report_attachment),
                                )
                                if channel == "slack":
                                    from app.services.notification import slack_service
                                    await slack_service.send_notification(
                                        webhook_url=webhook_url,
                                        title=title,
                                        body=digest_body,
                                        event_type="digest_delivery",
                                        metadata={},
                                        deployment_wide=deployment_wide,
                                    )
                                else:
                                    from app.services.notification import teams_service
                                    await teams_service.send_notification(
                                        webhook_url=webhook_url,
                                        title=title,
                                        body=digest_body,
                                        event_type="digest_delivery",
                                        metadata={},
                                        deployment_wide=deployment_wide,
                                    )
                            except Exception as e:
                                status = "failed"
                                error_detail = str(e)
                                logger.warning(
                                    "Digest %s delivery failed for sub %s: %s",
                                    channel, sub_id, e,
                                )

                    db.add(NotificationLog(
                        user_id=user_id,
                        project_id=project_id,
                        channel=channel,
                        event_type="digest_delivery",
                        title=f"{period.title()} Quality Digest",
                        body=(
                            "Skipped — no changes since last digest"
                            if status == "skipped"
                            else f"Digest for {digest.get('project_name', 'All Projects')}"
                        ),
                        status=status,
                        error_detail=error_detail,
                    ))
                    await db.commit()
            except Exception as exc:
                logger.error("Digest delivery failed for subscription %s: %s", sub_id, exc)

    with _beat_span("dispatch_scheduled_digests") as span:
        try:
            _run_async(_dispatch())
            span.set_attribute("status", "ok")
        except Exception as exc:
            span.set_attribute("status", "error")
            span.set_attribute("error.category", type(exc).__name__)
            raise
    logger.info("[Task %s] Digest dispatch completed", self.request.id)


@celery_app.task(
    name="app.worker.tasks.dispatch_weekly_flaky_debt_reviews",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
    queue="default",
)
def dispatch_weekly_flaky_debt_reviews(self):
    """Weekly beat (Mondays 07:10 UTC): send each channel-mapped team its
    flaky-debt review draft through the US-7.3 team channels (Agentic plan
    AI-7). Teams WITHOUT a channel are deliberately not handled here — their
    drafts fold into the project's weekly digest as a section instead
    (``digest_content_service.generate_digest``).

    All content is deterministic TEXT built from the quarantine lifecycle
    rows — no LLM calls. Per-project failures are logged and skipped inside
    the service (fail-open); delivery audit rows are written via
    ``notification_routing.record_team_delivery_logs``.
    """
    logger.info("[Task %s] Dispatching weekly flaky-debt reviews", self.request.id)
    from app.services.flaky_debt_review import deliver_flaky_debt_reviews

    with _beat_span("dispatch_weekly_flaky_debt_reviews") as span:
        try:
            counters = _run_async(deliver_flaky_debt_reviews())
            span.set_attribute("status", "ok")
        except Exception as exc:
            span.set_attribute("status", "error")
            span.set_attribute("error.category", type(exc).__name__)
            raise
    logger.info(
        "[Task %s] Flaky-debt review dispatch completed: %s",
        self.request.id, counters,
    )
    return counters


@celery_app.task(
    name="app.worker.tasks.close_stale_live_sessions",
    bind=True,
    queue="default",
    time_limit=300,
)
def close_stale_live_sessions(self, idle_minutes: int = 15) -> dict:
    """Periodic safety net for live sessions whose clients forget to send a
    ``run_complete`` event.

    Symptom this fixes: clients (especially raw curl/Postman users) post
    test results without a closing ``run_complete``. The LiveSession row
    stays ``status='active'`` forever, ``upsert_test_run`` never runs, and
    the run only ever appears in Live Execution — Runs / Overview / Coverage
    / Failures / Trends all read from ``test_runs`` so they show 0.

    Heuristic: any active LiveSession whose Redis state hash either no
    longer exists (24h Redis TTL has expired = definitely orphaned) or
    whose ``last_event_at`` is older than ``idle_minutes`` is closed via
    the normal ``stream_service.close_session`` path. That path is
    idempotent (it short-circuits if status='completed'), so a session
    that was closed legitimately between the LIST and the per-row close
    doesn't double-fire.
    """
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import LiveSession
    from app.services.stream_service import close_session, finalize_closed_session_redis
    from app.streams.live_run_state import RedisLiveRunState

    async def _sweep() -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=idle_minutes)
        closed = 0
        skipped_recent = 0
        errors = 0
        async with AsyncSessionLocal() as db:
            active = (
                await db.execute(
                    select(LiveSession).where(LiveSession.status == "active")
                )
            ).scalars().all()

            for session in active:
                try:
                    state = await RedisLiveRunState.get(str(session.id))
                    last_event_iso = (state or {}).get("last_event_at")
                    is_idle = True
                    if last_event_iso:
                        try:
                            last_event = datetime.fromisoformat(last_event_iso)
                            if last_event.tzinfo is None:
                                last_event = last_event.replace(tzinfo=timezone.utc)
                            is_idle = last_event < cutoff
                        except Exception:
                            # Malformed timestamp — treat as stale and close.
                            is_idle = True
                    else:
                        # No Redis state / no last_event_at field means
                        # either the Redis state hash expired (24h TTL —
                        # definitely abandoned) OR the session never
                        # received an event after registration. Fall back
                        # to comparing ``started_at`` against the cutoff
                        # so sessions that opened and were never used
                        # don't sit ``active`` forever. 2026-05-15: this
                        # branch added after finding 7 sessions on the
                        # homelab stuck idle 50-65 min with NULL
                        # last_event_at — they were registered by the
                        # SDK but the first event never arrived, and the
                        # prior reaper treated ``is_idle=True`` then
                        # skipped them via ``not is_idle`` being false.
                        # Result: sessions accumulated indefinitely.
                        started_at = session.started_at
                        if started_at and started_at.tzinfo is None:
                            started_at = started_at.replace(tzinfo=timezone.utc)
                        is_idle = bool(started_at and started_at < cutoff)

                    if not is_idle:
                        skipped_recent += 1
                        continue

                    await close_session(db, str(session.id))
                    await db.commit()
                    await finalize_closed_session_redis(str(session.id))
                    closed += 1
                except Exception as exc:
                    errors += 1
                    logger.warning(
                        "close_stale_live_sessions: failed to close %s: %s",
                        session.id, exc,
                    )
                    try:
                        await db.rollback()
                    except Exception:
                        pass

        return {
            "checked": len(active),
            "closed": closed,
            "skipped_recent": skipped_recent,
            "errors": errors,
            "idle_minutes": idle_minutes,
        }

    logger.info("[Task %s] close_stale_live_sessions starting (idle>%dm)",
                self.request.id, idle_minutes)
    result = cast(dict[str, Any], _run_async(_sweep()))
    logger.info("[Task %s] close_stale_live_sessions done: %s",
                self.request.id, result)
    return result


@celery_app.task(
    name="app.worker.tasks.reap_stuck_agent_pipelines",
    bind=True,
    queue="default",
    time_limit=300,
)
def reap_stuck_agent_pipelines(self, stale_minutes: int = 30) -> dict:
    """Periodic cleanup for agent_pipeline_runs that got stuck in
    ``status='running'`` because a stage crashed before the outer
    ``_mark_pipeline_done`` could record the failure.

    The /agents read-time derivation already shows the right status to
    end users; this task updates the DB rows so historical filters
    (``?status=failed``) and metrics queries don't have to special-case
    the running-but-actually-failed state.

    A pipeline is reaped if EITHER:
      * Any of its stage rows is ``status='failed'`` (downstream stages
        couldn't continue, so the run is definitionally done).
      * It has been ``running`` for longer than ``stale_minutes`` with
        no ``completed_at`` (matches the Celery task time_limit on
        ``run_agent_pipeline``).
    """
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select, exists
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import AgentPipelineRun, AgentStageResult

    async def _sweep() -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
        failed_due_to_stage = 0
        failed_due_to_age = 0
        errors = 0
        investigator_recovery = {"checked": 0, "reaped": 0}

        try:
            from app.agents.investigator.workflow import reap_stale_investigations

            investigator_recovery = await reap_stale_investigations()
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning(
                "reap_stuck_agent_pipelines: Investigator recovery failed (%s)",
                type(exc).__name__,
            )

        async with AsyncSessionLocal() as db:
            running = (
                await db.execute(
                    select(AgentPipelineRun).where(
                        AgentPipelineRun.status == "running"
                    )
                )
            ).scalars().all()

            for pipeline in running:
                try:
                    has_failed_stage = (
                        await db.execute(
                            select(
                                exists().where(
                                    AgentStageResult.pipeline_run_id == pipeline.id,
                                    AgentStageResult.status == "failed",
                                )
                            )
                        )
                    ).scalar()

                    is_age_stale = (
                        pipeline.started_at is not None
                        and pipeline.completed_at is None
                        and pipeline.started_at < cutoff
                    )

                    if not has_failed_stage and not is_age_stale:
                        continue

                    from app.services.workflow_run_state import apply_transition  # noqa: PLC0415

                    apply_transition(
                        pipeline,
                        "failed",
                        error=None if pipeline.error else (
                            "Stage failure detected by reaper" if has_failed_stage
                            else f"Pipeline exceeded {stale_minutes}m without completion"
                        ),
                    )

                    if has_failed_stage:
                        failed_due_to_stage += 1
                    else:
                        failed_due_to_age += 1

                    # A worker can die after reserving a graph budget but before
                    # _mark_pipeline_done. Reconcile the durable receipt while
                    # terminalizing the pipeline so no reservation remains stuck.
                    from app.services.pipeline_budget_service import reconcile_reaped_pipeline_metadata

                    pipeline.execution_metadata = reconcile_reaped_pipeline_metadata(
                        dict(pipeline.execution_metadata or {})
                    )
                except Exception as exc:
                    errors += 1
                    logger.warning(
                        "reap_stuck_agent_pipelines: failed for %s (%s)",
                        pipeline.id, type(exc).__name__,
                    )

            try:
                await db.commit()
            except Exception as exc:
                logger.error(
                    "reap_stuck_agent_pipelines: commit failed (%s)",
                    type(exc).__name__,
                )
                await db.rollback()
                errors += 1

        return {
            "checked": len(running),
            "failed_due_to_stage": failed_due_to_stage,
            "failed_due_to_age": failed_due_to_age,
            "errors": errors,
            "stale_minutes": stale_minutes,
            "investigator_recovery": investigator_recovery,
        }

    logger.info(
        "[Task %s] reap_stuck_agent_pipelines starting (stale>%dm)",
        self.request.id, stale_minutes,
    )
    result = cast(dict[str, Any], _run_async(_sweep()))
    logger.info(
        "[Task %s] reap_stuck_agent_pipelines done: %s",
        self.request.id, result,
    )
    return result


@celery_app.task(
    name="app.worker.tasks.flag_orphan_test_suites",
    bind=True,
    queue="default",
    time_limit=300,
)
def flag_orphan_test_suites(self, min_age_minutes: int = 60) -> dict:
    """Detect and structured-log orphan ``TestSuite`` rows for ops review.

    The ingestion pipeline's ``finalize_run`` commits each step in its own
    session via ``_run_isolated`` (resilience pattern: a failing canonical
    sync shouldn't roll back the suite sync that already succeeded). The
    trade-off is that suite_sync may create a TestSuite row, then
    canonical_sync fails before linking any CanonicalTestCase rows to it
    — leaving an empty suite dangling.

    This task runs nightly, finds non-default TestSuite rows that:

    * Have no ``CanonicalTestCase`` children, AND
    * Are older than ``min_age_minutes`` (default 60 — recent suites are
      still mid-ingest and not yet orphaned).

    For each orphan it emits a structured WARNING (greppable by
    ``event=orphan_test_suite``) and bumps the ``orphan_test_suites_total``
    Prometheus counter. The suite row is NOT deleted automatically — an
    operator decides whether to reassign / delete / wait for the next
    ingest to repopulate it.

    See docs/DATABASE_AUDIT_2026-05-16.md (P2-3).
    """
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import exists, select, and_
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import CanonicalTestCase, TestSuite

    async def _sweep() -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=min_age_minutes)
        orphans: list[dict] = []

        async with AsyncSessionLocal() as db:
            # Find TestSuite rows that are NOT default AND have no
            # canonical_test_cases children AND were created before the
            # cutoff. Use NOT EXISTS so we don't materialise the full
            # canonical_test_cases table.
            stmt = (
                select(TestSuite)
                .where(TestSuite.is_default.is_(False))
                .where(TestSuite.created_at < cutoff)
                .where(
                    ~exists().where(
                        and_(
                            CanonicalTestCase.test_suite_id == TestSuite.id,
                        )
                    )
                )
            )
            rows = (await db.execute(stmt)).scalars().all()

            for suite in rows:
                logger.warning(
                    "orphan_test_suite",
                    extra={
                        "event": "orphan_test_suite",
                        "test_suite_id": str(suite.id),
                        "project_id": str(suite.project_id),
                        "suite_name": suite.name,
                        "created_at": suite.created_at.isoformat() if suite.created_at else None,
                    },
                )
                orphans.append({
                    "test_suite_id": str(suite.id),
                    "project_id": str(suite.project_id),
                    "suite_name": suite.name,
                })

        try:
            from app.core.metrics import orphan_test_suites_total
            orphan_test_suites_total.inc(len(orphans))
        except (ImportError, AttributeError):  # pragma: no cover
            # Metrics module may not have the counter declared yet —
            # tolerate that gracefully so the reaper still runs.
            pass

        return {
            "min_age_minutes": min_age_minutes,
            "orphan_count": len(orphans),
            "orphans": orphans,
        }

    logger.info(
        "[Task %s] flag_orphan_test_suites starting (min_age=%dm)",
        self.request.id, min_age_minutes,
    )
    result = cast(dict[str, Any], _run_async(_sweep()))
    logger.info(
        "[Task %s] flag_orphan_test_suites done: %d orphan(s) flagged",
        self.request.id, result["orphan_count"],
    )
    return result


@celery_app.task(
    name="app.worker.tasks.reconcile_canonical_deletions",
    bind=True,
    queue="default",
    time_limit=600,
)
def reconcile_canonical_deletions(self) -> dict:
    """Nightly safety net for canonical-deletion detection (Phase I follow-up).

    ``finalize_run`` already calls ``test_suite_service.reconcile_canonical_deletions``
    in an isolated session for every completed run, which is the primary
    write path. This beat task exists for two failure modes that primary
    path can't catch:

      1. A run finalizes but the isolated reconcile step itself raises
         (transient DB blip, lock conflict). Without this safety net the
         canonical stays ``active`` until the next run for that project.
      2. A project that's gone quiet — no new runs for days — needs
         its catalog kept honest. Otherwise stale ``active`` rows
         persist indefinitely after the underlying tests were removed.

    Iterates every project and runs the same service function. Per-project
    failures are logged but never abort the sweep so one bad project
    doesn't starve the rest.
    """
    from sqlalchemy import select
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import Project
    from app.services.test_suite_service import (
        reconcile_canonical_deletions as _reconcile,
    )

    async def _sweep() -> dict:
        totals = {"projects_scanned": 0, "deleted": 0, "errors": 0}

        async with AsyncSessionLocal() as db:
            projects = (await db.execute(select(Project.id))).all()
            project_ids = [row[0] for row in projects]

        for project_id in project_ids:
            async with AsyncSessionLocal() as project_db:
                try:
                    result = await _reconcile(project_db, project_id)
                    await project_db.commit()
                    from app.services.test_management_metrics_service import (
                        emit_staged_test_management_metrics,
                    )

                    await emit_staged_test_management_metrics(project_db)
                    totals["projects_scanned"] += 1
                    totals["deleted"] += int(result.get("deleted", 0))
                except Exception as exc:
                    await project_db.rollback()
                    totals["errors"] += 1
                    logger.warning(
                        "reconcile_canonical_deletions project failed",
                        extra={
                            "event": "canonical_deletion_reconcile_failed",
                            "project_id": str(project_id),
                            "error": str(exc),
                        },
                    )

        return totals

    logger.info(
        "[Task %s] reconcile_canonical_deletions starting",
        self.request.id,
    )
    result = cast(dict[str, Any], _run_async(_sweep()))
    logger.info(
        "[Task %s] reconcile_canonical_deletions done: scanned=%d deleted=%d errors=%d",
        self.request.id,
        result["projects_scanned"], result["deleted"], result["errors"],
    )
    return result


@celery_app.task(
    name="app.worker.tasks.drain_active_live_sessions",
    bind=True,
    queue="default",
    time_limit=120,
)
def drain_active_live_sessions(self) -> dict:
    """Phase 4.5 — drain every active live session's Redis event buffer
    into Postgres ``test_cases`` rows.

    Runs on a 30-second beat schedule (``drain-active-live-sessions``)
    so a long-running session that exceeds the ``LTRIM`` cap doesn't
    lose its oldest per-test rows. The drain task is idempotent
    (per-run SET-NX lock + LRANGE/LTRIM atomicity under append-only
    writers) so overlapping ticks degrade gracefully.

    The terminal ``persist_live_session`` + ``finalize_run`` chain at
    ``close_session`` time is unchanged — this task only writes per-
    test rows progressively so close-time has less to do.
    """
    from app.services.live_session_drainer import drain_all_active_runs

    logger.info("[Task %s] drain_active_live_sessions starting", self.request.id)
    result = cast(dict[str, Any], _run_async(drain_all_active_runs()))
    logger.info(
        "[Task %s] drain_active_live_sessions done: %s",
        self.request.id, result,
    )
    return result


@celery_app.task(
    name="app.worker.tasks.backfill_placeholder_test_cases",
    bind=True,
    queue="default",
    time_limit=600,
)
def backfill_placeholder_test_cases(self, max_runs_per_project: int = 500) -> dict:
    """Retroactively synthesize placeholder TestCase rows.

    For every TestRun where ``failed_tests + broken_tests > 0`` but
    no ``test_cases`` rows exist (the live-stream-buffer-eviction or
    SDK-no-test_result-events scenario), this task inserts the same
    placeholder rows that ``persist_live_session`` now creates at
    write time for new runs. The follow-on
    ``backfill_unassigned_failures`` beat task (every 15 min) then
    picks them up via ``failed_test_assignment_service`` so the
    placeholders appear on ``/my-failures``.

    Idempotent — the candidate query filters to runs with zero
    test_cases, so a second tick after the first one's commit
    produces zero new rows.
    """
    from app.db.postgres import AsyncSessionLocal
    from app.services.placeholder_backfill_service import (
        backfill_placeholders_all_projects,
    )

    async def _run() -> dict:
        async with AsyncSessionLocal() as db:
            try:
                result = await backfill_placeholders_all_projects(
                    db, max_runs_per_project=max_runs_per_project,
                )
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

    logger.info(
        "[Task %s] backfill_placeholder_test_cases starting",
        self.request.id,
    )
    result = cast(dict[str, Any], _run_async(_run()))
    logger.info(
        "[Task %s] backfill_placeholder_test_cases done: %s",
        self.request.id, result,
    )
    return result


@celery_app.task(
    name="app.worker.tasks.auto_recover_completed_live_runs",
    bind=True,
    queue="default",
    time_limit=120,
)
def auto_recover_completed_live_runs(
    self,
    lookback_hours: int = 24,
    max_runs: int = 100,
) -> dict:
    """Recover REAL per-test rows from ``TestRun.event_archive`` for
    completed live_stream runs whose ``test_cases`` table is empty.

    Defensive net for the close_session → persist_live_session handoff.
    When the worker dispatch is dropped (silent apply_async failure,
    queue backpressure, worker restart) the run shows correct aggregates
    on /runs but per-test detail is missing on /test-management,
    /coverage/suite, and the run-detail page. The hourly
    ``backfill_placeholder_test_cases`` task eventually inserts marker
    rows but loses the real test names the SDK shipped. We capture
    those names in ``TestRun.event_archive`` at close-time (15-day TTL)
    so this task can materialise them when the regular handoff
    misfired. Runs on its own cadence (every 2 minutes) so users see
    real per-test detail within ~2 minutes of close_session, well
    before the placeholder backfill fires.
    """
    from app.db.postgres import AsyncSessionLocal
    from app.services.live_run_recovery_service import (
        auto_recover_completed_runs,
        repair_clobbered_primary_suite_names,
    )

    async def _run() -> dict:
        async with AsyncSessionLocal() as db:
            recover = await auto_recover_completed_runs(
                db,
                lookback_hours=lookback_hours,
                max_runs=max_runs,
            )
            # Companion sweep — heal runs whose finalize_run path
            # clobbered ``primary_suite_name`` with the per-event
            # dominant suite (e.g. a test class name from the old
            # TestNG-listener default). Reads ``LiveSession.suite_name``
            # as the authoritative session label. Bounded by the same
            # 24h window so we don't rewrite ancient runs the user
            # has long since accepted as-is.
            repair = await repair_clobbered_primary_suite_names(
                db,
                lookback_hours=lookback_hours,
                max_runs=max(max_runs, 200),
            )
            await db.commit()
            return {"recover": recover, "repair": repair}

    logger.info(
        "[Task %s] auto_recover_completed_live_runs starting", self.request.id
    )
    result = cast(dict[str, Any], _run_async(_run()))
    logger.info(
        "[Task %s] auto_recover_completed_live_runs done: %s",
        self.request.id, result,
    )
    return result


@celery_app.task(
    name="app.worker.tasks.backfill_unassigned_failures",
    bind=True,
    queue="default",
    time_limit=600,
)
def backfill_unassigned_failures(self, max_runs_per_project: int = 200) -> dict:
    """Retroactively assign FAILED/BROKEN TestCases left unassigned.

    Drives two related backfills:

    * ``default_qa_lead_service.backfill_default_qa_lead_for_all_projects``
      to provision the synthetic QA-lead user on projects created before
      this feature shipped.
    * ``failed_test_assignment_service.backfill_unassigned_failures`` for
      every project so already-ingested failures pick up the new owner.

    Both resolvers are idempotent (default-lead provisioning is a no-op
    when the FK is already set; per-run assignment only touches NULL
    rows), so this can run on a tight cadence without risking write
    storms. One project is processed per session so a stuck project
    doesn't starve the others.
    """
    from sqlalchemy import select
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import Project
    from app.services.default_qa_lead_service import (
        backfill_default_qa_lead_for_all_projects,
    )
    from app.services.failed_test_assignment_service import (
        backfill_unassigned_failures as _backfill,
    )
    from app.services.project_access_revocation_service import (
        reconcile_deleted_project_credentials,
    )

    async def _sweep() -> dict:
        totals = {
            "projects_scanned": 0,
            "default_leads_provisioned": 0,
            "credentials_revoked_accounts": 0,
            "credentials_revoked_api_keys": 0,
            "runs": 0,
            "assigned": 0,
            "unassigned": 0,
            "errors": 0,
        }

        # Pass 1: make sure every project has a default QA-lead user. The
        # per-run assignment in pass 2 reads ``default_qa_lead_user_id`` so
        # provisioning MUST land first.
        async with AsyncSessionLocal() as lead_db:
            try:
                lead_counts = await backfill_default_qa_lead_for_all_projects(lead_db)
                await lead_db.commit()
                totals["default_leads_provisioned"] = int(
                    lead_counts.get("provisioned", 0)
                )
            except Exception as exc:
                await lead_db.rollback()
                totals["errors"] += 1
                logger.warning(
                    "default_qa_lead_backfill failed: error=%s", exc,
                )

        # Pass 1b: the mirror image. Projects deleted BEFORE the delete
        # endpoint learned to revoke credentials left their QA-lead account
        # and their API keys live. Own session and own try/except: this is
        # unrelated to assignment, and a fault here must not cost pass 2.
        async with AsyncSessionLocal() as revoke_db:
            try:
                revoked = await reconcile_deleted_project_credentials(revoke_db)
                await revoke_db.commit()
                totals["credentials_revoked_accounts"] = int(
                    revoked.get("qa_lead_accounts", 0)
                )
                totals["credentials_revoked_api_keys"] = int(
                    revoked.get("api_keys", 0)
                )
            except Exception as exc:
                await revoke_db.rollback()
                totals["errors"] += 1
                logger.warning(
                    "deleted_project_credential_reconcile failed: error=%s", exc,
                )

        async with AsyncSessionLocal() as db:
            project_ids = [
                row[0] for row in (await db.execute(select(Project.id))).all()
            ]

        for project_id in project_ids:
            async with AsyncSessionLocal() as project_db:
                try:
                    result = await _backfill(
                        project_db, project_id, max_runs=max_runs_per_project
                    )
                    await project_db.commit()
                    totals["projects_scanned"] += 1
                    totals["runs"] += int(result.get("runs", 0))
                    totals["assigned"] += int(result.get("assigned", 0))
                    totals["unassigned"] += int(result.get("unassigned", 0))
                except Exception as exc:
                    await project_db.rollback()
                    totals["errors"] += 1
                    logger.warning(
                        "backfill_unassigned_failures project failed: project=%s error=%s",
                        str(project_id), exc,
                    )

        return totals

    logger.info(
        "[Task %s] backfill_unassigned_failures starting",
        self.request.id,
    )
    result = cast(dict[str, Any], _run_async(_sweep()))
    logger.info(
        "[Task %s] backfill_unassigned_failures done: %s",
        self.request.id, result,
    )
    return result


@celery_app.task(
    name="app.worker.tasks.notify_test_suite_owner",
    bind=True,
    max_retries=3,
    queue="default",
)
def notify_test_suite_owner(
    self,
    *,
    to_email: str,
    owner_name: str,
    test_name: str,
    suite_name: str | None,
    fail_count: int | None,
    days: int,
    project_id: str,
    project_name: str | None,
    latest_run_id: str | None,
    latest_run_build: str | None,
    is_fallback_owner: bool,
    triggered_by: str | None = None,
):
    """Dispatch the "test is failing repeatedly" notification email.

    Called from ``POST /api/v1/analytics/notify-owner`` after the caller has
    already resolved the recipient. Kept idempotent-ish via a short dedup
    window so a double-click doesn't fan out two emails.
    """
    import hashlib

    dedup_key = (
        f"testlookup:dedup:notify_owner:{project_id}:{to_email}:"
        f"{hashlib.sha256(test_name.encode()).hexdigest()[:16]}"
    )

    dedup_owner = str(self.request.id)

    async def _run():
        if await _is_duplicate(dedup_key, ttl=300, owner=dedup_owner):
            logger.info(
                "[Task %s] notify_test_suite_owner: dedup hit for %s / %s",
                self.request.id, to_email, test_name,
            )
            return {"queued": False, "reason": "deduplicated"}

        from app.core.config import settings
        from app.services.notification import email_service

        fallback_note = " (assigned as the project default — no explicit suite owner)" if is_fallback_owner else ""
        suite_clause = f" in suite \"{suite_name}\"" if suite_name else ""
        run_clause = (
            f"\nMost recent failing build: #{latest_run_build}" if latest_run_build else ""
        )
        triggered_clause = (
            f"\nFlagged by: {triggered_by}" if triggered_by else ""
        )
        body = (
            f"Hi {owner_name or 'there'},\n\n"
            f"The test \"{test_name}\"{suite_clause} has been failing repeatedly "
            f"over the last {days} day{'s' if days != 1 else ''}"
            + (f" (failed {fail_count} time{'s' if fail_count != 1 else ''})" if fail_count else "")
            + f".\n\nYou're receiving this because you're the test suite owner{fallback_note}.\n"
            + run_clause + triggered_clause + "\n\n"
            + "Open the Failures view in TestLookup to triage:\n"
        )

        dashboard_url = f"{settings.public_base_url}/failures?days={days}"
        try:
            await email_service.send_notification(
                to=to_email,
                title=f"⚠️ Recurring failure — {test_name}",
                body=body,
                event_type="test_owner_notification",
                metadata={
                    "project_name": project_name,
                    "build_number": latest_run_build,
                    "test_name": test_name,
                    "suite_name": suite_name,
                    "fail_count": fail_count,
                    "window_days": days,
                    "dashboard_url": dashboard_url,
                },
            )
            return {"queued": True, "sent_to": to_email}
        except Exception as exc:
            logger.warning(
                "[Task %s] notify_test_suite_owner email send failed for %s: %s",
                self.request.id, to_email, exc,
            )
            raise

    try:
        return _run_async(_run())
    except Exception as exc:
        # Let Celery retry with backoff; max_retries=3 caps it.
        _release_dedup_for_retry(dedup_key, dedup_owner, self.request.id)
        raise self.retry(exc=exc, countdown=_exponential_backoff(self.request.retries))


@celery_app.task(
    name="app.worker.tasks.flush_ai_pipeline_queue",
    bind=True,
    queue="default",
    time_limit=120,
)
def flush_ai_pipeline_queue(self) -> dict:
    """Drain the AI-pipeline debouncer (Phase 3).

    Scheduled every 2 minutes by Celery beat (see ``celery_app.py``).
    Pulls runs older than ``AI_PIPELINE_DEBOUNCE_WINDOW_SECONDS`` from
    the SortedSet, groups them by project, applies the per-project
    LLM cost-budget cap, and fans out one ``run_agent_pipeline`` per
    surviving run.

    Returns the flush-summary dict for log inspection. Errors are
    caught + logged inside ``flush_pending`` — this task body just
    schedules the async call and surfaces the result.
    """
    from app.services.ai_pipeline_debouncer import flush_pending

    try:
        return _run_async(flush_pending())
    except Exception as exc:
        logger.warning(
            "flush_ai_pipeline_queue_failed task=%s error=%s",
            self.request.id, exc,
        )
        return {"drained": 0, "error": str(exc)}


@celery_app.task(
    name="app.worker.tasks.run_duplicate_detection",
    bind=True,
    queue="default",
    time_limit=900,
)
def run_duplicate_detection(
    self,
    project_id: str | None = None,
    enable_semantic: bool | None = None,
) -> dict:
    """Phase 4 — tiered duplicate authored-test-case detection per project.

    Two modes:

    * **Sweep (``project_id`` is None — the nightly beat entry).** Loads every
      project id and FANS OUT one ``run_duplicate_detection.delay(project_id=…)``
      sub-task per project, so each project gets its OWN 900s time budget and a
      single slow/large project cannot starve the tail of the project list under
      the task's hard ``time_limit`` (previously the whole sweep ran in ONE task
      and a global timeout SIGKILLed the worker mid-loop, silently skipping every
      project after the kill point — always the same later-listed projects).
      Semantic is left OFF for the sweep (cheaper + deterministic); it is run on
      demand from the router instead.
    * **Single project (``project_id`` given — the fan-out sub-task or an ad-hoc
      call).** Runs ``detect_duplicates_for_project`` for that one project. This
      worker task is the COMMIT OWNER — the detection service never commits.

    Idempotent: candidates are upserted on ``(project_id, case_a_id, case_b_id)``
    and dismissed pairs are suppressed, so repeated runs converge.
    """
    import uuid as _uuid

    from sqlalchemy import select
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import Project
    from app.services.duplicate_detection_service import (
        detect_duplicates_for_project,
    )

    # Default semantic OFF for the sweep / unspecified; the router passes True
    # explicitly for ad-hoc single-project runs.
    semantic = bool(enable_semantic) if enable_semantic is not None else False

    async def _list_project_ids() -> list:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(Project.id))).all()
            return [row[0] for row in rows]

    # ── Fan-out sweep: no project_id → enqueue one sub-task per project. ──
    if project_id is None:
        project_ids = _run_async(_list_project_ids())
        enqueued = 0
        for pid in project_ids:
            run_duplicate_detection.delay(project_id=str(pid))
            enqueued += 1
        logger.info(
            "[Task %s] run_duplicate_detection sweep fanned out %d project sub-task(s)",
            self.request.id, enqueued,
        )
        return {
            "mode": "fan_out",
            "projects_enqueued": enqueued,
        }

    # ── Single-project run (fan-out sub-task or ad-hoc call). ──
    async def _run_one(pid) -> dict:
        totals = {
            "projects_scanned": 0,
            "candidates_created": 0,
            "cases_scanned": 0,
            "sampled_projects": 0,
            "errors": 0,
        }
        async with AsyncSessionLocal() as project_db:
            try:
                result = await detect_duplicates_for_project(
                    project_db, pid, enable_semantic=semantic
                )
                await project_db.commit()
                totals["projects_scanned"] += 1
                totals["candidates_created"] += int(result.get("candidates_created", 0))
                totals["cases_scanned"] += int(result.get("cases_scanned", 0))
                if result.get("sampled"):
                    totals["sampled_projects"] += 1
            except Exception as exc:
                await project_db.rollback()
                totals["errors"] += 1
                logger.warning(
                    "run_duplicate_detection project failed",
                    extra={
                        "event": "duplicate_detection_failed",
                        "project_id": str(pid),
                        "error": str(exc),
                    },
                )
        return totals

    pid = _uuid.UUID(str(project_id))
    logger.info(
        "[Task %s] run_duplicate_detection starting (project=%s, semantic=%s)",
        self.request.id, pid, semantic,
    )
    result = cast(dict[str, Any], _run_async(_run_one(pid)))
    logger.info(
        "[Task %s] run_duplicate_detection done: scanned=%d created=%d cases=%d sampled=%d errors=%d",
        self.request.id,
        result["projects_scanned"], result["candidates_created"],
        result["cases_scanned"], result["sampled_projects"], result["errors"],
    )
    return result


# ── Retention purge (PMF US-11.4) ───────────────────────────────────────────


async def _retention_purge_sweep(project_id: str | None = None) -> dict[str, Any]:
    """Execute-mode retention purge — one project or the nightly sweep.

    Kept as a module-level coroutine (not a closure) so the beat-sweep
    behavior — per-project isolation, audit-after-commit — is directly
    unit-testable without Celery plumbing.

    Per project:

    1. its OWN session runs ``retention_service.run_purge`` (service stages,
       the worker owns the commit — transaction ratchet);
    2. the purge-audit row is written AFTER that commit on a SEPARATE
       session (``flaky_quarantine_service`` pattern: auditing inside the
       purge transaction would make the log claim deletions that could
       still roll back);
    3. a try/except per project so one failing project can't stop the sweep
       — the failure lands in the audit row's ``errors`` list.

    A project with zero candidates still gets an audit row with zeroed
    counts — the explicit "the sweep ran and found nothing" signal.
    """
    import time as _time
    import uuid as _uuid_mod

    from sqlalchemy import select as _select

    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import ProjectRetentionPolicy, SettingsAuditLog
    from app.services import retention_service

    if project_id:
        targets = [_uuid_mod.UUID(str(project_id))]
    else:
        async with AsyncSessionLocal() as db:
            targets = list(
                (
                    await db.execute(
                        _select(ProjectRetentionPolicy.project_id).where(
                            ProjectRetentionPolicy.enabled == True  # noqa: E712
                        )
                    )
                ).scalars().all()
            )

    summary: dict[str, Any] = {"projects": len(targets), "errors": 0, "results": {}}
    from app.services import deletion_job_service

    for pid in targets:
        started = _time.monotonic()
        errors: list[str] = []
        out: dict[str, Any] | None = None

        # Opened BEFORE the purge and on its own session, so a job that dies
        # mid-flight still leaves a `running` row rather than no trace at all.
        # The audit row below is written after the fact and cannot express
        # "started but never finished".
        job_id = await deletion_job_service.open_job(
            project_id=pid, job_kind=deletion_job_service.KIND_SCHEDULED
        )
        try:
            async with AsyncSessionLocal() as db:
                out = await retention_service.run_purge(
                    db, project_id=pid, mode="execute"
                )
                await db.commit()
        except Exception as exc:
            errors.append(str(exc)[:500])
            summary["errors"] += 1
            logger.warning(
                "[retention] purge failed for project %s: %s", pid, exc,
            )
        duration_ms = int((_time.monotonic() - started) * 1000)

        # Closed on its own session too — a `failed` written on the session
        # that just rolled back would be rolled back with it, so the only
        # outcome such a writer could ever record is success.
        await deletion_job_service.close_job(
            job_id,
            status=(
                deletion_job_service.FAILED
                if errors
                else deletion_job_service.COMPLETED
            ),
            counts=(out or {}).get("counts"),
            error=errors[0] if errors else None,
        )

        # Purge-audit record — settings_audit_log is itself NEVER purged,
        # which is what keeps these records durable past every window.
        try:
            async with AsyncSessionLocal() as audit_db:
                audit_db.add(
                    SettingsAuditLog(
                        setting_key=(
                            f"{retention_service.PURGE_AUDIT_KEY_PREFIX}{pid}"
                        ),
                        action="purge",
                        actor_name="retention-scheduler",
                        changed_fields={
                            "project_id": str(pid),
                            "mode": "execute",
                            "cutoffs": (out or {}).get("cutoffs"),
                            "counts": (out or {}).get("counts"),
                            "duration_ms": duration_ms,
                            "errors": errors,
                        },
                    )
                )
                await audit_db.commit()
        except Exception as exc:
            logger.warning(
                "[retention] purge-audit write failed for project %s: %s",
                pid, exc,
            )

        summary["results"][str(pid)] = {
            "counts": (out or {}).get("counts"),
            "errors": errors,
        }
    return summary



@celery_app.task(
    name="app.worker.tasks.run_scheduled_agent_eval",
    queue="default",
    bind=True,
    max_retries=0,
)
def run_scheduled_agent_eval(self, change_id: str | None = None) -> dict:
    """Evaluate the agent stack against the golden datasets, on a schedule (F-11).

    Every other quality signal in this system is event-driven: the eval gate
    fires when a prompt changes and never otherwise, so a model swap, a routing
    change or a slow drift in output quality is invisible until someone opens
    an API route by hand. This is the scheduled reading.

    It seeds the golden datasets first, because without them the gate returns
    ``FAIL: no evaluation dataset found`` -- which reads as "quality regressed"
    when it means "nothing was measured". Seeding is idempotent, so the cost
    after the first run is four SELECTs.

    ``NO_BASELINE`` is already a blocking status upstream and is deliberately
    left that way: a gate with nothing to compare against has not passed.
    """
    async def _run() -> dict:
        from datetime import datetime, timezone

        from app.db.postgres import AsyncSessionLocal
        from app.services.eval_gate_service import (
            GateStatus,
            ensure_golden_datasets,
            evaluate_agent_stack_release_gate,
        )

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        async with AsyncSessionLocal() as db:
            seeded = await ensure_golden_datasets(db)
            result = await evaluate_agent_stack_release_gate(
                db,
                change_id=change_id or f"scheduled-{stamp}",
                persist=True,
            )
            await db.commit()

        gates = list(result.get("gate_results") or [])
        by_status: dict[str, int] = {}
        for gate in gates:
            key = str(gate.get("status") or "UNKNOWN")
            by_status[key] = by_status.get(key, 0) + 1
        # The gate's own status stays untouched -- NO_BASELINE must keep
        # blocking a RELEASE, because shipping against nothing is not a pass.
        # But a daily job that reports FAIL forever is a job everyone learns to
        # ignore, and "never baselined" is a different problem from "regressed".
        # Verified against the deployment before shipping: with zero baselines
        # all four gates return NO_BASELINE, so this is the first run's state,
        # not a hypothetical.
        gate_status = str(result.get("status") or "")
        statuses = {str(g.get("status") or "") for g in gates}
        signal = (
            "NOT_BASELINED"
            if gates and statuses == {GateStatus.NO_BASELINE}
            else gate_status
        )
        summary = {
            "status": gate_status,
            "signal": signal,
            "gates_evaluated": len(gates),
            "by_status": by_status,
            "blocking_gates": list(result.get("blocking_gates") or []),
            "datasets_seeded": seeded,
            "evaluated_at": stamp,
        }
        logger.info("scheduled_agent_eval_complete", **summary)
        return summary

    return _run_async(_run())

@celery_app.task(
    name="app.worker.tasks.run_retention_purges",
    queue="default",
    bind=True,
    max_retries=0,
)
def run_retention_purges(self, project_id: str | None = None) -> dict:
    """Nightly retention purge sweep (02:00 UTC beat), or a single-project
    execute-mode purge when enqueued from the router with ``project_id``.

    The beat path only touches projects whose policy row is ``enabled``;
    the explicit-project path was already gated by the router (ADMIN +
    typed-name confirmation + 409-when-disabled).
    """
    async def _run():
        with _beat_span("run_retention_purges") as span:
            out = await _retention_purge_sweep(project_id)
            span.set_attribute("result.projects", int(out.get("projects", 0)))
            span.set_attribute("result.errors", int(out.get("errors", 0)))
            span.set_attribute("explicit_project", bool(project_id))
            logger.info(
                "[Task %s] retention purge sweep: projects=%d errors=%d",
                self.request.id, out.get("projects", 0), out.get("errors", 0),
            )
            return out

    return cast(dict, _run_async(_run()))


@celery_app.task(bind=True, name="app.worker.tasks.calibrate_flaky_classifiers")
def calibrate_flaky_classifiers(self, project_id: str | None = None) -> dict:
    """Measure how well the flaky classifier actually works, per project.

    Roadmap Phase 0 (P0-2). The backtest is retrospective and reads a 90-day
    window of failures per project, so it runs off-peak on a beat rather than
    inline on any request path.

    Nothing consumes the result yet — Phase 0 measures, a later phase gates on
    it. A per-project try/except keeps one bad project from stopping the sweep,
    matching the retention-purge sweep's shape.
    """
    async def _run():
        # Imported inside the task, matching this module's convention: a
        # module-level ``AsyncSessionLocal`` binding would pin an engine the
        # worker disposes when it tears its loop down (see F-027).
        from sqlalchemy import select

        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import Project
        from app.services.flaky_classifier_calibration import (
            calibrate_project,
            store_calibration,
        )

        with _beat_span("calibrate_flaky_classifiers") as span:
            measured = insufficient = errors = 0
            async with AsyncSessionLocal() as db:
                if project_id:
                    ids = [uuid.UUID(project_id)]
                else:
                    ids = [
                        row for row in (
                            await db.execute(select(Project.id).where(Project.is_active.is_(True)))
                        ).scalars().all()
                    ]

                for pid in ids:
                    try:
                        result = await calibrate_project(db, pid)
                        await store_calibration(db, result)
                        await db.commit()
                        if result.is_measured:
                            measured += 1
                        else:
                            insufficient += 1
                    except Exception as exc:  # noqa: BLE001 — one project must not stop the sweep
                        errors += 1
                        await db.rollback()
                        # ``logger`` in this module is the STDLIB logger
                        # (line 13); ``_slog`` is the structlog one.
                        # Keyword fields belong on _slog — passing them
                        # to stdlib raises TypeError inside the except.
                        _slog.warning(
                            "flaky_calibration_failed",
                            project_id=str(pid),
                            error_type=type(exc).__name__,
                        )

            out = {
                "projects": len(ids),
                "measured": measured,
                "insufficient": insufficient,
                "errors": errors,
            }
            span.set_attribute("result.projects", out["projects"])
            span.set_attribute("result.measured", measured)
            span.set_attribute("result.errors", errors)
            _slog.info("flaky_calibration_sweep", **out)
            return out

    return cast(dict, _run_async(_run()))


@celery_app.task(bind=True, name="app.worker.tasks.recompute_flaky_scores")
def recompute_flaky_scores(self, project_id: str | None = None) -> dict:
    """Recompute the continuous flakiness score per project (roadmap Phase 2).

    Reads a 30-day window per project, so it runs off-peak on a beat rather
    than on any request path. Per-project try/except keeps one bad project from
    stopping the sweep, matching the retention-purge and calibration sweeps.

    Fingerprints below the evidence floor are skipped rather than stored as
    0.0 — a stored zero would read as "measured, and clean".
    """
    async def _run():
        from sqlalchemy import select

        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import Project
        from app.services.flaky_score_service import score_project, store_scores

        with _beat_span("recompute_flaky_scores") as span:
            scored = skipped = errors = 0
            async with AsyncSessionLocal() as db:
                if project_id:
                    ids = [uuid.UUID(project_id)]
                else:
                    ids = list(
                        (
                            await db.execute(
                                select(Project.id).where(Project.is_active.is_(True))
                            )
                        ).scalars().all()
                    )

                for pid in ids:
                    try:
                        results = await score_project(db, pid)
                        written = await store_scores(db, pid, results, window_days=30)
                        await db.commit()
                        scored += written
                        skipped += sum(1 for r in results if not r.is_scored)
                    except Exception as exc:  # noqa: BLE001 — one project must not stop the sweep
                        errors += 1
                        await db.rollback()
                        # ``logger`` in this module is the STDLIB logger; keyword
                        # fields belong on ``_slog``.
                        _slog.warning(
                            "flaky_score_failed",
                            project_id=str(pid),
                            error_type=type(exc).__name__,
                        )

            out = {
                "projects": len(ids),
                "scored": scored,
                "skipped_insufficient": skipped,
                "errors": errors,
            }
            span.set_attribute("result.projects", out["projects"])
            span.set_attribute("result.scored", scored)
            span.set_attribute("result.errors", errors)
            _slog.info("flaky_score_sweep", **out)
            return out

    return cast(dict, _run_async(_run()))


@celery_app.task(bind=True, name="app.worker.tasks.recompute_systemic_clusters")
def recompute_systemic_clusters(self, project_id: str | None = None) -> dict:
    """Rebuild systemic co-failure clusters per project (roadmap Phase 3).

    Reads a 60-day window of failures per project, so it runs off-peak on a
    beat. Per-project try/except keeps one bad project from stopping the sweep.

    Most projects legitimately produce ZERO clusters — reported as
    ``projects_without_clusters`` rather than treated as a failure.
    """
    async def _run():
        from sqlalchemy import select

        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import Project
        from app.services.systemic_cluster_service import cluster_project, store_clusters

        with _beat_span("recompute_systemic_clusters") as span:
            clusters_written = without = errors = 0
            async with AsyncSessionLocal() as db:
                if project_id:
                    ids = [uuid.UUID(project_id)]
                else:
                    ids = list(
                        (
                            await db.execute(
                                select(Project.id).where(Project.is_active.is_(True))
                            )
                        ).scalars().all()
                    )

                for pid in ids:
                    try:
                        found = await cluster_project(db, pid)
                        written = await store_clusters(db, pid, found)
                        await db.commit()
                        clusters_written += written
                        if not written:
                            without += 1
                    except Exception as exc:  # noqa: BLE001 — one project must not stop the sweep
                        errors += 1
                        await db.rollback()
                        # ``logger`` here is the STDLIB logger; keyword fields
                        # belong on ``_slog``.
                        _slog.warning(
                            "systemic_cluster_failed",
                            project_id=str(pid),
                            error_type=type(exc).__name__,
                        )

            out = {
                "projects": len(ids),
                "clusters": clusters_written,
                "projects_without_clusters": without,
                "errors": errors,
            }
            span.set_attribute("result.projects", out["projects"])
            span.set_attribute("result.clusters", clusters_written)
            span.set_attribute("result.errors", errors)
            _slog.info("systemic_cluster_sweep", **out)
            return out

    return cast(dict, _run_async(_run()))


@celery_app.task(bind=True, name="app.worker.tasks.screen_new_test_fingerprints")
def screen_new_test_fingerprints(self, project_id: str | None = None) -> dict:
    """Tier 1 of roadmap Phase 6: screen the new and directly-modified.

    Runs on a short beat rather than in ``finalize_run``. Screening buys nothing
    by being synchronous — TestLookup ingests results, it does not execute
    tests, so there is no re-run to trigger the moment a suspect appears — and
    keeping it off the ingest path means a screening bug cannot cost an
    ingestion.

    **Gated per project** on the ``flaky_detection_timing`` feature flag, which
    is off until a project opts in. Nothing here changes a verdict, but it does
    write state, and a project that has not asked for the tier should not
    accumulate it.
    """
    async def _run():
        from sqlalchemy import select

        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import Project
        from app.services.feature_flags import is_enabled
        from app.services.flaky_detection_timing_service import record_screening
        from app.services.flaky_screening_service import screen_project

        with _beat_span("screen_new_test_fingerprints") as span:
            screened = new_tests = modified = errors = skipped = 0
            async with AsyncSessionLocal() as db:
                if project_id:
                    ids = [uuid.UUID(project_id)]
                else:
                    ids = list(
                        (
                            await db.execute(
                                select(Project.id).where(Project.is_active.is_(True))
                            )
                        ).scalars().all()
                    )

                for pid in ids:
                    try:
                        if not await is_enabled(
                            "flaky_detection_timing", db=db, project_id=pid
                        ):
                            skipped += 1
                            continue
                        candidates = await screen_project(db, pid)
                        written = await record_screening(db, pid, candidates)
                        await db.commit()
                        screened += written
                        new_tests += sum(
                            1 for c in candidates if c.reason == "new_test"
                        )
                        modified += sum(
                            1 for c in candidates if c.reason == "modified_test"
                        )
                    except Exception as exc:  # noqa: BLE001 — one project must not stop the sweep
                        errors += 1
                        await db.rollback()
                        # ``logger`` in this module is the STDLIB logger; keyword
                        # fields belong on ``_slog``.
                        _slog.warning(
                            "flaky_screening_failed",
                            project_id=str(pid),
                            error_type=type(exc).__name__,
                        )

            out = {
                "projects": len(ids),
                "screened": screened,
                "new_tests": new_tests,
                "modified_tests": modified,
                "skipped_flag_off": skipped,
                "errors": errors,
            }
            span.set_attribute("result.projects", out["projects"])
            span.set_attribute("result.screened", screened)
            span.set_attribute("result.errors", errors)
            _slog.info("flaky_screening_sweep", **out)
            return out

    return cast(dict, _run_async(_run()))


@celery_app.task(bind=True, name="app.worker.tasks.sweep_flaky_detection")
def sweep_flaky_detection(self, project_id: str | None = None) -> dict:
    """Tier 2 of roadmap Phase 6: the continuous whole-corpus pass.

    Where tier 1 screens a diff, this reaches everything else — which is where
    environment- and dependency-induced flakiness lives, and that is the part
    of the corpus screening cannot see by construction.

    Nightly, and deliberately not faster: it is measured against the flakiness
    score's own 30-day window, which does not move enough inside a day to
    justify re-reading the corpus. It runs after the score recompute so a
    fingerprint that just cleared the evidence floor has its latency clock
    closed on the same night it happened, not a day later.

    Gated per project on the same flag as tier 1.
    """
    async def _run():
        from sqlalchemy import select

        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import Project
        from app.services.feature_flags import is_enabled
        from app.services.flaky_detection_timing_service import sweep_project

        with _beat_span("sweep_flaky_detection") as span:
            swept = adopted = scored = errors = skipped = 0
            async with AsyncSessionLocal() as db:
                if project_id:
                    ids = [uuid.UUID(project_id)]
                else:
                    ids = list(
                        (
                            await db.execute(
                                select(Project.id).where(Project.is_active.is_(True))
                            )
                        ).scalars().all()
                    )

                for pid in ids:
                    try:
                        if not await is_enabled(
                            "flaky_detection_timing", db=db, project_id=pid
                        ):
                            skipped += 1
                            continue
                        result = await sweep_project(db, pid)
                        await db.commit()
                        swept += result["swept"]
                        adopted += result["adopted"]
                        scored += result["scored"]
                    except Exception as exc:  # noqa: BLE001 — one project must not stop the sweep
                        errors += 1
                        await db.rollback()
                        _slog.warning(
                            "flaky_detection_sweep_failed",
                            project_id=str(pid),
                            error_type=type(exc).__name__,
                        )

            out = {
                "projects": len(ids),
                "swept": swept,
                "adopted": adopted,
                "newly_scored": scored,
                "skipped_flag_off": skipped,
                "errors": errors,
            }
            span.set_attribute("result.projects", out["projects"])
            span.set_attribute("result.swept", swept)
            span.set_attribute("result.errors", errors)
            _slog.info("flaky_detection_sweep", **out)
            return out

    return cast(dict, _run_async(_run()))


@celery_app.task(
    name="app.worker.tasks.delete_run_everywhere",
    bind=True,
    max_retries=2,
    queue="critical",
)
def delete_run_everywhere(
    self,
    run_id: str,
    job_id: str | None = None,
    reason: str = "",
    requested_by_id: str | None = None,
):
    """Delete ONE run across all five stores. Irreversible.

    Queued by ``DELETE /api/v1/runs/{run_id}``, which has already refused
    in-flight runs, cited runs, and prefixes outside the project scope. This
    performs the deletion the route promised with its 202.

    **Reuses ``execute_candidates``.** The Mongo -> MinIO -> Postgres ordering
    is the hardest part of a cross-store delete — the Postgres CASCADE destroys
    the only mapping from a run to its documents and keys, so those stores must
    be visited while the mapping still exists. A second copy of that sequence
    is exactly what S2a existed to prevent.

    **The tombstone lands in the same commit as the deletion.** Five code paths
    re-create a ``TestRun`` from a caller-supplied id on a SELECT miss.
    Committing the tombstone first blocks live ingestion into a run that still
    exists; committing it after leaves a window in which the run is gone and
    those paths are free to bring it back. One transaction has neither problem.

    ``queue="critical"`` because an operator is waiting on the 202 and polling
    the job; behind a long ingestion backlog it would look hung.
    """
    from sqlalchemy import select

    from app.db.mongo import get_mongo_db
    from app.db.postgres import AsyncSessionLocal
    from app.db.storage import get_storage_provider
    from app.models.postgres import TestRun
    from app.services import (
        deletion_job_service,
        run_deletion_service,
        semantic_search,
    )

    async def _run() -> dict[str, Any]:
        run_uuid = uuid.UUID(run_id)
        job_uuid = uuid.UUID(job_id) if job_id else None
        counts: dict[str, Any] = {}

        async with AsyncSessionLocal() as db:
            run = (
                await db.execute(select(TestRun).where(TestRun.id == run_uuid))
            ).scalar_one_or_none()
            if run is None:
                # Already gone — a duplicate delivery, not a failure. Celery
                # is at-least-once, so this task must be idempotent.
                logger.info("[delete-run] %s already absent; nothing to do", run_id)
                return {"deleted": False, "reason": "already_absent"}

            project_uuid = run.project_id

            # Scoped to the RUN, never the project (RET-D8). None means the
            # store could not be reached — it must not be reported as 0.
            index_documents = await semantic_search.purge_run_documents(
                str(project_uuid), run_id, execute=True
            )

            counts = await run_deletion_service.perform_run_deletion(
                db,
                run=run,
                mongo=get_mongo_db(),
                storage=get_storage_provider(),
                search_index_documents=index_documents,
                reason=reason,
                deleted_by_id=(
                    uuid.UUID(requested_by_id) if requested_by_id else None
                ),
                deletion_job_id=job_uuid,
            )

            # ONE commit covers the cross-store deletion and the tombstone.
            await db.commit()

        return {"deleted": True, "counts": counts, "run_id": run_id}

    try:
        result = _run_async(_run())
    except Exception as exc:
        _run_async(
            deletion_job_service.close_job(
                uuid.UUID(job_id) if job_id else None,
                status=deletion_job_service.FAILED,
                error=str(exc)[:500],
            )
        )
        logger.warning("[delete-run] %s failed: %s", run_id, exc)
        raise

    _run_async(
        deletion_job_service.close_job(
            uuid.UUID(job_id) if job_id else None,
            status=deletion_job_service.COMPLETED,
            counts=result.get("counts"),
            resolved_run_ids=[run_id],
        )
    )
    return result


@celery_app.task(
    name="app.worker.tasks.execute_criteria_deletion_task",
    bind=True,
    max_retries=0,
    queue="critical",
)
def execute_criteria_deletion_task(
    self,
    job_id: str,
    project_id: str,
    requested_by_id: str | None = None,
):
    """Replay a frozen candidate set, one run at a time.

    Per-run rather than one big transaction, deliberately. A criteria job can
    cover thousands of runs across five stores; a single transaction holding
    all of it would sit on locks for minutes and lose everything to one bad
    row. Per-run means a failure costs one run, and the others still go.

    That is also why ``partial`` exists as an outcome. The nightly purge
    self-heals within 24h because it re-runs; nothing retries this
    (``max_retries=0`` — a retry would replay deletions already done and
    report them as failures). "Some of them went" is therefore a real, final
    state, and reporting it as either success or failure would be a lie.
    """
    from sqlalchemy import select

    from app.db.mongo import get_mongo_db
    from app.db.postgres import AsyncSessionLocal
    from app.db.storage import get_storage_provider
    from app.models.postgres import TestRun
    from app.services import (
        deletion_job_service,
        run_deletion_service,
        semantic_search,
    )

    async def _run() -> dict[str, Any]:
        job_uuid = uuid.UUID(job_id)
        project_uuid = uuid.UUID(project_id)

        async with AsyncSessionLocal() as db:
            try:
                run_ids = await deletion_job_service.claim_frozen_set(
                    db, job_id=job_uuid, project_id=project_uuid
                )
            except deletion_job_service.FrozenSetRejected as rejected:
                # The route already validated this; reaching here means a
                # duplicate delivery or a race, and re-deleting would be worse
                # than declining.
                logger.warning(
                    "[criteria-delete] job %s not executable: %s",
                    job_id, rejected.detail,
                )
                return {"executed": False, "reason": rejected.detail}

        await deletion_job_service.close_job(
            job_uuid, status=deletion_job_service.RUNNING
        )

        mongo = get_mongo_db()
        storage = get_storage_provider()
        deleted: list[str] = []
        failures: list[str] = []
        totals: dict[str, Any] = {}

        for run_id in run_ids:
            try:
                async with AsyncSessionLocal() as db:
                    run = (
                        await db.execute(
                            select(TestRun).where(TestRun.id == run_id)
                        )
                    ).scalar_one_or_none()
                    if run is None:
                        # Already gone. Idempotent, not an error.
                        deleted.append(str(run_id))
                        continue

                    index_documents = await semantic_search.purge_run_documents(
                        str(project_uuid), str(run_id), execute=True
                    )
                    counts = await run_deletion_service.perform_run_deletion(
                        db,
                        run=run,
                        mongo=mongo,
                        storage=storage,
                        search_index_documents=index_documents,
                        reason="criteria deletion",
                        deleted_by_id=(
                            uuid.UUID(requested_by_id) if requested_by_id else None
                        ),
                        deletion_job_id=job_uuid,
                    )
                    await db.commit()
                deleted.append(str(run_id))
                for store, value in (counts or {}).items():
                    if isinstance(value, dict):
                        bucket = totals.setdefault(store, {})
                        for key, num in value.items():
                            if isinstance(num, int):
                                bucket[key] = bucket.get(key, 0) + num
            except Exception as exc:  # noqa: BLE001 — one run must not stop the rest
                failures.append(f"{run_id}: {str(exc)[:200]}")
                logger.warning("[criteria-delete] run %s failed: %s", run_id, exc)

        status = deletion_job_service.outcome_status(
            requested=len(run_ids), deleted=len(deleted)
        )
        await deletion_job_service.close_job(
            job_uuid,
            status=status,
            counts=totals or None,
            resolved_run_ids=deleted or None,
            error="; ".join(failures[:5]) if failures else None,
        )
        return {
            "executed": True,
            "status": status,
            "requested": len(run_ids),
            "deleted": len(deleted),
            "failed": len(failures),
        }

    return _run_async(_run())


@celery_app.task(
    name="app.worker.tasks.reconcile_active_releases",
    bind=True,
    queue="default",
    time_limit=300,
)
def reconcile_active_releases(self) -> dict:
    """Sweep for projects with no active release, repair them, and REPORT.

    Migration 0150. Every other enforcement point — project creation, rotation,
    deletion, reset, the defensive resolve at ingest — is supposed to keep the
    invariant true. This task exists because "supposed to" is not a guarantee,
    and because SQL cannot express "every project has a row over there".

    The reporting half is not incidental. A reconciliation task that silently
    repairs what it finds makes the invariant look perfect precisely *because*
    something keeps fixing it, and the defect that caused the violation is
    never seen. So every detection increments a counter and is written to the
    audit log with ``record_attempt`` — which commits on its own session and
    therefore survives a repair that fails.

    Two counters, deliberately: ``sweeps_total`` proves the sweep ran at all,
    because a violations counter sitting at 0 reads identically whether nothing
    is broken or nothing is checking. Alert on violations only against a
    non-zero, increasing sweep count.
    """

    async def _run() -> dict:
        from app.core.metrics import (
            release_invariant_sweeps_total,
            release_invariant_violations_total,
        )
        from app.db.postgres import AsyncSessionLocal
        from app.services import release_lifecycle_service
        from app.services.audit_log_service import record_attempt

        repaired: list[str] = []
        failed: list[str] = []

        async with AsyncSessionLocal() as db:
            project_ids = (
                await release_lifecycle_service.find_projects_without_active_release(db)
            )

        for project_id in project_ids:
            # Record the DETECTION before attempting the repair, on its own
            # session. If the repair then fails, the fact that the invariant
            # was violated is still on the record.
            await record_attempt(
                action="release.invariant_violation_detected",
                setting_key=f"release.active_missing:{project_id}",
                actor_name="system",
            )
            try:
                async with AsyncSessionLocal() as db:
                    await release_lifecycle_service.get_or_create_active_release(
                        db, project_id, reason="reconciliation"
                    )
                    await db.commit()
                repaired.append(str(project_id))
                release_invariant_violations_total.labels(outcome="repaired").inc()
            except Exception as exc:
                failed.append(str(project_id))
                release_invariant_violations_total.labels(
                    outcome="repair_failed"
                ).inc()
                _slog.warning(
                    "active_release_repair_failed",
                    project_id=str(project_id),
                    error=str(exc),
                )

        release_invariant_sweeps_total.inc()

        if project_ids:
            _slog.warning(
                "active_release_invariant_violations",
                found=len(project_ids),
                repaired=len(repaired),
                failed=len(failed),
            )

        return {
            "checked": True,
            "violations": len(project_ids),
            "repaired": len(repaired),
            "failed": len(failed),
        }

    return _run_async(_run())


@celery_app.task(
    name="app.worker.tasks.reconcile_primary_releases",
    bind=True,
    queue="default",
    time_limit=300,
)
def reconcile_primary_releases(self) -> dict:
    """Repair drift between ``test_runs.primary_release_id`` and the link table.

    Migration 0152 denormalized the primary release onto ``test_runs`` so a
    release filter is one indexed predicate rather than a join. Four live paths
    change which link is primary and each must call ``sync_primary_release``;
    this sweep exists because "must" is not "does".

    Drift is not cosmetic. Release-scoped analytics read the denormalized
    column while ``/runs`` reads the link table, so a stale value makes two
    surfaces disagree about which release a run belongs to — with no error
    anywhere. The counters make a missed call site measurable instead of
    invisible.
    """

    async def _run() -> dict:
        from app.core.metrics import (
            release_primary_drift_total,
            release_primary_sweeps_total,
        )
        from app.db.postgres import AsyncSessionLocal
        from app.services.release_linker import (
            find_primary_release_drift,
            sync_primary_release,
        )

        repaired: list[str] = []
        failed: list[str] = []

        async with AsyncSessionLocal() as db:
            drifted = await find_primary_release_drift(db)

        for run_id in drifted:
            try:
                async with AsyncSessionLocal() as db:
                    await sync_primary_release(db, run_id)
                    await db.commit()
                repaired.append(str(run_id))
                release_primary_drift_total.labels(outcome="repaired").inc()
            except Exception as exc:
                failed.append(str(run_id))
                release_primary_drift_total.labels(outcome="repair_failed").inc()
                _slog.warning(
                    "primary_release_repair_failed",
                    run_id=str(run_id),
                    error=str(exc),
                )

        release_primary_sweeps_total.inc()

        if drifted:
            _slog.warning(
                "primary_release_drift_detected",
                found=len(drifted),
                repaired=len(repaired),
                failed=len(failed),
            )

        return {
            "checked": True,
            "drifted": len(drifted),
            "repaired": len(repaired),
            "failed": len(failed),
        }

    return _run_async(_run())


@celery_app.task(
    name="app.worker.tasks.reconcile_release_sort_keys",
    bind=True,
    queue="default",
    time_limit=300,
)
def reconcile_release_sort_keys(self) -> dict:
    """Fill in ``releases.sort_key`` for rows that have none.

    Migration 0151 adds the column and 0153 indexes it, but neither computes
    it. An earlier draft reimplemented the encoder in SQL and got pre-releases
    wrong — every ``2.4.0-rc1`` landed in the text band, sorting after its own
    GA instead of before it, which is the precise inversion the encoder exists
    to prevent. Two implementations of one encoding is a drift this codebase
    has paid for before, and SQL is the copy that cannot be unit-tested, so it
    was deleted rather than patched.

    This sweep is the single writer's reach into rows the writers missed:
    releases created before 0151, and any future path that forgets. Safe to
    run repeatedly — it only touches NULLs.
    """

    async def _run() -> dict:
        from app.db.postgres import AsyncSessionLocal
        from app.services.release_linker import (
            find_releases_missing_sort_key,
            sync_release_sort_key,
        )

        filled: list[str] = []
        failed: list[str] = []

        async with AsyncSessionLocal() as db:
            missing = await find_releases_missing_sort_key(db)

        for release_id in missing:
            try:
                async with AsyncSessionLocal() as db:
                    await sync_release_sort_key(db, release_id)
                    await db.commit()
                filled.append(str(release_id))
            except Exception as exc:
                failed.append(str(release_id))
                _slog.warning(
                    "release_sort_key_backfill_failed",
                    release_id=str(release_id),
                    error=str(exc),
                )

        if missing:
            _slog.info(
                "release_sort_keys_backfilled",
                found=len(missing),
                filled=len(filled),
                failed=len(failed),
            )

        return {
            "checked": True,
            "missing": len(missing),
            "filled": len(filled),
            "failed": len(failed),
        }

    return _run_async(_run())
