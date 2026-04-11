"""Test run and test case list endpoints."""
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_accessible_project_ids, get_current_active_user, require_run_access
from app.db.postgres import get_db
from app.models.postgres import TestCase, TestRun, User
from app.models.schemas import TestCaseListResponse
from app.services.runs_service import get_run_with_release, list_project_runs, list_run_test_cases

router = APIRouter(prefix="/api/v1/runs", tags=["Test Runs"])


@router.get("")
async def list_runs(
    project_id: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    release_id: str | None = None,
    days: int | None = Query(6, ge=0, le=365, description="Show runs from last N days (0 = all time)"),
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
        items, total, pages = await list_project_runs(db, project_id, page, size, status, release_id, accessible_project_ids=accessible, days=effective_days)
    else:
        items, total, pages = await list_project_runs(db, project_id, page, size, status, release_id, days=effective_days)
    return {"items": items, "total": total, "page": page, "size": size, "pages": pages}


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
    _: Any = Depends(get_current_active_user),
):
    """
    Return a "What changed since last good run?" diff for the given test run.
    P3-9: Business logic extracted to regression_diff_service.
    """
    from app.services.regression_diff_service import compute_regression_diff

    run_result = await db.execute(select(TestRun).where(TestRun.id == run_id))
    run = run_result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Test run not found")

    return await compute_regression_diff(run, db)


@router.post("/{run_id}/release")
async def set_run_release(
    run_id: uuid.UUID,
    body: dict,
    db: AsyncSession = Depends(get_db),
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
