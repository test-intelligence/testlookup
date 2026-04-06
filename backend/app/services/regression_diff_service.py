"""
Regression diff service — extracted from runs router (P3-9).

Computes a "What changed since last good run?" comparison between
the current test run and the most recent passing baseline.
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import TestCase, TestRun

logger = logging.getLogger("services.regression_diff")


async def compute_regression_diff(run: TestRun, db: AsyncSession) -> dict:
    """Compare a test run against its most recent passing baseline.

    Returns a dict with new failing tests, resolved count, pass rate delta,
    and optionally a commit range from GitHub.
    """
    run_id = run.id

    # Find last passing baseline run (pass_rate >= threshold * 0.85) before this run
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
    current_failed_fps = {
        r.test_fingerprint: {"test_name": r.test_name, "suite_name": r.suite_name}
        for r in current_failed.all()
        if r.test_fingerprint
    }

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
            pass  # Commit range is nice-to-have

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
