"""User management endpoints — list, invite, role updates, project members."""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, require_project_access, require_role
from app.core.security import get_password_hash
from app.db.postgres import get_db
from app.models.postgres import Project, ProjectMember, SettingsAuditLog, User, UserInvitation, UserRole
from app.models.schemas import (
    AddProjectMemberRequest,
    AdminCreateUserRequest,
    AdminCreateUserResponse,
    InvitationMembershipView,
    InvitationSummary,
    InviteUserRequest,
    InviteUserResponse,
    PasswordResetLinkResponse,
    ProjectMemberResponse,
    UpdateProjectMemberRoleRequest,
    UpdateUserProfileRequest,
    UpdateUserRoleRequest,
    UpdateUserStatusRequest,
    UserListResponse,
)
from app.services.access_audit_service import log_access_change
from app.services.account_links_service import (
    INVITATION_LIFETIME,
    INVITE_PATH,
    RESET_PATH,
    invitation_status,
    send_link_email,
)
from app.services.activity.service import ActorRef, record as record_activity
from app.services.notification.email_templates import render_invitation_email
from app.services.one_time_tokens import fragment_link, new_token
from app.services.password_reset_service import create_password_reset_token, email_password_reset
from app.services.sql_utils import escape_like

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
        # REL-02: the release Owner picker offers people, not the synthetic
        # per-project QA lead or a CI service account, and not a deactivated
        # user. Without the flags the client could not tell them apart.
        is_service_account=bool(getattr(user, "is_service_account", False)),
        is_synthetic=bool(getattr(user, "is_synthetic", False)),
        is_active=getattr(user, "is_active", True) is not False,
        source=getattr(member, "source", None) if isinstance(getattr(member, "source", None), str) else None,
    )


# ── User CRUD ────────────────────────────────────────────────

@router.get("", response_model=list[UserListResponse])
async def list_users(
    response: Response,
    is_active: Optional[bool] = Query(None),
    role: Optional[UserRole] = Query(None),
    q: Optional[str] = Query(
        None, max_length=100,
        description="Case-insensitive substring of the username, email or full name (ADM-03).",
    ),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    """List all users (paginated). Requires QA_LEAD or higher.

    The body stays a bare list (other callers read it); the number of users
    matching the filters, across every page, is the ``X-Total-Count`` header.
    """
    filters = []
    if is_active is not None:
        filters.append(User.is_active == is_active)
    if role is not None:
        filters.append(User.role == role)
    needle = (q or "").strip()
    if needle:
        pattern = f"%{escape_like(needle)}%"
        filters.append(or_(
            User.username.ilike(pattern, escape="\\"),
            User.email.ilike(pattern, escape="\\"),
            User.full_name.ilike(pattern, escape="\\"),
        ))
    total = (await db.execute(select(func.count()).select_from(User).where(*filters))).scalar_one()
    response.headers["X-Total-Count"] = str(int(total or 0))
    stmt = (
        select(User).where(*filters).order_by(User.full_name, User.id)
        .offset((page - 1) * page_size).limit(page_size)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/invitations", response_model=list[InvitationSummary])
async def list_invitations(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    include_closed: bool = Query(False, description="Also list used and revoked invitations"),
):
    """W17b ADM-01: pending (and expired) invitations, newest first. Never a token. ADMIN.

    Declared before ``/{user_id}`` so ``invitations`` is not parsed as a user id.
    """
    stmt = select(UserInvitation).order_by(UserInvitation.created_at.desc(), UserInvitation.id).limit(500)
    if not include_closed:
        stmt = stmt.where(UserInvitation.is_used == False, UserInvitation.revoked_at.is_(None))  # noqa: E712
    invitations = list((await db.execute(stmt)).scalars().all())
    return await _invitation_summaries(db, invitations)


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
        must_change_password=True,
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
    """Invite a user by email with a role and optional project memberships. Requires ADMIN.

    W17b ADM-01: only ``sha256(token)`` is stored; the link carries the token in
    a URL fragment; the link is emailed when SMTP is configured and always
    returned so the admin can copy it (the offline path).
    """
    email = str(payload.email)
    existing = await db.execute(select(User).where(func.lower(User.email) == email.lower()))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    existing_invite = await db.execute(
        select(UserInvitation).where(
            func.lower(UserInvitation.email) == email.lower(),
            UserInvitation.is_used == False,  # noqa: E712
            UserInvitation.revoked_at.is_(None),
            UserInvitation.expires_at > datetime.now(timezone.utc),
        )
    )
    if existing_invite.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An active invitation already exists for this email. Resend or revoke it from Pending invitations.",
        )

    memberships = await _validated_invite_memberships(db, payload)

    raw, token_hash = new_token()
    invitation = UserInvitation(
        email=email,
        role=payload.role,
        token=None,
        token_hash=token_hash,
        invited_by_id=current_user.id,
        expires_at=datetime.now(timezone.utc) + INVITATION_LIFETIME,
        project_memberships=memberships or None,
    )
    db.add(invitation)
    await db.flush()
    for m in memberships:
        await record_activity(
            db,
            project_id=m["project_id"],
            event_type="invitation.sent",
            actor=ActorRef.from_user(current_user),
            entity_id=invitation.id,
            entity_label=email,
            context={"role": m["role"]},
        )
    await db.commit()
    await db.refresh(invitation)

    link = fragment_link(INVITE_PATH, raw)
    email_sent = await _email_invitation(invitation, current_user, link)
    logger.info("invitation_created", invitation_id=str(invitation.id), email_sent=email_sent, by=str(current_user.id))
    return InviteUserResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        invitation_link=link,
        email_sent=email_sent,
    )


async def _validated_invite_memberships(db: AsyncSession, payload: InviteUserRequest) -> list[dict]:
    """Each requested project must exist and be active; one entry per project."""
    out: dict[str, dict] = {}
    for m in payload.project_memberships:
        project = (
            await db.execute(select(Project).where(Project.id == m.project_id))
        ).scalar_one_or_none()
        if project is None or not getattr(project, "is_active", True):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
        out[str(m.project_id)] = {"project_id": str(m.project_id), "role": _normalize_user_role(m.role).value}
    return list(out.values())


async def _email_invitation(invitation: UserInvitation, inviter: User, link: str) -> bool:
    expires = invitation.expires_at.strftime("%Y-%m-%d %H:%M UTC")
    inviter_name = inviter.full_name or inviter.username
    role = _normalize_user_role(invitation.role).value.replace("_", " ").title()
    html_body = render_invitation_email(inviter_name, role, link, expires)
    text_body = (
        f"{inviter_name} invited you to join TestLookup as {role}.\n\n"
        f"Accept the invitation: {link}\n\nThis invitation expires on {expires}."
    )
    return await send_link_email(invitation.email, "You are invited to TestLookup", html_body, text_body)


async def _invitation_summaries(db: AsyncSession, invitations: list[UserInvitation]) -> list[InvitationSummary]:
    inviter_ids = {i.invited_by_id for i in invitations if i.invited_by_id}
    project_ids: set[uuid.UUID] = set()
    for inv in invitations:
        for m in inv.project_memberships or []:
            try:
                project_ids.add(uuid.UUID(str(m.get("project_id"))))
            except (ValueError, AttributeError):
                continue
    names: dict[uuid.UUID, str] = {}
    if inviter_ids:
        rows = await db.execute(select(User.id, User.full_name, User.username).where(User.id.in_(inviter_ids)))
        names = {r.id: (r.full_name or r.username) for r in rows}
    projects: dict[str, str] = {}
    if project_ids:
        rows = await db.execute(select(Project.id, Project.name).where(Project.id.in_(project_ids)))
        projects = {str(r.id): r.name for r in rows}
    now = datetime.now(timezone.utc)
    return [
        InvitationSummary(
            id=inv.id,
            email=inv.email,
            role=_normalize_user_role(inv.role),
            status=invitation_status(inv, now),
            expires_at=inv.expires_at,
            created_at=inv.created_at,
            used_at=inv.used_at,
            revoked_at=inv.revoked_at,
            invited_by_name=names.get(inv.invited_by_id) if inv.invited_by_id else None,
            project_memberships=[
                InvitationMembershipView(
                    project_id=m["project_id"],
                    project_name=projects.get(str(m["project_id"])),
                    role=str(m.get("role") or ""),
                )
                for m in (inv.project_memberships or [])
                if isinstance(m, dict) and m.get("project_id")
            ],
        )
        for inv in invitations
    ]


async def _get_invitation_for_update(db: AsyncSession, invitation_id: uuid.UUID) -> UserInvitation:
    invitation = (
        await db.execute(select(UserInvitation).where(UserInvitation.id == invitation_id).with_for_update())
    ).scalar_one_or_none()
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    return invitation


@router.post("/invitations/{invitation_id}/revoke", response_model=InvitationSummary)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Kill an invitation link. A used invitation cannot be revoked (the account exists). ADMIN."""
    invitation = await _get_invitation_for_update(db, invitation_id)
    if invitation_status(invitation) == "used":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This invitation was already accepted")
    if invitation.revoked_at is None:
        invitation.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(invitation)
    logger.info("invitation_revoked", invitation_id=str(invitation.id), by=str(current_user.id))
    return (await _invitation_summaries(db, [invitation]))[0]


@router.post("/invitations/{invitation_id}/resend", response_model=InviteUserResponse)
async def resend_invitation(
    invitation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Issue a new link (new token, new expiry); the previous link stops working. ADMIN.

    Also the only way to get a copyable link again: the raw token is never
    stored, so an old link cannot be shown twice.
    """
    invitation = await _get_invitation_for_update(db, invitation_id)
    state = invitation_status(invitation)
    if state in ("used", "revoked"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This invitation was {'accepted' if state == 'used' else 'revoked'}; send a new invitation instead",
        )
    raw, token_hash = new_token()
    invitation.token_hash = token_hash
    invitation.expires_at = datetime.now(timezone.utc) + INVITATION_LIFETIME
    await db.commit()
    await db.refresh(invitation)
    link = fragment_link(INVITE_PATH, raw)
    email_sent = await _email_invitation(invitation, current_user, link)
    logger.info("invitation_resent", invitation_id=str(invitation.id), email_sent=email_sent, by=str(current_user.id))
    return InviteUserResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        invitation_link=link,
        email_sent=email_sent,
    )


@router.post("/{user_id}/password-reset-link", response_model=PasswordResetLinkResponse, status_code=201)
async def issue_password_reset_link(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """W17b ADM-02: a single-use reset link for another user. ADMIN.

    The administrator never sees or sets the password. Older unused links for
    the user stop working. Refused for the caller's own account (change-password
    exists for that). Audited without the token.
    """
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use Change password for your own account",
        )
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The account is deactivated; activate it first")

    raw, expires_at = await create_password_reset_token(db, user, issued_by=current_user)
    db.add(
        SettingsAuditLog(
            setting_key=f"user:{user.id}",
            action="password_reset_issued",
            actor_id=current_user.id,
            actor_name=current_user.username,
            changed_fields=["password_reset_link"],
        )
    )
    await db.commit()
    link = fragment_link(RESET_PATH, raw)
    email_sent = await email_password_reset(user.email, link, issued_by_admin=True)
    logger.info("password_reset_link_issued", user_id=str(user.id), email_sent=email_sent, by=str(current_user.id))
    return PasswordResetLinkResponse(link=link, email_sent=email_sent, expires_at=expires_at)


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
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
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
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
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
    # Captured BEFORE the mutation: `member` is a live ORM object, so reading
    # `member.role` after the assignment would record the new value as the old
    # one and the audit row would show a change from X to X.
    previous_role = member.role
    member.role = _normalize_user_role(payload.role).value
    logger.info("updating_project_member_role", project_id=str(project_id), user_id=str(user_id),
                before=previous_role, after=member.role, by=str(current_user.id))
    # A privilege change inside a project was not audited at all, while the
    # grant beside it was. An admin could escalate a member to a higher project
    # role and the access trail showed only the original, lower grant.
    await log_access_change(db, "member_role_changed", current_user, target_user_id=user_id,
                            project_id=project_id,
                            before_value={"role": previous_role},
                            after_value={"role": member.role})
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
    # N20: require_project_access() confines a project-bound key to {project_id}.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
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
    # Read the role off the row before it is deleted -- afterwards there is
    # nothing left to say WHAT was revoked, only that something was.
    revoked_role = member.role
    logger.info("removing_project_member", project_id=str(project_id), user_id=str(user_id),
                role=revoked_role, by=str(current_user.id))
    # Revocation was the one membership event with no audit row. The trail
    # recorded who was granted access and never who lost it, so it could show a
    # user as a current member of a project they had been removed from -- the
    # exact question an access review asks.
    await log_access_change(db, "member_removed", current_user, target_user_id=user_id,
                            project_id=project_id,
                            before_value={"role": revoked_role},
                            after_value=None)
    await db.delete(member)
    await db.commit()

    # P3-2: Invalidate membership cache for the removed user
    from app.core.deps import invalidate_membership_cache
    await invalidate_membership_cache(user_id)

    return None
