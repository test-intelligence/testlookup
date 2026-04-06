"""Celery background tasks for ingestion and AI analysis."""
import asyncio
import logging
import random
from typing import Any, cast

from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run an async coroutine in a Celery task (sync context)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _exponential_backoff(attempt: int, base: int = 30, cap: int = 600) -> int:
    """Return jittered exponential backoff seconds: min(base * 2^attempt, cap) ± 20%."""
    delay = min(base * (2 ** attempt), cap)
    jitter = delay * 0.2 * random.random()
    return int(delay + jitter)


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
    dashboard_url: str = "#",
):
    """Background task: fan-out run-completion notifications to all subscribed users."""
    import uuid as _uuid
    from app.services.notification.manager import dispatch_run_notifications as _dispatch

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
            dashboard_url=f"/runs/{test_run_id}/intelligence",
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
                            "dashboard_url": f"/runs/{test_run_id}/intelligence",
                            "executive_panel": executive_panel,
                        },
                    )
                    # Update delivery tracking
                    sub.delivery_count = (sub.delivery_count or 0) + 1
                    sub.last_delivered_at = datetime.now(timezone.utc)
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
        from sqlalchemy import select

        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import DigestSubscription, NotificationLog, User
        from app.services.digest_content_service import generate_digest, render_digest_html

        now = datetime.now(timezone.utc)

        async with AsyncSessionLocal() as db:
            # Find subscriptions due for delivery
            # Only process time-based subscriptions (DAILY, WEEKLY).
            # Event-driven subscriptions (PER_RUN, PER_RELEASE, PER_SUITE)
            # are dispatched by their respective event triggers.
            result = await db.execute(
                select(DigestSubscription).where(
                    DigestSubscription.is_active == True,  # noqa: E712
                    DigestSubscription.is_paused == False,  # noqa: E712
                    DigestSubscription.next_delivery_at <= now,
                    DigestSubscription.schedule.in_(["DAILY", "WEEKLY"]),
                )
            )
            subs = result.scalars().all()
            logger.info("Found %d digest subscriptions due for delivery", len(subs))

            for sub in subs:
                try:
                    period = "daily" if sub.schedule == "DAILY" else "weekly"
                    digest = await generate_digest(db, sub.project_id, period)
                    html_body = render_digest_html(digest)

                    # Get user email
                    user_result = await db.execute(
                        select(User).where(User.id == sub.user_id)
                    )
                    user = user_result.scalar_one_or_none()
                    if not user:
                        continue

                    # Dispatch via email (primary channel for digests)
                    if sub.channel == "email":
                        try:
                            from app.services.notification.email_service import send_email
                            await send_email(
                                to_email=user.email,
                                subject=f"TestLookup — {period.title()} Quality Digest",
                                html_body=html_body,
                            )
                            status = "sent"
                        except Exception as e:
                            status = "failed"
                            logger.warning("Digest email failed for %s: %s", user.email, e)
                    else:
                        status = "sent"  # Slack/Teams handled by notification manager

                    # Log delivery
                    db.add(NotificationLog(
                        user_id=sub.user_id,
                        project_id=sub.project_id,
                        channel=sub.channel,
                        event_type="digest_delivery",
                        title=f"{period.title()} Quality Digest",
                        body=f"Digest for {digest.get('project_name', 'All Projects')}",
                        status=status,
                    ))

                    # Update subscription
                    sub.last_delivered_at = now
                    sub.delivery_count = (sub.delivery_count or 0) + 1
                    delta = timedelta(days=1) if sub.schedule == "DAILY" else timedelta(weeks=1)
                    sub.next_delivery_at = now + delta

                except Exception as exc:
                    logger.error("Digest delivery failed for subscription %s: %s", sub.id, exc)

            await db.commit()

    _run_async(_dispatch())
    logger.info("[Task %s] Digest dispatch completed", self.request.id)
