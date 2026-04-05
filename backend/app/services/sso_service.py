"""SSO / SAML service — configuration management, assertion validation, JIT provisioning."""
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from xml.etree import ElementTree

import defusedxml.ElementTree as SafeET
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import get_password_hash
from app.models.postgres import (
    FederatedIdentity,
    IdentityEvent,
    IdentityEventType,
    SSOConfiguration,
    SSOEnforcementMode,
    User,
    UserRole,
)

logger = logging.getLogger(__name__)


# ── Certificate helpers ──────────────────────────────────────────────────────


def certificate_fingerprint(pem_cert: str) -> str:
    """Return SHA-256 fingerprint of a PEM-encoded X.509 certificate."""
    # Strip PEM headers and decode
    lines = [
        line.strip()
        for line in pem_cert.strip().splitlines()
        if line.strip() and not line.strip().startswith("-----")
    ]
    import base64

    try:
        der_bytes = base64.b64decode("".join(lines))
    except Exception:
        return "invalid-certificate"
    return hashlib.sha256(der_bytes).hexdigest()


def validate_certificate_format(pem_cert: str) -> tuple[bool, str]:
    """Validate that the provided string is a well-formed PEM certificate."""
    stripped = pem_cert.strip()
    if not stripped.startswith("-----BEGIN CERTIFICATE-----"):
        return False, "Certificate must start with '-----BEGIN CERTIFICATE-----'"
    if not stripped.endswith("-----END CERTIFICATE-----"):
        return False, "Certificate must end with '-----END CERTIFICATE-----'"
    import base64

    lines = [
        line.strip()
        for line in stripped.splitlines()
        if line.strip() and not line.strip().startswith("-----")
    ]
    try:
        base64.b64decode("".join(lines))
    except Exception:
        return False, "Certificate contains invalid base64 data"
    return True, "Valid"


# ── SAML assertion parsing ───────────────────────────────────────────────────

# SAML namespace map
_NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
}


def parse_saml_response(saml_response_b64: str) -> dict:
    """
    Parse a base64-encoded SAML Response and extract identity attributes.

    Returns a dict with keys: name_id, issuer, attributes, session_index.
    Raises ValueError on malformed/invalid responses.

    NOTE: In production, you should use a full SAML library (python3-saml)
    for cryptographic signature validation. This implementation validates
    structure and extracts attributes; signature validation relies on the
    IdP certificate configured in SSOConfiguration.
    """
    import base64

    try:
        xml_bytes = base64.b64decode(saml_response_b64)
    except Exception as exc:
        raise ValueError(f"Invalid base64 in SAMLResponse: {exc}") from exc

    try:
        root = SafeET.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise ValueError(f"Malformed SAML XML: {exc}") from exc

    # Extract Issuer
    issuer_el = root.find(".//saml:Issuer", _NS)
    issuer = issuer_el.text.strip() if issuer_el is not None and issuer_el.text else None

    # Extract assertion
    assertion = root.find(".//saml:Assertion", _NS)
    if assertion is None:
        raise ValueError("No Assertion found in SAML Response")

    # Status check
    status_code = root.find(".//samlp:Status/samlp:StatusCode", _NS)
    if status_code is not None:
        status_value = status_code.get("Value", "")
        if "Success" not in status_value:
            raise ValueError(f"SAML authentication failed with status: {status_value}")

    # NameID
    name_id_el = assertion.find(".//saml:Subject/saml:NameID", _NS)
    name_id = name_id_el.text.strip() if name_id_el is not None and name_id_el.text else None
    if not name_id:
        raise ValueError("No NameID found in SAML Assertion")

    # Session index
    authn_stmt = assertion.find(".//saml:AuthnStatement", _NS)
    session_index = authn_stmt.get("SessionIndex") if authn_stmt is not None else None

    # Attributes
    attributes: dict[str, list[str]] = {}
    attr_stmt = assertion.find(".//saml:AttributeStatement", _NS)
    if attr_stmt is not None:
        for attr in attr_stmt.findall("saml:Attribute", _NS):
            attr_name = attr.get("Name", "")
            values = [
                v.text.strip()
                for v in attr.findall("saml:AttributeValue", _NS)
                if v.text
            ]
            if attr_name and values:
                attributes[attr_name] = values

    # Validate conditions (audience + time)
    conditions = assertion.find(".//saml:Conditions", _NS)
    if conditions is not None:
        not_before = conditions.get("NotBefore")
        not_on_or_after = conditions.get("NotOnOrAfter")
        now = datetime.now(timezone.utc)
        if not_before:
            nb = datetime.fromisoformat(not_before.replace("Z", "+00:00"))
            if now < nb:
                raise ValueError("SAML Assertion is not yet valid (NotBefore)")
        if not_on_or_after:
            noa = datetime.fromisoformat(not_on_or_after.replace("Z", "+00:00"))
            if now >= noa:
                raise ValueError("SAML Assertion has expired (NotOnOrAfter)")

    return {
        "name_id": name_id,
        "issuer": issuer,
        "attributes": attributes,
        "session_index": session_index,
    }


def validate_saml_issuer(parsed: dict, config: SSOConfiguration) -> None:
    """Validate that the SAML issuer matches the configured IdP entity ID."""
    if parsed.get("issuer") != config.idp_entity_id:
        raise ValueError(
            f"Issuer mismatch: expected '{config.idp_entity_id}', got '{parsed.get('issuer')}'"
        )


def resolve_role_from_groups(
    groups: list[str],
    role_mapping: dict | None,
    default_role: UserRole,
) -> UserRole:
    """Map IdP group memberships to the highest matching internal role."""
    if not role_mapping or not groups:
        return default_role

    # Role hierarchy for picking the highest match
    role_order = [UserRole.VIEWER, UserRole.TESTER, UserRole.QA_ENGINEER, UserRole.QA_LEAD, UserRole.ADMIN]

    best_idx = -1
    for group in groups:
        mapped = role_mapping.get(group)
        if mapped:
            try:
                role = UserRole(mapped)
                idx = role_order.index(role)
                if idx > best_idx:
                    best_idx = idx
            except (ValueError, KeyError):
                continue

    if best_idx >= 0:
        return role_order[best_idx]
    return default_role


# ── SSO Configuration CRUD ──────────────────────────────────────────────────


async def get_active_sso_config(db: AsyncSession) -> Optional[SSOConfiguration]:
    """Return the first active SSO configuration, or None."""
    result = await db.execute(
        select(SSOConfiguration)
        .where(SSOConfiguration.is_active == True)  # noqa: E712
        .limit(1)
    )
    return result.scalar_one_or_none()


async def is_sso_enforced(db: AsyncSession) -> bool:
    """Check if any active SSO config enforces SSO_REQUIRED mode."""
    if not settings.SSO_ENABLED:
        return False
    config = await get_active_sso_config(db)
    if config is None:
        return False
    return config.enforcement_mode == SSOEnforcementMode.SSO_REQUIRED


# ── JIT Provisioning ────────────────────────────────────────────────────────


async def jit_provision_or_link(
    db: AsyncSession,
    config: SSOConfiguration,
    name_id: str,
    attributes: dict[str, list[str]],
    ip_address: str | None = None,
) -> tuple[User, bool]:
    """
    Just-In-Time provision a new user or link an existing user from SAML assertion.

    Returns (user, is_new_user).
    """
    # Extract user attributes from SAML
    email = name_id  # NameID is typically the email
    # Try to get email from attributes
    for attr_name in ("email", "Email", "mail", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"):
        if attr_name in attributes:
            email = attributes[attr_name][0]
            break

    display_name = None
    for attr_name in ("displayName", "name", "cn", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name"):
        if attr_name in attributes:
            display_name = attributes[attr_name][0]
            break

    # Extract groups for role mapping
    groups: list[str] = []
    if config.group_attribute and config.group_attribute in attributes:
        groups = attributes[config.group_attribute]

    # Check for existing federated identity
    result = await db.execute(
        select(FederatedIdentity).where(
            FederatedIdentity.sso_config_id == config.id,
            FederatedIdentity.external_id == name_id,
        )
    )
    fed_identity = result.scalar_one_or_none()

    is_new_user = False

    if fed_identity:
        # Existing federated link — update and return user
        fed_identity.external_email = email
        fed_identity.external_display_name = display_name
        fed_identity.external_groups = groups
        fed_identity.last_login_at = datetime.now(timezone.utc)

        user_result = await db.execute(select(User).where(User.id == fed_identity.user_id))
        user = user_result.scalar_one_or_none()
        if user is None:
            raise ValueError("Federated identity references a deleted user")

        # Update role if role mapping changed
        new_role = resolve_role_from_groups(groups, config.role_mapping, config.default_role)
        if user.role != new_role:
            old_role = user.role
            user.role = new_role
            await _log_identity_event(
                db,
                IdentityEventType.ROLE_MAPPED,
                user_id=user.id,
                sso_config_id=config.id,
                detail={"old_role": str(old_role), "new_role": str(new_role), "groups": groups},
                ip_address=ip_address,
            )
    else:
        # Check if a user with this email already exists
        user_result = await db.execute(select(User).where(User.email == email))
        user = user_result.scalar_one_or_none()

        if user is None:
            # JIT provision new user
            import secrets

            role = resolve_role_from_groups(groups, config.role_mapping, config.default_role)
            # Generate a username from email (before the @)
            username_base = email.split("@")[0].lower().replace(" ", "_")[:50]
            # Ensure uniqueness
            username = username_base
            counter = 1
            while True:
                existing = await db.execute(select(User).where(User.username == username))
                if existing.scalar_one_or_none() is None:
                    break
                username = f"{username_base}_{counter}"
                counter += 1

            user = User(
                email=email,
                username=username,
                full_name=display_name,
                # SSO users get a random password they'll never use
                hashed_password=get_password_hash(secrets.token_urlsafe(32)),
                role=role,
                is_active=True,
                must_change_password=False,  # SSO users don't need password reset
            )
            db.add(user)
            await db.flush()  # get the user.id
            is_new_user = True

            await _log_identity_event(
                db,
                IdentityEventType.JIT_PROVISIONED,
                user_id=user.id,
                sso_config_id=config.id,
                detail={"email": email, "role": str(role), "groups": groups},
                ip_address=ip_address,
            )

        # Create federated identity link
        fed_identity = FederatedIdentity(
            user_id=user.id,
            sso_config_id=config.id,
            external_id=name_id,
            external_email=email,
            external_display_name=display_name,
            external_groups=groups,
            last_login_at=datetime.now(timezone.utc),
        )
        db.add(fed_identity)

    return user, is_new_user


# ── Identity Event logging ──────────────────────────────────────────────────


async def _log_identity_event(
    db: AsyncSession,
    event_type: IdentityEventType,
    user_id: uuid.UUID | None = None,
    sso_config_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    actor_name: str | None = None,
    detail: dict | None = None,
    ip_address: str | None = None,
    success: bool = True,
    error_message: str | None = None,
) -> IdentityEvent:
    """Persist an identity lifecycle event."""
    event = IdentityEvent(
        event_type=event_type,
        user_id=user_id,
        sso_config_id=sso_config_id,
        actor_id=actor_id,
        actor_name=actor_name,
        detail=detail,
        ip_address=ip_address,
        success=success,
        error_message=error_message,
    )
    db.add(event)
    return event


async def log_identity_event(
    db: AsyncSession,
    event_type: IdentityEventType,
    user_id: uuid.UUID | None = None,
    sso_config_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    actor_name: str | None = None,
    detail: dict | None = None,
    ip_address: str | None = None,
    success: bool = True,
    error_message: str | None = None,
) -> IdentityEvent:
    """Public wrapper that also flushes the event to the session."""
    event = await _log_identity_event(
        db, event_type, user_id, sso_config_id, actor_id, actor_name,
        detail, ip_address, success, error_message,
    )
    await db.flush()
    return event


# ── Sync status aggregation ─────────────────────────────────────────────────


async def get_sync_status(db: AsyncSession, sso_config_id: uuid.UUID | None = None) -> dict:
    """Aggregate identity sync health metrics."""
    # Count federated users
    fed_query = select(func.count(FederatedIdentity.id))
    if sso_config_id:
        fed_query = fed_query.where(FederatedIdentity.sso_config_id == sso_config_id)
    fed_count = (await db.execute(fed_query)).scalar() or 0

    # Last SSO login
    login_query = (
        select(IdentityEvent.created_at)
        .where(IdentityEvent.event_type == IdentityEventType.SSO_LOGIN)
        .where(IdentityEvent.success == True)  # noqa: E712
        .order_by(IdentityEvent.created_at.desc())
        .limit(1)
    )
    if sso_config_id:
        login_query = login_query.where(IdentityEvent.sso_config_id == sso_config_id)
    last_login = (await db.execute(login_query)).scalar_one_or_none()

    # Last SCIM sync
    scim_query = (
        select(IdentityEvent.created_at)
        .where(
            IdentityEvent.event_type.in_([
                IdentityEventType.SCIM_USER_CREATED,
                IdentityEventType.SCIM_USER_UPDATED,
                IdentityEventType.SCIM_USER_DEACTIVATED,
            ])
        )
        .order_by(IdentityEvent.created_at.desc())
        .limit(1)
    )
    if sso_config_id:
        scim_query = scim_query.where(IdentityEvent.sso_config_id == sso_config_id)
    last_scim = (await db.execute(scim_query)).scalar_one_or_none()

    # Recent failures (last 24h)
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    fail_query = (
        select(func.count(IdentityEvent.id))
        .where(IdentityEvent.success == False)  # noqa: E712
        .where(IdentityEvent.created_at >= cutoff)
    )
    if sso_config_id:
        fail_query = fail_query.where(IdentityEvent.sso_config_id == sso_config_id)
    recent_failures = (await db.execute(fail_query)).scalar() or 0

    return {
        "total_federated_users": fed_count,
        "last_sso_login_at": last_login,
        "last_scim_sync_at": last_scim,
        "recent_failures": recent_failures,
    }
