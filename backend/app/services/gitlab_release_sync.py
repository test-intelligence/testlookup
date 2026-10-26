"""Sync GitLab project milestones into releases (W19 INT-05) — the GitLab twin of ``github_release_sync``.

Read-only against GitLab, through the project's GitLab integration (Settings ›
GitLab: base URL, project path, PAT in ``secret_service``). Every request is
gated by the ``gitlab`` flag + ``AI_OFFLINE_MODE`` (``gitlab_integration_service.
_post_allowed``) and the SSRF guard, because the configured base URL receives
the project's PAT.

Keyed on the milestone ``iid`` (``source_system="gitlab"``, ``external_id=str(iid)``),
never the title: renaming a milestone renames the release instead of minting a
second one. Only identity is written (name, description on create, URL);
phases, criteria and run attribution stay TestLookup's.

Nothing here commits. The router owns the transaction.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.http_client import http_verify
from app.models.postgres import Release

logger = structlog.get_logger(__name__)

SOURCE_SYSTEM = "gitlab"
_PAGE_SIZE = 100
_TIMEOUT = 10.0


class GitLabSyncUnavailable(RuntimeError):
    """The sync cannot run; the message says why and is safe to show."""


async def _milestones(db: AsyncSession, project_id: uuid.UUID) -> list[Any]:
    from app.services import gitlab_integration_service as gl
    from app.services.url_safety import egress_block_reason

    if settings.AI_OFFLINE_MODE:
        raise GitLabSyncUnavailable("AI_OFFLINE_MODE is enabled — outbound calls are disabled")
    if not await gl._post_allowed(db, project_id=project_id):
        raise GitLabSyncUnavailable("The gitlab feature flag is off for this project")
    target = await gl.load_gitlab_target(db, project_id)
    if target.reason == "no_integration":
        raise GitLabSyncUnavailable("No GitLab integration for this project")
    if target.reason == "integration_disabled":
        raise GitLabSyncUnavailable("The project's GitLab integration is disabled")
    if target.reason or not target.pat or not target.base_url or not target.project_path:
        raise GitLabSyncUnavailable("The project's GitLab integration has no project path or token")
    url = f"{gl._api_root(target.base_url)}/projects/{gl._project_ref(target.project_path)}/milestones"
    block = await egress_block_reason(url)
    if block:
        raise GitLabSyncUnavailable(f"Target host is not allowed ({block})")
    try:
        async with httpx.AsyncClient(verify=http_verify(), timeout=_TIMEOUT, follow_redirects=False) as client:
            resp = await client.get(url, headers={"PRIVATE-TOKEN": target.pat, "Accept": "application/json"},
                                    params={"per_page": _PAGE_SIZE})
    except httpx.HTTPError as exc:
        raise GitLabSyncUnavailable(f"GitLab could not be reached ({type(exc).__name__})") from exc
    if resp.status_code != 200:
        raise GitLabSyncUnavailable(f"GitLab returned {resp.status_code}")
    body = resp.json() if resp.content else []
    return body if isinstance(body, list) else []


def _fields(raw: dict) -> dict[str, Any]:
    iid = raw.get("iid")
    return {
        "external_id": str(iid) if iid is not None else None,
        "name": str(raw.get("title") or "").strip()[:255] or None,
        "description": (str(raw.get("description")) if raw.get("description") else None),
        "external_url": str(raw.get("web_url") or "")[:500] or None,
    }


async def sync_milestones(db: AsyncSession, project_id: uuid.UUID) -> dict[str, int]:
    """Pull the project's GitLab milestones into ``releases``. Raises ``GitLabSyncUnavailable``."""
    from app.services.release_sort_key import compute_sort_key

    raw = await _milestones(db, project_id)
    now = datetime.now(timezone.utc)
    created = updated = skipped = 0
    for item in raw:
        if not isinstance(item, dict):
            skipped += 1
            continue
        f = _fields(item)
        if not f["name"] or not f["external_id"]:
            skipped += 1
            continue
        existing = (await db.execute(select(Release).where(
            Release.project_id == project_id, Release.source_system == SOURCE_SYSTEM,
            Release.external_id == f["external_id"],
        ))).scalar_one_or_none()
        if existing is None:
            db.add(Release(
                project_id=project_id, name=f["name"], description=f["description"], status="planning",
                source_system=SOURCE_SYSTEM, external_id=f["external_id"], external_url=f["external_url"],
                last_synced_at=now, sort_key=compute_sort_key(None, f["name"]), is_auto_named=False,
            ))
            created += 1
        else:
            existing.name = f["name"]
            existing.external_url = f["external_url"]
            existing.last_synced_at = now
            existing.sort_key = compute_sort_key(existing.version, f["name"])
            updated += 1
    await db.flush()
    logger.info("gitlab_milestones_synced", project_id=str(project_id), created=created, updated=updated, skipped=skipped)
    return {"created": created, "updated": updated, "skipped": skipped}
