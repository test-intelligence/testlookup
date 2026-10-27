"""W20 ADM-09: the administrator's view of SCIM groups and their project mappings.

* ``GET /api/v1/sso/scim-groups`` (ADMIN): every group the IdP pushed, its member count and its (project, role) grants;
* ``PUT /api/v1/sso/scim-groups/{group_id}/mappings`` (ADMIN): replace the group's grants; its members' SCIM-created
  memberships are reconciled at once (a membership a person granted is never changed).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.db.postgres import get_db
from app.models.postgres import IdentityEventType, Project, SCIMGroup, SCIMGroupMapping, SCIMGroupMember, User, UserRole
from app.services import scim_group_service as sgs
from app.services.activity.service import ActorRef, record as record_activity
from app.services.sso_service import log_identity_event

router = APIRouter(prefix="/api/v1/sso", tags=["SSO / SAML"])


class GroupMappingIn(BaseModel):
    project_id: uuid.UUID
    role: str


class GroupMappingsIn(BaseModel):
    mappings: list[GroupMappingIn] = Field(default_factory=list, max_length=200)


async def _groups(db: AsyncSession) -> list[dict]:
    groups = (await db.execute(select(SCIMGroup).order_by(SCIMGroup.display_name))).scalars().all()
    counts: dict[uuid.UUID, int] = {gid: n for gid, n in (await db.execute(
        select(SCIMGroupMember.group_id, func.count()).group_by(SCIMGroupMember.group_id))).all()}
    maps = (await db.execute(
        select(SCIMGroupMapping.group_id, SCIMGroupMapping.project_id, SCIMGroupMapping.project_role, Project.name)
        .join(Project, Project.id == SCIMGroupMapping.project_id).order_by(Project.name))).all()
    by_group: dict[uuid.UUID, list[dict]] = {}
    for gid, pid, role, pname in maps:
        by_group.setdefault(gid, []).append({"project_id": str(pid), "project_name": pname, "role": role})
    return [{"id": str(g.id), "display_name": g.display_name, "external_id": g.external_id,
             "member_count": int(counts.get(g.id, 0)), "mappings": by_group.get(g.id, [])} for g in groups]


@router.get("/scim-groups")
async def list_scim_groups(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return {"groups": await _groups(db), "roles": list(sgs.PROJECT_ROLES)}


@router.put("/scim-groups/{group_id}/mappings")
async def put_scim_group_mappings(
    group_id: uuid.UUID,
    payload: GroupMappingsIn,
    request: Request,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    group = (await db.execute(select(SCIMGroup).where(SCIMGroup.id == group_id))).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail="SCIM group not found")
    pairs = [(m.project_id, m.role) for m in payload.mappings]
    if pairs:
        known = set((await db.execute(select(Project.id).where(Project.id.in_([p for p, _ in pairs])))).scalars().all())
        if {p for p, _ in pairs} - known:
            raise HTTPException(status_code=400, detail="Unknown project in the mappings")
    try:
        changes: list = []
        reconciled = await sgs.set_mappings(db, group.id, pairs, changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await log_identity_event(
        db, IdentityEventType.SSO_CONFIG_UPDATED, actor_id=current_user.id, actor_name=current_user.username,
        detail={"scim_group": group.display_name, "mappings": [{"project_id": str(p), "role": r} for p, r in pairs],
                "reconciled_users": len(reconciled)},
        ip_address=request.client.host if request.client else None,
    )
    if changes:
        names: dict[uuid.UUID, str] = {uid: name for uid, name in (await db.execute(
            select(User.id, User.username).where(User.id.in_(list({c[2] for c in changes}))))).all()}
        for event, project_id, user_id, role in changes:
            await record_activity(
                db, project_id=project_id, event_type=event, actor=ActorRef.from_user(current_user),
                entity_id=user_id, entity_label=names.get(user_id, str(user_id)), entity_type="member",
                context={"role": role, "via": f"SCIM group {group.display_name}"},
            )
    await db.commit()
    await sgs.invalidate(reconciled)
    return {"group_id": str(group.id), "reconciled_users": len(reconciled), "groups": await _groups(db)}
