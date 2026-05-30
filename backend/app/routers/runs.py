"""Test run and test case list endpoints."""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_accessible_project_ids, get_current_active_user, require_run_access
from app.db.postgres import get_db
from app.models.postgres import LaunchStatus, TestCase, TestRun, User
from app.models.schemas import TestCaseListResponse
from app.services.runs_service import get_run_with_release, list_project_runs, list_run_test_cases

router = APIRouter(prefix="/api/v1/runs", tags=["Test Runs"])


@router.get("")
async def list_runs(
    project_id: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=500),
    status: str | None = None,
    release_id: str | None = None,
    days: int | None = Query(6, ge=0, le=365, description="Show runs from last N days (0 = all time)"),
    suite_name: str | None = Query(None, min_length=1, description="Filter runs by suite name, case-insensitive"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # days=0 means no date filter (all time)
    effective_days = days if days and days > 0 else None
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
        items, total, pages = await list_project_runs(
            db,
            project_id,
            page,
            size,
            status,
            release_id,
            days=effective_days,
            suite_name=suite_name,
        )
    return {"items": items, "total": total, "page": page, "size": size, "pages": pages}


@router.get("/failed-ids")
async def list_failed_run_ids(
    project_id: str | None = None,
    days: int | None = Query(6, ge=0, le=365, description="Look back window in days (0 = all time)"),
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
    if cutoff is not None:
        stmt = stmt.where(TestRun.created_at >= cutoff)

    if project_id:
        try:
            stmt = stmt.where(TestRun.project_id == uuid.UUID(project_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid project_id") from exc
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

    stmt = stmt.order_by(TestRun.created_at.desc()).limit(limit + 1)

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
    _: User = Depends(require_run_access()),
):
    release_name = (body.get("release_name") or "").strip()
    if not release_name:
        raise HTTPException(status_code=422, detail="release_name is required")

    run = (await db.execute(select(TestRun).where(TestRun.id == run_id))).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Test run not found")

    from app.services.release_linker import auto_link_release

    release, created = await auto_link_release(
        db=db,
        project_id=run.project_id,
        release_name=release_name,
        test_run_id=run.id,
    )
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
    from app.streams import LIVE_TESTCASES_KEY
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
    list_key = LIVE_TESTCASES_KEY.format(run_id=str(run_id))
    buffer_len = await redis.llen(list_key)
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
            # Stage archived events back into Redis so persist_live_session
            # can read from its usual location. Use a short TTL so staging
            # keys don't pile up; the task drains them and deletes the key
            # itself on success.
            import json as _json
            for ev in archive:
                await redis.rpush(list_key, _json.dumps(ev))
            await redis.expire(list_key, 3600)  # 1h is plenty for the worker
            buffer_len = len(archive)
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
        "source": source,
        "message": message,
    }
