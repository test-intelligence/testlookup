"""What every NEW project needs, in ONE place (W24 ADM-05; extracted from ``routers/projects.create_project``).

``POST /api/v1/projects`` and the project import (``project_import_service``)
both build a project through :func:`provision_new_project`, so "what a new
project needs" cannot drift between them. The steps, in this order, are the
ones ``create_project`` ran inline before W24 (behaviour identical by
construction — the same calls, moved):

1. the creator's ``ProjectMember`` (the creator's global role);
2. the default QA lead: a supplied ``default_qa_lead_user_id`` is validated
   (``assert_user_is_qa_lead_on_project``), otherwise the synthetic QA-lead
   user is provisioned (``ensure_default_qa_lead``);
3. the machine accounts' memberships (``enroll_service_accounts_in_project``);
4. the active release (migration 0150's invariant) — unless
   ``provision_release=False`` (the import restores it from the archive's
   releases, I-7);
5. the ``project.created`` activity event, context ``{"slug": …}`` plus
   ``activity_context``.

Stage-only (``STAGE_ONLY_SERVICES``): nothing here commits. The caller commits,
THEN invalidates the membership caches of the creator and of the returned
service-account ids.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Project, ProjectMember, User
from app.services.activity.service import ActorRef, record as record_activity


async def provision_new_project(
    db: AsyncSession,
    *,
    project: Project,
    creator: User,
    provision_release: bool = True,
    activity_context: Optional[dict[str, Any]] = None,
) -> list[uuid.UUID]:
    """Stage the memberships, the default QA lead, the active release and the
    ``project.created`` event of a project that was just added and flushed.
    Returns the enrolled service-account ids (invalidate their caches AFTER commit)."""
    # Auto-add the creator as a member so they can access the project
    # without an admin having to add them manually. Creator inherits their
    # global role (ADMIN stays ADMIN, QA_LEAD stays QA_LEAD, etc.).
    db.add(ProjectMember(
        user_id=creator.id,
        project_id=project.id,
        role=creator.role,
    ))

    # 0079: validate default_qa_lead_user_id, if provided, is permitted to
    # own suites. Run AFTER the creator's ProjectMember is staged so the
    # creator themselves (a QA_LEAD or higher) can be set as default in a
    # single create-then-default flow.
    if project.default_qa_lead_user_id is not None:
        await db.flush()
        from app.services.suite_review_service import assert_user_is_qa_lead_on_project
        await assert_user_is_qa_lead_on_project(
            db, project.default_qa_lead_user_id, project.id,
        )
    else:
        # Auto-provision a synthetic QA-lead user so the project has a
        # deterministic default failure assignee from day one. Without
        # this, every fresh project's /my-failures inbox would stay
        # empty until an admin manually configures an owner.
        from app.services.default_qa_lead_service import ensure_default_qa_lead
        await db.flush()
        await ensure_default_qa_lead(db, project)

    # Machine accounts (the MCP server) see projects through the same
    # membership mechanism as everyone else, so a project created after they
    # were provisioned would otherwise be invisible to them forever.
    from app.services.service_account_service import enroll_service_accounts_in_project
    enrolled_service_accounts = await enroll_service_accounts_in_project(db, project.id)

    if provision_release:
        # Every project must have exactly one active release at every moment
        # (migration 0150) — it is where a run with no release from the client
        # lands. Created in THIS transaction on purpose: doing it lazily at first
        # ingest would make the invariant true-after-the-first-repair rather than
        # true from t=0, and the reconciliation sweep could not then distinguish
        # "never provisioned" from "something broke it".
        from app.services.release_lifecycle_service import (
            ensure_active_release_for_new_project,
        )
        await ensure_active_release_for_new_project(db, project)

    # Epic ACT — the ledger's own first row for this project. Staged on this
    # session so it shares the create transaction: if the project does not
    # exist, neither does the event saying it was created.
    await record_activity(
        db,
        project_id=project.id,
        event_type="project.created",
        actor=ActorRef.from_user(creator),
        entity_id=project.id,
        entity_label=project.name,
        context={"slug": project.slug, **(activity_context or {})},
    )
    return enrolled_service_accounts
