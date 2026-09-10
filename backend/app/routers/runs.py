"""Test run and test case list endpoints."""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_accessible_project_ids,
    get_current_active_user,
    require_role,
    require_run_access,
    resolve_release_query_scope,
)
from app.db.postgres import get_db
from app.models.postgres import LaunchStatus, Project, TestCase, TestRun, User, UserRole
from app.models.schemas import (
    DeleteRunAcceptedResponse,
    DeleteRunRequest,
    EnrichedTestCaseDetailResponse,
    TestCaseHistoryResponse,
    TestCaseListResponse,
)
from app.services.runs_service import (
    get_run_with_release,
    list_project_runs,
    list_run_test_cases,
    natural_build_number_key,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/runs", tags=["Test Runs"])


async def _require_accessible_project(
    db: AsyncSession, current_user: User, project_id: str,
) -> tuple[uuid.UUID, set | None]:
    """Verify the caller may read an explicitly-requested project.

    Returns ``(parsed_uuid, accessible_set)`` where ``accessible_set`` is
    ``None`` for admins (no restriction). Raises 400 on a malformed id and 403
    when a non-admin caller isn't a member of the project. Closes the
    cross-tenant IDOR where a provided ``project_id`` bypassed the
    accessible-projects filter that only guarded the no-project_id path.
    """
    try:
        requested = uuid.UUID(project_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid project_id") from exc
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None and requested not in accessible:
        raise HTTPException(
            status_code=403, detail="You do not have access to this project"
        )
    return requested, accessible


@router.get("")
async def list_runs(
    project_id: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=500),
    status: str | None = None,
    release_id: str | None = None,
    # 30, not 6. A six-day default assumed a project ships runs most days;
    # when one does not, every caller that omits ``days`` — including the
    # agents page's suite+build picker — renders an empty list and the product
    # looks broken. Reported live against four projects whose most recent runs
    # were 8-10 days old. Matches DEFAULT_TIME_WINDOW_DAYS in the frontend
    # store; the two are meant to agree.
    days: int | None = Query(30, ge=0, le=365, description="Show runs from last N days (0 = all time)"),
    suite_name: str | None = Query(None, min_length=1, description="Filter runs by suite name, case-insensitive"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # days=0 means no date filter (all time)
    effective_days = days if days and days > 0 else None
    # Verify the PROVIDED release. This route was the only one accepting
    # ``release_id`` that skipped the check: a malformed value reached
    # ``uuid.UUID()`` in the service and raised ValueError -> 500 rather than
    # 422, and a well-formed id belonging to another tenant was never checked
    # at all. The architectural authorization ratchet cannot see it, because it
    # stops at the first scoped parameter a route satisfies and this route
    # satisfies it on ``project_id``.
    release_id = await resolve_release_query_scope(db, release_id, current_user)
    # Filter by accessible projects when no explicit project_id
    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None and not accessible:
            return {"items": [], "total": 0, "page": page, "size": size, "pages": 0}
        # Pass accessible set to service for filtering (None = admin, no filter)
        items, total, pages = await list_project_runs(
            db,
            project_id,
            page,
            size,
            status,
            release_id,
            accessible_project_ids=accessible,
            days=effective_days,
            suite_name=suite_name,
        )
    else:
        # Explicit project_id: verify the caller can read it (closes the
        # IDOR) and pass the accessible set for service-level defence-in-depth.
        _, accessible = await _require_accessible_project(db, current_user, project_id)
        items, total, pages = await list_project_runs(
            db,
            project_id,
            page,
            size,
            status,
            release_id,
            accessible_project_ids=accessible,
            days=effective_days,
            suite_name=suite_name,
        )
    return {"items": items, "total": total, "page": page, "size": size, "pages": pages}


@router.get("/failed-ids")
async def list_failed_run_ids(
    project_id: str | None = None,
    # 30 — same reasoning as ``list_runs`` above; the two must not diverge.
    days: int | None = Query(30, ge=0, le=365, description="Look back window in days (0 = all time)"),
    limit: int = Query(1000, ge=1, le=5000, description="Hard cap to prevent runaway fan-outs"),
    only_pending: bool = Query(
        False,
        description="When true, exclude runs that already have an active or recent agent pipeline (within the last 2h, matching the Celery dedup TTL)",
    ),
    suite_name: str | None = Query(None, min_length=1, description="Filter failed runs by suite name, case-insensitive"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return just the IDs of FAILED runs in the project + day window.

    Backs the "Trigger all FAILED across pages" shortcut on /runs. Returning
    only IDs (not the full row) keeps the payload tiny so the UI can fan out
    pipeline triggers in parallel without an oversized round-trip.

    With ``only_pending=true``, runs that already have an AgentPipelineRun
    in 'running' status, or any pipeline created in the last 2h, are
    excluded — matching the Celery task's dedup window so the user doesn't
    waste a click re-firing what's already in flight.
    """
    from app.models.postgres import AgentPipelineRun  # local import to avoid cycle

    effective_days = days if days and days > 0 else None
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=effective_days)
        if effective_days else None
    )

    stmt = select(TestRun.id).where(TestRun.status == LaunchStatus.FAILED)
    # Soft-deleted projects are excluded for every caller. The two branches
    # below are conditional by design (a pinned project, or a non-admin's
    # membership set), so an ADMIN with neither matched no filter at all.
    #
    # This endpoint feeds a fan-out — its own ``limit`` exists "to prevent
    # runaway fan-outs" — and measured live there were **1,133 FAILED runs on
    # deleted projects against 17 on live ones**, so the capped 1,000 ids it
    # returned were almost entirely work against projects nobody can open.
    stmt = stmt.where(
        TestRun.project_id.in_(select(Project.id).where(Project.is_active.is_(True)))
    )
    if cutoff is not None:
        stmt = stmt.where(TestRun.created_at >= cutoff)

    if project_id:
        # Verify access to the requested project — a provided project_id used
        # to skip the accessible-projects filter (cross-tenant IDOR).
        requested, _ = await _require_accessible_project(db, current_user, project_id)
        stmt = stmt.where(TestRun.project_id == requested)
    else:
        # Tenant isolation: non-admin sees only their accessible projects.
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            if not accessible:
                return {"ids": [], "count": 0, "truncated": False}
            stmt = stmt.where(TestRun.project_id.in_(accessible))

    if only_pending:
        # Dedup window mirrors the Celery task's 7200s dedup TTL — runs with
        # a pipeline created in the last 2h or currently running are
        # filtered out.
        dedup_cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        recent_pipelines = (
            select(AgentPipelineRun.test_run_id)
            .where(
                (AgentPipelineRun.status == "running")
                | (AgentPipelineRun.created_at >= dedup_cutoff)
            )
            .scalar_subquery()
        )
        stmt = stmt.where(TestRun.id.not_in(recent_pipelines))

    suite_key = (suite_name or "").strip().lower()
    if suite_key:
        stmt = stmt.where(
            or_(
                func.lower(func.trim(func.coalesce(TestRun.primary_suite_name, "Unknown Suite"))) == suite_key,
                select(TestCase.id)
                    .where(
                        TestCase.test_run_id == TestRun.id,
                        func.lower(func.trim(func.coalesce(TestCase.suite_name, "Unknown Suite"))) == suite_key,
                    )
                    .exists(),
            )
        )

    stmt = stmt.order_by(
        natural_build_number_key().desc().nulls_first(),
        TestRun.build_number.desc(),
        TestRun.created_at.desc(),
        TestRun.id.desc(),
    ).limit(limit + 1)

    result = await db.execute(stmt)
    rows = [str(row[0]) for row in result.all()]
    truncated = len(rows) > limit
    return {
        "ids": rows[:limit],
        "count": len(rows[:limit]),
        "truncated": truncated,
    }


@router.get("/{run_id}")
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    run = await get_run_with_release(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Test run not found")
    return run


@router.get("/{run_id}/downstream-status")
async def get_run_downstream_status(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """Expose durable publication state for required post-ingestion work."""
    from app.services.run_downstream_outbox import downstream_status_for_run

    return await downstream_status_for_run(db, run_id=run_id)


@router.get("/{run_id}/tests", response_model=TestCaseListResponse)
async def list_test_cases(
    run_id: uuid.UUID,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    status: str | None = None,
    suite: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    items, total, pages = await list_run_test_cases(db, run_id, page, size, status, suite)
    return {"items": items, "total": total, "page": page, "size": size, "pages": pages}


@router.get("/{run_id}/attribution")
async def run_attribution(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """Per-failure verdicts for this run: yours, flaky, infrastructure, or unknown.

    At Google roughly **84% of pass→fail transitions involve a flaky test**, so a
    raw "new failure" list is mostly noise and trains engineers to dismiss the
    real ones. This composes five signals — the transition against the previous
    run, the flakiness score, systemic co-failure cluster membership, overlap
    with the commit range's changed files, and this project's measured
    classifier calibration — into one verdict per failing test.

    Every verdict carries **all five inputs and the votes behind it**, because
    when a verdict disagrees with an engineer the useful question is *which
    input was wrong*, and that is unanswerable from a bare label.

    ``UNCERTAIN`` is a first-class answer, returned whenever the signals
    disagree or are too thin — not a failure to decide.

    **Advisory only.** Nothing here suppresses, hides or auto-closes a failure:
    a newly-flaky test reflects a real bug often enough that suppression is the
    one irreversible mistake available.
    """
    from app.services.failure_attribution_service import attribute_run

    results = await attribute_run(db, run_id)
    return {
        "items": [
            {
                "test_case_id": str(case.id),
                "test_name": case.test_name,
                "suite_name": case.suite_name,
                **attribution.to_dict(),
            }
            for case, attribution in results
        ],
        "total": len(results),
    }


@router.get("/{run_id}/tests/{test_id}")
async def get_test_case(
    run_id: uuid.UUID,
    test_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    result = await db.execute(
        select(TestCase).where(
            TestCase.id == test_id,
            TestCase.test_run_id == run_id,
        )
    )
    test_case = result.scalar_one_or_none()
    if not test_case:
        raise HTTPException(status_code=404, detail="Test case not found")
    return test_case


@router.get("/{run_id}/tests/{test_id}/steps")
async def get_test_case_steps(
    run_id: uuid.UUID,
    test_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """Granular step/attachment tree for one test (LATEST-RUN-ONLY snapshot).

    Lazy / separate from the test-case detail payload — the detail endpoint
    stays unchanged and clients fetch steps on demand. Guarded by
    ``require_run_access`` which verifies the PROVIDED ``run_id`` (IDOR ratchet).
    Returns the ordered, nested step tree with per-step + test-level attachments.
    """
    from app.services.runs_service import get_test_steps_tree

    tree = await get_test_steps_tree(db, run_id, test_id)
    if tree is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    return tree


@router.get(
    "/{run_id}/tests/{test_id}/rich-detail",
    response_model=EnrichedTestCaseDetailResponse,
)
async def get_enriched_test_case_detail_endpoint(
    run_id: uuid.UUID,
    test_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """Versioned additive detail contract with optional recursive steps.

    Access is checked against the supplied run before the service query. The
    endpoint is separate from the legacy flat detail route so existing clients
    retain their response shape during gradual rollout.
    """
    from app.services.runs_service import get_enriched_test_case_detail

    payload = await get_enriched_test_case_detail(db, run_id, test_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    return payload


@router.get("/{run_id}/tests/{test_id}/history", response_model=TestCaseHistoryResponse)
async def get_test_case_history_endpoint(
    run_id: uuid.UUID,
    test_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """Cross-run history + flakiness + metadata for one logical test (Phase 2).

    READ-ONLY. Resolves the test's ``test_fingerprint`` + project via the
    PROVIDED ``run_id`` (verified by ``require_run_access`` — IDOR ratchet), then
    returns a project-scoped timeline (``test_fingerprint`` is not salted, so the
    history query JOINs ``test_runs`` on ``project_id`` — no cross-project
    leakage), the computed flakiness value (reusing
    ``analytics_service``/``test_health_coach`` thresholds), and identity
    metadata (owner / first-last seen / suite / timestamps). No DB writes.
    """
    from app.services.test_case_history_service import get_test_case_history

    payload = await get_test_case_history(db, run_id, test_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    return payload


@router.get("/{run_id}/tests/{test_id}/step-flips")
async def get_test_case_step_flips(
    run_id: uuid.UUID,
    test_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """Cross-run step-flip report for one logical test (FLK-P6 slice 4).

    READ-ONLY. Unlike ``/steps`` (a LATEST-RUN-ONLY snapshot), this reads the
    retained per-run step outcomes (``test_step_runs``) and reports WHICH step
    oscillated PASSED<->FAILED across runs, how often, and in which direction —
    so a flickering step reads as step-level flakiness rather than a whole-test
    verdict. Resolves the test's ``test_fingerprint`` + project via the PROVIDED
    ``run_id`` (verified by ``require_run_access`` — IDOR ratchet); the underlying
    read is project-scoped. No DB writes. Returns 404 when ``test_id`` doesn't
    belong to ``run_id``; an empty-window "insufficient history" report otherwise.
    """
    from app.services.runs_service import step_flip_report_for_test

    payload = await step_flip_report_for_test(db, run_id, test_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    return payload


@router.get("/{run_id}/step-flips")
async def get_run_step_flips(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """Run-level roll-up of cross-run step-flip (FLK-P6 slice 5).

    READ-ONLY. Aggregates the per-test cross-run step-flip across the run's
    fingerprint-anchored tests so a QA engineer triaging a whole run sees WHICH
    TESTS have a flickering step — without opening each one. Resolves the run's
    project via the PROVIDED ``run_id`` (verified by ``require_run_access`` — IDOR
    ratchet); the underlying read is project-scoped. No DB writes. Returns 404
    when ``run_id`` has no run; otherwise a roll-up listing only the tests with at
    least one step flip (``truncated`` flags a run larger than the analysis cap).
    """
    from app.services.runs_service import step_flip_report_for_run

    payload = await step_flip_report_for_run(db, run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Test run not found")
    return payload


@router.get("/{run_id}/regression-diff")
async def get_regression_diff(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: Any = Depends(require_run_access()),
):
    """
    Return a "What changed since last good run?" diff for the given test run.
    P3-9: Business logic extracted to regression_diff_service.

    In-flight live runs (no TestRun row yet — only a LiveSession) get a
    graceful in-progress payload instead of a 404. The frontend renders
    "Diff will be available once the run completes" rather than a
    broken error toast. (Bug 2026-05-19 — Run Detail page hit 404s for
    sessions clicked during the first ~30s before the drainer
    materialised the TestRun row.)
    """
    from app.services.regression_diff_service import compute_regression_diff

    run_result = await db.execute(select(TestRun).where(TestRun.id == run_id))
    run = run_result.scalar_one_or_none()
    if not run:
        from app.models.postgres import LiveSession
        live = (
            await db.execute(select(LiveSession).where(LiveSession.id == run_id))
        ).scalar_one_or_none()
        if live is None:
            raise HTTPException(status_code=404, detail="Test run not found")
        return {
            "run_id": str(run_id),
            "status": "in_progress",
            "diff_available": False,
            "reason": "live_run_in_progress",
            "message": (
                "This run is still streaming. The regression diff "
                "becomes available once the run finalises."
            ),
            "added": [],
            "removed": [],
            "flipped_to_failing": [],
            "flipped_to_passing": [],
        }

    return await compute_regression_diff(run, db)


@router.post("/{run_id}/release")
async def set_run_release(
    run_id: uuid.UUID,
    body: dict,
    db: AsyncSession = Depends(get_db),
    # QA_LEAD, matching the sibling POST /releases/{id}/test-runs. Before S1
    # this route only added a link; it now also DEMOTES whichever link was
    # primary, which decides what every release-scoped analytic reads. Leaving
    # it on a membership-only guard would let any project member override a
    # QA lead's deliberate attribution.
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_run_access()),
):
    release_name = (body.get("release_name") or "").strip()
    if not release_name:
        raise HTTPException(status_code=422, detail="release_name is required")

    run = (await db.execute(select(TestRun).where(TestRun.id == run_id))).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Test run not found")

    from app.services.release_linker import auto_link_release

    from app.models.postgres import LinkSource, ReleaseTestRunLink

    release, created = await auto_link_release(
        db=db,
        project_id=run.project_id,
        release_name=release_name,
        test_run_id=run.id,
        # A person typed this release name into the Run Detail control, so it
        # is an assertion rather than something the system worked out — and it
        # must outrank whatever ingest attributed automatically.
        link_source=LinkSource.MANUAL_UI.value,
        # Record WHO. Without this the row claims manual_ui provenance with no
        # actor, which is weaker evidence than it looks — the scorecard counts
        # it as asserted while nothing can say by whom.
        linked_by_id=current_user.id,
    )

    # Make the human's choice the one analytics reads. Without this the run
    # keeps its automatic link as primary and the release badge goes on showing
    # the old value, so the control appears to do nothing.
    await db.execute(
        update(ReleaseTestRunLink)
        .where(
            ReleaseTestRunLink.test_run_id == run.id,
            ReleaseTestRunLink.release_id != release.id,
            ReleaseTestRunLink.is_primary.is_(True),
        )
        .values(is_primary=False)
    )
    await db.execute(
        update(ReleaseTestRunLink)
        .where(
            ReleaseTestRunLink.test_run_id == run.id,
            ReleaseTestRunLink.release_id == release.id,
        )
        .values(is_primary=True)
    )
    # Fourth path that changes the primary link; the denormalized column has
    # to follow or the release badge and release-scoped analytics disagree.
    from app.services.release_linker import sync_primary_release

    await sync_primary_release(db, run.id)
    await db.commit()

    return {
        "release_id": str(release.id),
        "release_name": release.name,
        "release_status": release.status,
        "auto_created": created,
    }


@router.post("/{run_id}/recover-live", status_code=202)
async def recover_live_run_from_buffer(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """Re-enqueue ``persist_live_session`` for a live-stream run whose
    per-test rows never landed in PostgreSQL.

    Looks up the run, checks that it's a ``live_stream`` run with zero
    ``test_cases`` rows, then fires the persistence task with the buffered
    events still sitting in Redis (TTL 25h). Idempotent — the task itself
    re-checks whether work is already done before inserting.

    Returns 422 when the run isn't recoverable (already populated / not a
    live run / no buffer left in Redis).
    """
    from sqlalchemy import func
    from app.db.redis_client import get_redis
    from app.models.postgres import TestCase
    from app.worker.tasks import persist_live_session

    run = (await db.execute(select(TestRun).where(TestRun.id == run_id))).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Test run not found")
    if run.trigger_source != "live_stream":
        raise HTTPException(
            status_code=422,
            detail="Recovery is only available for live-stream runs.",
        )

    tc_count = (
        await db.execute(
            select(func.count(TestCase.id)).where(TestCase.test_run_id == run_id)
        )
    ).scalar() or 0
    if tc_count > 0:
        raise HTTPException(
            status_code=422,
            detail=f"Run already has {tc_count} test case rows — nothing to recover.",
        )

    # Recovery sources, in preference order:
    #   1. Live Redis buffer (25-hour TTL). Fresh path; what
    #      persist_live_session would normally read from.
    #   2. ``TestRun.event_archive`` (migration 0086) — durable copy
    #      written at close_session time, retained for 15 days. Lets
    #      users recover per-test rows on day 2+ of a run, well past
    #      the Redis TTL.
    # Either source produces the same event payload, so the existing
    # persist_live_session task does the actual materialisation — we
    # just stage the payload back into Redis when source 2 wins so the
    # task can keep its single read path.
    redis = get_redis()
    from app.services.live_run_recovery_service import (
        buffered_live_evidence_counts,
    )

    buffer_len, batch_count, _buffer_source = await buffered_live_evidence_counts(
        redis,
        str(run_id),
    )
    source = "redis"

    if not buffer_len:
        # Try the durable archive. The 15-day window is enforced here so
        # an expired archive surfaces a clear "expired" error rather than
        # a silent "succeeded but produced nothing".
        archive = list(run.event_archive or [])
        archived_at = run.event_archive_at
        age_days: Optional[float] = None
        if archived_at is not None:
            from datetime import datetime as _dt, timezone as _tz
            now = _dt.now(_tz.utc)
            age_days = (now - archived_at).total_seconds() / 86400.0

        if not archive or age_days is None or age_days > 15:
            # Neither Redis buffer nor archive available. Previously this
            # 422'd with a "re-run the suite" message — leaving the user
            # stuck on a run that shows aggregates but no per-test detail
            # AND no way to recover. The synthesis branch in
            # ``persist_live_session`` covers exactly this case: when
            # events are empty but final_state reports tests ran, it
            # synthesizes one placeholder row per reported test so the
            # run-detail page surfaces SOMETHING the user can interact
            # with. Queue the same task with no buffered events so the
            # synthesis fires. (Bug 2026-05-19.)
            total_reported = (
                int(run.passed_tests or 0)
                + int(run.failed_tests or 0)
                + int(run.skipped_tests or 0)
                + int(run.broken_tests or 0)
                + int(getattr(run, "unknown_tests", 0) or 0)
            )
            if total_reported <= 0:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "No buffered events found and no aggregates to "
                        "synthesise from. Re-run the suite, or re-ingest "
                        "the results as a file upload."
                    ),
                )
            # Fall through into the apply_async call below; ``buffer_len``
            # stays 0 and the task's synthesis branch generates
            # ``total_reported`` placeholder rows.
            source = "synthesis"
        else:
            # Stage into the durable Stream.  This remains visible even when a
            # previous consumer group created an empty stream key, which used
            # to mask a recovery LIST from the stream-first drainer.
            from app.services.live_run_recovery_service import (
                _stage_archive_to_redis,
            )

            buffer_len = await _stage_archive_to_redis(str(run_id), archive)
            batch_count = 1 if buffer_len else 0
            source = "archive"

    # Phase 2.4 — same per-project shard routing the close_session path
    # uses, so a manual recovery from the dashboard doesn't bypass the
    # fairness queue.
    from app.worker.ingestion_routing import queue_for_project
    persist_live_session.apply_async(
        kwargs={
            "run_id": str(run_id),
            "project_id": str(run.project_id),
            "build_number": run.build_number or str(run_id),
            "branch": run.branch or "",
            "commit_hash": run.commit_hash or "",
            "final_state": {
                "passed": run.passed_tests or 0,
                "failed": run.failed_tests or 0,
                "skipped": run.skipped_tests or 0,
                "broken": run.broken_tests or 0,
                "unknown": getattr(run, "unknown_tests", 0) or 0,
                "total": run.total_tests or 0,
            },
            "suite_name": run.primary_suite_name or None,
        },
        queue=queue_for_project(str(run.project_id)),
        priority=7,
    )
    if source == "synthesis":
        message = (
            "No buffered events available — synthesising placeholder rows "
            "from the run's aggregates. The per-test view will populate "
            "with marker rows so triage and navigation work; rerun the "
            "suite to capture real per-test detail."
        )
    else:
        message = (
            f"Persistence task queued. {buffer_len} buffered events will "
            "be materialised into TestCase rows."
        )
    return {
        "queued": True,
        "run_id": str(run_id),
        "buffered_events": buffer_len,
        "buffered_batches": batch_count,
        "source": source,
        "message": message,
    }


@router.delete("/{run_id}", status_code=202, response_model=DeleteRunAcceptedResponse)
async def delete_run(
    run_id: uuid.UUID,
    body: DeleteRunRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    _: User = Depends(require_run_access()),
):
    """Delete one run across all five stores. Irreversible.

    **202, not 204.** A synchronous delete of a multi-GB prefix times out at
    the gateway, and the Mongo -> MinIO -> Postgres ordering means the caller
    would get a 504 with the artifacts already gone and the run still listed.
    The work runs as a Celery job; poll it at
    ``GET /api/v1/retention/{project_id}/deletion/jobs/{job_id}``.

    Four guards, each a real hazard rather than defensive habit:

    * **In flight.** ``IN_PROGRESS`` is refused — the drainer and the persist
      task are writing to this run right now.
    * **Resurrection.** A tombstone is written in the same transaction as the
      row deletion, because five code paths re-create a ``TestRun`` from a
      caller-supplied id on a SELECT miss. Refusing ``IN_PROGRESS`` narrows
      that window; it does not close it.
    * **Citations.** A run cited by a compliance pack, linked to a release, or
      named by a decision report is refused — deleting it strands evidence
      whose subject no longer exists.
    * **Prefix scope.** ``minio_prefix`` is uploader-derived and unvalidated
      beyond non-empty. Prefixes outside the project's scope are refused and
      REPORTED, never deleted.
    """
    from app.db.mongo import get_mongo_db
    from app.services import deletion_job_service, run_deletion_service
    from app.services.run_tombstone_service import run_is_tombstoned
    from app.worker.tasks import delete_run_everywhere

    if not body.confirm:
        raise HTTPException(
            status_code=422,
            detail="confirm must be true — this deletes across five stores "
                   "and cannot be undone",
        )

    run = (
        await db.execute(select(TestRun).where(TestRun.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        # A tombstoned run is also 404: it is gone, and saying so differently
        # would leak that the id once existed.
        raise HTTPException(status_code=404, detail="Test run not found")

    if await run_is_tombstoned(db, run_id):
        # The row EXISTS and is tombstoned, which means something re-created it
        # despite the guards — the drainer's tombstone lookup fails open on a
        # database error, so this is reachable. Proceed with the delete: this
        # is exactly when an operator needs it to work. Returning 404 here
        # would leave a resurrected, dataless run that nothing could remove.
        logger.warning(
            "run_resurrection_detected run_id=%s project_id=%s",
            run_id, run.project_id,
        )

    if run_deletion_service.status_blocks_deletion(run.status):
        raise HTTPException(
            status_code=409,
            detail=(
                "Run is still executing. Deleting it now races the drainer "
                "and the persist task, which would re-create the row with "
                "none of its data. Stop the run first."
            ),
        )

    mongo = get_mongo_db()
    blockers = await run_deletion_service.citation_blockers(
        db, run_id=run_id, mongo=mongo
    )
    if blockers:
        raise HTTPException(
            status_code=409,
            detail={"run_id": str(run_id), "blockers": blockers},
        )

    _safe, refused = run_deletion_service.safe_artifact_prefixes(
        run.project_id, run_id, run.minio_prefix
    )

    job_id = await deletion_job_service.open_job(
        project_id=run.project_id,
        job_kind=deletion_job_service.KIND_SINGLE_ENTITY,
        criteria={"run_id": str(run_id), "reason": body.reason},
        requested_by_id=current_user.id,
    )

    delete_run_everywhere.delay(
        str(run_id),
        str(job_id) if job_id else None,
        body.reason,
        str(current_user.id),
    )

    return DeleteRunAcceptedResponse(
        job_id=job_id,
        run_id=run_id,
        refused_prefixes=refused,
    )
