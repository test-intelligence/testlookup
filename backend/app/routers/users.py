"""User management endpoints — list, invite, role updates, project members."""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, require_project_access, require_role
from app.core.security import get_password_hash
from app.db.postgres import get_db
from app.models.postgres import Project, ProjectMember, User, UserInvitation, UserRole
from app.models.schemas import (
    AddProjectMemberRequest,
    AdminCreateUserRequest,
    AdminCreateUserResponse,
    InviteUserRequest,
    InviteUserResponse,
    ProjectMemberResponse,
    UpdateProjectMemberRoleRequest,
    UpdateUserProfileRequest,
    UpdateUserRoleRequest,
    UpdateUserStatusRequest,
    UserListResponse,
)
from app.services.access_audit_service import log_access_change

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/users", tags=["User Management"])
projects_router = APIRouter(prefix="/api/v1/projects", tags=["User Management"])


def _normalize_user_role(value: UserRole | str) -> UserRole:
    """Convert a stored role string to a UserRole enum.

    After migration 0045, the ``UserRole.`` prefix guard is a safety net only.
    """
    if isinstance(value, UserRole):
        return value
    raw_value = str(value).strip()
    if raw_value.startswith("UserRole."):
        raw_value = raw_value.split(".", 1)[1]
    return UserRole(raw_value)


_ROLE_ORDER: list[UserRole] = [
    UserRole.VIEWER,
    UserRole.TESTER,
    UserRole.QA_ENGINEER,
    UserRole.QA_LEAD,
    UserRole.ADMIN,
]


def _role_rank(role: UserRole | str) -> int:
    try:
        return _ROLE_ORDER.index(_normalize_user_role(role))
    except ValueError:
        return -1


def _enforce_grant_ceiling(current_user: User, granted_role: UserRole, target_user_id: uuid.UUID) -> None:
    """Reject project-role grants that exceed the caller's authority.

    A global ADMIN may grant any project role. Anyone else (QA_LEAD managing
    members) may not grant a project role above their own global role, and may
    not change their own membership role at all — both close the project-scoped
    privilege-escalation path where a QA_LEAD mints a project-ADMIN (incl. self).
    """
    if _normalize_user_role(current_user.role) == UserRole.ADMIN:
        return
    if target_user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot change your own project role",
        )
    if _role_rank(granted_role) > _role_rank(current_user.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot grant a project role higher than your own",
        )


async def _assert_not_last_admin(db: AsyncSession, target_user: User) -> None:
    """Block demoting/deactivating the last remaining active ADMIN.

    Self-changes are already blocked upstream; this guards the case where an
    ADMIN removes admin access from the *only other* admin, locking everyone out.
    """
    if _normalize_user_role(target_user.role) != UserRole.ADMIN or not target_user.is_active:
        return
    other_admins = (
        await db.execute(
            select(func.count())
            .select_from(User)
            .where(
                User.role == UserRole.ADMIN,
                User.is_active == True,  # noqa: E712
                User.id != target_user.id,
            )
        )
    ).scalar() or 0
    if other_admins == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove the last active administrator",
        )


def _build_project_member_response(member: ProjectMember, user: User) -> ProjectMemberResponse:
    return ProjectMemberResponse(
        id=member.id,
        user_id=member.user_id,
        project_id=member.project_id,
        role=_normalize_user_role(member.role),
        created_at=member.created_at,
        email=user.email,
        username=user.username,
        full_name=user.full_name,
    )


# ── User CRUD ────────────────────────────────────────────────

@router.get("", response_model=list[UserListResponse])
async def list_users(
    is_active: Optional[bool] = Query(None),
    role: Optional[UserRole] = Query(None),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    """List all users (paginated). Requires QA_LEAD or higher."""
    stmt = select(User).order_by(User.full_name)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    if role is not None:
        stmt = stmt.where(User.role == role)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{user_id}", response_model=UserListResponse)
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    """Get a specific user by ID."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.patch("/{user_id}/role", response_model=UserListResponse)
async def update_user_role(
    user_id: uuid.UUID,
    payload: UpdateUserRoleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Update a user's global role. Requires ADMIN."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot change your own role")
    # Last-admin guard: refuse to strip ADMIN from the only other active admin.
    if _normalize_user_role(payload.role) != UserRole.ADMIN:
        await _assert_not_last_admin(db, user)
    old_role = str(user.role)
    user.role = payload.role
    await log_access_change(db, "role_changed", current_user, target_user_id=user_id,
                            before_value={"role": old_role}, after_value={"role": str(payload.role)})
    await db.commit()
    await db.refresh(user)
    return user


@router.patch("/{user_id}/status", response_model=UserListResponse)
async def update_user_status(
    user_id: uuid.UUID,
    payload: UpdateUserStatusRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Activate or deactivate a user account. Requires ADMIN."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot deactivate your own account")
    # Last-admin guard: refuse to deactivate the only other active admin.
    if payload.is_active is False:
        await _assert_not_last_admin(db, user)
    old_active = user.is_active
    user.is_active = payload.is_active
    await log_access_change(db, "status_changed", current_user, target_user_id=user_id,
                            before_value={"is_active": old_active}, after_value={"is_active": payload.is_active})
    await db.commit()
    await db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserListResponse)
async def update_user_profile(
    user_id: uuid.UUID,
    payload: UpdateUserProfileRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Update a user's profile attributes. Requires ADMIN. Only non-None fields are applied."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Check email uniqueness if changing
    if payload.email is not None and payload.email != user.email:
        existing = await db.execute(select(User).where(User.email == payload.email))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
        user.email = payload.email

    # Check username uniqueness if changing
    if payload.username is not None and payload.username != user.username:
        existing = await db.execute(select(User).where(User.username == payload.username))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already in use")
        user.username = payload.username

    if payload.full_name is not None:
        user.full_name = payload.full_name

    if payload.role is not None:
        if user.id == current_user.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot change your own role")
        if _normalize_user_role(payload.role) != UserRole.ADMIN:
            await _assert_not_last_admin(db, user)
        user.role = payload.role

    if payload.is_active is not None:
        if user.id == current_user.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot deactivate your own account")
        if payload.is_active is False:
            await _assert_not_last_admin(db, user)
        user.is_active = payload.is_active

    await db.commit()
    await db.refresh(user)
    logger.info("user_profile_updated", user_id=str(user_id), by=str(current_user.id))
    return user


@router.post("", response_model=AdminCreateUserResponse, status_code=201)
async def admin_create_user(
    payload: AdminCreateUserRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Admin creates a user directly with a temporary password. Requires ADMIN."""
    existing = await db.execute(
        select(User).where(
            (User.email == payload.email) | (User.username == payload.username)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email or username already registered",
        )

    temp_password = secrets.token_urlsafe(12)
    logger.info("admin_creating_user", email=payload.email, role=str(payload.role), by=str(current_user.id))
    user = User(
        email=payload.email,
        username=payload.username,
        full_name=payload.full_name,
        hashed_password=get_password_hash(temp_password),
        role=payload.role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return AdminCreateUserResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        temp_password=temp_password,
    )


@router.post("/invite", response_model=InviteUserResponse, status_code=201)
async def invite_user(
    payload: InviteUserRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Invite a user by email with a specified role. Requires ADMIN."""
    # Check if email already registered
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    # Check for existing unexpired invite
    existing_invite = await db.execute(
        select(UserInvitation).where(
            UserInvitation.email == payload.email,
            UserInvitation.is_used == False,  # noqa: E712
            UserInvitation.expires_at > datetime.now(timezone.utc),
        )
    )
    if existing_invite.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An active invitation already exists for this email",
        )

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)

    invitation = UserInvitation(
        email=payload.email,
        role=payload.role,
        token=token,
        invited_by_id=current_user.id,
        expires_at=expires_at,
    )
    db.add(invitation)
    await db.commit()
    await db.refresh(invitation)

    # In production this would send an email — for now return the link
    invitation_link = f"/register?token={token}&email={payload.email}"

    return InviteUserResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        invitation_link=invitation_link,
    )


# ── User Memberships (aggregated — replaces N+1 per-project fetch) ────────────

@router.get("/{user_id}/memberships")
async def get_user_memberships(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    """
    Return all project memberships for a user in a single query.
    Replaces the N+1 pattern of fetching each project's members individually.
    """
    result = await db.execute(
        select(ProjectMember, Project)
        .join(Project, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user_id, Project.is_active.is_(True))
        .order_by(Project.name)
    )
    rows = result.all()
    return [
        {
            "project_id": str(pm.project_id),
            "project_name": p.name,
            "role": _normalize_user_role(pm.role).value,
            "created_at": pm.created_at.isoformat() if pm.created_at else None,
        }
        for pm, p in rows
    ]


@router.get("/{user_id}/access-audit")
async def get_user_access_audit(
    user_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.QA_LEAD)),
):
    """Return access audit trail for a specific user."""
    from app.services.access_audit_service import get_access_audit_log
    return await get_access_audit_log(db, target_user_id=user_id, limit=limit)


# ── Project Members ───────────────────────────────────────────

@projects_router.get("/{project_id}/members", response_model=list[ProjectMemberResponse])
async def list_project_members(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
):
    """List all members of a project."""
    # Verify project exists
    proj_result = await db.execute(select(Project).where(Project.id == project_id))
    if not proj_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    result = await db.execute(
        select(ProjectMember, User)
        .join(User, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project_id)
        .order_by(User.full_name)
    )
    rows = result.all()

    return [
        _build_project_member_response(member, user)
        for member, user in rows
    ]


@projects_router.post("/{project_id}/members", response_model=ProjectMemberResponse, status_code=201)
async def add_project_member(
    project_id: uuid.UUID,
    payload: AddProjectMemberRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_project_access()),
):
    """Add a user to a project with a role. Requires QA_LEAD or higher."""
    # Verify project
    proj_result = await db.execute(select(Project).where(Project.id == project_id))
    if not proj_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Verify user
    user_result = await db.execute(select(User).where(User.id == payload.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Privilege-escalation guard: a non-admin cannot grant a project role above
    # their own, nor add themselves above their current standing.
    _enforce_grant_ceiling(current_user, payload.role, payload.user_id)

    # Check not already a member
    existing = await db.execute(
        select(ProjectMember).where(
            ProjectMember.user_id == payload.user_id,
            ProjectMember.project_id == project_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this project",
        )

    normalized_role = _normalize_user_role(payload.role)
    logger.info("adding_project_member", project_id=str(project_id), user_id=str(payload.user_id), role=normalized_role.value, by=str(current_user.id))
    member = ProjectMember(user_id=payload.user_id, project_id=project_id, role=normalized_role.value)
    db.add(member)
    await log_access_change(db, "member_added", current_user, target_user_id=payload.user_id,
                            project_id=project_id, after_value={"role": normalized_role.value})
    await db.commit()
    await db.refresh(member)

    # P3-2: Invalidate membership cache for the added user
    from app.core.deps import invalidate_membership_cache
    await invalidate_membership_cache(payload.user_id)

    return _build_project_member_response(member, user)


@projects_router.patch("/{project_id}/members/{user_id}", response_model=ProjectMemberResponse)
async def update_project_member_role(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: UpdateProjectMemberRoleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_project_access()),
):
    """Update a project member's role. Requires QA_LEAD or higher."""
    # Privilege-escalation guard: a non-admin cannot grant a project role above
    # their own, nor change their own membership role.
    _enforce_grant_ceiling(current_user, payload.role, user_id)

    result = await db.execute(
        select(ProjectMember, User)
        .join(User, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project membership not found")

    member, user = row
    member.role = _normalize_user_role(payload.role).value
    await db.commit()
    await db.refresh(member)

    # P3-2: Invalidate membership cache for the updated user
    from app.core.deps import invalidate_membership_cache
    await invalidate_membership_cache(user_id)

    return _build_project_member_response(member, user)


@projects_router.delete("/{project_id}/members/{user_id}", status_code=204)
async def remove_project_member(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    _: User = Depends(require_project_access()),
):
    """Remove a user from a project. Requires ADMIN."""
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project membership not found")
    await db.delete(member)
    await db.commit()

    # P3-2: Invalidate membership cache for the removed user
    from app.core.deps import invalidate_membership_cache
    await invalidate_membership_cache(user_id)

    return None
