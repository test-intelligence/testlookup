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
    TestAttachment,
    TestCase,
    TestCaseHistory,
    TestRun,
    TestStatus,
    TestStep,
    TestStepRun,
)
from app.models.schemas import SentinelFile
from app.services.allure_parser import parse_allure_result
from app.services.run_status import terminal_run_status
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


#: How concerning each outcome is when one report names the same test more
#: than once. Higher wins. Ties keep the later entry, which preserves the
#: previous last-one-wins behaviour for combinations that were never harmful.
#:
#: The ordering says: a failure must never be hidden by a later pass, and a
#: skip should not be reported as a pass either — claiming coverage that did
#: not run is the same class of misreport, just smaller.
_DUPLICATE_STATUS_PRECEDENCE = {
    "failed": 3,
    "broken": 2,
    "skipped": 1,
    "passed": 0,
    "unknown": 0,
}


def collapse_duplicate_cases(results: list[dict]) -> list[dict]:
    """Collapse repeated tests within ONE report, worst outcome winning.

    A single report may legitimately name the same test more than once —
    parameterised cases that share a name, and above all **retry frameworks**,
    which emit the failing attempt and the passing retry as sibling
    ``<testcase>`` elements.

    Persistence is keyed on ``(test_run_id, test_fingerprint)``, which is the
    right idempotency key for re-ingesting a file but also means the last
    occurrence overwrote every earlier one. Measured on the live deployment
    with three same-named cases:

        failure listed first -> run reported PASSED, 0 failures
        failure listed last  -> run reported FAILED, 1 failure

    Identical inputs, opposite verdicts, decided by document order. The
    fail-then-pass shape is exactly what a retry emits, so the signal most
    worth keeping was the one most reliably dropped.

    Collapsing here rather than in ``_upsert_test_case`` keeps that upsert's
    semantics intact: a *separate* re-ingest of the same run still overwrites,
    so a corrected report can still flip a verdict. Only duplicates **within
    one payload** are merged.

    Returns a new list in first-appearance order; the input is not mutated.
    """
    winners: dict[str, dict] = {}
    order: list[str] = []

    for case in results:
        fp = make_test_fingerprint(
            case.get("test_name", ""), case.get("class_name")
        )
        incumbent = winners.get(fp)
        if incumbent is None:
            winners[fp] = case
            order.append(fp)
            continue

        def _rank(c: dict) -> int:
            return _DUPLICATE_STATUS_PRECEDENCE.get(
                str(c.get("status", "unknown")).lower(), 0
            )

        # `>=` keeps the later entry on a tie — the prior behaviour.
        if _rank(case) >= _rank(incumbent):
            winners[fp] = case

    return [winners[fp] for fp in order]


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
                        logger.warning(
                            "allure_parse_failed",
                            object_key=obj["Key"],
                            error=str(e),
                        )
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
                        logger.warning(
                            "testng_parse_failed",
                            object_key=obj["Key"],
                            error=str(e),
                        )
                        return []

            testng_results = await asyncio.gather(*[_fetch_testng(obj) for obj in xml_files])
            # Dedup by fingerprint (test_name + class_name), NOT bare test_name:
            # two distinct tests with the same method name in different classes
            # are different tests and must both survive — the same multi-class
            # case the per-run fingerprint already distinguishes everywhere else.
            existing_fps = {
                make_test_fingerprint(p["test_name"], p.get("class_name"))
                for p in parsed_cases
            }
            for xml_cases in testng_results:
                for case in xml_cases:
                    fp = make_test_fingerprint(case["test_name"], case.get("class_name"))
                    if fp not in existing_fps:
                        parsed_cases.append(case)
                        existing_fps.add(fp)

            # ── Upsert test cases to PostgreSQL ────────────
            # Prefetch existing rows in ONE query and pass them through so
            # _upsert_test_case skips its per-row SELECT. Without this the
            # MinIO/sentinel path was an N+1 — one SELECT per parsed case
            # before each INSERT (a 1000-test upload = 1000 extra round
            # trips). Mirrors the prefetch in
            # ingestion_pipeline.ingest_test_results.
            case_fps = [
                make_test_fingerprint(c.get("test_name", ""), c.get("class_name"))
                for c in parsed_cases
            ]
            existing_by_fp: dict[str, TestCase] = {}
            if case_fps:
                existing_rows = (
                    await db.execute(
                        select(TestCase).where(
                            TestCase.test_run_id == run.id,
                            TestCase.test_fingerprint.in_(case_fps),
                        )
                    )
                ).scalars().all()
                existing_by_fp = {r.test_fingerprint: r for r in existing_rows}
            for case_data, fingerprint in zip(parsed_cases, case_fps):
                await _upsert_test_case(
                    db, case_data, run,
                    existing=existing_by_fp.get(fingerprint),
                    fingerprint=fingerprint,
                )

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
            # NOTE: this module binds a STRUCTLOG logger (line 32).
            # structlog's BoundLogger.warning is ``(event, **kwargs)`` —
            # positional %s args raise TypeError mid-call, which would
            # escape the except and 500 the ingest. Every log inside an
            # except block on this hot post-ingest path uses kwargs.
            try:
                from app.services.suite_sync_service import sync_suite_membership
                await sync_suite_membership(db, run.project_id, run.id)
            except Exception as sync_err:
                logger.warning("suite_membership_sync_failed", error=str(sync_err))

            # ── Auto-tag test cases and run (TG-5/6) ─────
            try:
                from app.services.auto_tagging_service import auto_tag_test_cases, auto_tag_test_run
                await auto_tag_test_cases(db, run.id)
                await auto_tag_test_run(db, run.id)
            except Exception as tag_err:
                logger.warning("auto_tagging_failed", error=str(tag_err))

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
                        "auto_release_created",
                        release_name=result[0].name, run_id=str(run.id),
                    )
            except Exception as rel_err:
                logger.warning(
                    "release_linking_failed",
                    run_id=str(run.id), error=str(rel_err),
                )

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
                logger.warning(
                    "run_notifications_enqueue_failed", error=str(notify_err),
                )

            # Transition-based notifications (PMF US-7.1/US-7.2) — own task,
            # own isolation, idempotent per (fingerprint, run).
            try:
                from app.worker.tasks import (
                    dispatch_transition_notifications as _transitions_task,
                )
                _transitions_task.delay(run_id=str(run.id))
            except Exception as trans_err:
                logger.warning(
                    "transition_notifications_enqueue_failed",
                    error=str(trans_err),
                )

            # Trigger the multi-agent analysis pipeline
            try:
                from app.worker.tasks import run_agent_pipeline as _pipeline_task
                _pipeline_task.delay(
                    test_run_id=str(run.id),
                    project_id=str(run.project_id),
                    build_number=run.build_number,
                    workflow_type="offline",
                )
                logger.info("agent_pipeline_queued", run_id=str(run.id))
            except Exception as pipeline_err:
                logger.warning(
                    "agent_pipeline_queue_failed", error=str(pipeline_err),
                )

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
            ingestion_source="file",
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


async def _upsert_test_case(
    db,
    case_data: dict,
    run: TestRun,
    *,
    existing: Optional[TestCase] = None,
    fingerprint: Optional[str] = None,
) -> TestCase:
    """Upsert a test case — idempotent on (run_id, test_fingerprint).

    ``existing`` and ``fingerprint`` may be supplied by the caller to
    skip the per-row SELECT — the prefetch path in ``ingest_test_results``
    fetches every row's existing TestCase in one query and passes the
    match (or None) here. The unguarded call site still queries inline,
    so this stays a drop-in replacement.
    """
    if fingerprint is None:
        fingerprint = make_test_fingerprint(
            case_data.get("test_name", ""),
            case_data.get("class_name"),
        )

    if existing is None:
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

    # Record history — exactly ONE row per (test_case, run). On the update
    # path (a retry's extra Allure ``-result.json`` for the same fingerprint,
    # or a same-build re-ingest under a different minio_prefix), refresh the
    # existing row's final status/duration instead of inserting a duplicate.
    # Duplicates have no unique-key guard and would inflate the per-fingerprint
    # run count that flaky detection + historical-recurrence scoring divide by,
    # mis-classifying flaky tests. Only probe for an existing row when the
    # TestCase already existed (a brand-new TestCase can't have history yet);
    # autoflush makes a history row added earlier in THIS ingest visible here.
    hist_existing: Optional[TestCaseHistory] = None
    if existing is not None:
        hist_existing = (
            await db.execute(
                select(TestCaseHistory).where(
                    TestCaseHistory.test_case_id == tc.id,
                    TestCaseHistory.test_run_id == run.id,
                )
            )
        ).scalar_one_or_none()

    if hist_existing is not None:
        hist_existing.status = status
        hist_existing.duration_ms = case_data.get("duration_ms")
    else:
        db.add(TestCaseHistory(
            test_case_id=tc.id,
            test_run_id=run.id,
            test_fingerprint=fingerprint,
            status=status,
            duration_ms=case_data.get("duration_ms"),
        ))

    # ── Per-run granular metadata + latest-run-only step/attachment snapshot ──
    raw_steps = case_data.get("steps") or []
    raw_attachments = case_data.get("attachments") or []
    tc.retry_count = case_data.get("retry_count")
    tc.is_flaky_run = case_data.get("is_flaky") if case_data.get("is_flaky") is not None else None
    tc.stack_trace = _sanitize(case_data.get("stack_trace") or "") or None
    tc.step_count = len(raw_steps) if raw_steps else (0 if "steps" in case_data else None)

    if raw_steps or raw_attachments:
        await _persist_step_snapshot(
            db, run, fingerprint, case_data, tc,
            steps=raw_steps, attachments=raw_attachments,
        )

    return cast(TestCase, tc)


async def _persist_step_snapshot(
    db,
    run: TestRun,
    fingerprint: str,
    case_data: dict,
    tc: TestCase,
    *,
    steps: list,
    attachments: list,
) -> None:
    """Materialise the LATEST-RUN-ONLY granular snapshot for a logical test.

    Anchored to the project-scoped ``CanonicalTestCase`` (one snapshot per
    ``(project_id, test_fingerprint)``). On every ingest we DELETE the prior
    snapshot's steps + attachments for this anchor and INSERT the new ones —
    delete-then-insert overwrite — so the snapshot always reflects the latest
    run. Everything is staged inside the caller's ingestion-pipeline
    transaction; this function NEVER commits (router owns the commit).

    Idempotent on re-ingest of the same run: the delete clears any rows a prior
    pass for this fingerprint inserted, so re-running yields the same final set.
    """
    from app.services.test_suite_service import get_or_create_canonical  # noqa: PLC0415
    from app.services.privacy_service import sanitize_for_persistence as _sanitize  # noqa: PLC0415
    from sqlalchemy import delete as _sql_delete  # noqa: PLC0415

    canonical = await get_or_create_canonical(
        db,
        run.project_id,
        run.id,
        test_fingerprint=fingerprint,
        test_name=case_data.get("test_name", "Unknown"),
        class_name=case_data.get("class_name"),
        suite_name=case_data.get("suite_name"),
    )

    # Link the per-run TestCase to its canonical anchor at WRITE time. The read
    # path (``runs_service.get_test_steps_tree``) resolves the snapshot via
    # ``tc.canonical_test_case_id``; otherwise that column is only populated by
    # the later ``sync_canonical_test_cases`` pass, which runs ONLY inside
    # ``ingestion_pipeline.finalize_run`` — NOT on the MinIO/sentinel
    # (``process_sentinel``) ingest path. Setting it here makes the steps
    # endpoint self-consistent regardless of ingest path (sentinel + pipeline),
    # and closes the transient-NULL window on the pipeline path between the
    # ingest commit and the separately-committed canonical sync. The later sync
    # re-selects this same canonical and is an idempotent no-op confirm.
    tc.canonical_test_case_id = canonical.id

    # Delete-then-insert: drop the prior snapshot for this anchor. Attachments
    # FK steps with ON DELETE CASCADE, but step-level rows also reference the
    # canonical directly — delete attachments first, then steps (children before
    # parents is handled by the self-FK CASCADE on parent_step_id).
    await db.execute(
        _sql_delete(TestAttachment).where(
            TestAttachment.canonical_test_case_id == canonical.id
        )
    )
    await db.execute(
        _sql_delete(TestStep).where(
            TestStep.canonical_test_case_id == canonical.id
        )
    )
    # Per-run step history (test_step_runs) RETAINS prior runs — so the delete is
    # scoped to THIS run only (idempotent re-ingest of the same run overwrites
    # its own rows, never the cross-run history we need for step-flip analysis).
    await db.execute(
        _sql_delete(TestStepRun).where(
            TestStepRun.canonical_test_case_id == canonical.id,
            TestStepRun.source_test_run_id == run.id,
        )
    )
    await db.flush()

    # Test-level attachments (no owning step).
    for att in attachments:
        if not isinstance(att, dict):
            continue
        db.add(TestAttachment(
            canonical_test_case_id=canonical.id,
            test_step_id=None,
            source_test_run_id=run.id,
            name=_sanitize(str(att.get("name") or "attachment"))[:500],
            source_ref=(_sanitize(str(att["source_ref"]))[:1000] if att.get("source_ref") else None),
            media_type=(str(att["media_type"])[:100] if att.get("media_type") else None),
        ))

    # Recursively insert the step tree (depth-first, preserving ordinal order).
    counter = {"ordinal": 0}
    for node in steps:
        if isinstance(node, dict):
            await _insert_step(db, canonical.id, run.id, node, None, 0, counter)


# Defensive write-side cap, mirroring the parser cap in allure_parser. Even
# though the in-scope parsers cap the tree, _insert_step takes the common step
# dict from ANY producer; a hostile/large tree must not blow Python's recursion
# limit or materialise an unbounded number of test_steps rows + flushes inside
# the single ingestion transaction. Stop once the per-test node budget / depth
# cap is hit; the remainder is dropped (snapshot is best-effort, not an audit).
_MAX_STEP_DEPTH = 20
_MAX_STEP_NODES = 2000


async def _insert_step(
    db,
    canonical_id,
    run_id,
    node: dict,
    parent_id,
    depth: int,
    counter: dict,
) -> None:
    """Insert one step (and its children/attachments) from the common step dict."""
    from app.services.privacy_service import sanitize_for_persistence as _sanitize  # noqa: PLC0415
    from app.services.redaction_service import redact_dict as _redact_dict  # noqa: PLC0415

    if depth > _MAX_STEP_DEPTH or counter["ordinal"] >= _MAX_STEP_NODES:
        return  # depth/node budget exhausted — truncate the rest of the tree

    status = _map_step_status(node.get("status"))
    ordinal = counter["ordinal"]
    counter["ordinal"] += 1
    params = node.get("parameters")
    # Assign the PK up front (the model's Python-side ``default=uuid.uuid4`` only
    # fires at flush) so children reference it as ``parent_step_id`` and
    # attachments as ``test_step_id`` WITHOUT a per-node ``db.flush()``. This
    # kills the ingestion flush storm — a test with N step nodes previously did N
    # flushes (up to _MAX_STEP_NODES=2000) inside one transaction; now the whole
    # tree is buffered and inserted in the caller's single flush/commit. Ordering
    # is safe: nodes are added depth-first (parent before child), which SQLAlchemy
    # preserves for same-mapper INSERTs, and Postgres evaluates the self-FK
    # (parent_step_id → test_steps.id) at statement end, so intra-batch parent
    # references resolve. Verified end-to-end against real Postgres.
    step_id = uuid.uuid4()
    step = TestStep(
        id=step_id,
        canonical_test_case_id=canonical_id,
        source_test_run_id=run_id,
        parent_step_id=parent_id,
        ordinal=ordinal,
        depth=depth,
        name=str(node.get("name") or "step")[:2000],
        keyword=(str(node["keyword"])[:50] if node.get("keyword") else None),
        status=status,
        duration_ms=node.get("duration_ms"),
        start_ms=node.get("start_ms"),
        assertion_message=_sanitize(node.get("assertion_message") or "") or None,
        assertion_trace=_sanitize(node.get("assertion_trace") or "") or None,
        # expected/actual frequently echo response bodies → PII; redact them at
        # the same boundary as assertion_message/trace above.
        expected_value=(_sanitize(str(node["expected"])) if node.get("expected") is not None else None),
        actual_value=(_sanitize(str(node["actual"])) if node.get("actual") is not None else None),
        # ``parameters`` carry test INPUT data (fixtures) — emails/tokens/keys/
        # passwords land here; recursively redact via redact_dict before persist.
        parameters=(
            _redact_dict_or_list(params, _redact_dict)
            if isinstance(params, (list, dict)) and params else None
        ),
    )
    db.add(step)
    # Retain a compact per-run copy of this step's outcome for cross-run
    # step-flip analysis. Reuses the snapshot's already-truncated name/keyword
    # and the same ordinal/depth, so the two stay aligned; carries no heavy /
    # PII columns (those stay on the latest-run snapshot only).
    db.add(TestStepRun(
        canonical_test_case_id=canonical_id,
        source_test_run_id=run_id,
        ordinal=ordinal,
        depth=depth,
        name=step.name,
        keyword=step.keyword,
        status=status,
        duration_ms=node.get("duration_ms"),
    ))

    for att in (node.get("attachments") or []):
        if not isinstance(att, dict):
            continue
        db.add(TestAttachment(
            canonical_test_case_id=canonical_id,
            test_step_id=step_id,
            source_test_run_id=run_id,
            name=_sanitize(str(att.get("name") or "attachment"))[:500],
            source_ref=(_sanitize(str(att["source_ref"]))[:1000] if att.get("source_ref") else None),
            media_type=(str(att["media_type"])[:100] if att.get("media_type") else None),
        ))

    for child in (node.get("steps") or []):
        if isinstance(child, dict):
            await _insert_step(db, canonical_id, run_id, child, step_id, depth + 1, counter)


def _redact_dict_or_list(value, redact_dict):
    """Redact a step ``parameters`` value (Allure = list[{name,value}]; pytest /
    others may use a dict). ``redact_dict`` only takes a dict, so wrap a list:
    redact each dict item, leave non-dict items as-is (mirrors redact_dict's own
    list branch)."""
    if isinstance(value, dict):
        return redact_dict(value)
    if isinstance(value, list):
        return [redact_dict(item) if isinstance(item, dict) else item for item in value]
    return value


def _map_step_status(raw) -> str:
    """Map any parser step status string into the strict TestStatus vocab."""
    valid = {"PASSED", "FAILED", "SKIPPED", "BROKEN", "UNKNOWN"}
    s = str(raw or "").upper()
    return s if s in valid else "UNKNOWN"


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
            func.sum((TestCase.status == TestStatus.UNKNOWN).cast(Integer)).label("unknown"),
        ).where(TestCase.test_run_id == run_id)
    )
    counts = result.one()

    total = counts.total or 0
    passed = counts.passed or 0
    failed = counts.failed or 0
    broken = counts.broken or 0
    # ``total`` is a COUNT(*), so UNKNOWN rows were always inside it while no
    # column reported them — the four status columns simply did not add up to
    # total_tests. Counted explicitly now, on both ingest paths.
    unknown = counts.unknown or 0
    # Canonical pass_rate (single source of truth — finalize_run calls this for
    # BOTH file and live ingestion). EXCLUDES skipped from the denominator: a
    # skipped test wasn't executed, so it's neither a pass nor a fail. This now
    # matches the live-stream transient displays (live_consumer / stream_service)
    # and the FAILED-iff-failed+broken status rule, so a run's pass_rate no
    # longer shifts when a live run finalizes. (Previously divided by ``total``
    # which included skipped — see docs/reviews/live-stream-ingestion.)
    executed = passed + failed + broken
    pass_rate = round((passed / executed * 100), 2) if executed > 0 else 0.0

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

    # Don't clobber a primary_suite_name supplied at session/upload time
    # by recomputing it from per-event ``suite_name`` values. The live-
    # stream path stamps it from the SDK-supplied session ``suite_name``
    # (authoritative — that's the user's chosen run label, e.g. the
    # testng.xml ``<suite name="…">`` value). Earlier this function
    # overwrote it with the dominant per-event suite, which surfaced as
    # the "API Regression Multi-Class" label being replaced by a test
    # class name like ``com.example.OrderApiRegressionTests`` after
    # finalize_run ran. File uploads still get a value because the
    # initial create leaves ``primary_suite_name`` NULL and the IS NULL
    # branch fills it on first pass. (Bug 2026-05-19.)
    existing_psn_q = await db.execute(
        select(TestRun.primary_suite_name).where(TestRun.id == run_id)
    )
    existing_psn = (existing_psn_q.scalar_one_or_none() or "").strip() or None

    # A finalized run that executed nothing (empty/parse-failed upload, or a
    # fully-skipped suite) grades as STOPPED, not PASSED — see run_status
    # for the rationale. Shared with the live-stream close path for parity.
    run_status = terminal_run_status(executed, failed, broken, unknown)

    values_to_update: dict = {
        "total_tests":   total,
        "passed_tests":  passed,
        "failed_tests":  counts.failed or 0,
        "skipped_tests": counts.skipped or 0,
        "broken_tests":  counts.broken or 0,
        "unknown_tests": unknown,
        "pass_rate":     pass_rate,
        "status":        run_status,
        "end_time":      datetime.now(timezone.utc),
        # ``suite_names`` is the full set actually present in the
        # events — always refresh it (its purpose is to mirror the
        # current per-event reality, not to encode a user-chosen
        # label). ``primary_suite_name`` is what users see in the UI;
        # see the IS NULL guard above.
        "suite_names":   suite_names_sorted,
    }
    if existing_psn is None:
        values_to_update["primary_suite_name"] = primary_suite

    await db.execute(
        update(TestRun)
        .where(TestRun.id == run_id)
        .values(**values_to_update)
    )
