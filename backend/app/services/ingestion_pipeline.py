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
from app.models.postgres import LaunchStatus, Project, TestCase, TestRun
from app.services.ingestion import (
    _update_run_aggregates,
    _upsert_test_case,
)

logger = structlog.get_logger("services.ingestion_pipeline")


async def _unique_build_number(db: AsyncSession, project_id: uuid.UUID, base: str) -> str:
    """Return a build label unique within the project, suffixing ``-N`` (then a
    short random token as a final backstop) when ``base`` is already taken.

    Used by the manual-upload path so a colliding label creates a distinct run
    instead of merging. The DB UNIQUE(project_id, build_number, jenkins_job)
    constraint remains the ultimate guard against a concurrent race.
    """
    candidate = base
    for n in range(2, 51):
        taken = (
            await db.execute(
                select(TestRun.id).where(
                    TestRun.project_id == project_id,
                    TestRun.build_number == candidate,
                )
            )
        ).first()
        if not taken:
            return candidate
        candidate = f"{base}-{n}"
    # Pathological: 50 collisions — fall back to a guaranteed-unique token.
    return f"{base}-{uuid.uuid4().hex[:8]}"


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
    ingestion_source: str = "unknown",
    reuse_existing: bool = True,
    ci_provider: Optional[str] = None,
    ci_repo: Optional[str] = None,
    pr_number: Optional[int] = None,
    ci_actor: Optional[str] = None,
    ci_run_url: Optional[str] = None,
    commit_range: Optional[list] = None,
) -> TestRun:
    """
    Create a TestRun record for API-ingested data.

    When ``reuse_existing`` is True (default — the SDK/CI batch path), this
    upserts on (project_id, build_number): a run with the same build is reused
    so a CI retry adds cases to the same run (idempotent re-ingest).

    When ``reuse_existing`` is False (the manual-upload path), it must NEVER
    merge into a pre-existing run — an operator-typed or timestamp-defaulted
    build label could collide with an unrelated live/sdk/file run and silently
    blend datasets (and rewrite that run's aggregates). Instead we always
    create a fresh run, auto-suffixing the build label to keep
    (project_id, build_number) unique. The returned run's ``id`` is therefore
    authoritative for the caller's 202 response and ``View run`` navigation.
    """
    pid = uuid.UUID(project_id)

    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == pid))
    project = result.scalar_one_or_none()
    if not project:
        raise ValueError(f"Project {project_id} not found")

    effective_build = build_number
    if reuse_existing:
        # Check for existing run with same build_number
        result = await db.execute(
            select(TestRun).where(
                TestRun.project_id == pid,
                TestRun.build_number == build_number,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            # CI-context backfill (US-4.3): a CI retry may supply context the
            # first ingest lacked. Fill only NULL fields — never overwrite a
            # value already recorded (mirrors the primary_suite_name IS NULL
            # guard discipline).
            for field, value in (
                ("ci_provider", ci_provider),
                ("ci_repo", ci_repo),
                ("pr_number", pr_number),
                ("ci_actor", ci_actor),
                ("ci_run_url", ci_run_url),
            ):
                if value is not None and getattr(existing, field) is None:
                    setattr(existing, field, value)
            # US-8.1 — persist a caller-supplied commit range (air-gapped
            # attribution path). Staged under this session; caller commits.
            await _store_supplied_commit_range(db, existing, commit_range)
            logger.info("Reusing existing run", run_id=str(existing.id), build=build_number)
            return existing
    else:
        # Never reuse — resolve a unique build label so the create below can't
        # collide with (or merge into) an unrelated run.
        effective_build = await _unique_build_number(db, pid, build_number)
        if effective_build != build_number:
            logger.info(
                "build_label_collision_suffixed",
                project=project_id,
                requested=build_number,
                resolved=effective_build,
            )

    run = TestRun(
        id=uuid.UUID(run_id) if run_id else uuid.uuid4(),
        project_id=pid,
        build_number=effective_build,
        branch=branch,
        commit_hash=commit_hash,
        trigger_source=trigger_source,
        ingestion_source=ingestion_source,
        ci_provider=ci_provider,
        ci_repo=ci_repo,
        pr_number=pr_number,
        ci_actor=ci_actor,
        ci_run_url=ci_run_url,
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
    # US-8.1 — persist a caller-supplied commit range (air-gapped attribution
    # path). Staged under this session; the caller owns the commit.
    await _store_supplied_commit_range(db, run, commit_range)
    logger.info("Created new run", run_id=str(run.id), build=build_number, project=project_id)
    return run


async def _store_supplied_commit_range(
    db: AsyncSession, run: TestRun, commit_range: Optional[list],
) -> None:
    """Best-effort persist of a caller-supplied commit range (US-8.1).

    Never raises — a malformed commit list must not fail ingestion. Empty /
    absent lists are a no-op (finalize's connector path may still resolve one).
    """
    if not commit_range:
        return
    try:
        from app.services.commit_attribution_service import store_supplied_range
        await store_supplied_range(db, run, commit_range)
    except Exception as exc:
        logger.warning(
            "supplied_commit_range_store_failed", run_id=str(run.id), error=str(exc),
        )


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
    # Phase 2: pre-fill ``suite_name`` with the project's default suite when the
    # payload omits one. Keeps the per-run TestCase.suite_name field populated for
    # downstream consumers (analytics, suite_sync_service, primary_suite_name
    # attribution) while CanonicalTestCase rows are reconciled in finalize_run.
    # Lookup is hoisted out of the loop so it costs one query per ingest, not N.
    default_suite_name: Optional[str] = None
    needs_default = any(
        not (case.get("suite_name") or "").strip() for case in results
    )
    if needs_default:
        project_result = await db.execute(
            select(Project).where(Project.id == run.project_id)
        )
        project = project_result.scalar_one_or_none()
        if project is not None:
            from app.services.test_suite_service import default_suite_name_for
            default_suite_name = default_suite_name_for(project.name)

    # P2-1 (audit doc): prefetch every existing TestCase row for this
    # run keyed by fingerprint, so the per-row upsert loop below can
    # skip its own SELECT. Before the prefetch, ingesting a 1000-test
    # run did 1000 SELECTs + 1000 INSERTs; after, it's 1 SELECT + 1000
    # INSERTs. The full bulk-INSERT optimisation is deferred (needs
    # partial-failure design — current per-row try/except is preserved).
    from app.services.ingestion import make_test_fingerprint  # noqa: PLC0415
    fingerprints = [
        make_test_fingerprint(case.get("test_name", ""), case.get("class_name"))
        for case in results
    ]
    existing_by_fp: dict[str, TestCase] = {}
    if fingerprints:
        existing_rows = (
            await db.execute(
                select(TestCase).where(
                    TestCase.test_run_id == run.id,
                    TestCase.test_fingerprint.in_(fingerprints),
                )
            )
        ).scalars().all()
        existing_by_fp = {r.test_fingerprint: r for r in existing_rows}

    count = 0
    failed = 0
    for case_data, fingerprint in zip(results, fingerprints):
        if default_suite_name and not (case_data.get("suite_name") or "").strip():
            # Rebind to a copy — mutating the caller's dict in place injects the
            # backend default into the caller's results list and masks the "SDK
            # omitted suite_name" signal the primary_suite_name repair sweeps use.
            case_data = {**case_data, "suite_name": default_suite_name}
        try:
            await _upsert_test_case(
                db,
                case_data,
                run,
                existing=existing_by_fp.get(fingerprint),
                fingerprint=fingerprint,
            )
            count += 1
        except Exception as e:
            failed += 1
            logger.warning(
                "Failed to upsert test case",
                test_name=case_data.get("test_name"),
                error=str(e),
            )

    # Surface the failure signal: a run where EVERY row failed to upsert
    # returns count=0 and otherwise looks identical to an empty payload —
    # but it still flows to finalize_run and gets marked "complete". Without
    # a loud signal the only trace is N scattered per-row warnings.
    if failed:
        if count == 0 and results:
            logger.error(
                "ingest_test_results_all_failed",
                run_id=str(run.id),
                attempted=len(results),
                failed=failed,
            )
        else:
            logger.warning(
                "ingest_test_results_partial_failure",
                run_id=str(run.id),
                ingested=count,
                failed=failed,
            )
    return count


async def finalize_run(
    run_id: str,
    project_id: str,
    build_number: str,
    release_name: Optional[str] = None,
    run_ai: bool = True,
) -> None:
    """
    Post-ingestion steps: update aggregates, auto-tag, link release,
    queue notifications, queue agent pipeline.

    Runs in a fresh DB session since Celery tasks don't carry the original session.
    """
    rid = uuid.UUID(run_id)
    pid = uuid.UUID(project_id)

    # Step 1: aggregates — critical, must succeed for the run to be usable.
    # Commits in its own transaction so subsequent non-blocking steps can't poison it.
    async with AsyncSessionLocal() as db:
        try:
            await _update_run_aggregates(db, rid)
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    # Steps 2-4: each runs in an isolated session so a failure in one does not
    # leave the SQLAlchemy session in a failed state and does not skip subsequent
    # steps. Each step commits or rolls back independently.
    async def _run_isolated(step_name: str, coro_factory):
        async with AsyncSessionLocal() as step_db:
            try:
                await coro_factory(step_db)
                await step_db.commit()
            except Exception as e:
                await step_db.rollback()
                logger.warning(
                    "isolated_step_failed",
                    step=step_name,
                    error=str(e),
                )

    from app.services.suite_sync_service import sync_suite_membership
    from app.services.test_suite_service import (
        reconcile_canonical_deletions,
        sync_canonical_test_cases,
    )
    from app.services.auto_tagging_service import auto_tag_test_cases, auto_tag_test_run

    await _run_isolated(
        "suite_sync",
        lambda d: sync_suite_membership(d, pid, rid),
    )

    # Phase 2 dual-write: reconcile canonical_test_cases against this run. Runs
    # in its own session so a failure can't poison the suite_sync transaction
    # above (or the auto_tagging step below). suite_memberships is still the
    # source of truth during the dual-write window; canonical_test_cases is
    # written in parallel so the API/UI on top of it has up-to-date data.
    await _run_isolated(
        "canonical_sync",
        lambda d: sync_canonical_test_cases(d, pid, rid),
    )

    # Phase I follow-up: project-scoped deletion detection across the last
    # N runs. sync_canonical_test_cases handles the *appearance* half
    # (insert new, restore previously-deleted on re-sighting); this step
    # handles the *disappearance* half. Required before Phase 2b can drop
    # the legacy suite_memberships ``<suite>-deleted`` bucket.
    #
    # Isolated session: a project-wide read sweep + write isn't worth
    # poisoning the per-run finalize transaction. Reconciler is a no-op
    # when CANONICAL_DELETION_WINDOW_RUNS == 0 (the cutover-comparison knob).
    await _run_isolated(
        "canonical_deletion_reconcile",
        lambda d: reconcile_canonical_deletions(d, pid),
    )

    # 0080: Assign every failed/broken TestCase in this run to the resolved
    # suite owner so the action queue for QA leads is populated immediately
    # after ingest. Runs AFTER canonical_sync because new suites get their
    # TestSuiteOwner rows seeded there (via _maybe_seed_default_owner).
    # Isolated session so an assignment failure can't poison auto-tagging
    # or the AI pipeline that follow.
    from app.services.failed_test_assignment_service import (
        assign_failed_tests_to_suite_owners,
    )
    await _run_isolated(
        "assign_failed_tests",
        lambda d: assign_failed_tests_to_suite_owners(d, pid, rid),
    )

    async def _tag(d: AsyncSession) -> None:
        await auto_tag_test_cases(d, rid)
        await auto_tag_test_run(d, rid)

    await _run_isolated("auto_tagging", _tag)

    # Tier 1 item 3 — tag any test cases in this run that are currently
    # under active quarantine so release gate scoring, dashboards, and
    # defect promotion can exclude them. The service returns an empty set
    # when the ``flaky_auto_quarantine`` feature flag is off, making this
    # step a no-op for deployments that haven't enabled the workflow.
    async def _apply_quarantine_tags(d: AsyncSession) -> None:
        from app.services.flaky_quarantine_service import active_quarantines_for_project
        fingerprints = await active_quarantines_for_project(d, pid)
        if not fingerprints:
            return
        # Fetch all test cases in this run whose fingerprint matches. Bounded
        # by the run so the query is cheap even when the fingerprint set is
        # large.
        result = await d.execute(
            select(TestCase).where(
                TestCase.test_run_id == rid,
                TestCase.test_fingerprint.in_(fingerprints),
            )
        )
        rows = list(result.scalars().all())
        if not rows:
            return
        for row in rows:
            tags = list(row.tags or [])
            if "quarantined" not in tags:
                tags.append("quarantined")
                row.tags = tags
        logger.info(
            "quarantine_tags_applied",
            run_id=str(rid),
            project_id=str(pid),
            count=len(rows),
        )

    await _run_isolated("quarantine_tagging", _apply_quarantine_tags)

    # Release linking — explicit name wins; otherwise fall back to the
    # project's default release (auto-created on first use; migration 0077).
    from app.services.release_linker import link_run_or_default
    await _run_isolated(
        "release_linking",
        lambda d: link_run_or_default(
            db=d, project_id=pid,
            release_name=release_name,
            test_run_id=rid,
        ),
    )

    # Tier 1 item 5 — post a GitHub check run for this commit SHA. The
    # service is the hard kill switch: it returns a ``skipped`` dict
    # when the ``github_checks`` feature flag is off, ``AI_OFFLINE_MODE``
    # is on, there's no integration configured for the project, or the
    # run lacks a full 40-char commit SHA. Ingestion never blocks on a
    # GitHub outage — errors land in ``github_integrations.last_error``
    # so the Integration Health dashboard surfaces them.
    try:
        from app.services.github_checks_service import post_check_run_for_run
        gh_result = await post_check_run_for_run(rid)
        if gh_result and not gh_result.get("skipped"):
            logger.info("github_check_post_result", run_id=str(rid), result=gh_result)
    except Exception as gh_exc:
        logger.warning(
            "github_check_post_unhandled",
            run_id=str(rid),
            error=str(gh_exc),
        )

    # PMF US-4.1 — sticky PR summary comment. Same isolation contract as
    # the check-run post above: the service internally gates on
    # AI_OFFLINE_MODE + the github_checks feature flag + per-integration
    # pr_comment_mode, only fires for runs carrying PR context
    # (pr_number + ci_repo, US-4.3) that matches the configured repo,
    # and never raises — ingestion must not block on a GitHub outage.
    # Errors land in ``github_integrations.last_error``.
    try:
        from app.services.github_pr_comment_service import post_pr_summary_for_run
        pr_comment_result = await post_pr_summary_for_run(rid)
        if pr_comment_result and not pr_comment_result.get("skipped"):
            logger.info(
                "github_pr_comment_result",
                run_id=str(rid),
                result=pr_comment_result,
            )
    except Exception as pr_exc:
        logger.warning(
            "github_pr_comment_unhandled",
            run_id=str(rid),
            error=str(pr_exc),
        )

    # Epic 8 US-8.1 — resolve + persist this run's commit range (commits
    # since the last green run). Own session, idempotent per run: a
    # caller-supplied range (air-gapped path) already stored at ingest wins;
    # otherwise the connector path fetches it when GitHub is configured +
    # online; otherwise an honest ``unavailable`` row is persisted. Gated by
    # AI_OFFLINE_MODE + the github_checks flag inside the service — never
    # raises into the pipeline.
    from app.services.commit_attribution_service import resolve_commit_range
    await _run_isolated(
        "commit_range",
        lambda d: resolve_commit_range(d, rid),
    )

    # Fetch run for notification data
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(TestRun).where(TestRun.id == rid))
        run = result.scalar_one_or_none()
        if not run:
            logger.warning("Run not found for post-ingestion", run_id=run_id)
            return

    # Tier 2 item 6 — fan out the ``run.completed`` event to any
    # customer-managed webhook subscriptions. Gated by feature flag +
    # AI_OFFLINE_MODE. Failures land in per-subscription last_error for
    # the settings UI; ingestion never blocks on delivery.
    try:
        from app.services.webhook_service import emit_event
        await emit_event(
            "run.completed",
            project_id=pid,
            payload={
                "run_id": str(rid),
                "project_id": str(pid),
                "build_number": run.build_number,
                "branch": run.branch,
                "commit_hash": run.commit_hash,
                "total_tests": int(run.total_tests or 0),
                "passed_tests": int(run.passed_tests or 0),
                "failed_tests": int(run.failed_tests or 0),
                "broken_tests": int(run.broken_tests or 0),
                "skipped_tests": int(run.skipped_tests or 0),
                "pass_rate": float(run.pass_rate or 0.0),
                "status": run.status,
                "start_time": run.start_time.isoformat() if run.start_time else None,
                "end_time": run.end_time.isoformat() if run.end_time else None,
            },
        )
    except Exception as wh_exc:
        logger.warning(
            "webhook_emit_run_completed_failed",
            run_id=str(rid),
            error=str(wh_exc),
        )

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
        logger.warning("notification_enqueue_failed", error=str(e))

    # PMF US-7.1/US-7.2 — transition-based notifications. Same isolation
    # contract as the per-run dispatch above: evaluation runs in its own
    # Celery task, is idempotent per (fingerprint, run) via the
    # notification_test_states stamp, and never blocks finalization.
    try:
        from app.worker.tasks import dispatch_transition_notifications as _transitions
        _transitions.delay(run_id=str(rid))
    except Exception as e:
        logger.warning("transition_notification_enqueue_failed", error=str(e))

    # Trigger agent pipeline (unless the caller opted out, e.g. a manual upload
    # with "skip AI analysis" or a bulk historical import).
    if run_ai:
        try:
            from app.worker.tasks import run_agent_pipeline as _pipeline
            _pipeline.delay(
                test_run_id=str(rid),
                project_id=str(pid),
                build_number=build_number,
                workflow_type="offline",
            )
            logger.info("agent_pipeline_queued", run_id=run_id)
        except Exception as e:
            logger.warning("agent_pipeline_queue_failed", error=str(e))
    else:
        logger.info("agent_pipeline_skipped", run_id=run_id)

    # Precompute latest-vs-previous suite comparison reports so the default
    # nightly view is ready before users arrive in the morning. This is
    # best-effort and never blocks ingestion finalization.
    try:
        from app.worker.tasks import precompute_suite_comparisons_for_run as _suite_compare
        _suite_compare.delay(test_run_id=str(rid), project_id=str(pid))
        logger.info("suite_comparison_precompute_queued", run_id=run_id)
    except Exception as e:
        logger.warning("suite_comparison_precompute_queue_failed", error=str(e))
