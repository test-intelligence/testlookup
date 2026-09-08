"""
Shared ingestion pipeline — processes normalized test results into PostgreSQL.

Used by:
  1. process_sentinel() — MinIO webhook path (Allure/TestNG from S3)
  2. ingest_uploaded_results task — POST /api/v1/ingest (JSON batch)
  3. ingest_uploaded_file task — POST /api/v1/ingest/file (file upload)
"""
import hashlib
import uuid
from datetime import datetime
from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import AsyncSessionLocal
from app.models.postgres import LaunchStatus, Project, TestCase, TestRun
from app.models.schemas import BUILD_NUMBER_MAX_LENGTH
from app.services.execution_time import resolve_execution_time
from app.services.run_environment import normalize_environment
from app.services.run_tombstone_service import run_is_tombstoned
from app.services.ingestion import (
    _chunked,
    _prefetch_test_cases,
    _update_run_aggregates,
    _upsert_test_case,
)

logger = structlog.get_logger("services.ingestion_pipeline")

# Persist enough samples to diagnose a malformed batch without allowing a
# report with thousands of bad rows to grow one unbounded JSON value.
MAX_INGESTION_REJECTION_SAMPLES = 20
_PERSISTED_STATUS_LABELS = frozenset(
    {"PASSED", "FAILED", "SKIPPED", "BROKEN", "UNKNOWN"}
)


def _safe_rejection_reason(
    row_index: int,
    fingerprint: str,
    exc: Exception,
) -> dict[str, Any]:
    """Return bounded, non-sensitive metadata for one rejected test result.

    Driver exception strings can contain SQL parameter values, and test names
    can contain customer data. Persist the row position, already-hashed
    fingerprint, exception class and stable driver code instead.
    """
    original = getattr(exc, "orig", None)
    code = (
        getattr(original, "sqlstate", None)
        or getattr(original, "pgcode", None)
        or getattr(exc, "code", None)
    )
    reason: dict[str, Any] = {
        "row_index": row_index,
        "fingerprint": fingerprint,
        "error_type": type(exc).__name__[:100],
    }
    if code is not None:
        reason["code"] = str(code)[:64]
    return reason

def _one_or_none(result):
    """Return one row; ambiguous legacy duplicates must not pick arbitrarily."""
    try:
        return result.scalar_one_or_none()
    except MultipleResultsFound:
        return None


def build_ingestion_identity(
    *,
    project_id: str | uuid.UUID,
    build_number: str,
    ingestion_source: str = "unknown",
    ci_provider: Optional[str] = None,
    ci_repo: Optional[str] = None,
    ci_run_url: Optional[str] = None,
    jenkins_job: Optional[str] = None,
) -> str:
    """Return a bounded, source-aware idempotency key for reusable ingests.

    CI callers should send ci_run_url so parallel jobs sharing a build
    label remain separate. Provider/repository/build are the deterministic
    fallback for SDK callers without a run URL. Hashing keeps the DB key fixed
    at 64 characters while descriptive CI fields stay on TestRun.
    """
    provider = (ci_provider or "").strip().lower()
    repo = (ci_repo or "").strip().lower().strip("/")
    run_url = (ci_run_url or "").strip().rstrip("/")
    job = (jenkins_job or "").strip().lower()
    source = (ingestion_source or "unknown").strip().lower()
    run_key = run_url or "|".join((job, str(build_number).strip()))
    canonical = "|".join(("v1", str(project_id), source, provider, repo, run_key))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _unique_build_number(db: AsyncSession, project_id: uuid.UUID, base: str) -> str:
    """Return a build label unique within the project, suffixing ``-N`` (then a
    short random token as a final backstop) when ``base`` is already taken.

    Used by the manual-upload path so a colliding label creates a distinct run
    instead of merging. The DB UNIQUE(project_id, build_number, jenkins_job)
    constraint remains the ultimate guard against a concurrent race.
    """
    # Keep the canonical label and every collision suffix within the database
    # column's 100-character limit. Boundary validation rejects longer labels
    # at the API, while this defensive bound also protects internal callers.
    base = base[:BUILD_NUMBER_MAX_LENGTH]
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
        suffix = f"-{n}"
        candidate = f"{base[:BUILD_NUMBER_MAX_LENGTH - len(suffix)]}{suffix}"
    # Pathological: 50 collisions — fall back to a guaranteed-unique token.
    suffix = f"-{uuid.uuid4().hex[:8]}"
    return f"{base[:BUILD_NUMBER_MAX_LENGTH - len(suffix)]}{suffix}"


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
    jenkins_job: Optional[str] = None,
    # Optional (migration 0129). Absent means "not recorded", NOT "default" —
    # see services/run_environment.py for why that distinction is load-bearing.
    environment: Optional[str] = None,
    # When the run actually executed. Bounded by services/execution_time before
    # it reaches start_time — see that module for why an unbounded client value
    # is a run that silently vanishes from every default window rather than a
    # validation error.
    executed_at: Optional[datetime] = None,
    # Either supplied wire shape: a bare commit list, or the boundary-carrying
    # ``{base, head, commits}`` object (``SuppliedCommitRangeInput``).
    commit_range: Optional[Any] = None,
) -> TestRun:
    """
    Create a TestRun record for API-ingested data.

    When ``reuse_existing`` is True (default — the SDK/CI batch path), this reuses the source-aware ingestion identity. A CI retry with the same provider/repository/run URL adds cases to the same run, while distinct CI run URLs remain separate even when their build labels match.

    When ``reuse_existing`` is False (the manual-upload path), it must NEVER
    merge into a pre-existing run — an operator-typed or timestamp-defaulted
    build label could collide with an unrelated live/sdk/file run and silently
    blend datasets (and rewrite that run's aggregates). Instead we always
    create a fresh run, auto-suffixing the build label to keep
    (project_id, build_number) unique. The returned run's ``id`` is therefore
    authoritative for the caller's 202 response and ``View run`` navigation.
    """
    pid = uuid.UUID(project_id)
    ingestion_identity = (
        build_ingestion_identity(
            project_id=pid,
            build_number=build_number,
            ingestion_source=ingestion_source,
            ci_provider=ci_provider,
            ci_repo=ci_repo,
            ci_run_url=ci_run_url,
            jenkins_job=jenkins_job,
        )
        if reuse_existing
        else None
    )

    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == pid))
    project = result.scalar_one_or_none()
    if not project:
        raise ValueError(f"Project {project_id} not found")

    # ── Retry resumption ─────────────────────────────────────────────────────
    # An explicit ``run_id`` that ALREADY exists in this project means a prior
    # attempt of this same unit of work got as far as inserting the row. Resume
    # it instead of inserting again.
    #
    # Without this, a Celery retry re-ran the insert with the pre-generated id
    # (``routers/ingest.py`` mints ``run_id`` and passes it to
    # ``ingest_uploaded_file.delay(run_id=…)``) and died on
    # ``test_runs_pkey``, so it could NEVER succeed. Any transient failure
    # became a permanently IN_PROGRESS run whose aggregates never populated,
    # while the task retried every ~2 minutes forever. Measured on a 60-run
    # bulk ingest: 56 runs stuck, 38 tasks looping, 145 duplicate-key events.
    #
    # This is NOT the ``reuse_existing`` merge the branch below guards against.
    # That one matches fuzzily on (project_id, build_number) and could blend two
    # unrelated datasets; this matches the caller's OWN explicit primary key, so
    # it can only ever resume the run this very task created. The project scope
    # keeps a cross-tenant id from resolving here.
    if run_id:
        prior = (
            await db.execute(
                select(TestRun).where(
                    TestRun.id == uuid.UUID(run_id),
                    TestRun.project_id == pid,
                )
            )
        ).scalar_one_or_none()
        if prior is not None:
            logger.info(
                "resuming_run_from_prior_attempt",
                run_id=str(prior.id),
                build=prior.build_number,
                project=project_id,
            )
            await _store_supplied_commit_range(db, prior, commit_range)
            return prior

    effective_build = build_number
    if reuse_existing:
        # Prefer the explicit source identity. The identity index guarantees
        # at most one match for all rows written after migration 0160.
        result = await db.execute(
            select(TestRun).where(
                TestRun.project_id == pid,
                TestRun.ingestion_identity == ingestion_identity,
            )
        )
        existing = _one_or_none(result)
        if existing is None and not (ci_provider or ci_repo or ci_run_url or jenkins_job):
            # Legacy rows have no identity. Reuse one only when the label is
            # unambiguous; multiple historical jobs require a new canonical
            # identity rather than silently appending to an arbitrary run.
            legacy = await db.execute(
                select(TestRun).where(
                    TestRun.project_id == pid,
                    TestRun.ingestion_identity.is_(None),
                    TestRun.build_number == build_number,
                )
            )
            existing = _one_or_none(legacy)
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
            if getattr(existing, "ingestion_identity", None) is None:
                existing.ingestion_identity = ingestion_identity
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

    # A caller-supplied run_id can name a run an operator deleted. Creating it
    # here would resurrect the row without any of its data.
    resolved_run_id = uuid.UUID(run_id) if run_id else uuid.uuid4()
    if run_id and await run_is_tombstoned(db, resolved_run_id):
        raise ValueError(
            f"run {resolved_run_id} was deleted and cannot be re-created; "
            "ingest under a new run id"
        )

    run = TestRun(
        id=resolved_run_id,
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
        jenkins_job=jenkins_job,
        ingestion_identity=ingestion_identity,
        environment=normalize_environment(environment),
        status=LaunchStatus.IN_PROGRESS,
        total_tests=0,
        passed_tests=0,
        failed_tests=0,
        skipped_tests=0,
        broken_tests=0,
        start_time=resolve_execution_time(executed_at, context="upload"),
    )
    db.add(run)
    try:
        await db.flush()
    except IntegrityError:
        # A concurrent request may pass the lookup before the winner commits.
        # Roll back the failed insert and return the row protected by the
        # unique identity index so callers converge on its canonical UUID.
        await db.rollback()
        if ingestion_identity is not None:
            winner = (
                await db.execute(
                    select(TestRun).where(
                        TestRun.project_id == pid,
                        TestRun.ingestion_identity == ingestion_identity,
                    )
                )
            ).scalar_one_or_none()
            if winner is not None:
                return winner
        raise
    # US-8.1 — persist a caller-supplied commit range (air-gapped attribution
    # path). Staged under this session; the caller owns the commit.
    await _store_supplied_commit_range(db, run, commit_range)
    logger.info("Created new run", run_id=str(run.id), build=build_number, project=project_id)
    return run


async def _store_supplied_commit_range(
    db: AsyncSession, run: TestRun, commit_range: Optional[Any],
) -> None:
    """Best-effort persist of a caller-supplied commit range (US-8.1).

    Accepts either wire shape (bare list, or ``{base, head, commits}`` — only
    the latter lets the base ref be persisted). Never raises — a malformed
    commit range must not fail ingestion. Empty / absent ranges are a no-op
    (finalize's connector path may still resolve one).
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


def _count_ingested_cases(
    framework: Optional[str],
    accepted_status_counts: dict[str, int],
) -> None:
    """Record ingested cases by framework and outcome. Never raises.

    ``ingestion_test_cases_total`` was declared and never emitted, so the
    Grafana overview's "test cases ingested by framework" panel has been empty
    since it was written. An operator reading it during an ingestion stall saw
    the same picture as a quiet afternoon.

    Counts come from rows whose savepoint completed, so rejected source rows do
    not inflate the dashboard above the durable run total.
    """
    try:
        from app.core.metrics import ingestion_test_cases_total

        label = (framework or "unknown").strip().lower() or "unknown"
        for status_label, n in accepted_status_counts.items():
            if n <= 0:
                continue
            ingestion_test_cases_total.labels(
                framework=label, status=status_label.strip().lower() or "unknown"
            ).inc(n)
    except Exception:  # noqa: BLE001 -- metrics must never break ingestion
        pass


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
    # One report may name the same test more than once — parameterised cases,
    # and above all retry frameworks emitting the failed attempt and the passing
    # retry side by side. Persistence is keyed on (run, fingerprint), so the
    # last occurrence used to overwrite the rest: the same three cases reported
    # PASSED or FAILED purely by document order. Collapse first, worst outcome
    # winning, so a failure can't be hidden by a later pass.
    from app.services.ingestion import collapse_duplicate_cases  # noqa: PLC0415

    results = collapse_duplicate_cases(results)

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
    existing_by_fp = await _prefetch_test_cases(db, run.id, fingerprints)

    count = 0
    failed = 0
    rejection_reasons: list[dict[str, Any]] = []
    accepted_status_counts = {status: 0 for status in _PERSISTED_STATUS_LABELS}
    for row_index, (case_data, fingerprint) in enumerate(zip(results, fingerprints)):
        if default_suite_name and not (case_data.get("suite_name") or "").strip():
            # Rebind to a copy — mutating the caller's dict in place injects the
            # backend default into the caller's results list and masks the "SDK
            # omitted suite_name" signal the primary_suite_name repair sweeps use.
            case_data = {**case_data, "suite_name": default_suite_name}
        try:
            # A PostgreSQL error aborts the current transaction until rollback.
            # Isolate the complete per-row write (case, history, steps and
            # attachments) in a SAVEPOINT so one rejected row cannot poison
            # later siblings or discard earlier accepted rows.
            async with db.begin_nested():
                test_case = await _upsert_test_case(
                    db,
                    case_data,
                    run,
                    existing=existing_by_fp.get(fingerprint),
                    fingerprint=fingerprint,
                    existing_was_prefetched=True,
                )
            # Do not cache a row until the savepoint committed successfully.
            existing_by_fp[fingerprint] = test_case
            count += 1
            status_label = str(case_data.get("status") or "UNKNOWN").strip().upper()
            if status_label not in _PERSISTED_STATUS_LABELS:
                status_label = "UNKNOWN"
            accepted_status_counts[status_label] += 1
        except Exception as e:
            failed += 1
            reason = _safe_rejection_reason(row_index, fingerprint, e)
            if len(rejection_reasons) < MAX_INGESTION_REJECTION_SAMPLES:
                rejection_reasons.append(reason)
            logger.warning(
                "Failed to upsert test case",
                row_index=row_index,
                test_fingerprint=fingerprint,
                error_type=reason["error_type"],
                error_code=reason.get("code"),
            )

    # Persist the outcome in the same outer transaction as the accepted rows.
    # A retry replaces this snapshot, allowing a once-incomplete run to become
    # complete when every source row is eventually accepted.
    run.ingestion_attempted_tests = len(results)
    run.ingestion_rejected_tests = failed
    run.ingestion_complete = failed == 0
    run.ingestion_rejection_reasons = rejection_reasons or None
    if failed and count == 0 and results:
        # Persist a safe terminal state before the aggregate finalizer runs.
        # File uploads finalize without AI in this case, while direct callers
        # still cannot leave an all-rejected run looking IN_PROGRESS.
        run.status = LaunchStatus.STOPPED
    # Advisory upload status is emitted before a fresh aggregate read would be
    # useful. Keep exact accepted counts on this in-process object; durable run
    # counts are independently recomputed by ``finalize_run``.
    run._ingestion_accepted_status_counts = accepted_status_counts

    # Surface the failure signal: a run where every row failed to upsert
    # returns count=0 and otherwise looks identical to an empty payload.
    # Durable outcome fields and aggregate finalization preserve the terminal
    # state; this event keeps the operational failure visible in logs.
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

    _count_ingested_cases(
        getattr(run, "framework", None),
        accepted_status_counts,
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
            run_result = await db.execute(select(TestRun).where(TestRun.id == rid))
            run_for_dispatch = run_result.scalar_one_or_none()
            project_result = await db.execute(select(Project).where(Project.id == pid))
            project_for_dispatch = project_result.scalar_one_or_none()
            if run_for_dispatch is None or project_for_dispatch is None:
                raise RuntimeError("finalized run or project disappeared before outbox staging")
            from app.services.run_downstream_outbox import stage_finalize_operations

            # Children remain non-dispatchable until every required finalize
            # step below has observed the durable run state.
            await stage_finalize_operations(
                db,
                run=run_for_dispatch,
                project=project_for_dispatch,
                run_ai=run_ai,
                ready=False,
            )
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
                from app.services.test_management_metrics_service import (
                    emit_staged_test_management_metrics,
                )

                await emit_staged_test_management_metrics(step_db)
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
        rows = []
        for fingerprint_chunk in _chunked(list(dict.fromkeys(fingerprints))):
            result = await d.execute(
                select(TestCase).where(
                    TestCase.test_run_id == rid,
                    TestCase.test_fingerprint.in_(fingerprint_chunk),
                )
            )
            rows.extend(result.scalars().all())
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

    # Release linking — explicit name wins; otherwise fall back to whichever
    # release was ACTIVE WHEN THE RUN EXECUTED (migration 0150).
    #
    # ``executed_at`` is the run's start_time. On this path that is currently
    # stamped at ingest rather than carried from the client, so for a batch
    # upload it is close to ingest time and the as-of lookup degrades to "the
    # release active now" — the same answer the old default bucket gave. It is
    # passed anyway so this path is already correct once S3a plumbs a
    # client-supplied execution timestamp, rather than needing a second edit.
    from app.services.release_linker import link_run_or_default

    async def _link_release(d):
        # Read start_time on the step's own session — ``_run_isolated``
        # discards its callable's return value, so the lookup has to live
        # inside the step that uses it.
        started_at = await d.scalar(
            select(TestRun.start_time).where(TestRun.id == rid)
        )
        return await link_run_or_default(
            db=d, project_id=pid,
            release_name=release_name,
            test_run_id=rid,
            executed_at=started_at,
        )

    await _run_isolated("release_linking", _link_release)

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

    # PMF Epic 3 US-3.2 — GitLab MR note + commit status. Same isolation
    # contract as the GitHub surfaces above: both services internally gate on
    # AI_OFFLINE_MODE + the ``gitlab`` feature flag + per-integration
    # enabled/mode toggles, only fire for runs carrying MR context
    # (pr_number == CI_MERGE_REQUEST_IID + ci_repo == CI_PROJECT_PATH) that
    # matches the configured project, and never raise — ingestion must not
    # block on a GitLab outage. Errors land in ``gitlab_integrations.last_error``.
    # Each surface gets its OWN try/except (GitHub precedent above) so an
    # MR-note failure can never suppress the commit-status post.
    try:
        from app.services.gitlab_integration_service import post_mr_note_for_run
        gl_note_result = await post_mr_note_for_run(rid)
        if gl_note_result and not gl_note_result.get("skipped"):
            logger.info("gitlab_mr_note_result", run_id=str(rid), result=gl_note_result)
    except Exception as gl_note_exc:
        logger.warning(
            "gitlab_mr_note_unhandled",
            run_id=str(rid),
            error=str(gl_note_exc),
        )

    try:
        from app.services.gitlab_integration_service import post_commit_status_for_run
        gl_status_result = await post_commit_status_for_run(rid)
        if gl_status_result and not gl_status_result.get("skipped"):
            logger.info(
                "gitlab_commit_status_result", run_id=str(rid), result=gl_status_result,
            )
    except Exception as gl_status_exc:
        logger.warning(
            "gitlab_commit_status_unhandled",
            run_id=str(rid),
            error=str(gl_status_exc),
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

    # This is the readiness gate: a relay cannot observe the child intents as
    # pending until finalization has reached its end. If this commit fails, the
    # owning Celery task retries finalization and activates the same unique rows.
    await _activate_finalize_children(rid)

    logger.info(
        "post_ingestion_operations_staged",
        run_id=run_id,
        ai_requested=run_ai,
    )


async def _activate_finalize_children(run_id: uuid.UUID) -> None:
    """Commit the readiness gate, surfacing failure to the owning worker."""
    from app.services.run_downstream_outbox import activate_finalize_operations

    async with AsyncSessionLocal() as db:
        try:
            await activate_finalize_operations(db, run_id=run_id)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
