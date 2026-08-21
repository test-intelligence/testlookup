"""Celery background tasks for ingestion and AI analysis."""
import asyncio
import logging
import random
import uuid
from typing import Any, cast

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)
_slog = structlog.get_logger("worker.tasks")


def _run_async(coro):
    """Run an async coroutine in a Celery task (sync context).

    Each Celery task runs in a separate thread / process.  The global async
    clients (Redis, SQLAlchemy engine) hold references to the event loop that
    was current when they were first created.  If that loop was already closed
    (e.g. from a previous task invocation) we get "Event loop is closed" /
    "Future attached to a different loop" errors.

    Fix: reset the module-level singletons before creating the new loop so
    that the first `get_redis()` call inside the coroutine creates a fresh
    client bound to the *current* loop.

    BUG-003: the SQLAlchemy async engine has the same problem but worse — its
    asyncpg connections are *pooled* across tasks via the ``@lru_cache``'d
    ``get_engine()``. The pool stays bound to the loop that first built it; when
    that loop is closed here, the pooled connections become attached to a dead
    loop and asyncpg raises ``RuntimeError: Event loop is closed`` when it later
    tries to terminate/GC them ("Exception terminating connection …"). That
    surfaced as the AI pipeline reporting ``errors=1`` / status ``partial``.
    Fix: dispose the engine *inside this loop* in the ``finally`` block (which
    closes its connections on the loop that owns them) and clear the lazy-build
    cache so the next task rebuilds a fresh engine on its own loop.
    """
    from app.db.loop_bound import reset_loop_bound_clients
    reset_loop_bound_clients()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            # Dispose the async engine on THIS loop before it closes, so its
            # pooled asyncpg connections are torn down on the loop that owns
            # them (BUG-003). Must run before shutdown_asyncgens / loop.close().
            from app.db.postgres import dispose_engine_for_loop
            loop.run_until_complete(dispose_engine_for_loop())
        except Exception as _exc:
            # Previously a bare ``pass``. A failing teardown leaves the pool
            # alive with connections bound to a loop that is about to close,
            # and said nothing at all — which is exactly the kind of silence
            # that hid F-027. Log it; still never raise from a finally block.
            logger.warning("engine_dispose_failed_in_task_teardown error=%r", _exc)
        try:
            # The shared httpx.AsyncClient is rotated per loop by
            # get_http_client(), but the OUTGOING one was only ever
            # dropped -- close_http_client() is called from the FastAPI
            # lifespan and from nowhere in the worker path, so each task
            # abandoned a client whose pool still held sockets bound to
            # the loop about to close. Same reasoning as the engine
            # disposal above: drain it on the loop that owns it.
            from app.core.http_client import close_http_client
            loop.run_until_complete(close_http_client())
        except Exception as _exc:
            logger.warning(
                "http_client_close_failed_in_task_teardown error=%r", _exc
            )
        try:
            # Close all async generators and pending tasks cleanly
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()


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
    """Return jittered exponential backoff seconds: min(base * 2^attempt, cap) ± 20%."""
    delay = min(base * (2 ** attempt), cap)
    jitter = delay * 0.2 * random.random()
    return int(delay + jitter)


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


# ── Tasks ─────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="app.worker.tasks.persist_live_session",
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
):
    """
    Persist a completed live execution session to PostgreSQL.

    Reads the test-event buffer from Redis (LIVE_TESTCASES_KEY) and creates:
      - One TestRun row (with aggregated counts from final_state / Redis data)
      - One TestCase row per event

    Called by DELETE /api/v1/stream/sessions/{session_id} after run_complete.
    Deduplicates by run_id so retries are safe.
    """
    import hashlib
    import json
    import uuid as _uuid_mod
    from datetime import datetime, timezone

    final_state = final_state or {}

    async def _run():
        # Idempotency rule: skip only when persistence has actually completed
        # — i.e., TestCase rows already exist for this run's canonical UUID.
        # Determined by a single COUNT(*) query, **on the same session as
        # the writes below**. A previous version opened a separate
        # AsyncSessionLocal() for the count, which under asyncpg's
        # connection pool raced with the main session's writes and raised
        # "asyncpg.InterfaceError: cannot perform operation: another
        # operation is in progress" — silently dropping retries.
        from sqlalchemy import select as _sel, func as _func

        from app.db.postgres import AsyncSessionLocal
        from app.db.redis_client import get_redis
        from app.streams import LIVE_TESTCASES_KEY
        from app.models.postgres import (
            TestCase, TestRun, TestStatus,
        )
        from app.services.stream_service import canonical_test_run_uuid
        from app.services.run_status import terminal_run_status

        redis = get_redis()
        list_key = LIVE_TESTCASES_KEY.format(run_id=run_id)

        # ── Read buffered test events ─────────────────────────────────────────
        raw_entries = await redis.lrange(list_key, 0, -1)
        events = []
        for raw in raw_entries:
            try:
                events.append(json.loads(raw))
            except Exception:
                pass

        logger.info(
            "[Task %s] Persisting live session: run=%s events=%d",
            self.request.id, run_id, len(events),
        )

        # ── Compute aggregate counts ──────────────────────────────────────────
        # ``final_state`` comes from the authoritative HINCRBY counters
        # (LIVE_STATE_KEY hash). It's accurate even when the per-test
        # buffer hit its LTRIM cap mid-run or got partially drained by
        # the Phase 4.5 incremental-drain task. Prefer it whenever a
        # ``total`` was reported; only fall back to event-derived counts
        # for legacy paths that never populated final_state (e.g.
        # ``recover_live_run_from_buffer``).
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

            # Idempotency check. Phase 4.5 incremental drain means a run
            # can legitimately have BOTH existing TestCase rows AND a
            # non-empty buffer (the tail of events that landed between
            # the last drain tick and close_session). So we only skip
            # when there's truly nothing left to do: buffer empty AND
            # rows already present. The "buffer empty + rows present"
            # path covers Celery retries firing this task twice for the
            # same close, plus the legacy manual-recovery flow.
            existing_tc_count = (
                await db.execute(
                    _sel(_func.count(TestCase.id)).where(TestCase.test_run_id == run_uuid)
                )
            ).scalar() or 0
            if existing_tc_count > 0 and not events:
                logger.info(
                    "[Task %s] Skipping persist for run=%s — %d TestCase rows already "
                    "present and buffer is empty (incremental drain or earlier retry)",
                    self.request.id, run_id, existing_tc_count,
                )
                # Genuine no-op — a previous tick already drained
                # everything OR this is a duplicate close_session
                # retry. Skip finalize_run too; running it a second
                # time double-fires auto-tagging + suite_sync.
                return

            # Upsert TestRun — skip if already exists (idempotent)
            existing = await db.execute(select(TestRun).where(TestRun.id == run_uuid))
            run = existing.scalar_one_or_none()

            session_suite = (suite_name or "").strip() or None
            if run is None:
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
                    start_time=now,
                    end_time=now,
                )
                db.add(run)
                await db.flush()   # assigns DB id before we reference it in TestCase FKs
            else:
                # Update aggregates on the existing row
                run.status       = run_status
                run.total_tests  = total
                run.passed_tests = passed
                run.failed_tests = failed
                run.skipped_tests = skipped
                run.broken_tests  = broken
                run.unknown_tests = unknown
                run.pass_rate     = pass_rate
                run.end_time      = now
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
                test_name  = event.get("test_name") or ""
                class_name = event.get("class_name") or ""
                raw_status = (event.get("status") or "UNKNOWN").upper()

                try:
                    tc_status = TestStatus(raw_status)
                except ValueError:
                    tc_status = TestStatus.UNKNOWN

                fingerprint = hashlib.md5(
                    f"{test_name}:{class_name}".encode()
                ).hexdigest()

                event_suite = (event.get("suite_name") or "").strip()
                resolved_suite = (event_suite or default_suite or "")[:500] or None

                rows.append({
                    "id": _uuid_mod.uuid4(),
                    "test_run_id": run.id,
                    "test_fingerprint": fingerprint,
                    "test_name": test_name[:1000],
                    "suite_name": resolved_suite,
                    "class_name": class_name[:500] or None,
                    "status": tc_status.value if hasattr(tc_status, "value") else tc_status,
                    "duration_ms": event.get("duration_ms"),
                    "error_message": event.get("error_message"),
                    "tags": event.get("tags"),
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
            else:
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
        try:
            from app.services.ingestion_pipeline import finalize_run
            await finalize_run(
                run_id=str(run_uuid),
                project_id=str(proj_uuid),
                build_number=build_number,
            )
        except Exception as exc:
            # finalize_run runs each step inside an isolated session and
            # logs its own failures; an outer failure here is unexpected.
            # Don't fail the task — TestCase rows are already committed and
            # the next persist retry will short-circuit on the dedup check.
            logger.warning(
                "[Task %s] finalize_run failed after live persist run=%s: %s",
                self.request.id, run_id, exc,
            )

        # ── Clean up Redis buffer ─────────────────────────────────────────────
        # Guarded: rows are already committed, so a Redis blip here must not
        # raise and trigger a spurious full-task retry (which would re-run the
        # dedup-skip path anyway). The 25h TTL reclaims the buffer regardless.
        try:
            await redis.delete(list_key)
        except Exception as exc:
            logger.warning(
                "[Task %s] buffer cleanup failed for run=%s (TTL will reclaim): %s",
                self.request.id, run_id, exc,
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
def ingest_test_run(self, sentinel_dict: dict, minio_prefix: str):
    """
    Background task: parse Allure JSON + TestNG XML from MinIO and
    upsert structured data into PostgreSQL + MongoDB.
    Deduplicates by minio_prefix so concurrent webhooks don't double-ingest.
    """
    from app.models.schemas import SentinelFile
    from app.services.ingestion import process_sentinel

    dedup_key = f"testlookup:dedup:ingest:{minio_prefix}"

    async def _run():
        if await _is_duplicate(dedup_key):
            logger.info("[Task %s] Skipping duplicate ingestion for %s", self.request.id, minio_prefix)
            return
        sentinel = SentinelFile(**sentinel_dict)
        await process_sentinel(sentinel, minio_prefix)

    logger.info("[Task %s] Starting ingestion: %s", self.request.id, minio_prefix)
    try:
        _run_async(_run())
        logger.info("[Task %s] Ingestion complete", self.request.id)

        # ROI-04: Trigger incremental search indexing after ingestion
        try:
            reindex_search.apply_async(kwargs={"full": False}, countdown=5)
        except Exception:
            pass  # Non-blocking — indexing will catch up on the next hourly beat
    except Exception as exc:
        logger.error("[Task %s] Ingestion failed: %s", self.request.id, exc, exc_info=True)
        countdown = _exponential_backoff(self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)


# ── Unified Ingest Tasks (POST /api/v1/ingest) ──────────────────────────────


@celery_app.task(
    name="app.worker.tasks.ingest_uploaded_results",
    bind=True,
    max_retries=3,
    queue="ingestion",
)
def ingest_uploaded_results(self, run_id: str, payload: dict, user_id: str):
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
            run_id=run_id,
            project_id=payload["project_id"],
            build_number=payload["build_number"],
            release_name=payload.get("release_name"),
        )

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
    file_content: str,
    file_name: str,
    file_format: str,
    project_id: str,
    build_number: str,
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
    environment: str = None,
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
        try:
            results = _parse_file_to_results(
                file_content, file_format, file_name, run_id, disabled_formats=disabled_formats,
            )
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
                    environment=environment,
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

        await finalize_run(
            run_id=run_id,
            project_id=project_id,
            build_number=build_number,
            release_name=release_name,
            run_ai=run_ai,
        )
        # Counts reflect what was actually ingested (total=count); per-status
        # breakdown is computed case-insensitively because parsers disagree on
        # casing (testng/allure emit lowercase; cypress/playwright uppercase).
        summary = _summarize_upload(results, ingested=count)
        await upload_status.set_status(
            task_id, run_id=run_id, project_id=project_id,
            state=upload_status.STATE_SUCCEEDED, result=summary,
        )
        _m.uploads_total.labels(state="succeeded", format=file_format).inc()
        _m.upload_processing_seconds.observe(_time.monotonic() - _t0)
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


def _summarize_upload(results: list[dict], *, ingested: int) -> dict:
    """Build the SUCCEEDED-status result summary.

    ``total`` reflects rows actually ingested (not parsed) so the UI count
    matches the run. Per-status counts are case-insensitive because parsers
    disagree on casing: testng/allure emit lowercase, cypress/playwright upper.
    """
    counts: dict[str, int] = {}
    for r in results:
        key = str(r.get("status") or "").upper()
        counts[key] = counts.get(key, 0) + 1
    return {
        "total": ingested,
        "passed": counts.get("PASSED", 0),
        "failed": counts.get("FAILED", 0),
        "skipped": counts.get("SKIPPED", 0),
        "broken": counts.get("BROKEN", 0),
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
        ))
    except Exception as exc:
        logger.error("[Task %s] Notification dispatch failed: %s", self.request.id, exc, exc_info=True)
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.worker.tasks.dispatch_transition_notifications",
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
    from app.services.notification_transitions import evaluate_run_transitions

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
    except Exception as exc:
        logger.error("[Task %s] Transition notification dispatch failed: %s", self.request.id, exc, exc_info=True)
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.worker.tasks.run_agent_pipeline",
    bind=True,
    max_retries=2,
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

    async def _run():
        if await _is_duplicate(dedup_key, ttl=7200, owner=dedup_owner):
            logger.info(
                "[Task %s] Skipping duplicate pipeline for run=%s type=%s",
                self.request.id, test_run_id, workflow_type,
            )
            return {"completed_stages": [], "error_count": 0, "duplicate": True}

        if workflow_type == "deep":
            return await run_deep_pipeline(
                test_run_id=test_run_id,
                project_id=project_id,
                build_number=build_number,
            )
        return await run_offline_pipeline(
            test_run_id=test_run_id,
            project_id=project_id,
            build_number=build_number,
            workflow_type=workflow_type,
        )

    logger.info(
        "[Task %s] Starting agent pipeline run=%s build=%s type=%s",
        self.request.id, test_run_id, build_number, workflow_type,
    )
    try:
        final_state = _run_async(_run())
        stages_done = final_state.get("completed_stages", [])
        errors = final_state.get("errors", [])
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

        # EM-1: Dispatch AI summary email after pipeline completes
        if "summary" in stages_done:
            try:
                dispatch_ai_summary_email.delay(
                    test_run_id=test_run_id,
                    project_id=project_id,
                    build_number=build_number,
                )
                logger.debug("[Task %s] AI summary email queued for run %s", self.request.id, test_run_id)
            except Exception as email_exc:
                logger.warning(
                    "[Task %s] AI summary email dispatch failed (non-blocking, %s)",
                    self.request.id,
                    type(email_exc).__name__,
                )

        return {"completed_stages": stages_done, "error_count": len(errors)}
    except Exception as exc:
        safe_error = f"{type(exc).__name__}: agent pipeline failed"
        logger.error(
            "[Task %s] Pipeline failed (%s)",
            self.request.id,
            type(exc).__name__,
        )
        try:
            _run_async(_release_duplicate_lock(dedup_key, dedup_owner))
        except Exception as release_exc:
            logger.warning(
                "[Task %s] Failed to release pipeline dedup lock after error (%s)",
                self.request.id,
                type(release_exc).__name__,
            )
        if self.request.retries >= self.max_retries:
            # Move to DLQ before the final exception propagates
            _run_async(_send_to_dlq(
                task_name=self.name,
                task_id=self.request.id,
                kwargs={"test_run_id": test_run_id, "build_number": build_number},
                error=safe_error,
            ))
        countdown = _exponential_backoff(self.request.retries)
        raise self.retry(exc=RuntimeError(safe_error), countdown=countdown)


@celery_app.task(
    name="app.worker.tasks.resume_agent_pipeline",
    bind=True,
    max_retries=0,
    queue="ai_analysis",
    time_limit=1800,
)
def resume_agent_pipeline(self, pipeline_run_id: str, build_number: str = "resume"):
    """Resume a failed/partial pipeline under its existing pipeline identity.

    The workflow atomically claims the terminal row, preserves the immutable
    initial plan, and replays only checksum-authorized completed checkpoints.
    Duplicate deliveries return ``pipeline_not_resumable`` without spending
    another model call.
    """
    _bind_task_context(self, pipeline_run_id=pipeline_run_id)
    from app.agents.workflow import resume_pipeline

    try:
        return _run_async(resume_pipeline(pipeline_run_id, build_number))
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
        from app.models.postgres import TestCase
        from app.services import run_compare_ai_service, run_compare_service

        rid = _uuid.UUID(test_run_id)
        pid = _uuid.UUID(project_id)
        generated = 0
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
            suites = [row.suite_name for row in suites_result.all() if row.suite_name]
            for suite in suites:
                try:
                    previous, latest = await run_compare_service.resolve_latest_suite_pair(
                        db,
                        project_id=pid,
                        suite_name=suite,
                    )
                    if latest.id != rid:
                        continue
                    selection = {
                        "mode": "latest_vs_previous",
                        "scope": "suite",
                        "suite_name": suite,
                        "selection_reason": "Precomputed after run completion for the latest suite run on this branch.",
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
                    await run_compare_ai_service.generate_and_save_report(
                        db,
                        project_id=pid,
                        left_run_id=previous.id,
                        right_run_id=latest.id,
                        suite_name=suite,
                        compare_payload=compare_payload,
                    )
                    generated += 1
                except LookupError:
                    continue
                except Exception as exc:
                    logger.warning("[Task %s] Suite comparison precompute failed for %s: %s", self.request.id, suite, exc)
        return {"generated": generated}

    try:
        return _run_async(_run())
    except Exception as exc:
        logger.error("[Task %s] Suite comparison precompute failed: %s", self.request.id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=_exponential_backoff(self.request.retries))


@celery_app.task(
    name="app.worker.tasks.dispatch_ai_summary_email",
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
    subscribed to AI_ANALYSIS_COMPLETE notifications. Deduplicates per run.
    """
    _bind_task_context(self, run_id=test_run_id, project_id=project_id)
    import uuid as _uuid

    from datetime import datetime, timezone

    dedup_key = f"testlookup:dedup:ai_email:{test_run_id}"

    async def _dispatch():
        # Dedup check
        if await _is_duplicate(dedup_key, ttl=3600):
            logger.info("[AI Email] Skipping duplicate for run %s", test_run_id)
            return

        from app.db.postgres import AsyncSessionLocal
        from app.db.mongo import get_mongo_db, Collections
        from app.models.postgres import Project as _Project, TestRun as _TestRun
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            # Load run and project
            run = (await db.execute(select(_TestRun).where(_TestRun.id == _uuid.UUID(test_run_id)))).scalar_one_or_none()
            if not run:
                logger.warning("[AI Email] Run %s not found", test_run_id)
                return

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
        from app.services.notification.manager import dispatch_ai_summary_notifications

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
        )

        # 2. EM-4: Dispatch to PER_RUN digest subscribers
        try:
            from app.models.postgres import DigestSubscription, User as _User
            from app.services.notification import email_service

            async with AsyncSessionLocal() as db:
                per_run_result = await db.execute(
                    select(DigestSubscription).where(
                        DigestSubscription.schedule == "PER_RUN",
                        DigestSubscription.is_active == True,  # noqa: E712
                        DigestSubscription.is_paused == False,  # noqa: E712
                    )
                )
                per_run_subs = per_run_result.scalars().all()

                for sub in per_run_subs:
                    # Scope check: global or matching project
                    if sub.project_id and str(sub.project_id) != project_id:
                        continue
                    # Trigger filter: failed_only skips all-green runs
                    if sub.trigger_filter == "failed_only" and _failed_tests == 0:
                        continue
                    if sub.trigger_filter == "degraded_only" and _pass_rate >= 90:
                        continue

                    # Get user email
                    user = (await db.execute(select(_User).where(_User.id == sub.user_id))).scalar_one_or_none()
                    if not user or not user.email:
                        continue

                    # Dedup per (subscription, run)
                    sub_dedup = f"testlookup:dedup:per_run_email:{sub.id}:{test_run_id}"
                    if await _is_duplicate(sub_dedup, ttl=3600):
                        continue

                    title = f"🤖 AI Summary — Build {build_number} ({project_name})"

                    await email_service.send_notification(
                        to=user.email,
                        title=title,
                        body=executive_summary,
                        event_type="ai_analysis_complete",
                        metadata={
                            "project_name": project_name,
                            "build_number": build_number,
                            "pass_rate": _pass_rate,
                            "total_tests": _total_tests,
                            "failed_tests": _failed_tests,
                            "dashboard_url": f"{settings.public_base_url}/runs/{test_run_id}/intelligence",
                            "executive_panel": executive_panel,
                        },
                    )
                    # Update delivery tracking atomically so concurrent
                    # PER_RUN dispatches (different runs landing at the same
                    # time) cannot lose a delivery_count increment.
                    from sqlalchemy import update as _sql_update
                    await db.execute(
                        _sql_update(DigestSubscription)
                        .where(DigestSubscription.id == sub.id)
                        .values(
                            delivery_count=DigestSubscription.delivery_count + 1,
                            last_delivered_at=datetime.now(timezone.utc),
                        )
                    )
                    await db.commit()
                    logger.debug("[AI Email] Per-run email sent to %s for run %s (sub %s)", user.email, test_run_id, sub.id)
        except Exception as sub_exc:
            logger.warning("[AI Email] Per-run subscription dispatch failed (non-blocking): %s", sub_exc)

        # TG-5/6: Apply AI-derived signal tags after analysis
        try:
            from app.services.auto_tagging_service import auto_tag_after_analysis
            async with AsyncSessionLocal() as tag_db:
                await auto_tag_after_analysis(tag_db, _uuid.UUID(test_run_id))
                await tag_db.commit()
        except Exception as tag_exc:
            logger.warning("[AI Email] Post-analysis auto-tagging failed (non-blocking): %s", tag_exc)

        logger.info("[AI Email] Summary email dispatched for run %s (build %s)", test_run_id, build_number)

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
    from app.services.test_case_ai_agent import generate_test_cases_tool

    async def _persist(result: dict) -> int:
        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import ManagedTestCase, TestCaseVersion

        saved = 0
        async with AsyncSessionLocal() as db:
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
                ver = TestCaseVersion(
                    test_case_id=tc.id,
                    version=1,
                    title=tc.title,
                    description=tc.description,
                    steps=tc.steps,
                    expected_result=tc.expected_result,
                    status="draft",
                    changed_by_id=_uuid.UUID(author_id),
                    change_summary="AI generated",
                    change_type="created",
                )
                db.add(ver)
                saved += 1
            await db.commit()
        return saved

    logger.info("[Task %s] AI generate test cases project=%s", self.request.id, project_id)
    try:
        raw = generate_test_cases_tool.invoke({"requirements": requirements})
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
    from app.services.test_case_ai_agent import optimize_test_plan_tool

    async def _build_and_save() -> dict:
        from sqlalchemy import select
        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import ManagedTestCase, TestPlan, TestPlanItem

        async with AsyncSessionLocal() as db:
            q = select(ManagedTestCase).where(
                ManagedTestCase.project_id == _uuid.UUID(project_id),
                ManagedTestCase.status.in_(["approved", "active"]),
            )
            result = await db.execute(q)
            cases = result.scalars().all()
            if not cases:
                return {"error": "No approved test cases found for this project"}

            tc_json = json.dumps([{
                "title": c.title,
                "priority": c.priority,
                "test_type": c.test_type,
                "estimated_duration_minutes": c.estimated_duration_minutes or 5,
            } for c in cases], indent=2)
            constraints_text = constraints or "No specific constraints. Optimize for maximum risk coverage."

            raw = optimize_test_plan_tool.invoke({
                "test_cases_json": tc_json,
                "constraints": constraints_text,
            })
            optimization = json.loads(raw) if isinstance(raw, str) else raw

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
                    test_case_id=tc.id,
                    order_index=order_map.get(tc.title, 999),
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
    from app.services.test_case_ai_agent import generate_test_strategy_tool

    async def _build_and_save() -> dict:
        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import TestStrategy
        from app.core.config import settings

        raw = generate_test_strategy_tool.invoke({"project_context": project_context})
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
        return {"indexed_count": count, "project_id": project_id, "mode": "full" if full else "incremental"}

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
    """Periodic task: find stale/failed sources and enqueue individual sync tasks."""
    async def _find_and_enqueue():
        from app.db.postgres import AsyncSessionLocal
        from app.services.knowledge_sync_service import list_stale_sources

        async with AsyncSessionLocal() as db:
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

        logger.info("Knowledge resync scheduled: enqueued=%d, total_stale=%d", enqueued, len(stale))
        return {"enqueued": enqueued, "total_stale": len(stale)}

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
def deliver_webhook(self, delivery_id: str) -> dict:
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
            return await deliver(_uuid_mod.UUID(delivery_id))
        except Exception as exc:
            logger.warning(
                "[Task %s] deliver_webhook unhandled error: %s",
                self.request.id, exc,
            )
            return {"error": str(exc), "retry": False}

    result = cast(dict, _run_async(_run()))

    if result.get("retry"):
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

    Runs three passes in order:

      1. ``expire_stale_proposals`` — PROPOSED rows older than 7 days
         flip to EXPIRED so the UI stays readable.
      2. ``schedule_pending_rechecks`` — QUARANTINED rows whose
         ``recheck_at`` has passed move to RECHECK_SCHEDULED.
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

async def _send_to_dlq(task_name: str, task_id: str, kwargs: dict, error: str) -> None:
    """Write a failed task to the Redis DLQ stream for manual inspection and replay."""
    try:
        import json
        from app.db.redis_client import get_redis
        from app.streams import DLQ_STREAM
        redis = get_redis()
        await redis.xadd(
            DLQ_STREAM,
            {
                "source": "celery",
                "task_name": task_name,
                "task_id": task_id,
                "kwargs": json.dumps(kwargs),
                "error": error[:500],
            },
            maxlen=5000,
            approximate=True,
        )
        logger.error("Moved failed task to DLQ: task=%s id=%s error=%s", task_name, task_id, error)
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

                    if skip_unchanged:
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
                        webhook_url = None
                        for p in prefs:
                            webhook_url = (
                                p.slack_webhook_url if channel == "slack"
                                else p.teams_webhook_url
                            )
                            if webhook_url:
                                break
                        if not webhook_url:
                            global_webhooks = await resolve_global_notification_webhooks(db)
                            if global_webhooks[f"{channel}_enabled"]:
                                webhook_url = global_webhooks[f"{channel}_webhook_url"]
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
                                    )
                                else:
                                    from app.services.notification import teams_service
                                    await teams_service.send_notification(
                                        webhook_url=webhook_url,
                                        title=title,
                                        body=digest_body,
                                        event_type="digest_delivery",
                                        metadata={},
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
    from app.services.stream_service import close_session
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
                    state = await RedisLiveRunState.get(session.run_id)
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

                    pipeline.status = "failed"
                    pipeline.completed_at = datetime.now(timezone.utc)
                    if not pipeline.error:
                        pipeline.error = (
                            "Stage failure detected by reaper" if has_failed_stage
                            else f"Pipeline exceeded {stale_minutes}m without completion"
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

    async def _run():
        if await _is_duplicate(dedup_key, ttl=300):
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
    for pid in targets:
        started = _time.monotonic()
        errors: list[str] = []
        out: dict[str, Any] | None = None
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
        # module-level ``AsyncSessionLocal`` binding would pin the engine that
        # ``_run_async`` disposes between tasks (see F-027).
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
