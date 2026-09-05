"""
Test Health Coach Router.

Endpoints for per-run test health findings and project-level flaky coaching.
"""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.core.deps import (
    get_current_active_user,
    require_project_access,
    require_role,
    require_run_access,
    resolve_release_query_scope,
)
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import Project, TestRun, User, UserRole
from app.models.schemas import FlakyCoachResponse, TestHealthResponse
from app.services.test_health_coach_service import (
    get_flaky_coach,
    get_run_test_health,
    refresh_flaky_coach,
)

logger = logging.getLogger("routers.test_health")

router = APIRouter(prefix="/api/v1", tags=["Test Health"])


@router.get("/runs/{run_id}/test-health", response_model=TestHealthResponse)
async def get_test_health(
    run_id: uuid.UUID,
    current_user: User = Depends(require_run_access()),
):
    """
    Get test health findings for a specific run.
    Returns anti-pattern analysis, health scores, and stabilization recommendations.
    """
    async with AsyncSessionLocal() as db:
        # Verify run exists
        run_result = await db.execute(select(TestRun).where(TestRun.id == run_id))
        run = run_result.scalar_one_or_none()
        if not run:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Test run not found.",
            )
        return await get_run_test_health(run_id, db)


@router.get("/projects/{project_id}/flaky-coach", response_model=FlakyCoachResponse)
async def get_project_flaky_coach(
    project_id: uuid.UUID,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
    # Selects WHICH ranked tests are shown. It does NOT rescope the impact
    # score — see the `scope` block in the response.
    release_id: Optional[str] = Query(
        None, description="Only flaky tests that ran in this release."
    ),
):
    """
    Get project-level flaky test leaderboard with quarantine recommendations.
    Ranked by impact score (failure_rate × frequency).
    """
    async with AsyncSessionLocal() as db:
        # Verify project exists
        proj_result = await db.execute(select(Project).where(Project.id == project_id))
        if not proj_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found.",
            )
        release = await resolve_release_query_scope(db, release_id, current_user)
        return await get_flaky_coach(
            project_id, db, days=days, limit=limit, release_id=release
        )


@router.post("/projects/{project_id}/flaky-coach/refresh")
async def refresh_project_flaky_coach(
    project_id: uuid.UUID,
    days: int = Query(default=30, ge=1, le=365),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
    _: User = Depends(require_project_access()),
):
    """
    Trigger a refresh of the flaky coach data for a project.
    Recomputes quarantine recommendations from test case history.
    """
    async with AsyncSessionLocal() as db:
        proj_result = await db.execute(select(Project).where(Project.id == project_id))
        if not proj_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found.",
            )
        count = await refresh_flaky_coach(project_id, db, days=days)
        await db.commit()

    return {"status": "completed", "flaky_tests_found": count}
