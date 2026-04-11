"""
Shared ingestion pipeline — processes normalized test results into PostgreSQL.

Used by:
  1. process_sentinel() — MinIO webhook path (Allure/TestNG from S3)
  2. ingest_uploaded_results task — POST /api/v1/ingest (JSON batch)
  3. ingest_uploaded_file task — POST /api/v1/ingest/file (file upload)
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import AsyncSessionLocal
from app.models.postgres import LaunchStatus, Project, TestRun
from app.services.ingestion import (
    _update_run_aggregates,
    _upsert_test_case,
)

logger = structlog.get_logger("services.ingestion_pipeline")


async def create_run_from_payload(
    db: AsyncSession,
    *,
    project_id: str,
    build_number: str,
    run_id: Optional[str] = None,
    branch: Optional[str] = None,
    commit_hash: Optional[str] = None,
    trigger_source: str = "api",
    release_name: Optional[str] = None,
    framework: Optional[str] = None,
) -> TestRun:
    """
    Create a TestRun record for API-ingested data.

    Upserts on (project_id, build_number) — if a run with the same build
    already exists for this project, it is reused (and new test cases are
    added to it).
    """
    pid = uuid.UUID(project_id)

    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == pid))
    project = result.scalar_one_or_none()
    if not project:
        raise ValueError(f"Project {project_id} not found")

    # Check for existing run with same build_number
    result = await db.execute(
        select(TestRun).where(
            TestRun.project_id == pid,
            TestRun.build_number == build_number,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        logger.info("Reusing existing run", run_id=str(existing.id), build=build_number)
        return existing

    run = TestRun(
        id=uuid.UUID(run_id) if run_id else uuid.uuid4(),
        project_id=pid,
        build_number=build_number,
        branch=branch,
        commit_hash=commit_hash,
        trigger_source=trigger_source,
        status=LaunchStatus.IN_PROGRESS,
        total_tests=0,
        passed_tests=0,
        failed_tests=0,
        skipped_tests=0,
        broken_tests=0,
        start_time=datetime.now(timezone.utc),
    )
    db.add(run)
    await db.flush()
    logger.info("Created new run", run_id=str(run.id), build=build_number, project=project_id)
    return run


async def ingest_test_results(
    db: AsyncSession,
    run: TestRun,
    results: list[dict],
) -> int:
    """
    Process a list of normalized test result dicts and upsert into PostgreSQL.

    Each result dict should have at minimum: test_name, status.
    Optional: duration_ms, suite_name, class_name, error_message, stack_trace, tags.

    Returns the count of processed cases.
    """
    count = 0
    for case_data in results:
        try:
            await _upsert_test_case(db, case_data, run)
            count += 1
        except Exception as e:
            logger.warning(
                "Failed to upsert test case",
                test_name=case_data.get("test_name"),
                error=str(e),
            )
    return count


async def finalize_run(
    run_id: str,
    project_id: str,
    build_number: str,
    release_name: Optional[str] = None,
) -> None:
    """
    Post-ingestion steps: update aggregates, auto-tag, link release,
    queue notifications, queue agent pipeline.

    Runs in a fresh DB session since Celery tasks don't carry the original session.
    """
    rid = uuid.UUID(run_id)
    pid = uuid.UUID(project_id)

    async with AsyncSessionLocal() as db:
        try:
            # Update aggregated counts
            await _update_run_aggregates(db, rid)

            # Sync suite membership traceability
            try:
                from app.services.suite_sync_service import sync_suite_membership
                await sync_suite_membership(db, pid, rid)
            except Exception as e:
                logger.warning("Suite sync failed (non-blocking): %s", e)

            # Auto-tag test cases and run
            try:
                from app.services.auto_tagging_service import auto_tag_test_cases, auto_tag_test_run
                await auto_tag_test_cases(db, rid)
                await auto_tag_test_run(db, rid)
            except Exception as e:
                logger.warning("Auto-tagging failed (non-blocking): %s", e)

            # Link to release
            if release_name and release_name.strip():
                try:
                    from app.services.release_linker import auto_link_release
                    await auto_link_release(
                        db=db, project_id=pid,
                        release_name=release_name.strip(),
                        test_run_id=rid,
                    )
                except Exception as e:
                    logger.warning("Release linking failed: %s", e)

            await db.commit()
        except Exception:
            await db.rollback()
            raise

    # Fetch run for notification data
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(TestRun).where(TestRun.id == rid))
        run = result.scalar_one_or_none()
        if not run:
            logger.warning("Run not found for post-ingestion", run_id=run_id)
            return

    # Enqueue notifications
    try:
        from app.worker.tasks import dispatch_run_notifications as _notify
        result_proj = None
        async with AsyncSessionLocal() as db:
            r = await db.execute(select(Project).where(Project.id == pid))
            result_proj = r.scalar_one_or_none()
        _notify.delay(
            project_id=str(pid),
            run_id=str(rid),
            build_number=build_number,
            pass_rate=float(run.pass_rate or 0),
            total_tests=int(run.total_tests or 0),
            failed_tests=int(run.failed_tests or 0),
            project_name=result_proj.name if result_proj else str(pid),
        )
    except Exception as e:
        logger.warning("Failed to enqueue notifications: %s", e)

    # Trigger agent pipeline
    try:
        from app.worker.tasks import run_agent_pipeline as _pipeline
        _pipeline.delay(
            test_run_id=str(rid),
            project_id=str(pid),
            build_number=build_number,
            workflow_type="offline",
        )
        logger.info("Agent pipeline queued for run %s", run_id)
    except Exception as e:
        logger.warning("Failed to queue agent pipeline: %s", e)
