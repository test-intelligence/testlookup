"""
Background ingestion service.
Parses Allure JSON and TestNG XML from MinIO S3 and routes data
to PostgreSQL (structured metrics) and MongoDB (raw payloads).
"""
import asyncio
import hashlib
import json
import structlog
import uuid
from datetime import datetime, timezone
from typing import Optional, cast

from sqlalchemy import Integer, func, select, update

from app.core.config import settings
from app.db.storage import get_storage_provider
from app.db.mongo import Collections, get_mongo_db
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import (
    LaunchStatus,
    TestCase,
    TestCaseHistory,
    TestRun,
    TestStatus,
)
from app.models.schemas import SentinelFile
from app.services.allure_parser import parse_allure_result
from app.services.testng_parser import parse_testng_xml
from app.services.ocp_client import get_pod_metadata

logger = structlog.get_logger("services.ingestion")

# Import WebSocket manager lazily to avoid circular imports at module load time
def _get_ws_manager():
    from app.routers.live import manager as ws_manager
    return ws_manager


def make_test_fingerprint(test_name: str, class_name: Optional[str]) -> str:
    """Create a stable hash identifying a unique test across runs."""
    key = f"{class_name or ''}::{test_name}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


async def process_sentinel(sentinel: SentinelFile, minio_prefix: str) -> None:
    """
    Main ingestion entry point — called after sentinel file upload.

    1. Find and parse all Allure JSON result files
    2. Parse TestNG XML if available
    3. Upsert structured data to PostgreSQL
    4. Store raw payloads to MongoDB
    5. Enrich with OpenShift metadata
    6. Update test run aggregates
    """
    logger.info(f"Starting ingestion: project={sentinel.project_id} build={sentinel.build_number}")

    async with AsyncSessionLocal() as db:
        try:
            # ── Get or create test run ─────────────────────
            run = await _upsert_test_run(db, sentinel, minio_prefix)
            storage = get_storage_provider()

            # ── Process Allure results ─────────────────────
            allure_prefix = f"{minio_prefix}allure/"
            allure_objects = await storage.list_objects(allure_prefix)
            result_files = [obj for obj in allure_objects if obj["Key"].endswith("-result.json")]

            logger.info(f"Found {len(result_files)} Allure result files")

            # Parallel S3 fetches bounded by semaphore to prevent resource exhaustion
            sem = asyncio.Semaphore(settings.INGESTION_S3_CONCURRENCY)

            async def _fetch_allure(obj: dict) -> Optional[tuple]:
                async with sem:
                    try:
                        content = await storage.get_object_content(obj["Key"])
                        result_data = json.loads(content)
                        parsed = parse_allure_result(result_data, str(run.id), obj["Key"])
                        if parsed:
                            return parsed, result_data
                    except Exception as e:
                        logger.warning(f"Failed to parse {obj['Key']}: {e}")
                return None

            allure_results = await asyncio.gather(*[_fetch_allure(obj) for obj in result_files])

            parsed_cases = []
            mongo_docs = []
            for item in allure_results:
                if item:
                    parsed, result_data = item
                    parsed_cases.append(parsed)
                    mongo_docs.append((parsed, result_data))

            # Batch store raw JSON to MongoDB
            if mongo_docs:
                await _store_raw_allure_batch(mongo_docs)

            # ── Process TestNG XML ─────────────────────────
            testng_prefix = f"{minio_prefix}testng/"
            testng_objects = await storage.list_objects(testng_prefix)
            xml_files = [obj for obj in testng_objects if obj["Key"].endswith(".xml")]

            async def _fetch_testng(obj: dict) -> list:
                async with sem:
                    try:
                        content = await storage.get_object_content(obj["Key"])
                        return parse_testng_xml(content.decode("utf-8"), str(run.id))
                    except Exception as e:
                        logger.warning(f"Failed to parse TestNG XML {obj['Key']}: {e}")
                        return []

            testng_results = await asyncio.gather(*[_fetch_testng(obj) for obj in xml_files])
            existing_names = {p["test_name"] for p in parsed_cases}
            for xml_cases in testng_results:
                for case in xml_cases:
                    if case["test_name"] not in existing_names:
                        parsed_cases.append(case)
                        existing_names.add(case["test_name"])

            # ── Upsert test cases to PostgreSQL ────────────
            for case_data in parsed_cases:
                await _upsert_test_case(db, case_data, run)

            # ── Enrich with OCP metadata ───────────────────
            if sentinel.ocp_pod_name and sentinel.ocp_namespace:
                try:
                    pod_meta = await get_pod_metadata(
                        sentinel.ocp_pod_name,
                        sentinel.ocp_namespace,
                    )
                    await db.execute(
                        update(TestRun)
                        .where(TestRun.id == run.id)
                        .values(ocp_metadata=pod_meta)
                    )
                except Exception as e:
                    logger.warning(f"OCP metadata enrichment failed: {e}")

            # ── Update run aggregates ──────────────────────
            await _update_run_aggregates(db, run.id)

            # ── Sync suite membership traceability (TS-2) ──
            try:
                from app.services.suite_sync_service import sync_suite_membership
                await sync_suite_membership(db, run.project_id, run.id)
            except Exception as sync_err:
                logger.warning("Suite membership sync failed (non-blocking): %s", sync_err)

            # ── Auto-tag test cases and run (TG-5/6) ─────
            try:
                from app.services.auto_tagging_service import auto_tag_test_cases, auto_tag_test_run
                await auto_tag_test_cases(db, run.id)
                await auto_tag_test_run(db, run.id)
            except Exception as tag_err:
                logger.warning("Auto-tagging failed (non-blocking): %s", tag_err)

            # ── Link to release (explicit name wins; falls back to the
            # project's default release — migration 0077) ────────────────
            try:
                from app.services.release_linker import link_run_or_default
                result = await link_run_or_default(
                    db=db,
                    project_id=run.project_id,
                    release_name=sentinel.release_name,
                    test_run_id=run.id,
                )
                if result and result[1]:
                    logger.info(
                        "Auto-created release '%s' for run %s",
                        result[0].name, run.id,
                    )
            except Exception as rel_err:
                logger.warning("Release linking failed for run %s: %s", run.id, rel_err)

            await db.commit()
            logger.info(f"Ingestion complete: {len(parsed_cases)} test cases processed")

            # Broadcast live update via WebSocket
            try:
                ws = _get_ws_manager()
                await ws.broadcast(str(sentinel.project_id), {
                    "type": "run_completed",
                    "run_id": str(run.id),
                    "build_number": run.build_number,
                    "total_tests": run.total_tests,
                    "passed_tests": run.passed_tests,
                    "failed_tests": run.failed_tests,
                    "pass_rate": run.pass_rate,
                    "status": getattr(run.status, "value", run.status),
                })
            except Exception as ws_err:
                logger.debug(f"WS broadcast skipped: {ws_err}")

            # Enqueue async notifications (email / Slack / Teams)
            try:
                from app.worker.tasks import dispatch_run_notifications as _notify_task
                from app.models.postgres import Project as _Project
                async with AsyncSessionLocal() as _db:
                    _proj_result = await _db.execute(
                        select(_Project).where(_Project.id == run.project_id)
                    )
                    _project = _proj_result.scalar_one_or_none()
                    _project_name = _project.name if _project else str(run.project_id)

                _notify_task.delay(
                    project_id=str(run.project_id),
                    run_id=str(run.id),
                    build_number=run.build_number,
                    pass_rate=float(run.pass_rate or 0),
                    total_tests=int(run.total_tests or 0),
                    failed_tests=int(run.failed_tests or 0),
                    project_name=_project_name,
                )
            except Exception as notify_err:
                logger.warning("Failed to enqueue run notifications: %s", notify_err)

            # Trigger the multi-agent analysis pipeline
            try:
                from app.worker.tasks import run_agent_pipeline as _pipeline_task
                _pipeline_task.delay(
                    test_run_id=str(run.id),
                    project_id=str(run.project_id),
                    build_number=run.build_number,
                    workflow_type="offline",
                )
                logger.info("Agent pipeline queued for run %s", run.id)
            except Exception as pipeline_err:
                logger.warning("Failed to queue agent pipeline: %s", pipeline_err)

        except Exception as e:
            await db.rollback()
            logger.error(f"Ingestion failed for run {sentinel.build_number}: {e}", exc_info=True)
            raise


async def _upsert_test_run(db, sentinel: SentinelFile, minio_prefix: str) -> TestRun:
    """Idempotent upsert of a test run record."""
    from app.models.postgres import Project

    # Find project by id or slug
    result = await db.execute(
        select(Project).where(
            (Project.id == sentinel.project_id) | (Project.slug == sentinel.project_id)
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise ValueError(f"Project not found: {sentinel.project_id}")

    # Check if run already exists (idempotent)
    result = await db.execute(
        select(TestRun).where(
            TestRun.project_id == project.id,
            TestRun.build_number == sentinel.build_number,
            TestRun.jenkins_job == sentinel.jenkins_job,
        )
    )
    run = result.scalar_one_or_none()

    if not run:
        run = TestRun(
            project_id=project.id,
            build_number=sentinel.build_number,
            jenkins_job=sentinel.jenkins_job,
            trigger_source=sentinel.trigger_source,
            branch=sentinel.branch,
            commit_hash=sentinel.commit_hash,
            ocp_pod_name=sentinel.ocp_pod_name,
            ocp_namespace=sentinel.ocp_namespace,
            minio_prefix=minio_prefix,
            status=LaunchStatus.IN_PROGRESS,
            start_time=datetime.now(timezone.utc),
        )
        db.add(run)
        await db.flush()

    return cast(TestRun, run)


async def _upsert_test_case(db, case_data: dict, run: TestRun) -> TestCase:
    """Upsert a test case — idempotent on (run_id, test_fingerprint)."""
    fingerprint = make_test_fingerprint(
        case_data.get("test_name", ""),
        case_data.get("class_name"),
    )

    result = await db.execute(
        select(TestCase).where(
            TestCase.test_run_id == run.id,
            TestCase.test_fingerprint == fingerprint,
        )
    )
    existing = result.scalar_one_or_none()

    status_map = {
        "passed": TestStatus.PASSED,
        "failed": TestStatus.FAILED,
        "broken": TestStatus.BROKEN,
        "skipped": TestStatus.SKIPPED,
        "unknown": TestStatus.UNKNOWN,
    }
    status = status_map.get(case_data.get("status", "unknown").lower(), TestStatus.UNKNOWN)

    # PR-3: Sanitize error_message before persistence
    from app.services.privacy_service import sanitize_for_persistence as _sanitize  # noqa: PLC0415
    _safe_error = _sanitize(case_data.get("error_message") or "")

    if existing:
        existing.status = status
        existing.duration_ms = case_data.get("duration_ms")
        existing.error_message = _safe_error
        tc = existing
    else:
        tc = TestCase(
            test_run_id=run.id,
            test_fingerprint=fingerprint,
            test_name=case_data.get("test_name", "Unknown"),
            full_name=case_data.get("full_name"),
            suite_name=case_data.get("suite_name"),
            class_name=case_data.get("class_name"),
            package_name=case_data.get("package_name"),
            status=status,
            duration_ms=case_data.get("duration_ms"),
            severity=case_data.get("severity"),
            feature=case_data.get("feature"),
            story=case_data.get("story"),
            epic=case_data.get("epic"),
            owner=case_data.get("owner"),
            tags=case_data.get("tags", []),
            error_message=_safe_error,
            minio_s3_prefix=case_data.get("minio_s3_prefix"),
            has_attachments=bool(case_data.get("attachments")),
        )
        db.add(tc)

    await db.flush()

    # Record history entry
    history = TestCaseHistory(
        test_case_id=tc.id,
        test_run_id=run.id,
        test_fingerprint=fingerprint,
        status=status,
        duration_ms=case_data.get("duration_ms"),
    )
    db.add(history)

    return cast(TestCase, tc)


async def _store_raw_allure(case_data: dict, raw_json: dict) -> None:
    """Store full Allure JSON payload in MongoDB."""
    db = get_mongo_db()
    await db[Collections.RAW_ALLURE_JSON].update_one(
        {"test_case_id": case_data.get("allure_uuid")},
        {"$set": {
            "test_case_id": case_data.get("allure_uuid"),
            "test_run_id": case_data.get("test_run_id"),
            "test_name": case_data.get("test_name"),
            "raw_result": raw_json,
            "updated_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )


async def _store_raw_allure_batch(docs: list[tuple[dict, dict]]) -> None:
    """Batch store Allure JSON payloads in MongoDB using bulk_write for efficiency."""
    from pymongo import UpdateOne
    db = get_mongo_db()
    now = datetime.now(timezone.utc)
    operations = [
        UpdateOne(
            {"test_case_id": case_data.get("allure_uuid")},
            {"$set": {
                "test_case_id": case_data.get("allure_uuid"),
                "test_run_id": case_data.get("test_run_id"),
                "test_name": case_data.get("test_name"),
                "raw_result": raw_json,
                "updated_at": now,
            }},
            upsert=True,
        )
        for case_data, raw_json in docs
    ]
    try:
        await db[Collections.RAW_ALLURE_JSON].bulk_write(operations, ordered=False)
    except Exception as e:
        logger.warning("mongo_bulk_write_fallback", error=str(e))
        for case_data, raw_json in docs:
            await _store_raw_allure(case_data, raw_json)


def compute_suite_attribution(
    suite_counts: list[tuple[str | None, int]],
) -> tuple[str | None, list[str] | None]:
    """Given (suite_name, count) rows, return (primary_suite, sorted_distinct_suites).

    - NULL/empty suite names are dropped.
    - primary = highest count; alphabetical tiebreak.
    - Returns (None, None) when nothing usable remains.
    """
    rows = [(name, int(n)) for name, n in suite_counts if name]
    if not rows:
        return None, None
    sorted_names = sorted({name for name, _ in rows})
    primary = sorted(rows, key=lambda x: (-x[1], x[0]))[0][0]
    return primary, sorted_names


async def _update_run_aggregates(db, run_id: uuid.UUID) -> None:
    """Recalculate and update aggregated counts on the test run."""
    result = await db.execute(
        select(
            func.count(TestCase.id).label("total"),
            func.sum((TestCase.status == TestStatus.PASSED).cast(Integer)).label("passed"),
            func.sum((TestCase.status == TestStatus.FAILED).cast(Integer)).label("failed"),
            func.sum((TestCase.status == TestStatus.SKIPPED).cast(Integer)).label("skipped"),
            func.sum((TestCase.status == TestStatus.BROKEN).cast(Integer)).label("broken"),
        ).where(TestCase.test_run_id == run_id)
    )
    counts = result.one()

    total = counts.total or 0
    passed = counts.passed or 0
    pass_rate = round((passed / total * 100), 2) if total > 0 else 0.0

    # Suite attribution — distinct suite_name values + dominant suite.
    suite_q = await db.execute(
        select(TestCase.suite_name, func.count(TestCase.id).label("n"))
        .where(
            TestCase.test_run_id == run_id,
            TestCase.suite_name.is_not(None),
            TestCase.suite_name != "",
        )
        .group_by(TestCase.suite_name)
    )
    primary_suite, suite_names_sorted = compute_suite_attribution(
        [(row.suite_name, int(row.n)) for row in suite_q.all()],
    )

    await db.execute(
        update(TestRun)
        .where(TestRun.id == run_id)
        .values(
            total_tests=total,
            passed_tests=passed,
            failed_tests=counts.failed or 0,
            skipped_tests=counts.skipped or 0,
            broken_tests=counts.broken or 0,
            pass_rate=pass_rate,
            status=LaunchStatus.PASSED if pass_rate == 100 else LaunchStatus.FAILED,
            end_time=datetime.now(timezone.utc),
            primary_suite_name=primary_suite,
            suite_names=suite_names_sorted,
        )
    )
