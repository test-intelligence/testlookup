"""W20 ADM-09: SCIM groups → project memberships.

A group pushed over SCIM ``/Groups`` holds members; an administrator maps the group to (project, project role)
pairs. ``reconcile_user`` makes a user's SCIM-created memberships equal to what their groups grant:

* the highest rank any of the user's groups maps to a project wins; ``source`` names that group (``scim:<name>``);
* a group mapped to a job role (RELEASE_MANAGER, PROJECT_MANAGER, PRODUCT_MANAGER, DEVELOPER) ADDS it to the
  membership's ``additional_roles`` -- no precedence fight with the rank; leaving the group removes it. A project
  only job-role groups grant gets VIEWER as its primary role (enterprise roles E00-S05);
* a membership a person granted (``source IS NULL``) is never changed or removed — at most it is left alone;
* a SCIM-created membership no group grants any more is removed; one whose grant changed is updated.

Everything stages on the caller's session (the router commits) and ends with ``invalidate_membership_cache``
for each reconciled user, after the caller's commit via ``reconcile_and_invalidate``.
"""
from __future__ import annotations

import uuid
from typing import Iterable, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import EXTRA_ROLE_CHOICES, LADDER, job_roles_in, rank_index
from app.models.postgres import ProjectMember, SCIMGroup, SCIMGroupMapping, SCIMGroupMember, UserRole

SOURCE_PREFIX = "scim:"
_ORDER = [role.value for role in LADDER]
#: Job roles a group may map to: they add an extra role, never the rank.
JOB_ROLES = tuple(role.value for role in EXTRA_ROLE_CHOICES)
#: Roles a group may map to: every rank but ADMIN, then the job roles. Never AGENT.
PROJECT_ROLES = tuple(r for r in _ORDER if r != UserRole.ADMIN.value) + JOB_ROLES

#: (primary role, source, extra job roles in enum order)
Grant = tuple[str, str, tuple[str, ...]]


def _rank(role: str) -> int:
    return rank_index(role) if role in _ORDER else -1


async def desired_grants(db: AsyncSession, user_id: uuid.UUID) -> dict[uuid.UUID, Grant]:
    """project id → (role, source, extra roles) the user's groups grant.

    The highest rank wins the primary role (ties keep the first group by name); every job role a group maps to
    is an extra role. A project only job-role groups grant: VIEWER, with the first such group as the source.
    """
    rows = (await db.execute(
        select(SCIMGroupMapping.project_id, SCIMGroupMapping.project_role, SCIMGroup.display_name)
        .join(SCIMGroupMember, SCIMGroupMember.group_id == SCIMGroupMapping.group_id)
        .join(SCIMGroup, SCIMGroup.id == SCIMGroupMapping.group_id)
        .where(SCIMGroupMember.user_id == user_id)
        .order_by(SCIMGroup.display_name)
    )).all()
    ranked: dict[uuid.UUID, tuple[str, str]] = {}
    extras: dict[uuid.UUID, dict[str, str]] = {}
    seen: dict[uuid.UUID, None] = {}  # projects in the order the groups (by name) first grant them
    for project_id, role, group_name in rows:
        seen.setdefault(project_id, None)
        source = f"{SOURCE_PREFIX}{group_name}"[:80]
        if role in JOB_ROLES:
            extras.setdefault(project_id, {}).setdefault(role, source)
            continue
        current = ranked.get(project_id)
        if current is None or _rank(role) > _rank(current[0]):
            ranked[project_id] = (role, source)
    out: dict[uuid.UUID, Grant] = {}
    for project_id in seen:
        job = extras.get(project_id, {})
        held = tuple(r for r in JOB_ROLES if r in job)
        if project_id in ranked:
            role, source = ranked[project_id]
        else:
            role, source = UserRole.VIEWER.value, job[held[0]]
        out[project_id] = (role, source, held)
    return out


def _held_extras(member: ProjectMember) -> tuple[str, ...]:
    return tuple(role.value for role in job_roles_in(getattr(member, "additional_roles", None)))


async def reconcile_user(db: AsyncSession, user_id: uuid.UUID, changes: Optional[list] = None) -> dict[str, int]:
    """Make the user's SCIM-created memberships match their groups. Returns counts (added/updated/removed/kept);
    ``changes`` (when given) collects ``(event, project_id, user_id, role)`` per change for the activity ledger."""
    log = changes if changes is not None else []
    want = await desired_grants(db, user_id)
    have = (await db.execute(select(ProjectMember).where(ProjectMember.user_id == user_id))).scalars().all()
    counts = {"added": 0, "updated": 0, "removed": 0, "kept_manual": 0}
    seen: set[uuid.UUID] = set()
    for member in have:
        seen.add(member.project_id)
        scim_owned = bool(member.source and member.source.startswith(SOURCE_PREFIX))
        grant = want.get(member.project_id)
        if not scim_owned:
            if grant:
                counts["kept_manual"] += 1
            continue
        if grant is None:
            log.append(("member.removed", member.project_id, user_id, member.role))
            await db.delete(member)
            counts["removed"] += 1
        elif (member.role, member.source, _held_extras(member)) != grant:
            if member.role != grant[0] or _held_extras(member) != grant[2]:
                log.append(("member.role_changed", member.project_id, user_id, grant[0]))
            member.role, member.source = UserRole(grant[0]), grant[1]
            member.additional_roles = list(grant[2]) or None
            counts["updated"] += 1
    for project_id, (role, source, held) in want.items():
        if project_id not in seen:
            db.add(ProjectMember(
                user_id=user_id, project_id=project_id, role=role, source=source,
                additional_roles=list(held) or None,
            ))
            log.append(("member.added", project_id, user_id, role))
            counts["added"] += 1
    await db.flush()
    return counts


async def group_member_ids(db: AsyncSession, group_id: uuid.UUID) -> list[uuid.UUID]:
    return list((await db.execute(select(SCIMGroupMember.user_id).where(SCIMGroupMember.group_id == group_id))).scalars().all())


async def set_members(db: AsyncSession, group_id: uuid.UUID, user_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """Replace the group's members; returns every user whose membership changed (to reconcile)."""
    new = set(user_ids)
    old = set(await group_member_ids(db, group_id))
    if old - new:
        await db.execute(delete(SCIMGroupMember).where(SCIMGroupMember.group_id == group_id,
                                                       SCIMGroupMember.user_id.in_(list(old - new))))
    for uid in new - old:
        db.add(SCIMGroupMember(group_id=group_id, user_id=uid))
    await db.flush()
    return old ^ new


async def reconcile_users(db: AsyncSession, user_ids: Iterable[uuid.UUID], changes: Optional[list] = None) -> list[uuid.UUID]:
    done = []
    for uid in sorted(set(user_ids), key=str):
        await reconcile_user(db, uid, changes)
        done.append(uid)
    return done


async def invalidate(user_ids: Iterable[uuid.UUID]) -> None:
    """After the caller's commit: drop the cached membership sets."""
    from app.core.deps import invalidate_membership_cache

    for uid in set(user_ids):
        await invalidate_membership_cache(uid)


async def set_mappings(
    db: AsyncSession, group_id: uuid.UUID, mappings: list[tuple[uuid.UUID, str]], changes: Optional[list] = None,
) -> list[uuid.UUID]:
    """Replace the group's (project, role) grants and reconcile its members. Returns the reconciled users."""
    for _pid, role in mappings:
        if role not in PROJECT_ROLES:
            raise ValueError(f"Invalid project role '{role}'")
    await db.execute(delete(SCIMGroupMapping).where(SCIMGroupMapping.group_id == group_id))
    for pid, role in dict(mappings).items():
        db.add(SCIMGroupMapping(group_id=group_id, project_id=pid, project_role=role))
    await db.flush()
    return await reconcile_users(db, await group_member_ids(db, group_id), changes)


async def delete_group(db: AsyncSession, group: SCIMGroup) -> list[uuid.UUID]:
    members = await group_member_ids(db, group.id)
    await db.delete(group)
    await db.flush()
    return await reconcile_users(db, members)


def membership_source_label(source: Optional[str]) -> Optional[str]:
    """``scim:payments-qa`` → ``via SCIM group payments-qa`` (None for a person's grant)."""
    if source and source.startswith(SOURCE_PREFIX):
        return f"via SCIM group {source[len(SOURCE_PREFIX):]}"
    return None
