"""Keep machine accounts enrolled in every project.

Non-admin users can only see projects they are a member of
(``get_accessible_project_ids``). A service account is created once, at deploy
time, so without this every project created afterwards is invisible to it — the
MCP server would authenticate successfully and then answer "No active projects
found." for the rest of the deployment's life.

Deliberately *not* solved by giving machine accounts ADMIN: they should hold
the role the operator chose (QA_LEAD by default) and see projects through the
same membership mechanism as everyone else, so per-project authorisation still
applies to them.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import ProjectMember, User

logger = logging.getLogger(__name__)


async def enroll_service_accounts_in_project(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> list[uuid.UUID]:
    """Stage a ProjectMember for every active service account.

    Returns the user ids enrolled so the caller can invalidate their cached
    membership sets *after* committing — the set is cached in Redis for five
    minutes, and a service account that has just been granted access but whose
    cache still says "no projects" is indistinguishable from one that was never
    enrolled at all.

    Stage-only: the caller owns the commit, so enrolment lands in the same
    transaction as the project it grants access to. A service account can never
    end up holding membership of a project whose creation was rolled back.
    """
    accounts = (
        await db.execute(
            select(User).where(
                User.is_service_account.is_(True),
                User.is_active.is_(True),
            )
        )
    ).scalars().all()
    if not accounts:
        return []

    already = set(
        (
            await db.execute(
                select(ProjectMember.user_id).where(
                    ProjectMember.project_id == project_id
                )
            )
        ).scalars().all()
    )

    enrolled: list[uuid.UUID] = []
    for account in accounts:
        if account.id in already:
            continue
        db.add(ProjectMember(
            user_id=account.id,
            project_id=project_id,
            role=account.role,
        ))
        enrolled.append(account.id)

    if enrolled:
        logger.info(
            "enrolled %d service account(s) in project %s", len(enrolled), project_id
        )
    return enrolled
