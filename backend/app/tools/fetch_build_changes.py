"""
Tool: Fetch CI build changes (commits, changed files) from version control or CI system.
Used by FlakySentinelAgent to correlate flakiness onset with specific code changes.
"""
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from langchain_core.tools import tool

from app.core.config import settings
from app.db.postgres import AsyncSessionLocal

logger = logging.getLogger("tools.fetch_build_changes")


async def _fetch_github_commits(repo: str, since: str, until: str, token: str) -> list[dict]:
    """Fetch commits from GitHub API between two ISO timestamps."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{repo}/commits",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"},
                params={"since": since, "until": until, "per_page": 20},
            )
            if resp.status_code != 200:
                return []
            commits = resp.json()
            return [
                {
                    "sha": c["sha"][:8],
                    "message": c["commit"]["message"].split("\n")[0][:200],
                    "author": c["commit"]["author"]["name"],
                    "timestamp": c["commit"]["author"]["date"],
                    "files_changed": [],
                }
                for c in commits
            ]
    except Exception as exc:
        logger.debug("GitHub API call failed: %s", exc)
        return []


@tool
async def fetch_build_changes(params_json: str) -> str:
    """
    Fetch commits and changed files between the last-stable and first-flaky build.

    Input JSON keys:
      - test_fingerprint: fingerprint of the flaky test
      - stable_build_number: last build where test was consistently passing
      - flaky_build_number: first build where test started flaking
      - project_id: project ID for config lookup

    Returns: JSON with commits, changed_files, and a change_summary.
    """
    import json

    try:
        params = json.loads(params_json)
    except (json.JSONDecodeError, AttributeError) as exc:
        return json.dumps({"error": f"Invalid JSON: {exc}"})

    test_fingerprint: str = params.get("test_fingerprint", "")
    stable_build: str = params.get("stable_build_number", "")
    flaky_build: str = params.get("flaky_build_number", "")
    project_id: str = params.get("project_id", "")

    # Look up build timestamps from DB
    from sqlalchemy import select
    from app.models.postgres import TestRun
    async with AsyncSessionLocal() as db:
        runs: list[Any] = []
        if project_id:
            result = await db.execute(
                select(TestRun)
                .where(TestRun.project_id == project_id)
                .where(TestRun.build_number.in_([stable_build, flaky_build]))
                .order_by(TestRun.created_at)
            )
            runs = list(result.scalars().all())

    stable_run = next((r for r in runs if r.build_number == stable_build), None)
    flaky_run = next((r for r in runs if r.build_number == flaky_build), None)

    since_ts = stable_run.created_at.isoformat() if stable_run else datetime.now(timezone.utc).isoformat()
    until_ts = flaky_run.created_at.isoformat() if flaky_run else datetime.now(timezone.utc).isoformat()

    # Check if we have GitHub config in environment
    github_token = getattr(settings, "GITHUB_TOKEN", None)
    github_repo = getattr(settings, "GITHUB_REPO", None)
    commits: list[dict] = []

    if github_token and github_repo:
        commits = await _fetch_github_commits(github_repo, since_ts, until_ts, github_token)

    # Build change summary from available data
    if commits:
        change_summary = (
            f"Found {len(commits)} commits between stable build '{stable_build}' "
            f"and first-flaky build '{flaky_build}': "
            + "; ".join(f"[{c['sha']}] {c['message'][:80]} by {c['author']}" for c in commits[:5])
        )
    else:
        # Fall back to what we know from the run records
        stable_info = f"build {stable_build} at {since_ts}" if stable_build else "unknown"
        flaky_info = f"build {flaky_build} at {until_ts}" if flaky_build else "unknown"
        change_summary = (
            f"GitHub API not configured (set GITHUB_TOKEN + GITHUB_REPO). "
            f"Test '{test_fingerprint}' was stable at {stable_info} and first flaked at {flaky_info}. "
            f"Investigate commits and deployments in this window manually."
        )

    return json.dumps({
        "test_fingerprint": test_fingerprint,
        "stable_build": stable_build,
        "flaky_build": flaky_build,
        "commits": commits,
        "change_summary": change_summary,
        "time_window": {"since": since_ts, "until": until_ts},
    })
