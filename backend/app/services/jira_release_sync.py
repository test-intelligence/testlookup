"""Pull Jira fix versions into ``releases`` (S3c).

The sibling of ``github_release_sync``, and deliberately built as one rather
than merely resembling it: same three gates, same identity keying, same refusal
to write anything TestLookup owns. Two syncs that differ in shape are two syncs
somebody has to reason about separately, and the second one is where the gate
gets forgotten.

TestLookup is not the system of record for release IDENTITY. Jira fix versions
and GitHub milestones are where teams actually name their releases; this
product owns release QUALITY — attribution, criteria, policy, the verdict. So
only identity is read, and a synced release's phases, gate policy and run
attribution are never touched by a sync.

Three gates, all in one place
------------------------------
Offline mode, a configured credential, and an SSRF check on the host — funnelled
through ``_authorized_get`` so a future caller cannot add a request that skips
one. ``AI_OFFLINE_MODE`` is a hard kill switch: an air-gapped deployment must
not egress because somebody enabled an integration.

Keyed on the version ID, never the name
----------------------------------------
Renaming a fix version in Jira must UPDATE the existing release, not orphan its
run history and mint a second one. A name-keyed sync does exactly that, silently,
the first time somebody tidies up a version string — and the damage is invisible
because both rows look plausible.
"""
from __future__ import annotations

import base64
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import Release
from app.services.defect_jira_service import resolve_jira_config
from app.services.github_checks_service import _ssrf_block_reason

logger = structlog.get_logger(__name__)

SOURCE_SYSTEM = "jira"

_TIMEOUT = httpx.Timeout(10.0)


class JiraSyncUnavailable(RuntimeError):
    """Sync cannot run: offline mode, integration disabled, or no credential."""


def _auth_header(cfg: dict) -> str:
    raw = f"{cfg['email']}:{cfg['api_token']}"
    return f"Basic {base64.b64encode(raw.encode()).decode()}"


async def _authorized_get(
    db: AsyncSession, path: str, params: Optional[dict] = None
) -> Any:
    """GET a Jira path, or raise ``JiraSyncUnavailable``.

    Every outbound request funnels through here so the gates cannot be bypassed
    by a future caller. Mirrors ``github_release_sync._authorized_get`` on
    purpose: a reader who has understood one has understood both.

    **Where the credential comes from.** ``resolve_jira_config``, not
    ``settings``. A deployment configured through Settings -> Integrations
    stores the API token in the SECRET SERVICE, because the AppSetting value it
    writes is stripped of secrets -- so reading ``settings.JIRA_API_TOKEN``
    directly finds nothing and this sync would refuse to run, with "No Jira
    credential configured", on a Jira that is fully configured and working for
    every other feature. Sharing the resolver is also what keeps one deployment
    from having two different answers to "is Jira set up".

    What is deliberately NOT shared is the request itself. ``_jira_get`` in that
    module issues its call with no SSRF check, and the Jira domain is
    operator-configurable -- an SSRF sink by design. This egress keeps its own
    guard, and keeps being the only one here.
    """
    if settings.AI_OFFLINE_MODE:
        # The hard kill switch, checked FIRST and read from process config
        # rather than the resolved document: an air-gapped deployment must not
        # egress because somebody enabled an integration in the UI.
        raise JiraSyncUnavailable("AI_OFFLINE_MODE is enabled")

    cfg = await resolve_jira_config(db)

    # The three remaining gates stay spelled out here rather than delegating to
    # ``availability_reason``, which collapses them into one "not_configured".
    # A reader who has to fix this needs to know WHICH half is missing.
    if not cfg.get("enabled"):
        raise JiraSyncUnavailable("Jira integration is not enabled")

    if not cfg.get("domain"):
        raise JiraSyncUnavailable("No Jira domain configured")

    if not (cfg.get("email") and cfg.get("api_token")):
        # Without both halves the Basic header is well-formed and useless, so
        # the request would 401 rather than fail here with a readable reason.
        raise JiraSyncUnavailable("No Jira credential configured")

    url = f"https://{cfg['domain']}/rest/api/3{path}"

    # Re-checked at egress rather than at configuration time: that also defends
    # against DNS rebinding, and against rows configured before the guard
    # existed.
    block = await _ssrf_block_reason(url)
    if block:
        logger.warning("jira_release_sync_blocked", reason=block)
        raise JiraSyncUnavailable(f"Target host is not allowed ({block})")

    headers = {
        "Authorization": _auth_header(cfg),
        "Accept": "application/json",
        "User-Agent": "TestLookup/1.0",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url, headers=headers, params=params or {})
    if resp.status_code != 200:
        raise JiraSyncUnavailable(f"Jira returned {resp.status_code} for {path}")
    return resp.json() if resp.content else []


def _version_fields(raw: dict) -> dict:
    """Project a Jira version onto the columns TestLookup does not own.

    Deliberately narrow, matching the GitHub sibling: only identity the external
    system owns is read. Parsing more would invite writing more, and status,
    criteria and policy stay TestLookup's regardless of where the release came
    from.

    Note what is NOT taken: Jira's ``released`` and ``archived`` booleans. They
    describe the version's lifecycle in Jira, not whether this product's gate
    has passed, and mapping them onto ``Release.status`` would let an external
    tool silently mark a release shipped that the gate never approved.
    """
    return {
        "name": (raw.get("name") or "").strip(),
        "external_id": str(raw.get("id")) if raw.get("id") is not None else "",
        "external_url": raw.get("self"),
        "description": raw.get("description") or None,
    }


async def sync_fix_versions(
    db: AsyncSession,
    project_id: uuid.UUID,
    jira_project_key: Optional[str] = None,
) -> dict[str, int]:
    """Pull Jira fix versions into ``releases``. Returns a small summary.

    Keyed on ``external_id``, never on name — renaming a version must update the
    existing release rather than orphan its run history and mint a second one.

    Only identity is written. A synced release's phases, criteria, gate policy
    and run attribution are untouched, because those are TestLookup's and Jira
    knows nothing about them.

    Does not commit. The caller owns the transaction, per the repo's
    transaction-boundary rule.
    """
    from app.services.release_sort_key import compute_sort_key

    # Resolved, not read from ``settings``: a Settings-UI deployment overrides
    # the default project key the same way it overrides the credential.
    cfg = await resolve_jira_config(db)
    key = jira_project_key or cfg.get("default_project_key")
    raw = await _authorized_get(db, f"/project/{key}/versions")

    now = datetime.now(timezone.utc)
    created = updated = skipped = 0

    for item in raw or []:
        if not isinstance(item, dict):
            skipped += 1
            continue
        fields = _version_fields(item)
        if not fields["name"] or not fields["external_id"]:
            # A version with no name cannot become a release anyone can refer
            # to. Skip rather than invent a name for it.
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
                    # A person named this in Jira, so it is not a placeholder:
                    # it must not trigger the "name your release" prompt, and it
                    # IS eligible to become active on rotation.
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
        "jira_fix_versions_synced",
        project_id=str(project_id),
        jira_project_key=key,
        created=created,
        updated=updated,
        skipped=skipped,
    )
    return {"created": created, "updated": updated, "skipped": skipped}
