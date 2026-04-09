"""Analytics endpoints: flaky tests, failure clusters, coverage, defects."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_accessible_project_ids, get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import User
from app.services import analytics_service

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])

_EMPTY_LIST = {"items": [], "period_days": 0, "total": 0}


# ── Flaky Test Leaderboard ─────────────────────────────────────────────────

@router.get("/flaky-tests")
async def flaky_tests(
    project_id: str | None = None,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return tests with highest flakiness rate (intermittent pass/fail pattern)."""
    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            return _EMPTY_LIST
    return await analytics_service.flaky_tests(db, project_id, days, limit)


# ── Failure Category Distribution ─────────────────────────────────────────

@router.get("/failure-categories")
async def failure_categories(
    project_id: str | None = None,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return distribution of failure categories for AI-analysed test cases."""
    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            return {"categories": [], "period_days": days}
    return await analytics_service.failure_categories(db, project_id, days)


# ── Top Failing Tests ──────────────────────────────────────────────────────

@router.get("/top-failing")
async def top_failing_tests(
    project_id: str | None = None,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(15, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return tests with the highest total failure count in the period."""
    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            return _EMPTY_LIST
    return await analytics_service.top_failing_tests(db, project_id, days, limit)


# ── Coverage Snapshot ──────────────────────────────────────────────────────

@router.get("/coverage")
async def coverage_stats(
    project_id: str | None = None,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return test suite coverage stats aggregated over the period."""
    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            return {"suites": [], "period_days": days, "total_suites": 0}
    return await analytics_service.coverage_stats(db, project_id, days)


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
