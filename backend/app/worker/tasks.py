"""Celery background tasks for ingestion and AI analysis."""
import asyncio
import logging
import random
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
    """
    import app.db.redis_client as _redis_mod
    _redis_mod._pool = None
    _redis_mod._client = None

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
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

async def _is_duplicate(key: str, ttl: int = 3600) -> bool:
    """
    Return True if `key` already exists in Redis (task already running/done).
    Otherwise, set the key with TTL and return False.
    """
    from app.db.redis_client import get_redis
    redis = get_redis()
    # SET NX — only sets if key does not exist; returns True on first write
    was_set = await redis.set(key, "1", ex=ttl, nx=True)
    return not bool(was_set)


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

    dedup_key = f"testlookup:dedup:live_persist:{run_id}"
    final_state = final_state or {}

    async def _run():
        if await _is_duplicate(dedup_key, ttl=3600):
            logger.info("[Task %s] Skipping duplicate live persist for %s", self.request.id, run_id)
            return

        from app.db.redis_client import get_redis
        from app.streams import LIVE_TESTCASES_KEY
        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import (
            LaunchStatus, TestCase, TestRun, TestStatus,
        )

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
        passed  = sum(1 for e in events if (e.get("status") or "").upper() == "PASSED")
        failed  = sum(1 for e in events if (e.get("status") or "").upper() == "FAILED")
        skipped = sum(1 for e in events if (e.get("status") or "").upper() == "SKIPPED")
        broken  = sum(1 for e in events if (e.get("status") or "").upper() == "BROKEN")
        total   = len(events) or final_state.get("total", 0)

        # Fall back to Redis final_state if events are missing (e.g. buffer expired)
        if not events:
            passed  = final_state.get("passed",  0)
            failed  = final_state.get("failed",  0)
            skipped = final_state.get("skipped", 0)
            broken  = final_state.get("broken",  0)
            total   = final_state.get("total",   0)

        # If total wasn't tracked explicitly, derive it from component counts
        total = total or (passed + failed + skipped + broken)

        pass_rate = round(passed / (passed + failed + broken) * 100, 2) if (passed + failed + broken) > 0 else None
        run_status = LaunchStatus.FAILED if (failed + broken) > 0 else LaunchStatus.PASSED

        # ── Resolve project UUID ──────────────────────────────────────────────
        try:
            proj_uuid = _uuid_mod.UUID(project_id)
        except ValueError:
            logger.error("[Task %s] Invalid project_id %s — aborting", self.request.id, project_id)
            return

        # ── Resolve run UUID (use run_id if it looks like a UUID, else generate) ──
        try:
            run_uuid = _uuid_mod.UUID(run_id)
        except ValueError:
            run_uuid = _uuid_mod.uuid5(_uuid_mod.NAMESPACE_DNS, run_id)

        now = datetime.now(timezone.utc)

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            # Upsert TestRun — skip if already exists (idempotent)
            existing = await db.execute(select(TestRun).where(TestRun.id == run_uuid))
            run = existing.scalar_one_or_none()

            if run is None:
                run = TestRun(
                    id=run_uuid,
                    project_id=proj_uuid,
                    build_number=build_number,
                    trigger_source="live_stream",
                    branch=branch or None,
                    commit_hash=commit_hash or None,
                    status=run_status,
                    total_tests=total,
                    passed_tests=passed,
                    failed_tests=failed,
                    skipped_tests=skipped,
                    broken_tests=broken,
                    pass_rate=pass_rate,
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
                run.pass_rate     = pass_rate
                run.end_time      = now

            # ── Insert TestCase rows ──────────────────────────────────────────
            for event in events:
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

                tc = TestCase(
                    id=_uuid_mod.uuid4(),
                    test_run_id=run.id,
                    test_fingerprint=fingerprint,
                    test_name=test_name[:1000],
                    suite_name=(event.get("suite_name") or "")[:500] or None,
                    class_name=class_name[:500] or None,
                    status=tc_status,
                    duration_ms=event.get("duration_ms"),
                    error_message=event.get("error_message"),
                    tags=event.get("tags"),
                )
                db.add(tc)

            await db.commit()
            logger.info(
                "[Task %s] Persisted run=%s tests=%d passed=%d failed=%d",
                self.request.id, run_id, total, passed, failed,
            )

        # ── Clean up Redis buffer ─────────────────────────────────────────────
        await redis.delete(list_key)

    try:
        _run_async(_run())
    except Exception as exc:
        logger.error("[Task %s] persist_live_session failed: %s", self.request.id, exc)
        raise self.retry(exc=exc, countdown=_exponential_backoff(self.request.retries))


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

    async def _run():
        from app.db.postgres import AsyncSessionLocal

        # Parse file into normalized result dicts
        results = _parse_file_to_results(file_content, file_format, file_name, run_id)

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
                )
                count = await ingest_test_results(db, run, results)
                await db.commit()
                logger.info(
                    "[Task %s] File ingested: %d cases from %s (%s)",
                    self.request.id, count, file_name, file_format,
                )
            except Exception:
                await db.rollback()
                raise

        await finalize_run(
            run_id=run_id,
            project_id=project_id,
            build_number=build_number,
            release_name=release_name,
        )

    logger.info("[Task %s] Processing uploaded file: %s (%s)", self.request.id, file_name, file_format)
    try:
        _run_async(_run())
    except Exception as exc:
        logger.error("[Task %s] File ingest failed: %s", self.request.id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=_exponential_backoff(self.request.retries))


def _parse_file_to_results(content: str, fmt: str, filename: str, run_id: str) -> list[dict]:
    """Parse a test result file into normalized result dicts.

    Dispatch table — each parser returns ``list[dict]`` matching the
    TestLookup ingestion contract. New frameworks register here and add
    their content-sniff rules in ``routers/ingest._detect_format``.
    """
    import json as _json

    if fmt == "allure":
        from app.services.allure_parser import parse_allure_result
        try:
            raw = _json.loads(content)
        except _json.JSONDecodeError:
            logger.warning("Invalid JSON in allure file: %s", filename)
            return []
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
    from app.services.agent import run_triage_agent
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
            result = await run_triage_agent(
                test_case_id=test_case_id,
                test_name=test_name,
                run_id=run_id,
                project_id=project_id,
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
    from app.services.agent import run_triage_agent
    from app.streams.circuit_breaker import LLMCircuitBreaker

    async def _run():
        if not await LLMCircuitBreaker.is_available():
            retry_after = await LLMCircuitBreaker.retry_after_seconds()
            raise RuntimeError(f"LLM circuit open — retry in {retry_after}s")

        try:
            result = await run_triage_agent(
                test_case_id=test_case_id,
                test_name=test_name,
                **kwargs,
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

    async def _run():
        if await _is_duplicate(dedup_key, ttl=7200):
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
            logger.warning("[Task %s] Snapshot invalidation failed (non-blocking): %s", self.request.id, inv_exc)

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
                logger.warning("[Task %s] AI summary email dispatch failed (non-blocking): %s", self.request.id, email_exc)

        return {"completed_stages": stages_done, "error_count": len(errors)}
    except Exception as exc:
        logger.error("[Task %s] Pipeline failed: %s", self.request.id, exc, exc_info=True)
        if self.request.retries >= self.max_retries:
            # Move to DLQ before the final exception propagates
            _run_async(_send_to_dlq(
                task_name=self.name,
                task_id=self.request.id,
                kwargs={"test_run_id": test_run_id, "build_number": build_number},
                error=str(exc),
            ))
        countdown = _exponential_backoff(self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)


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

    Every pass is a no-op when the ``flaky_auto_quarantine`` feature flag
    is off, so enabling this beat entry is safe on existing deployments.
    """
    async def _run():
        from app.services.flaky_quarantine_service import (
            expire_stale_proposals,
            run_recheck_cycle,
            schedule_pending_rechecks,
        )
        with _beat_span("run_flaky_quarantine_maintenance") as span:
            expired = await expire_stale_proposals()
            rechecks_scheduled = await schedule_pending_rechecks()
            outcomes = await run_recheck_cycle()
            span.set_attribute("result.expired", int(expired))
            span.set_attribute("result.rechecks_scheduled", int(rechecks_scheduled))
            span.set_attribute("result.released", int(outcomes["released"]))
            span.set_attribute("result.re_quarantined", int(outcomes["re_quarantined"]))
            span.set_attribute("result.insufficient_data", int(outcomes["insufficient_data"]))
            logger.info(
                "[Task %s] flaky quarantine maintenance: expired=%d scheduled=%d "
                "released=%d re_quarantined=%d insufficient_data=%d",
                self.request.id,
                expired,
                rechecks_scheduled,
                outcomes["released"],
                outcomes["re_quarantined"],
                outcomes["insufficient_data"],
            )
            return {
                "expired": expired,
                "rechecks_scheduled": rechecks_scheduled,
                **outcomes,
            }

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
        # double-dispatch the same email.
        async with AsyncSessionLocal() as db:
            discovery = await db.execute(
                select(DigestSubscription.id, DigestSubscription.schedule).where(
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
        for sub_id, schedule in due:
            delta = timedelta(days=1) if schedule == "DAILY" else timedelta(weeks=1)
            period = "daily" if schedule == "DAILY" else "weekly"
            is_retro = schedule == "WEEKLY_RETRO"

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
                        digest = await generate_digest(db, project_id, period)
                    html_body = render_digest_html(digest)

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

                    if channel == "email":
                        try:
                            from app.services.notification.email_service import send_email
                            await send_email(
                                to_email=user.email,
                                subject=f"TestLookup — {period.title()} Quality Digest",
                                html_body=html_body,
                            )
                        except Exception as e:
                            status = "failed"
                            logger.warning("Digest email failed for %s: %s", user.email, e)
                    # Slack/Teams handled by notification manager; status stays "sent".

                    db.add(NotificationLog(
                        user_id=user_id,
                        project_id=project_id,
                        channel=channel,
                        event_type="digest_delivery",
                        title=f"{period.title()} Quality Digest",
                        body=f"Digest for {digest.get('project_name', 'All Projects')}",
                        status=status,
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
