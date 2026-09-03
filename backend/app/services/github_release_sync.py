"""Sync GitHub milestones into releases, and resolve a run's release from git.

Two jobs, both read-only against GitHub.

**Sync** pulls milestones into ``releases`` so a team whose releases live in
GitHub does not maintain them twice. **Resolution** is ladder rung 2: a run
already carries ``commit_hash``, ``branch``, ``ci_repo`` and ``pr_number``, and
that is enough to find the release it belongs to without asking the CI job to
send anything new.

Why GitHub first, ahead of Jira
-------------------------------
``github_checks_service`` already establishes everything a sync needs: a
per-project PAT in the secret service, a GHE-capable configurable base URL, an
SSRF guard on that base, the ``AI_OFFLINE_MODE`` kill switch, and a pre-enable
connection test. Jira has none of it — its client is write-only and configured
from one app-level setting — so proving the sync contract here costs a reader,
and proving it there would cost a client.

The guard that is easy to skip
------------------------------
``api_base_url`` is QA_LEAD-configurable and the project's PAT is sent to it.
That makes an unguarded base an SSRF read primitive regardless of HTTP verb —
the risk lives in where the credential goes, not in whether anything is
written. It is easier to forget on a read path precisely because nothing is
being mutated, so every request here runs through the same
``_ssrf_block_reason`` the write path uses.

Nothing here commits. Callers own their transaction.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import Release
from app.services.github_checks_service import (
    SECRET_SCOPE,
    _secret_key,
    _ssrf_block_reason,
    get_integration,
)

logger = structlog.get_logger(__name__)

SOURCE_SYSTEM = "github"

#: GitHub caps page size at 100. One page is deliberate: a project with more
#: than 100 milestones has a process problem that paging would hide, and an
#: unbounded loop against a remote API is a hang waiting to happen.
_PAGE_SIZE = 100

_TIMEOUT = httpx.Timeout(10.0)


class GitHubSyncUnavailable(RuntimeError):
    """Sync cannot run: offline mode, no integration, or no credential."""


async def _authorized_get(
    db: AsyncSession,
    project_id: uuid.UUID,
    path: str,
    params: Optional[dict] = None,
) -> Any:
    """GET a GitHub path for *project_id*, or raise ``GitHubSyncUnavailable``.

    Every outbound request funnels through here so the three gates cannot be
    bypassed by a future caller: offline mode, a stored credential, and the
    SSRF check on the configurable base.
    """
    if settings.AI_OFFLINE_MODE:
        # The hard kill switch. An air-gapped deployment must not egress
        # because somebody enabled an integration.
        raise GitHubSyncUnavailable("AI_OFFLINE_MODE is enabled")

    row = await get_integration(db, project_id)
    if row is None or not row.enabled:
        raise GitHubSyncUnavailable("No enabled GitHub integration for this project")

    from app.services import secret_service

    pat = await secret_service.read_secret(db, SECRET_SCOPE, _secret_key(project_id))
    if not pat:
        raise GitHubSyncUnavailable("No PAT stored for this integration")

    base = row.api_base_url.rstrip("/")
    url = f"{base}/repos/{row.repo_owner}/{row.repo_name}{path}"

    # Re-checked at egress rather than at configuration time: that also
    # defends against DNS rebinding, and against rows configured before the
    # guard existed.
    block = await _ssrf_block_reason(url)
    if block:
        logger.warning(
            "github_release_sync_blocked",
            project_id=str(project_id),
            reason=block,
        )
        raise GitHubSyncUnavailable(f"Target host is not allowed ({block})")

    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "TestLookup/1.0",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url, headers=headers, params=params or {})
    if resp.status_code != 200:
        raise GitHubSyncUnavailable(f"GitHub returned {resp.status_code} for {path}")
    return resp.json() if resp.content else []


def _milestone_fields(raw: dict) -> dict:
    """Project a GitHub milestone onto the columns TestLookup does not own.

    Deliberately narrow: only identity fields the external system owns are
    read. Parsing more would invite writing more, and status, criteria and
    policy stay TestLookup's regardless of where the release came from.
    """
    return {
        "name": (raw.get("title") or "").strip(),
        "external_id": str(raw.get("number")) if raw.get("number") is not None else "",
        "external_url": raw.get("html_url"),
        "description": raw.get("description") or None,
    }


async def sync_milestones(db: AsyncSession, project_id: uuid.UUID) -> dict[str, int]:
    """Pull GitHub milestones into ``releases``. Returns a small summary.

    Keyed on ``external_id``, never on name — renaming a milestone must update
    the existing release rather than orphan its entire run history and mint a
    second one, which is exactly what a name-keyed sync does silently the
    moment someone tidies up a title.

    Only identity is written. A synced release's phases, criteria, gate policy
    and run attribution are untouched, because those are TestLookup's and
    GitHub knows nothing about them.
    """
    from app.services.release_sort_key import compute_sort_key

    raw = await _authorized_get(
        db, project_id, "/milestones", {"state": "all", "per_page": _PAGE_SIZE}
    )
    now = datetime.now(timezone.utc)
    created = updated = skipped = 0

    for item in raw or []:
        if not isinstance(item, dict):
            skipped += 1
            continue
        fields = _milestone_fields(item)
        if not fields["name"] or not fields["external_id"]:
            # A milestone with no title cannot become a release anyone can
            # refer to. Skip rather than invent a name for it.
            skipped += 1
            continue

        existing = (
            await db.execute(
                select(Release).where(
                    Release.project_id == project_id,
                    Release.source_system == SOURCE_SYSTEM,
                    Release.external_id == fields["external_id"],
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            db.add(
                Release(
                    project_id=project_id,
                    name=fields["name"],
                    description=fields["description"],
                    status="planning",
                    source_system=SOURCE_SYSTEM,
                    external_id=fields["external_id"],
                    external_url=fields["external_url"],
                    last_synced_at=now,
                    sort_key=compute_sort_key(None, fields["name"]),
                    # A person named this in GitHub, so it is not a
                    # placeholder: it must not trigger the "name your release"
                    # prompt, and it IS eligible to become active on rotation.
                    is_auto_named=False,
                )
            )
            created += 1
        else:
            existing.name = fields["name"]
            existing.external_url = fields["external_url"]
            existing.last_synced_at = now
            existing.sort_key = compute_sort_key(existing.version, fields["name"])
            updated += 1

    logger.info(
        "github_milestones_synced",
        project_id=str(project_id),
        created=created,
        updated=updated,
        skipped=skipped,
    )
    return {"created": created, "updated": updated, "skipped": skipped}


async def resolve_release_for_run(
    db: AsyncSession, project_id: uuid.UUID, run: Any
) -> Optional[Release]:
    """Ladder rung 2: the synced release a run's git context points at.

    Reads the milestone off the PR the run was built from, because that is the
    link GitHub itself maintains between code and milestone. A commit does not
    carry a milestone, so there is nothing to read from ``commit_hash`` alone.

    Returns None for every ordinary reason — no PR number, no integration,
    offline mode, a PR with no milestone — and the ladder simply continues to
    rung 3. **A sync failure must never fail an ingest:** the test results are
    the thing of value, and attribution can be repaired afterwards, so every
    exception is swallowed into None rather than propagated.
    """
    pr_number = getattr(run, "pr_number", None)
    if not pr_number:
        return None

    try:
        pr = await _authorized_get(db, project_id, f"/pulls/{pr_number}")
    except GitHubSyncUnavailable as exc:
        # Expected and common: not configured, offline, no credential. Info,
        # not a warning — this is the normal state for most projects.
        logger.info(
            "github_rung2_unavailable",
            project_id=str(project_id),
            reason=str(exc),
        )
        return None
    except Exception as exc:
        # Network fault, malformed JSON, anything. Worth a warning because it
        # is unexpected, but still not worth failing the ingest over.
        logger.warning(
            "github_rung2_failed",
            project_id=str(project_id),
            error=str(exc),
        )
        return None

    milestone = (pr or {}).get("milestone") if isinstance(pr, dict) else None
    if not milestone or milestone.get("number") is None:
        return None

    return (
        await db.execute(
            select(Release).where(
                Release.project_id == project_id,
                Release.source_system == SOURCE_SYSTEM,
                Release.external_id == str(milestone["number"]),
            )
        )
    ).scalar_one_or_none()
