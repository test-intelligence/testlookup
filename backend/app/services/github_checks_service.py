"""
GitHub Checks API integration — Tier 1 item 5.

Outbound integration that posts a check run to a commit SHA every time
TestLookup ingests a test run that has a GitHub-recognizable commit. The
check appears next to the PR's CI results and deep-links back to Run
Intelligence, giving developers a single place to see "did my PR pass
TestLookup's gate".

Design notes
------------

* **Per-project config** — the ``github_integrations`` table holds repo
  owner/name/API-base. The PAT lives in ``secret_service`` so a DB
  dump cannot recover tokens.

* **Offline-mode gate** — when ``AI_OFFLINE_MODE=true`` the service is a
  silent no-op. Air-gapped customers won't leak egress traffic if the
  feature flag gets flipped on accidentally.

* **Feature flag** — ``github_checks`` gates the whole subsystem. When
  off, every entry point returns early before touching the DB.

* **Retry + circuit breaker** — delegates to ``services/resilience``
  for exponential backoff on 5xx / network errors.

* **Never raises** from the public entry point. Ingestion pipeline
  triggers this after every run and a GitHub outage must not block
  test-case persistence. Errors land in ``last_error`` so Integration
  Health surfaces them instead.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import GitHubIntegration, Project, TestRun, User

logger = structlog.get_logger("services.github_checks")

# ``secret_service`` scope under which we store the PAT. Consumers must
# use the exact string — it becomes part of the encryption key namespace.
SECRET_SCOPE = "github_integration"


def _secret_key(project_id: uuid.UUID | str) -> str:
    return f"project:{project_id}:pat"


# Commit-SHA recognizer. Rejects short SHAs because the Checks API
# requires a full 40-char SHA on the target commit.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class GitHubChecksDisabledError(RuntimeError):
    """Raised by sync helpers when the feature flag is off."""


# ── Feature-flag + offline gate ────────────────────────────────────────────


async def _post_allowed(db: Optional[AsyncSession] = None) -> bool:
    """Compound gate: feature flag ON and offline-mode OFF.

    ``AI_OFFLINE_MODE`` is the hard kill switch for every outbound
    integration — we respect it regardless of feature flag state so
    air-gapped customers don't accidentally egress traffic when an
    admin toggles ``github_checks`` on.
    """
    if settings.AI_OFFLINE_MODE:
        return False
    try:
        from app.services.feature_flags import is_enabled
        return await is_enabled("github_checks", db=db)
    except Exception as exc:
        logger.debug("github_checks flag check failed", error=str(exc))
        return False


# ── Config CRUD ────────────────────────────────────────────────────────────


async def get_integration(
    db: AsyncSession, project_id: uuid.UUID,
) -> Optional[GitHubIntegration]:
    result = await db.execute(
        select(GitHubIntegration).where(GitHubIntegration.project_id == project_id)
    )
    return result.scalar_one_or_none()


async def upsert_integration(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: User,
    enabled: bool,
    repo_owner: str,
    repo_name: str,
    api_base_url: str,
    pat: Optional[str],
) -> GitHubIntegration:
    """Create or update a project's GitHub integration.

    ``pat`` semantics:

    * ``None``   — leave the existing secret alone.
    * ``""``     — clear the stored secret + flip ``has_pat=False``.
    * any value  — upsert the secret + flip ``has_pat=True``.
    """
    row = await get_integration(db, project_id)
    if row is None:
        row = GitHubIntegration(
            project_id=project_id,
            repo_owner=repo_owner,
            repo_name=repo_name,
            api_base_url=api_base_url.rstrip("/"),
            enabled=enabled,
            updated_by_user_id=actor.id,
        )
        db.add(row)
    else:
        row.repo_owner = repo_owner
        row.repo_name = repo_name
        row.api_base_url = api_base_url.rstrip("/")
        row.enabled = enabled
        row.updated_by_user_id = actor.id
        row.updated_at = datetime.now(timezone.utc)

    # Token handling — delegated to secret_service so we don't store the
    # plaintext on the DB row.
    if pat is not None:
        from app.services import secret_service
        if pat == "":
            # Explicit clear — mark has_pat False and (best-effort) blank
            # the secret value. secret_service doesn't have a delete_secret
            # helper today so we overwrite with empty and flip the flag.
            await secret_service.store_secret(
                db, SECRET_SCOPE, _secret_key(project_id), "", actor.id,
            )
            row.has_pat = False
        else:
            await secret_service.store_secret(
                db, SECRET_SCOPE, _secret_key(project_id), pat, actor.id,
            )
            row.has_pat = True

    await db.commit()
    await db.refresh(row)

    # Audit.
    try:
        from app.models.postgres import SettingsAuditLog
        entry = SettingsAuditLog(
            setting_key=f"github_integration:{project_id}",
            action="update",
            actor_id=actor.id,
            actor_name=getattr(actor, "username", None) or getattr(actor, "email", None),
            changed_fields=["repo_owner", "repo_name", "api_base_url", "enabled"]
            + (["pat"] if pat is not None else []),
        )
        db.add(entry)
        await db.commit()
    except Exception as exc:
        logger.warning("github integration audit log failed", error=str(exc))

    return row


# ── Connection test ────────────────────────────────────────────────────────


async def test_connection(
    db: AsyncSession, project_id: uuid.UUID,
) -> dict[str, Any]:
    """Probe the configured GitHub repo to confirm the PAT works.

    Runs even when the ``github_checks`` feature flag is off so admins
    can set things up before enabling. Still respects ``AI_OFFLINE_MODE``
    as a hard gate.
    """
    if settings.AI_OFFLINE_MODE:
        return {
            "success": False,
            "status_code": None,
            "message": "AI_OFFLINE_MODE is enabled — outbound calls are disabled",
            "repo_html_url": None,
        }

    row = await get_integration(db, project_id)
    if row is None:
        return {
            "success": False,
            "status_code": None,
            "message": "No GitHub integration configured for this project",
            "repo_html_url": None,
        }

    from app.services import secret_service
    pat = await secret_service.read_secret(db, SECRET_SCOPE, _secret_key(project_id))
    if not pat:
        return {
            "success": False,
            "status_code": None,
            "message": "No PAT stored for this integration",
            "repo_html_url": None,
        }

    url = f"{row.api_base_url.rstrip('/')}/repos/{row.repo_owner}/{row.repo_name}"
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "TestLookup/1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
    except Exception as exc:
        return {
            "success": False,
            "status_code": None,
            "message": f"Network error: {exc}",
            "repo_html_url": None,
        }

    if resp.status_code == 200:
        body = resp.json() if resp.content else {}
        return {
            "success": True,
            "status_code": 200,
            "message": f"OK — repo '{body.get('full_name', '?')}' reachable",
            "repo_html_url": body.get("html_url"),
        }
    return {
        "success": False,
        "status_code": resp.status_code,
        "message": f"GitHub returned {resp.status_code}: {resp.text[:200]}",
        "repo_html_url": None,
    }


# ── Check run posting ──────────────────────────────────────────────────────


def _format_check_summary(run: TestRun, project_name: Optional[str]) -> dict[str, Any]:
    """Build the ``check_run`` payload from a TestRun row.

    The Checks API accepts a name, status, conclusion, and a
    ``output`` block with title/summary/text rendered as markdown.
    Keeps the summary short because GitHub truncates at 4 KB per field.
    """
    total = int(run.total_tests or 0)
    passed = int(run.passed_tests or 0)
    failed = int(run.failed_tests or 0)
    skipped = int(run.skipped_tests or 0)
    broken = int(run.broken_tests or 0)
    pass_rate = float(run.pass_rate or 0)

    if failed == 0 and broken == 0:
        conclusion = "success"
        title_icon = "✅"
    elif failed > 0 or broken > 0:
        conclusion = "failure"
        title_icon = "❌"
    else:
        conclusion = "neutral"
        title_icon = "ℹ️"

    # Deep link back to Run Intelligence. Falls back to relative path if
    # the public base URL isn't configured — the customer's GitHub will
    # render it as plain text rather than a link.
    base_url = (getattr(settings, "PUBLIC_BASE_URL", "") or "").rstrip("/")
    intel_path = f"/intelligence/{run.id}"
    deep_link = f"{base_url}{intel_path}" if base_url else intel_path

    proj_label = f"{project_name}" if project_name else "TestLookup"
    title = f"{title_icon} {proj_label} — {passed}/{total} passed ({pass_rate:.1f}%)"

    summary_lines = [
        f"**Build:** {run.build_number or run.id}",
        f"**Branch:** {run.branch or '—'}",
        f"**Passed:** {passed}    **Failed:** {failed}    **Broken:** {broken}    **Skipped:** {skipped}",
        f"**Pass rate:** {pass_rate:.1f}%",
        "",
        f"[→ Open Run Intelligence]({deep_link})",
    ]

    return {
        "name": f"TestLookup · {proj_label}",
        "status": "completed",
        "conclusion": conclusion,
        "completed_at": (run.end_time or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z"),
        "output": {
            "title": title,
            "summary": "\n".join(summary_lines),
        },
        "details_url": deep_link if base_url else None,
    }


async def post_check_run_for_run(run_id: uuid.UUID) -> Optional[dict[str, Any]]:
    """Post a check run to GitHub for a completed test run.

    Best-effort — always returns a dict describing what happened but
    never raises. When the integration is disabled / offline / missing
    config, returns ``{"skipped": "<reason>"}``.

    The caller (ingestion_pipeline.finalize_run) treats every non-None
    return value as informational and never fails the pipeline on it.
    """
    if not await _post_allowed():
        return {"skipped": "feature_flag_off_or_offline_mode"}

    async with AsyncSessionLocal() as db:
        run_result = await db.execute(select(TestRun).where(TestRun.id == run_id))
        run = run_result.scalar_one_or_none()
        if run is None:
            return {"skipped": "run_not_found"}
        if not run.commit_hash or not _SHA_RE.match(run.commit_hash):
            return {"skipped": "no_full_commit_sha"}

        integration = await get_integration(db, run.project_id)
        if integration is None or not integration.enabled:
            return {"skipped": "integration_disabled_or_missing"}

        from app.services import secret_service
        pat = await secret_service.read_secret(
            db, SECRET_SCOPE, _secret_key(run.project_id),
        )
        if not pat:
            return {"skipped": "no_pat_configured"}

        # Pull the project name for a more readable check title.
        project_name: Optional[str] = None
        try:
            proj_row = await db.execute(
                select(Project.name).where(Project.id == run.project_id)
            )
            project_name = proj_row.scalar_one_or_none()
        except Exception:
            project_name = None

        payload = _format_check_summary(run, project_name)
        payload["head_sha"] = run.commit_hash

        url = (
            f"{integration.api_base_url.rstrip('/')}"
            f"/repos/{integration.repo_owner}/{integration.repo_name}/check-runs"
        )
        headers = {
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "TestLookup/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async def _do_post() -> httpx.Response:
            async with httpx.AsyncClient(timeout=15.0) as client:
                return await client.post(url, headers=headers, json=payload)

        from app.services.resilience import async_retry
        try:
            resp: httpx.Response = await async_retry(
                _do_post,
                max_retries=3,
                base_delay=1.0,
                operation_name="github_checks_post",
            )
        except Exception as exc:
            integration.last_error = f"{type(exc).__name__}: {str(exc)[:480]}"
            integration.last_error_at = datetime.now(timezone.utc)
            await db.commit()
            logger.warning(
                "github_checks post failed",
                project_id=str(run.project_id),
                run_id=str(run_id),
                error=str(exc),
            )
            return {"error": str(exc)}

        if resp.status_code in (200, 201):
            integration.last_posted_at = datetime.now(timezone.utc)
            integration.last_error = None
            integration.last_error_at = None
            await db.commit()
            logger.info(
                "github_checks posted",
                project_id=str(run.project_id),
                run_id=str(run_id),
                status_code=resp.status_code,
                commit_sha=run.commit_hash[:8],
            )
            return {
                "posted": True,
                "status_code": resp.status_code,
                "check_run_id": (resp.json() or {}).get("id") if resp.content else None,
            }

        # Non-success from GitHub — record the error so Integration Health
        # shows it and the user knows what to fix.
        integration.last_error = f"HTTP {resp.status_code}: {resp.text[:480]}"
        integration.last_error_at = datetime.now(timezone.utc)
        await db.commit()
        logger.warning(
            "github_checks rejected",
            project_id=str(run.project_id),
            run_id=str(run_id),
            status_code=resp.status_code,
        )
        return {"error": f"HTTP {resp.status_code}", "status_code": resp.status_code}
