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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # Filter by accessible projects when no explicit project_id
    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None and not accessible:
            return {"items": [], "total": 0, "page": page, "size": size, "pages": 0}
        # Pass accessible set to service for filtering (None = admin, no filter)
        items, total, pages = await list_project_runs(db, project_id, page, size, status, release_id, accessible_project_ids=accessible)
    else:
        items, total, pages = await list_project_runs(db, project_id, page, size, status, release_id)
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

    Finds the most recent passing baseline run for the same project, then compares:
    - New failing tests (not failing in baseline)
    - Pass rate delta
    - Build number range
    - Commit range (from GitHub API if configured)
    """
    # Fetch current run
    run_result = await db.execute(select(TestRun).where(TestRun.id == run_id))
    run = run_result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Test run not found")

    from app.core.config import settings

    # Find last passing baseline run (pass_rate >= threshold) before this run
    baseline_result = await db.execute(
        select(TestRun)
        .where(
            TestRun.project_id == run.project_id,
            TestRun.id != run_id,
            TestRun.pass_rate >= settings.RELEASE_PASS_RATE_THRESHOLD * 0.85,
            TestRun.created_at < run.created_at,
        )
        .order_by(TestRun.created_at.desc())
        .limit(1)
    )
    baseline = baseline_result.scalar_one_or_none()

    if not baseline:
        return {
            "run_id": str(run_id),
            "baseline_run_id": None,
            "baseline_available": False,
            "message": "No recent passing baseline run found for this project",
        }

    # Get failing test fingerprints in current run
    current_failed = await db.execute(
        select(TestCase.test_fingerprint, TestCase.test_name, TestCase.suite_name)
        .where(TestCase.test_run_id == run_id, TestCase.status.in_(["FAILED", "BROKEN"]))
    )
    current_failed_fps = {r.test_fingerprint: {"test_name": r.test_name, "suite_name": r.suite_name}
                          for r in current_failed.all() if r.test_fingerprint}

    # Get failing test fingerprints in baseline run
    baseline_failed = await db.execute(
        select(TestCase.test_fingerprint)
        .where(TestCase.test_run_id == baseline.id, TestCase.status.in_(["FAILED", "BROKEN"]))
    )
    baseline_failed_fps = {r.test_fingerprint for r in baseline_failed.all() if r.test_fingerprint}

    # New failures = in current but not in baseline
    new_failing = [
        {"test_name": info["test_name"], "suite_name": info["suite_name"]}
        for fp, info in current_failed_fps.items()
        if fp not in baseline_failed_fps
    ][:50]

    # Resolved failures = in baseline but not in current
    resolved_count = len(baseline_failed_fps - set(current_failed_fps.keys()))

    pass_rate_delta = round((run.pass_rate or 0) - (baseline.pass_rate or 0), 2)

    # Fetch commit range from GitHub API if configured
    commits: list[dict] = []
    if settings.GITHUB_TOKEN and settings.GITHUB_REPO and run.start_time and baseline.end_time:
        try:
            from app.tools.fetch_build_changes import _fetch_github_commits
            commits = await _fetch_github_commits(
                repo=settings.GITHUB_REPO,
                since=baseline.end_time.isoformat(),
                until=run.start_time.isoformat(),
                token=settings.GITHUB_TOKEN,
            )
        except Exception:
            pass  # Commit range is nice-to-have, not critical

    return {
        "run_id": str(run_id),
        "baseline_run_id": str(baseline.id),
        "baseline_available": True,
        "build_number": run.build_number,
        "baseline_build_number": baseline.build_number,
        "pass_rate": run.pass_rate,
        "baseline_pass_rate": baseline.pass_rate,
        "pass_rate_delta": pass_rate_delta,
        "new_failing_tests": new_failing,
        "new_failing_count": len(new_failing),
        "resolved_count": resolved_count,
        "commit_range": commits,
        "commit_count": len(commits),
    }


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
