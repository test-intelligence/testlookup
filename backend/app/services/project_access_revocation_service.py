"""Credential lifecycle for a deleted project.

``DELETE /projects/{id}`` is a soft delete — it flips ``is_active`` and
commits. Before this module it did nothing else, so everything that grants
access to the project outlived the delete:

* the auto-provisioned QA-lead account stayed ``is_active=True`` with role
  ``QA_LEAD``, and could still log in;
* every API key scoped to the project kept authenticating, because
  ``_validate_api_key`` checks the key's own ``is_active``, its expiry and
  the owner's ``is_active`` — and never looks at the project it is bound to.

Measured on a real deployment: 107 soft-deleted projects had left behind 108
live QA-lead accounts and 3 active API keys, two of which had a
``last_used_at``. A CI job holding one of those keys keeps ingesting into a
project the operator believes is gone.

Deactivating rather than deleting: these rows are referenced by history —
the QA-lead account owns thousands of test-case assignments, and API keys
carry an audit trail. ``is_active=False`` is the field an operator already
toggles by hand, it blocks login (``get_current_active_user`` answers 403 on
an inactive user, so existing tokens die with it — no separate revocation
needed) and it blocks key auth, while leaving every foreign key intact.

Deletion is one-way through the API: ``ProjectUpdate`` has no ``is_active``
field and no router sets one back to ``True``. So there is no restore path
to mirror. If a project is ever un-deleted by a direct database edit, its
credentials stay off and have to be re-enabled deliberately — the right
default for a credential.
"""
from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import ApiKey, Project, User

logger = structlog.get_logger(__name__)


async def revoke_project_credentials(
    db: AsyncSession,
    project: Project,
) -> dict[str, int]:
    """Deactivate everything that grants access to ``project``.

    Returns counts of what changed. Idempotent — re-running on an
    already-revoked project reports zeros. Caller owns commit, per the
    single-owner transaction rule in ``backend/CLAUDE.md``.
    """
    totals = {"qa_lead_accounts": 0, "api_keys": 0}

    lead_id = project.default_qa_lead_user_id
    if lead_id is not None:
        lead = await db.get(User, lead_id)
        if lead is not None and lead.is_active:
            # Only if no ACTIVE project still relies on this account. Slugs
            # are unique so sharing should not happen, but deactivating a
            # live project's assignee would break its /my-failures inbox,
            # and that is not a risk worth taking on an assumption.
            still_used = (
                await db.execute(
                    select(Project.id).where(
                        Project.default_qa_lead_user_id == lead_id,
                        Project.is_active.is_(True),
                        Project.id != project.id,
                    ).limit(1)
                )
            ).first()
            if still_used is None:
                lead.is_active = False
                totals["qa_lead_accounts"] = 1

    keys = (
        await db.execute(
            select(ApiKey).where(
                ApiKey.project_id == project.id,
                ApiKey.is_active.is_(True),
            )
        )
    ).scalars().all()
    for key in keys:
        key.is_active = False
    totals["api_keys"] = len(keys)

    if totals["qa_lead_accounts"] or totals["api_keys"]:
        await db.flush()
        logger.info(
            "project_credentials_revoked",
            project_id=str(project.id),
            qa_lead_accounts=totals["qa_lead_accounts"],
            api_keys=totals["api_keys"],
        )
    return totals


async def reconcile_deleted_project_credentials(
    db: AsyncSession,
) -> dict[str, int]:
    """Revoke credentials left live by projects deleted before this shipped.

    The code fix above only covers deletes from here on. Existing rows need
    a sweep, and a sweep that runs on the beat is better than a one-off
    script an operator has to remember: it is idempotent, it reports zeros
    once it has caught up, and it also repairs a project deleted by a direct
    database edit that bypassed the endpoint.
    """
    totals = {"projects": 0, "qa_lead_accounts": 0, "api_keys": 0}
    projects = (
        await db.execute(
            select(Project).where(Project.is_active.is_(False))
        )
    ).scalars().all()
    for project in projects:
        counts = await revoke_project_credentials(db, project)
        if counts["qa_lead_accounts"] or counts["api_keys"]:
            totals["projects"] += 1
            totals["qa_lead_accounts"] += counts["qa_lead_accounts"]
            totals["api_keys"] += counts["api_keys"]
    return totals
