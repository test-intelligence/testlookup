"""SCIM 2.0 provisioning router — user lifecycle management for IdP integration."""
import json
import logging
import re
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import require_role
from app.core.scim_errors import SCIMJSONResponse
from app.db.postgres import get_db
from app.models.postgres import IdentityEventType, SCIMToken, User, UserRole
from app.models.schemas import (
    SCIM_DISPLAY_NAME_MAX_LENGTH,
    SCIM_EMAILS_MAX_ITEMS,
    SCIM_EXTERNAL_ID_MAX_LENGTH,
    SCIM_GROUP_DISPLAY_MAX_LENGTH,
    SCIM_GROUP_VALUE_MAX_LENGTH,
    SCIM_GROUPS_MAX_ITEMS,
    SCIM_USER_SCHEMA,
    SCIM_USERNAME_MAX_LENGTH,
    SCIMEmail,
    SCIMListResponse,
    SCIMPatchRequestPayload,
    SCIMTokenCreate,
    SCIMTokenCreatedResponse,
    SCIMTokenResponse,
    SCIMUserRequest,
    SCIMUserResource,
)
from app.services.scim_service import (
    SCIMInvalidFilterError,
    SCIMUserNotFoundError,
    create_scim_token,
    scim_create_user,
    scim_get_user,
    scim_identity_map,
    scim_list_users,
    scim_update_user,
    user_to_scim_resource,
    validate_scim_token,
)
from app.services.sso_service import log_identity_event

logger = logging.getLogger(__name__)

_SCIM_PATCH_PATHS = {
    "username": "userName",
    "externalid": "externalId",
    "active": "active",
    "displayname": "displayName",
    "emails": "emails",
    "groups": "groups",
}

router = APIRouter(
    prefix="/api/v1/scim/v2",
    tags=["SCIM 2.0"],
    default_response_class=SCIMJSONResponse,
)

# SCIM token management router (protected — admin only)
token_router = APIRouter(prefix="/api/v1/scim-tokens", tags=["SCIM Tokens"])


async def _raise_scim_integrity_conflict(db: AsyncSession, exc: IntegrityError) -> None:
    """Recover a failed transaction and expose no database implementation detail."""
    await db.rollback()
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="SCIM resource conflicts with existing directory data",
    ) from exc


def _scim_group_refs(value: object) -> list[dict[str, str]] | None:
    """Preserve SCIM group identity and display data in a canonical form."""
    if not isinstance(value, list) or len(value) > SCIM_GROUPS_MAX_ITEMS:
        return None
    refs_by_value: dict[str, dict[str, str]] = {}
    for item in value:
        if isinstance(item, dict):
            group_value = item.get("value")
            display = item.get("display")
        elif hasattr(item, "value"):
            group_value = item.value
            display = getattr(item, "display", None)
        else:
            group_value = item if isinstance(item, str) else None
            display = None
        if (not isinstance(group_value, str) or not group_value) and isinstance(display, str):
            group_value = display
        if not isinstance(group_value, str) or not group_value:
            return None
        if len(group_value) > SCIM_GROUP_VALUE_MAX_LENGTH:
            return None
        if isinstance(display, str) and len(display) > SCIM_GROUP_DISPLAY_MAX_LENGTH:
            return None
        ref = {"value": group_value}
        if isinstance(display, str) and display:
            ref["display"] = display
        existing = refs_by_value.get(group_value)
        if existing is not None:
            existing_display = existing.get("display")
            new_display = ref.get("display")
            if existing_display and new_display and existing_display != new_display:
                return None
            if not existing_display and new_display:
                existing["display"] = new_display
            continue
        refs_by_value[group_value] = ref
    return list(refs_by_value.values())


def _scim_patch_email(value: object) -> str | None:
    """Return the primary/first email from a valid SCIM email array."""
    if not isinstance(value, list) or not value or len(value) > SCIM_EMAILS_MAX_ITEMS:
        return None
    try:
        candidates = [SCIMEmail.model_validate(item) for item in value]
    except ValidationError:
        return None
    primary = [item for item in candidates if item.primary]
    if len(primary) > 1:
        return None
    selected = str((primary[0] if primary else candidates[0]).value).strip().casefold()
    return selected or None


def _scim_patch_string(
    value: object,
    attribute: str,
    max_length: int,
    *,
    allow_empty: bool = False,
) -> str:
    """Validate a PATCH scalar against its persistence boundary."""
    if not isinstance(value, str) or (not allow_empty and not value):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise HTTPException(status_code=400, detail=f"{attribute} must be {qualifier}")
    if len(value) > max_length:
        raise HTTPException(
            status_code=400,
            detail=f"{attribute} must be at most {max_length} characters",
        )
    return value


def _scim_group_names(refs: list[dict[str, str]]) -> list[str]:
    """Return display-first names used by configured role mappings."""
    return [ref.get("display") or ref["value"] for ref in refs]


def _scim_group_filter_name(path: str | None) -> str | None:
    """Extract a group name from ``groups[value eq \"name\"]``."""
    if not path:
        return None
    match = re.fullmatch(
        r'groups\[value\s+eq\s+("(?:\\.|[^"\\])*")\]',
        path,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, str) and value else None


def _canonical_scim_patch_path(path: str | None) -> str | None:
    """Canonicalize SCIM attribute names without modifying filter values."""
    if path is None:
        return None
    unqualified = path
    core_prefix = f"{SCIM_USER_SCHEMA}:"
    if path.casefold().startswith(core_prefix.casefold()):
        unqualified = path[len(core_prefix):]
    return _SCIM_PATCH_PATHS.get(unqualified.casefold(), unqualified)


def _canonical_scim_patch_object(value: dict) -> dict:
    """Canonicalize bulk keys and reject aliases that target one attribute twice."""
    canonical: dict = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("PATCH attribute names must be strings")
        canonical_key = _canonical_scim_patch_path(key)
        if canonical_key not in _SCIM_PATCH_PATHS.values():
            raise ValueError(f"Unsupported PATCH attribute: {key}")
        if canonical_key in canonical:
            raise ValueError(f"Duplicate PATCH attribute: {canonical_key}")
        canonical[canonical_key] = item
    return canonical


# ── SCIM Bearer Token Authentication ────────────────────────────────────────


async def verify_scim_bearer(
    request: Request,
    authorization: str = Header(..., alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> SCIMToken:
    """Validate the SCIM bearer token from the Authorization header."""
    if not settings.SCIM_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SCIM provisioning is not enabled",
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization scheme — expected Bearer token",
        )

    bearer_token = authorization[7:]
    token = await validate_scim_token(db, bearer_token)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired SCIM token",
        )

    return token


# ── SCIM User Endpoints ─────────────────────────────────────────────────────


@router.get("/Users")
async def scim_list(
    request: Request,
    startIndex: int = Query(1, ge=1, description="1-based start index (SCIM)"),
    count: int = Query(100, ge=0, le=200, description="Page size (bounded to protect the directory)"),
    filter: str | None = None,
    scim_token: SCIMToken = Depends(verify_scim_bearer),
    db: AsyncSession = Depends(get_db),
):
    """SCIM 2.0: List users."""
    try:
        users, total = await scim_list_users(
            db,
            start_index=startIndex,
            count=count,
            filter_str=filter,
            sso_config_id=scim_token.sso_config_id,
        )
    except SCIMInvalidFilterError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"detail": str(exc), "scimType": "invalidFilter"},
        ) from exc
    identities = await scim_identity_map(
        db,
        [user.id for user in users],
        scim_token.sso_config_id,
    )
    base_url = str(request.base_url).rstrip("/")

    return SCIMListResponse(
        totalResults=total,
        startIndex=startIndex,
        itemsPerPage=len(users),
        Resources=[
            SCIMUserResource(**user_to_scim_resource(u, base_url, identities.get(u.id)))
            for u in users
        ],
    )


@router.get("/Users/{user_id}")
async def scim_get(
    user_id: uuid.UUID,
    request: Request,
    scim_token: SCIMToken = Depends(verify_scim_bearer),
    db: AsyncSession = Depends(get_db),
):
    """SCIM 2.0: Get a single user."""
    user = await scim_get_user(db, user_id, sso_config_id=scim_token.sso_config_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    identities = await scim_identity_map(db, [user.id], scim_token.sso_config_id)
    base_url = str(request.base_url).rstrip("/")
    return SCIMUserResource(
        **user_to_scim_resource(user, base_url, identities.get(user.id))
    )


@router.post("/Users", status_code=201)
async def scim_create(
    payload: SCIMUserRequest,
    request: Request,
    scim_token: SCIMToken = Depends(verify_scim_bearer),
    db: AsyncSession = Depends(get_db),
):
    """SCIM 2.0: Create a user."""
    if not payload.emails:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one email is required",
        )
    email = _scim_patch_email(payload.emails)
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="emails must contain valid values and at most one primary",
        )
    if scim_token.sso_config_id is not None and not payload.externalId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="externalId is required for an IdP-bound SCIM token",
        )

    display_name = payload.displayName
    if not display_name and payload.name:
        parts = [payload.name.givenName, payload.name.familyName]
        display_name = " ".join(p for p in parts if p) or None

    group_refs = _scim_group_refs(payload.groups)
    if group_refs is None:
        raise HTTPException(status_code=400, detail="groups contain conflicting or invalid values")
    groups = _scim_group_names(group_refs)
    client_ip = request.client.host if request.client else None

    try:
        user = await scim_create_user(
            db=db,
            username=payload.userName,
            email=email,
            display_name=display_name,
            external_id=payload.externalId,
            groups=groups,
            group_refs=group_refs,
            active=payload.active,
            sso_config_id=scim_token.sso_config_id,
            ip_address=client_ip,
        )
        await db.commit()
        await db.refresh(user)
    except IntegrityError as exc:
        await _raise_scim_integrity_conflict(db, exc)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    base_url = str(request.base_url).rstrip("/")
    identities = await scim_identity_map(db, [user.id], scim_token.sso_config_id)
    return SCIMUserResource(
        **user_to_scim_resource(user, base_url, identities.get(user.id))
    )


@router.put("/Users/{user_id}")
async def scim_replace(
    user_id: uuid.UUID,
    payload: SCIMUserRequest,
    request: Request,
    scim_token: SCIMToken = Depends(verify_scim_bearer),
    db: AsyncSession = Depends(get_db),
):
    """SCIM 2.0: Replace (full update) a user."""
    if scim_token.sso_config_id is not None and not payload.externalId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="externalId is required for an IdP-bound SCIM token",
        )
    if not payload.emails:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one email is required",
        )
    email = _scim_patch_email(payload.emails)
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="emails must contain valid values and at most one primary",
        )

    display_name = payload.displayName
    if not display_name and payload.name:
        parts = [payload.name.givenName, payload.name.familyName]
        display_name = " ".join(p for p in parts if p) or None

    group_refs = _scim_group_refs(payload.groups)
    if group_refs is None:
        raise HTTPException(status_code=400, detail="groups contain conflicting or invalid values")
    groups = _scim_group_names(group_refs)
    client_ip = request.client.host if request.client else None

    try:
        user = await scim_update_user(
            db=db,
            user_id=user_id,
            username=payload.userName,
            email=email,
            display_name=display_name,
            external_id=payload.externalId,
            active=payload.active,
            groups=groups,
            group_refs=group_refs,
            sso_config_id=scim_token.sso_config_id,
            ip_address=client_ip,
            full_replace=True,
        )
        await db.commit()
        await db.refresh(user)
    except IntegrityError as exc:
        await _raise_scim_integrity_conflict(db, exc)
    except SCIMUserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    base_url = str(request.base_url).rstrip("/")
    identities = await scim_identity_map(db, [user.id], scim_token.sso_config_id)
    return SCIMUserResource(
        **user_to_scim_resource(user, base_url, identities.get(user.id))
    )


@router.patch("/Users/{user_id}")
async def scim_patch(
    user_id: uuid.UUID,
    payload: SCIMPatchRequestPayload,
    request: Request,
    scim_token: SCIMToken = Depends(verify_scim_bearer),
    db: AsyncSession = Depends(get_db),
):
    """SCIM 2.0: Patch (partial update) a user."""
    client_ip = request.client.host if request.client else None

    username = None
    email = None
    display_name = None
    external_id = None
    active = None
    groups = None
    group_operations: list[tuple[str, list[dict[str, str]] | None]] = []

    if not payload.Operations:
        raise HTTPException(status_code=400, detail="At least one PATCH operation is required")

    for op in payload.Operations:
        op_type = op.op.lower()
        path = _canonical_scim_patch_path(op.path)
        if op_type == "replace":
            if path == "userName":
                username = _scim_patch_string(
                    op.value, "userName", SCIM_USERNAME_MAX_LENGTH
                )
            elif path == "externalId":
                external_id = _scim_patch_string(
                    op.value, "externalId", SCIM_EXTERNAL_ID_MAX_LENGTH
                )
            elif path == "active":
                if not isinstance(op.value, bool):
                    raise HTTPException(status_code=400, detail="active must be a boolean")
                active = op.value
            elif path == "displayName":
                display_name = _scim_patch_string(
                    op.value,
                    "displayName",
                    SCIM_DISPLAY_NAME_MAX_LENGTH,
                    allow_empty=True,
                )
            elif path == "emails":
                email = _scim_patch_email(op.value)
                if email is None:
                    raise HTTPException(status_code=400, detail="emails must contain a valid value")
            elif path == "groups":
                refs = _scim_group_refs(op.value)
                if refs is None:
                    raise HTTPException(status_code=400, detail="groups must be an array")
                group_operations.append(("replace", refs))
            elif path is None and isinstance(op.value, dict):
                # Bulk replace
                try:
                    bulk_value = _canonical_scim_patch_object(op.value)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                if "userName" in bulk_value:
                    username = _scim_patch_string(
                        bulk_value["userName"], "userName", SCIM_USERNAME_MAX_LENGTH
                    )
                if "externalId" in bulk_value:
                    external_id = _scim_patch_string(
                        bulk_value["externalId"],
                        "externalId",
                        SCIM_EXTERNAL_ID_MAX_LENGTH,
                    )
                if "active" in bulk_value:
                    if not isinstance(bulk_value["active"], bool):
                        raise HTTPException(status_code=400, detail="active must be a boolean")
                    active = bulk_value["active"]
                if "displayName" in bulk_value:
                    display_name = _scim_patch_string(
                        bulk_value["displayName"],
                        "displayName",
                        SCIM_DISPLAY_NAME_MAX_LENGTH,
                        allow_empty=True,
                    )
                if "emails" in bulk_value:
                    email = _scim_patch_email(bulk_value["emails"])
                    if email is None:
                        raise HTTPException(status_code=400, detail="emails must contain a valid value")
                if "groups" in bulk_value:
                    refs = _scim_group_refs(bulk_value["groups"])
                    if refs is None:
                        raise HTTPException(status_code=400, detail="groups must be an array")
                    group_operations.append(("replace", refs))
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported PATCH path: {op.path}")
        elif op_type == "add" and path == "groups":
            refs = _scim_group_refs(op.value)
            if refs is None:
                raise HTTPException(status_code=400, detail="groups must be an array")
            group_operations.append(("add", refs))
        elif op_type == "remove":
            filtered_name = _scim_group_filter_name(path)
            if filtered_name is not None:
                group_operations.append(("remove", [{"value": filtered_name}]))
            elif path == "groups":
                if op.value is None:
                    group_operations.append(("replace", []))
                else:
                    refs = _scim_group_refs(op.value)
                    if refs is None:
                        raise HTTPException(status_code=400, detail="groups must be an array")
                    group_operations.append(("remove", refs))
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported PATCH path: {op.path}")
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported PATCH operation: {op.op}")

    if external_id is not None and scim_token.sso_config_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="externalId updates require an IdP-bound SCIM token",
        )

    try:
        user = await scim_update_user(
            db=db,
            user_id=user_id,
            username=username,
            email=email,
            display_name=display_name,
            external_id=external_id,
            active=active,
            groups=groups,
            group_operations=group_operations or None,
            sso_config_id=scim_token.sso_config_id,
            ip_address=client_ip,
        )
        await db.commit()
        await db.refresh(user)
    except IntegrityError as exc:
        await _raise_scim_integrity_conflict(db, exc)
    except SCIMUserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    base_url = str(request.base_url).rstrip("/")
    identities = await scim_identity_map(db, [user.id], scim_token.sso_config_id)
    return SCIMUserResource(
        **user_to_scim_resource(user, base_url, identities.get(user.id))
    )


@router.delete("/Users/{user_id}", status_code=204, response_class=Response)
async def scim_delete(
    user_id: uuid.UUID,
    request: Request,
    scim_token: SCIMToken = Depends(verify_scim_bearer),
    db: AsyncSession = Depends(get_db),
):
    """SCIM 2.0: Deactivate (soft-delete) a user."""
    client_ip = request.client.host if request.client else None

    try:
        await scim_update_user(
            db=db,
            user_id=user_id,
            active=False,
            sso_config_id=scim_token.sso_config_id,
            ip_address=client_ip,
        )
        await db.commit()
    except IntegrityError as exc:
        await _raise_scim_integrity_conflict(db, exc)
    except SCIMUserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    return None


# ── SCIM Token Management (Admin-only) ──────────────────────────────────────


@token_router.get("", response_model=list[SCIMTokenResponse])
async def list_scim_tokens(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """List all SCIM tokens (ADMIN only)."""
    result = await db.execute(
        select(SCIMToken).order_by(SCIMToken.created_at.desc())
    )
    return result.scalars().all()


@token_router.post("", response_model=SCIMTokenCreatedResponse, status_code=201)
async def create_token(
    payload: SCIMTokenCreate,
    request: Request,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Create a new SCIM bearer token (ADMIN only). The raw token is shown once."""
    token, raw_token = await create_scim_token(
        db=db,
        name=payload.name,
        created_by_id=current_user.id,
        sso_config_id=payload.sso_config_id,
        expires_days=payload.expires_days,
    )
    await log_identity_event(
        db,
        IdentityEventType.SCIM_TOKEN_CREATED,
        sso_config_id=token.sso_config_id,
        actor_id=current_user.id,
        actor_name=current_user.username,
        detail={
            "token_id": str(token.id),
            "name": token.name,
            "token_hint": token.token_hint,
            "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        },
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    await db.refresh(token)

    return SCIMTokenCreatedResponse(
        id=token.id,
        name=token.name,
        token_hint=token.token_hint,
        sso_config_id=token.sso_config_id,
        is_active=token.is_active,
        last_used_at=token.last_used_at,
        expires_at=token.expires_at,
        created_at=token.created_at,
        raw_token=raw_token,
    )


@token_router.delete("/{token_id}", status_code=204)
async def revoke_scim_token(
    token_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a SCIM token (ADMIN only)."""
    result = await db.execute(
        select(SCIMToken).where(SCIMToken.id == token_id)
    )
    token = result.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SCIM token not found")

    token.is_active = False
    await log_identity_event(
        db,
        IdentityEventType.SCIM_TOKEN_REVOKED,
        sso_config_id=token.sso_config_id,
        actor_id=current_user.id,
        actor_name=current_user.username,
        detail={
            "token_id": str(token.id),
            "name": token.name,
            "token_hint": token.token_hint,
        },
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    return None
