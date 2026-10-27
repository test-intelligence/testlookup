"""
Tool: Fetch CI build changes (commits, changed files) from version control or CI system.
Used by FlakySentinelAgent to correlate flakiness onset with specific code changes.

W10 FAIL-09: the commits come from the PROJECT's GitHub / GitHub Enterprise
integration (Settings > GitHub), resolved by ``github_target.resolve_github_target``
with the gate ON (offline mode, the ``github_checks`` flag) on the tool's short
session, which is closed before the one HTTP call. The env ``GITHUB_TOKEN`` /
``GITHUB_REPO`` are not read here any more (they bypassed the egress guard and
the offline gate); ``github_status`` in the answer says why no commits were
fetched.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from langchain_core.tools import tool

from app.core.http_client import get_http_client
from app.db.postgres import AsyncSessionLocal
from app.services.github_target import GitHubTarget, resolve_github_target
from app.services.url_safety import egress_block_reason

logger = logging.getLogger("tools.fetch_build_changes")

#: The no-commit summary's words for each ``github_target`` reason.
_UNAVAILABLE_TEXT = {
    "offline_mode": "offline mode",
    "github_checks_off": "GitHub checks are turned off for this project",
    "no_integration": "no GitHub integration",
    "integration_disabled": "the project's GitHub integration is turned off",
    "repo_mismatch": "the run's repository is not the project's GitHub repository",
    "no_token": "no GitHub token is stored for this project",
}


async def _fetch_github_commits(target: GitHubTarget, since: str, until: str) -> list[dict]:
    """Commits of the project's repository between two ISO timestamps.

    Plain connector coordinates, NO session. The base is checked by the egress
    guard before the PAT is sent; any failure answers ``[]``."""
    url = f"{target.api_base}/repos/{target.repo}/commits"
    try:
        block = await egress_block_reason(url)
        if block:
            logger.info("GitHub commits refused: target host is not allowed (%s)", block)
            return []
        client = get_http_client()
        resp = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {target.pat}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params={"since": since, "until": until, "per_page": 20},
            timeout=15.0,
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
        logger.debug("GitHub API call failed: %s", type(exc).__name__)
        return []


async def _fetch_gitlab_commits(base_url: str, project_path: str, pat: str, since: str, until: str) -> list[dict]:
    """W19 INT-05: the GitLab branch — ``/repository/commits?since=&until=``; same shape, any failure ``[]``."""
    from app.services.gitlab_integration_service import _api_root, _project_ref

    url = f"{_api_root(base_url)}/projects/{_project_ref(project_path)}/repository/commits"
    try:
        block = await egress_block_reason(url)
        if block:
            logger.info("GitLab commits refused: target host is not allowed (%s)", block)
            return []
        client = get_http_client()
        resp = await client.get(url, headers={"PRIVATE-TOKEN": pat, "Accept": "application/json"},
                                params={"since": since, "until": until, "per_page": 20}, timeout=15.0)
        if resp.status_code != 200:
            return []
        return [
            {
                "sha": str(c.get("id") or "")[:8],
                "message": str(c.get("title") or c.get("message") or "").split("\n")[0][:200],
                "author": c.get("author_name"),
                "timestamp": c.get("committed_date") or c.get("created_at"),
                "files_changed": [],
            }
            for c in resp.json() if isinstance(c, dict)
        ]
    except Exception as exc:
        logger.debug("GitLab API call failed: %s", type(exc).__name__)
        return []


def _project_uuid(project_id: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(project_id))
    except (TypeError, ValueError):
        return None


@tool
async def fetch_build_changes(params_json: str) -> str:
    """
    Fetch commits and changed files between the last-stable and first-flaky build.

    Input JSON keys:
      - test_fingerprint: fingerprint of the flaky test
      - stable_build_number: last build where test was consistently passing
      - flaky_build_number: first build where test started flaking
      - project_id: project ID for config lookup

    Returns: JSON with commits, changed_files, github_status and a change_summary.
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
    pid = _project_uuid(project_id) if project_id else None

    # Look up build timestamps and the project's GitHub target from the DB,
    # on a short session that is closed before any HTTP.
    from sqlalchemy import select
    from app.models.postgres import TestRun
    runs: list[Any] = []
    target: Optional[GitHubTarget] = None
    gitlab: Optional[tuple[str, str, str]] = None  # W19 INT-05: (base_url, project_path, pat)
    github_status: Optional[str] = "no_integration"
    async with AsyncSessionLocal() as db:
        if pid is not None:
            result = await db.execute(
                select(TestRun)
                .where(TestRun.project_id == pid)
                .where(TestRun.build_number.in_([stable_build, flaky_build]))
                .order_by(TestRun.created_at)
            )
            runs = list(result.scalars().all())
            stable = next((r for r in runs if r.build_number == stable_build), None)
            flaky = next((r for r in runs if r.build_number == flaky_build), None)
            ci_repo = getattr(flaky, "ci_repo", None) or getattr(stable, "ci_repo", None)
            resolution = await resolve_github_target(db, pid, ci_repo=ci_repo)
            target, github_status = resolution.target, resolution.reason
            if target is None:
                # W19 INT-05: no usable GitHub target -> the project's GitLab (its own gate).
                from app.services import gitlab_integration_service as gl

                if await gl._post_allowed(db, project_id=pid):
                    t = await gl.load_gitlab_target(db, pid)
                    if not t.reason and t.pat and t.base_url and t.project_path:
                        gitlab = (t.base_url, t.project_path, t.pat)

    stable_run = next((r for r in runs if r.build_number == stable_build), None)
    flaky_run = next((r for r in runs if r.build_number == flaky_build), None)

    since_ts = stable_run.created_at.isoformat() if stable_run else datetime.now(timezone.utc).isoformat()
    until_ts = flaky_run.created_at.isoformat() if flaky_run else datetime.now(timezone.utc).isoformat()

    commits: list[dict] = await _fetch_github_commits(target, since_ts, until_ts) if target else []
    if not target and gitlab:
        commits = await _fetch_gitlab_commits(*gitlab, since_ts, until_ts)
        github_status = None if commits else github_status

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
        why = (
            f"GitHub commits unavailable for this project ({_UNAVAILABLE_TEXT.get(github_status, github_status)})."
            if github_status
            else "No GitHub commits were found in this window."
        )
        change_summary = (
            f"{why} "
            f"Test '{test_fingerprint}' was stable at {stable_info} and first flaked at {flaky_info}. "
            f"Investigate commits and deployments in this window manually."
        )

    return json.dumps({
        "test_fingerprint": test_fingerprint,
        "stable_build": stable_build,
        "flaky_build": flaky_build,
        "commits": commits,
        "github_status": github_status,
        "change_summary": change_summary,
        "time_window": {"since": since_ts, "until": until_ts},
    })
