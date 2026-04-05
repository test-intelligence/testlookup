"""SCIM 2.0 provisioning service — user create/update/deactivate with bearer token auth."""
import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.models.postgres import (
    FederatedIdentity,
    IdentityEventType,
    SCIMToken,
    SSOConfiguration,
    User,
    UserRole,
)
from app.services.sso_service import log_identity_event, resolve_role_from_groups

logger = logging.getLogger(__name__)


# ── SCIM Token management ───────────────────────────────────────────────────


def _hash_token(raw_token: str) -> str:
    """SHA-256 hash of a SCIM bearer token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def create_scim_token(
    db: AsyncSession,
    name: str,
    created_by_id: uuid.UUID,
    sso_config_id: uuid.UUID | None = None,
    expires_days: int | None = None,
) -> tuple[SCIMToken, str]:
    """Create a new SCIM bearer token. Returns (token_record, raw_token)."""
    raw_token = f"scim_{secrets.token_urlsafe(32)}"
    token_hash = _hash_token(raw_token)
    token_hint = raw_token[:8] + "..."

    expires_at = None
    if expires_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)

    scim_token = SCIMToken(
        name=name,
        token_hash=token_hash,
        token_hint=token_hint,
        sso_config_id=sso_config_id,
        created_by_id=created_by_id,
        expires_at=expires_at,
    )
    db.add(scim_token)
    await db.flush()
    return scim_token, raw_token


async def validate_scim_token(db: AsyncSession, bearer_token: str) -> Optional[SCIMToken]:
    """Validate a SCIM bearer token. Returns the SCIMToken record or None."""
    token_hash = _hash_token(bearer_token)
    result = await db.execute(
        select(SCIMToken).where(
            SCIMToken.token_hash == token_hash,
            SCIMToken.is_active == True,  # noqa: E712
        )
    )
    token = result.scalar_one_or_none()
    if token is None:
        return None

    # Check expiry
    if token.expires_at and token.expires_at < datetime.now(timezone.utc):
        return None

    # Update last_used_at
    token.last_used_at = datetime.now(timezone.utc)
    return token


# ── SCIM User operations ────────────────────────────────────────────────────


async def scim_create_user(
    db: AsyncSession,
    username: str,
    email: str,
    display_name: str | None = None,
    external_id: str | None = None,
    groups: list[str] | None = None,
    active: bool = True,
    sso_config_id: uuid.UUID | None = None,
    ip_address: str | None = None,
) -> User:
    """Create a new user via SCIM provisioning."""
    # Check for existing user by email
    result = await db.execute(select(User).where(User.email == email))
    existing = result.scalar_one_or_none()
    if existing:
        raise ValueError(f"User with email '{email}' already exists")

    # Check for existing username
    result = await db.execute(select(User).where(User.username == username))
    if result.scalar_one_or_none():
        raise ValueError(f"User with username '{username}' already exists")

    # Resolve role from groups
    config = None
    if sso_config_id:
        cfg_result = await db.execute(
            select(SSOConfiguration).where(SSOConfiguration.id == sso_config_id)
        )
        config = cfg_result.scalar_one_or_none()

    role = UserRole.VIEWER
    if config and groups:
        role = resolve_role_from_groups(groups, config.role_mapping, config.default_role)

    user = User(
        email=email,
        username=username,
        full_name=display_name,
        hashed_password=get_password_hash(secrets.token_urlsafe(32)),
        role=role,
        is_active=active,
        must_change_password=False,
    )
    db.add(user)
    await db.flush()

    # Create federated identity link if SSO config provided
    if sso_config_id and external_id:
        fed_identity = FederatedIdentity(
            user_id=user.id,
            sso_config_id=sso_config_id,
            external_id=external_id,
            external_email=email,
            external_display_name=display_name,
            external_groups=groups or [],
        )
        db.add(fed_identity)

    await log_identity_event(
        db,
        IdentityEventType.SCIM_USER_CREATED,
        user_id=user.id,
        sso_config_id=sso_config_id,
        detail={"email": email, "username": username, "role": str(role), "external_id": external_id},
        ip_address=ip_address,
    )

    return user


async def scim_update_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    username: str | None = None,
    email: str | None = None,
    display_name: str | None = None,
    active: bool | None = None,
    groups: list[str] | None = None,
    sso_config_id: uuid.UUID | None = None,
    ip_address: str | None = None,
) -> User:
    """Update an existing user via SCIM."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise ValueError(f"User {user_id} not found")

    changes: dict = {}
    if username is not None and username != user.username:
        # Check uniqueness
        dup = await db.execute(select(User).where(User.username == username, User.id != user_id))
        if dup.scalar_one_or_none():
            raise ValueError(f"Username '{username}' is already taken")
        changes["username"] = {"old": user.username, "new": username}
        user.username = username

    if email is not None and email != user.email:
        dup = await db.execute(select(User).where(User.email == email, User.id != user_id))
        if dup.scalar_one_or_none():
            raise ValueError(f"Email '{email}' is already taken")
        changes["email"] = {"old": user.email, "new": email}
        user.email = email

    if display_name is not None:
        user.full_name = display_name

    if active is not None:
        old_active = user.is_active
        user.is_active = active
        if old_active and not active:
            changes["deactivated"] = True
        elif not old_active and active:
            changes["reactivated"] = True

    # Update role from groups if config available
    if groups is not None and sso_config_id:
        cfg_result = await db.execute(
            select(SSOConfiguration).where(SSOConfiguration.id == sso_config_id)
        )
        config = cfg_result.scalar_one_or_none()
        if config:
            new_role = resolve_role_from_groups(groups, config.role_mapping, config.default_role)
            if user.role != new_role:
                changes["role"] = {"old": str(user.role), "new": str(new_role)}
                user.role = new_role

    # Update federated identity groups
    if sso_config_id and groups is not None:
        fed_result = await db.execute(
            select(FederatedIdentity).where(
                FederatedIdentity.user_id == user_id,
                FederatedIdentity.sso_config_id == sso_config_id,
            )
        )
        fed = fed_result.scalar_one_or_none()
        if fed:
            fed.external_groups = groups
            if email:
                fed.external_email = email
            if display_name:
                fed.external_display_name = display_name

    # Determine event type
    event_type = IdentityEventType.SCIM_USER_UPDATED
    if changes.get("deactivated"):
        event_type = IdentityEventType.SCIM_USER_DEACTIVATED
    elif changes.get("reactivated"):
        event_type = IdentityEventType.SCIM_USER_REACTIVATED

    await log_identity_event(
        db,
        event_type,
        user_id=user.id,
        sso_config_id=sso_config_id,
        detail=changes or {"no_changes": True},
        ip_address=ip_address,
    )

    return user


async def scim_get_user(db: AsyncSession, user_id: uuid.UUID) -> Optional[User]:
    """Get a user by ID for SCIM responses."""
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def scim_list_users(
    db: AsyncSession,
    start_index: int = 1,
    count: int = 100,
    filter_str: str | None = None,
) -> tuple[list[User], int]:
    """List users for SCIM with optional filter. Returns (users, total_count)."""
    query = select(User)

    # Basic SCIM filter support: userName eq "value" or email eq "value"
    if filter_str:
        filter_str = filter_str.strip()
        if 'userName eq' in filter_str:
            value = _extract_scim_filter_value(filter_str)
            if value:
                query = query.where(User.username == value)
        elif 'email eq' in filter_str or 'emails.value eq' in filter_str:
            value = _extract_scim_filter_value(filter_str)
            if value:
                query = query.where(User.email == value)
        elif 'externalId eq' in filter_str:
            value = _extract_scim_filter_value(filter_str)
            if value:
                fed_result = await db.execute(
                    select(FederatedIdentity.user_id).where(
                        FederatedIdentity.external_id == value
                    )
                )
                user_ids = [row[0] for row in fed_result.all()]
                if user_ids:
                    query = query.where(User.id.in_(user_ids))
                else:
                    return [], 0

    # Total count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate (SCIM uses 1-based indexing)
    offset = max(0, start_index - 1)
    query = query.order_by(User.created_at).offset(offset).limit(count)
    result = await db.execute(query)
    users = list(result.scalars().all())

    return users, total


def _extract_scim_filter_value(filter_str: str) -> str | None:
    """Extract the value from a simple SCIM filter like 'attr eq \"value\"'."""
    import re

    match = re.search(r'eq\s+"([^"]*)"', filter_str)
    if match:
        return match.group(1)
    match = re.search(r"eq\s+'([^']*)'", filter_str)
    if match:
        return match.group(1)
    return None


def user_to_scim_resource(user: User, base_url: str = "") -> dict:
    """Convert a User model to a SCIM 2.0 User resource dict."""
    resource = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": str(user.id),
        "userName": user.username,
        "displayName": user.full_name or user.username,
        "active": user.is_active,
        "emails": [
            {"value": user.email, "type": "work", "primary": True}
        ],
        "name": {
            "formatted": user.full_name or user.username,
        },
        "meta": {
            "resourceType": "User",
            "created": user.created_at.isoformat() if user.created_at else None,
            "lastModified": (user.updated_at or user.created_at).isoformat() if user.created_at else None,
            "location": f"{base_url}/api/v1/scim/v2/Users/{user.id}" if base_url else f"/api/v1/scim/v2/Users/{user.id}",
        },
    }
    return resource
