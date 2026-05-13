"""Analytics endpoints: flaky tests, failure clusters, coverage, defects."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_accessible_project_ids, get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import User
from app.models.schemas import NotifyTestOwnerRequest, NotifyTestOwnerResponse
from app.services import analytics_service

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])

_EMPTY_LIST = {"items": [], "period_days": 0, "total": 0}


# ── Flaky Test Leaderboard ─────────────────────────────────────────────────

@router.get("/flaky-tests")
async def flaky_tests(
    project_id: str | None = None,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(20, ge=1, le=100),
    suite_name: str | None = Query(None, min_length=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return tests with highest flakiness rate (intermittent pass/fail pattern)."""
    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            return _EMPTY_LIST
    return await analytics_service.flaky_tests(db, project_id, days, limit, suite_name=suite_name)


# ── Failure Category Distribution ─────────────────────────────────────────

@router.get("/failure-categories")
async def failure_categories(
    project_id: str | None = None,
    days: int = Query(30, ge=1, le=365),
    suite_name: str | None = Query(None, min_length=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return distribution of failure categories for AI-analysed test cases."""
    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            return {"categories": [], "period_days": days}
    return await analytics_service.failure_categories(db, project_id, days, suite_name=suite_name)


# ── Top Failing Tests ──────────────────────────────────────────────────────

@router.get("/top-failing")
async def top_failing_tests(
    project_id: str | None = None,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(15, ge=1, le=50),
    suite_name: str | None = Query(None, min_length=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return tests with the highest total failure count in the period."""
    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            return _EMPTY_LIST
    return await analytics_service.top_failing_tests(db, project_id, days, limit, suite_name=suite_name)


# ── Coverage Snapshot ──────────────────────────────────────────────────────

@router.get("/coverage")
async def coverage_stats(
    project_id: str | None = None,
    days: int = Query(30, ge=1, le=365),
    suite_name: str | None = Query(None, min_length=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return test suite coverage stats aggregated over the period."""
    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            return {"suites": [], "period_days": days, "total_suites": 0}
    return await analytics_service.coverage_stats(db, project_id, days, suite_name=suite_name)


# ── Suite Detail ───────────────────────────────────────────────────────────

@router.get("/suite-detail")
async def suite_detail(
    project_id: str | None = None,
    suite_name: str = "",
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Return detailed breakdown for a single test suite:
      - Summary KPIs (unique tests, executions, pass rate, avg duration)
      - Per-test-case aggregates with flakiness flag
      - Last 10 test runs that included this suite
    """
    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            return {"summary": {}, "tests": [], "recent_runs": []}
    return await analytics_service.suite_detail(db, project_id, suite_name, days)


# ── Defects List ───────────────────────────────────────────────────────────

@router.get("/defects")
async def list_defects(
    project_id: str | None = None,
    resolution_status: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return defects for a project with optional resolution status filter."""
    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            return {"items": [], "total": 0, "page": page, "size": size, "pages": 0}
    return await analytics_service.list_defects(db, project_id, resolution_status, page, size)


# ── AI Analysis Summary ────────────────────────────────────────────────────

@router.get("/ai-summary")
async def ai_analysis_summary(
    project_id: str | None = None,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return summary of AI analysis results for the project."""
    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            return {"summary": {}, "period_days": days}
    return await analytics_service.ai_analysis_summary(db, project_id, days)


# ── Notify suite owner about a recurring failure ───────────────────────────


@router.post("/notify-owner", response_model=NotifyTestOwnerResponse)
async def notify_suite_owner(
    payload: NotifyTestOwnerRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Fire an email at the suite owner of ``payload.test_name`` so they can
    triage the recurring failure. Resolution chain mirrors the suite-owner
    review feature: explicit ``test_suite_owners`` row → ``Project.manager_user_id``.
    Returns ``{queued: false, reason}`` when no owner can be resolved instead
    of erroring, so the UI can show a clear actionable message.
    """
    # Tenant isolation: non-admin callers must be members of the project.
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None and payload.project_id not in accessible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this project",
        )

    from app.models.postgres import Project
    from app.services.suite_review_service import resolve_test_to_suite_owner

    resolution = await resolve_test_to_suite_owner(
        db,
        payload.project_id,
        payload.test_name,
        payload.days,
    )
    if resolution["latest_run_id"] is None:
        return NotifyTestOwnerResponse(
            queued=False,
            reason=(
                f"\"{payload.test_name}\" has no failing runs in the last "
                f"{payload.days} days — nothing to notify on."
            ),
        )

    owner: User | None = resolution["owner"]
    if owner is None or not owner.email:
        return NotifyTestOwnerResponse(
            queued=False,
            suite_name=resolution["suite_name"],
            reason=(
                "No suite owner is configured for this test, and the project "
                "doesn't have a fallback project manager set. Assign an owner "
                "from /test-management → Test Suites or set a project manager."
            ),
        )

    project = (
        await db.execute(select(Project).where(Project.id == payload.project_id))
    ).scalar_one_or_none()

    from app.worker.tasks import notify_test_suite_owner as _task

    _task.delay(
        to_email=owner.email,
        owner_name=owner.full_name or owner.username,
        test_name=payload.test_name,
        suite_name=resolution["suite_name"],
        fail_count=payload.fail_count,
        days=payload.days,
        project_id=str(payload.project_id),
        project_name=project.name if project else None,
        latest_run_id=(
            str(resolution["latest_run_id"]) if resolution["latest_run_id"] else None
        ),
        latest_run_build=resolution["latest_run_build"],
        is_fallback_owner=resolution["is_fallback"],
        triggered_by=current_user.full_name or current_user.username or current_user.email,
    )

    return NotifyTestOwnerResponse(
        queued=True,
        sent_to=owner.email,
        owner_name=owner.full_name or owner.username,
        suite_name=resolution["suite_name"],
        is_fallback_owner=resolution["is_fallback"],
    )
