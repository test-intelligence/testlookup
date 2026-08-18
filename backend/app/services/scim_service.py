"""SCIM 2.0 provisioning service — user create/update/deactivate with bearer token auth."""
import hashlib
import logging
import re
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

_SCIM_EQUALITY_FILTER = re.compile(
    r"^(userName|email|emails\.value|externalId)\s+eq\s+(?:\"([^\"]+)\"|'([^']+)')$",
    re.IGNORECASE,
)


class SCIMUserNotFoundError(ValueError):
    """Raised when a SCIM user does not exist within the token's directory scope."""


def _normalize_group_refs(groups: list | None) -> list[dict[str, str]]:
    """Normalize legacy string groups and canonical SCIM group references."""
    refs: list[dict[str, str]] = []
    for group in groups or []:
        if isinstance(group, str) and group:
            refs.append({"value": group})
        elif isinstance(group, dict):
            value = group.get("value")
            display = group.get("display")
            if isinstance(value, str) and value:
                ref = {"value": value}
                if isinstance(display, str) and display:
                    ref["display"] = display
                refs.append(ref)
    return refs


def _group_role_names(refs: list[dict[str, str]]) -> list[str]:
    return [ref.get("display") or ref["value"] for ref in refs]


def _group_ref_matches(candidate: dict[str, str], target: dict[str, str]) -> bool:
    """Match removals by stable value, with display fallback for legacy callers."""
    needles = {value for value in target.values() if value}
    return candidate["value"] in needles or candidate.get("display") in needles


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

    # A token bound to an IdP is delegated authority from that configuration.
    # Replacing or disabling the IdP must therefore revoke that authority even
    # when the token row itself has not been manually revoked. Unbound tokens
    # remain intentionally system-wide.
    if token.sso_config_id is not None:
        config_result = await db.execute(
            select(SSOConfiguration.is_active).where(
                SSOConfiguration.id == token.sso_config_id
            )
        )
        if config_result.scalar_one_or_none() is not True:
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
    group_refs: list[dict[str, str]] | None = None,
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
            external_groups=group_refs if group_refs is not None else groups or [],
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
    external_id: str | None = None,
    active: bool | None = None,
    groups: list[str] | None = None,
    group_refs: list[dict[str, str]] | None = None,
    group_operations: list[tuple[str, list[dict[str, str]] | None]] | None = None,
    sso_config_id: uuid.UUID | None = None,
    ip_address: str | None = None,
) -> User:
    """Update an existing user via SCIM."""
    query = select(User).where(User.id == user_id)
    query = _scope_user_query(query, sso_config_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    if user is None:
        raise SCIMUserNotFoundError(f"User {user_id} not found")

    fed = None
    if group_operations:
        if sso_config_id is None:
            raise ValueError("SCIM group add/remove operations require an IdP-bound token")
        fed_result = await db.execute(
            select(FederatedIdentity).where(
                FederatedIdentity.user_id == user_id,
                FederatedIdentity.sso_config_id == sso_config_id,
            )
        )
        fed = fed_result.scalar_one_or_none()
        if fed is None:
            raise SCIMUserNotFoundError(f"User {user_id} not found")

        patched_refs = _normalize_group_refs(fed.external_groups)
        for operation, refs in group_operations:
            if operation == "replace":
                patched_refs = _normalize_group_refs(refs)
            elif operation == "add":
                for ref in _normalize_group_refs(refs):
                    if not any(existing["value"] == ref["value"] for existing in patched_refs):
                        patched_refs.append(ref)
            elif operation == "remove":
                targets = _normalize_group_refs(refs)
                patched_refs = [
                    candidate
                    for candidate in patched_refs
                    if not any(_group_ref_matches(candidate, target) for target in targets)
                ]
        group_refs = patched_refs
        groups = _group_role_names(patched_refs)

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

    # Update federated identity attributes owned by this directory.
    if sso_config_id and (groups is not None or external_id is not None):
        if fed is None:
            fed_result = await db.execute(
                select(FederatedIdentity).where(
                    FederatedIdentity.user_id == user_id,
                    FederatedIdentity.sso_config_id == sso_config_id,
                )
            )
            fed = fed_result.scalar_one_or_none()
        if fed:
            if external_id is not None and external_id != fed.external_id:
                changes["external_id"] = {"old": fed.external_id, "new": external_id}
                fed.external_id = external_id
            if groups is not None:
                fed.external_groups = group_refs if group_refs is not None else groups
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


def _scope_user_query(query, sso_config_id: uuid.UUID | None):
    """Restrict a user query to identities delegated to one SSO configuration."""
    if sso_config_id is None:
        return query
    return query.where(
        User.id.in_(
            select(FederatedIdentity.user_id).where(
                FederatedIdentity.sso_config_id == sso_config_id
            )
        )
    )


async def scim_get_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    sso_config_id: uuid.UUID | None = None,
) -> Optional[User]:
    """Get a user by ID for SCIM responses."""
    query = _scope_user_query(select(User).where(User.id == user_id), sso_config_id)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def scim_list_users(
    db: AsyncSession,
    start_index: int = 1,
    count: int = 100,
    filter_str: str | None = None,
    sso_config_id: uuid.UUID | None = None,
) -> tuple[list[User], int]:
    """List users for SCIM with optional filter. Returns (users, total_count)."""
    query = _scope_user_query(select(User), sso_config_id)

    # Basic SCIM filter support: userName eq "value" or email eq "value".
    # An unrecognised filter must NOT silently return the whole directory —
    # that surprises an IdP expecting a narrowed result. Return empty instead.
    if filter_str:
        parsed_filter = _parse_scim_filter(filter_str)
        if parsed_filter is None:
            logger.warning("Unsupported SCIM filter, returning empty result: %s", filter_str)
            return [], 0
        attribute, value = parsed_filter
        if attribute == "username":
            query = query.where(User.username == value)
        elif attribute in {"email", "emails.value"}:
            query = query.where(User.email == value)
        else:
            fed_result = await db.execute(
                select(FederatedIdentity.user_id).where(
                    FederatedIdentity.external_id == value,
                    *(
                        [FederatedIdentity.sso_config_id == sso_config_id]
                        if sso_config_id is not None
                        else []
                    ),
                )
            )
            user_ids = [row[0] for row in fed_result.all()]
            if not user_ids:
                return [], 0
            query = query.where(User.id.in_(user_ids))

    # Total count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # RFC 7644 permits count=0 when a client needs only totalResults.
    if count == 0:
        return [], total

    # Paginate (SCIM uses 1-based indexing)
    offset = max(0, start_index - 1)
    query = query.order_by(User.created_at, User.id).offset(offset).limit(count)
    result = await db.execute(query)
    users = list(result.scalars().all())

    return users, total


async def scim_identity_map(
    db: AsyncSession,
    user_ids: list[uuid.UUID],
    sso_config_id: uuid.UUID | None,
) -> dict[uuid.UUID, FederatedIdentity]:
    """Batch-load response identity metadata for one bound SCIM directory."""
    if sso_config_id is None or not user_ids:
        return {}
    result = await db.execute(
        select(FederatedIdentity).where(
            FederatedIdentity.user_id.in_(user_ids),
            FederatedIdentity.sso_config_id == sso_config_id,
        )
    )
    return {identity.user_id: identity for identity in result.scalars().all()}


def _parse_scim_filter(filter_str: str) -> tuple[str, str] | None:
    """Parse one complete supported SCIM equality predicate."""
    match = _SCIM_EQUALITY_FILTER.fullmatch(filter_str.strip())
    if match is None:
        return None
    return match.group(1).lower(), match.group(2) or match.group(3)


def _extract_scim_filter_value(filter_str: str) -> str | None:
    """Return a supported filter value for compatibility with existing callers."""
    parsed_filter = _parse_scim_filter(filter_str)
    return parsed_filter[1] if parsed_filter is not None else None


def user_to_scim_resource(
    user: User,
    base_url: str = "",
    federated_identity: FederatedIdentity | None = None,
) -> dict:
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
    if federated_identity is not None:
        resource["externalId"] = federated_identity.external_id
        resource["groups"] = _normalize_group_refs(federated_identity.external_groups)
    return resource
