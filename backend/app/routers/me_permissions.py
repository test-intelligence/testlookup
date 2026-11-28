"""What the signed-in caller may do (enterprise roles, epic E00-S04).

``GET /api/v1/me/permissions?project_id=<uuid>`` answers the caller's roles and
permission sets in one project, or globally without ``project_id``. The SPA's
``can(permission, {write})`` reads it, so a control is offered only to someone
the server would let through. The server stays the authority: every route
checks its own guard.

* Reads use the project roles; writes the intersection of the caller's instance
  roles and project roles ("the lower of the two decides"). No membership (an
  ADMIN looking at a project they are not a member of) means the instance roles.
* A project the caller cannot read is a 403 (``resolve_project_scope``), the
  same answer every project-scoped read gives.
* A GET with no side effects, so no role gate: every signed-in role may ask.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, resolve_project_scope
from app.core.permissions import Grants, assigned_roles, effective_grants, job_roles_in, user_roles
from app.db.postgres import get_db
from app.models.postgres import ProjectMember, User, UserRole
from app.models.schemas import MyPermissionsResponse

router = APIRouter(prefix="/api/v1/me", tags=["My Permissions"])


def _names(grants: Grants) -> list[str]:
    return sorted(permission.value for permission in grants)


def _conditional(read: Grants, write: Grants) -> dict[str, list[str]]:
    out: dict[str, set[str]] = {}
    for grants in (read, write):
        for permission, conditions in grants.items():
            if conditions:
                out.setdefault(permission.value, set()).update(conditions)
    return {name: sorted(conditions) for name, conditions in sorted(out.items())}


@router.get("/permissions", response_model=MyPermissionsResponse)
async def get_my_permissions(
    project_id: Optional[str] = Query(None, description="Answer for this project; omitted, the global sets"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> MyPermissionsResponse:
    """The caller's roles and the permissions they hold, for reads and for writes."""
    instance = user_roles(current_user)
    pinned: Optional[uuid.UUID] = None
    project_roles: Optional[tuple[UserRole, ...]] = None
    if project_id:
        pinned, _ = await resolve_project_scope(db, current_user, project_id)
        row = (await db.execute(
            select(ProjectMember.role, ProjectMember.additional_roles).where(
                ProjectMember.user_id == current_user.id, ProjectMember.project_id == pinned,
            )
        )).first()
        if row is not None:
            try:
                project_roles = assigned_roles(row[0], row[1])
            except ValueError:
                project_roles = (UserRole.VIEWER,)
    read = effective_grants(instance, project_roles, is_write=False)
    write = effective_grants(instance, project_roles, is_write=True)
    return MyPermissionsResponse(
        role=instance[0],
        additional_roles=[role.value for role in instance[1:]],
        project_id=pinned,
        project_role=project_roles[0] if project_roles else None,
        project_additional_roles=[role.value for role in job_roles_in(project_roles[1:])] if project_roles else [],
        read_permissions=_names(read),
        write_permissions=_names(write),
        conditional=_conditional(read, write),
    )
