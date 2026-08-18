"""SSO / SAML router — admin configuration CRUD, SP-initiated login, SAML ACS."""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import require_role
from app.core.security import create_access_token
from app.services.refresh_token_service import issue_refresh_token
from app.db.postgres import get_db
from app.models.postgres import (
    IdentityEventType,
    SSOConfiguration,
    User,
    UserRole,
)
from app.models.schemas import (
    SSOConfigCreate,
    SSOConfigResponse,
    SSOConfigUpdate,
    SSOLoginResponse,
    SSOTestConnectionResponse,
    UserResponse,
)
from app.services.sso_service import (
    certificate_fingerprint,
    enforce_saml_security,
    get_active_sso_config,
    jit_provision_or_link,
    log_identity_event,
    parse_saml_response,
    probe_idp_endpoint,
    remember_saml_request,
    validate_certificate_format,
    validate_saml_issuer,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sso", tags=["SSO / SAML"])

# Upper bound on an inbound base64 SAMLResponse before we decode/parse it.
_MAX_SAML_RESPONSE_BYTES = 1_000_000

# ── Public endpoints (no JWT required) ───────────────────────────────────────


@router.get("/metadata", response_model=dict)
async def get_sp_metadata():
    """Return SP metadata for configuring the IdP."""
    return {
        "entity_id": settings.SAML_SP_ENTITY_ID,
        "acs_url": f"{settings.SAML_BASE_URL}/api/v1/sso/acs",
        "slo_url": f"{settings.SAML_BASE_URL}/api/v1/sso/slo",
        "name_id_format": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
    }


@router.get("/login-url", response_model=dict)
async def get_sso_login_url(db: AsyncSession = Depends(get_db)):
    """
    Get the SSO login redirect URL for SP-initiated login.
    Returns the IdP SSO URL the frontend should redirect to.
    """
    if not settings.SSO_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SSO is not enabled")

    config = await get_active_sso_config(db)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active SSO configuration found",
        )

    # SP-initiated: redirect to IdP SSO URL. Persist the minted request id
    # (single-use, short TTL) so the ACS can bind the IdP's response to this
    # request via InResponseTo and reject replays / unsolicited responses.
    request_id = "_" + uuid.uuid4().hex
    await remember_saml_request(request_id)
    redirect_url = config.idp_sso_url

    return {
        "redirect_url": redirect_url,
        "request_id": request_id,
        "sp_entity_id": config.sp_entity_id,
    }


@router.post("/acs", response_model=SSOLoginResponse)
async def saml_acs(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    SAML Assertion Consumer Service — processes the SAML Response from the IdP.

    This endpoint receives the base64-encoded SAMLResponse via form POST,
    validates the assertion, and either logs in an existing user or JIT-provisions
    a new one.
    """
    if not settings.SSO_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SSO is not enabled")

    config = await get_active_sso_config(db)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active SSO configuration",
        )

    # Parse form data (SAML POST binding)
    form = await request.form()
    saml_response_b64 = form.get("SAMLResponse")
    if not saml_response_b64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing SAMLResponse in form data",
        )
    # Bound the untrusted payload before base64-decoding + XML parsing on this
    # public endpoint (cheap-DoS guard). Real assertions are a few KB; 1 MB is
    # generous headroom for large signed multi-attribute responses.
    if len(str(saml_response_b64)) > _MAX_SAML_RESPONSE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="SAMLResponse too large",
        )

    client_ip = request.client.host if request.client else None

    # Fail closed: an active config must carry a non-empty IdP certificate or
    # we cannot verify the signature. Refuse rather than downgrade to an
    # unsigned parse.
    if not config.idp_certificate or not config.idp_certificate.strip():
        logger.error("SAML ACS rejected: active SSO config %s has no IdP certificate", config.id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SSO is misconfigured. Contact your administrator.",
        )

    try:
        # Pass the configured IdP X.509 cert so parse_saml_response can
        # cryptographically verify the assertion's XML-DSig signature
        # before extracting any claims, then enforce response-binding /
        # anti-replay (audience, recipient, InResponseTo, single-use).
        parsed = parse_saml_response(
            str(saml_response_b64),
            idp_certificate_pem=config.idp_certificate,
        )
        validate_saml_issuer(parsed, config)
        await enforce_saml_security(parsed, config)
    except ValueError as exc:
        await log_identity_event(
            db,
            IdentityEventType.SSO_LOGIN_FAILED,
            sso_config_id=config.id,
            detail={"error": str(exc)},
            ip_address=client_ip,
            success=False,
            error_message=str(exc),
        )
        await db.commit()
        # Keep the specific reason server-side only; returning it to the
        # unauthenticated caller leaks signal that helps tune forged responses.
        logger.warning("SAML assertion validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="SAML assertion validation failed.",
        )

    try:
        user, is_new = await jit_provision_or_link(
            db=db,
            config=config,
            name_id=parsed["name_id"],
            attributes=parsed["attributes"],
            ip_address=client_ip,
        )
    except ValueError as exc:
        await log_identity_event(
            db,
            IdentityEventType.SSO_LOGIN_FAILED,
            sso_config_id=config.id,
            detail={"error": str(exc), "name_id": parsed.get("name_id")},
            ip_address=client_ip,
            success=False,
            error_message=str(exc),
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    # Issue tokens
    access_token = create_access_token(str(user.id))
    refresh_token = await issue_refresh_token(db, user.id)

    await log_identity_event(
        db,
        IdentityEventType.SSO_LOGIN,
        user_id=user.id,
        sso_config_id=config.id,
        detail={"name_id": parsed["name_id"], "is_new_user": is_new},
        ip_address=client_ip,
    )
    await db.commit()

    logger.info("SSO login successful for user: %s (new=%s)", user.username, is_new)

    return SSOLoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse.model_validate(user),
        is_new_user=is_new,
    )


@router.get("/status", response_model=dict)
async def get_sso_status(db: AsyncSession = Depends(get_db)):
    """Public endpoint to check if SSO is enabled and available."""
    if not settings.SSO_ENABLED:
        return {"sso_enabled": False, "has_active_config": False, "enforcement_mode": None}

    config = await get_active_sso_config(db)
    return {
        "sso_enabled": True,
        "has_active_config": config is not None,
        "enforcement_mode": config.enforcement_mode if config else None,
    }


# ── Admin endpoints (JWT + ADMIN role required) ─────────────────────────────


@router.get("/configs", response_model=list[SSOConfigResponse])
async def list_sso_configs(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """List all SSO configurations (ADMIN only)."""
    result = await db.execute(
        select(SSOConfiguration).order_by(SSOConfiguration.created_at.desc())
    )
    configs = result.scalars().all()
    return [_config_to_response(c) for c in configs]


@router.get("/configs/{config_id}", response_model=SSOConfigResponse)
async def get_sso_config(
    config_id: uuid.UUID,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific SSO configuration (ADMIN only)."""
    result = await db.execute(
        select(SSOConfiguration).where(SSOConfiguration.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SSO configuration not found")
    return _config_to_response(config)


@router.post("/configs", response_model=SSOConfigResponse, status_code=201)
async def create_sso_config(
    payload: SSOConfigCreate,
    request: Request,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Create a new SSO configuration (ADMIN only)."""
    # Validate certificate
    cert_valid, cert_msg = validate_certificate_format(payload.idp_certificate)
    if not cert_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid IdP certificate: {cert_msg}",
        )

    # Validate role mapping values
    if payload.role_mapping:
        valid_roles = {r.value for r in UserRole}
        for group_name, role_value in payload.role_mapping.items():
            if role_value not in valid_roles:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid role '{role_value}' in role_mapping for group '{group_name}'. "
                           f"Valid roles: {', '.join(valid_roles)}",
                )

    config = SSOConfiguration(
        display_name=payload.display_name,
        provider_type=payload.provider_type,
        idp_entity_id=payload.idp_entity_id,
        idp_sso_url=payload.idp_sso_url,
        idp_slo_url=payload.idp_slo_url,
        idp_certificate=payload.idp_certificate,
        sp_entity_id=payload.sp_entity_id,
        sp_acs_url=payload.sp_acs_url,
        audience=payload.audience,
        role_mapping=payload.role_mapping or {},
        default_role=payload.default_role,
        group_attribute=payload.group_attribute,
        enforcement_mode=payload.enforcement_mode,
    )
    db.add(config)

    client_ip = request.client.host if request.client else None
    await log_identity_event(
        db,
        IdentityEventType.SSO_CONFIG_CREATED,
        sso_config_id=config.id,
        actor_id=current_user.id,
        actor_name=current_user.username,
        detail={"display_name": config.display_name, "provider_type": str(config.provider_type)},
        ip_address=client_ip,
    )

    await db.commit()
    await db.refresh(config)
    logger.info("SSO config created: %s by %s", config.display_name, current_user.username)
    return _config_to_response(config)


@router.patch("/configs/{config_id}", response_model=SSOConfigResponse)
async def update_sso_config(
    config_id: uuid.UUID,
    payload: SSOConfigUpdate,
    request: Request,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Update an SSO configuration (ADMIN only)."""
    result = await db.execute(
        select(SSOConfiguration).where(SSOConfiguration.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SSO configuration not found")

    update_data = payload.model_dump(exclude_unset=True)

    # Validate certificate if being updated
    if "idp_certificate" in update_data and update_data["idp_certificate"]:
        cert_valid, cert_msg = validate_certificate_format(update_data["idp_certificate"])
        if not cert_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid IdP certificate: {cert_msg}",
            )

    # Validate role mapping if being updated
    if "role_mapping" in update_data and update_data["role_mapping"]:
        valid_roles = {r.value for r in UserRole}
        for group_name, role_value in update_data["role_mapping"].items():
            if role_value not in valid_roles:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid role '{role_value}' in role_mapping for group '{group_name}'",
                )

    changes: dict[str, object] = {}
    for field, value in update_data.items():
        old_val = getattr(config, field, None)
        if old_val != value:
            changes[field] = {"old": str(old_val) if field != "idp_certificate" else "(masked)", "new": str(value) if field != "idp_certificate" else "(masked)"}
            setattr(config, field, value)

    client_ip = request.client.host if request.client else None
    await log_identity_event(
        db,
        IdentityEventType.SSO_CONFIG_UPDATED,
        sso_config_id=config.id,
        actor_id=current_user.id,
        actor_name=current_user.username,
        detail=changes or {"no_changes": True},
        ip_address=client_ip,
    )

    await db.commit()
    await db.refresh(config)
    logger.info("SSO config updated: %s by %s", config.display_name, current_user.username)
    return _config_to_response(config)


@router.delete("/configs/{config_id}", status_code=204)
async def delete_sso_config(
    config_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Delete an SSO configuration (ADMIN only)."""
    result = await db.execute(
        select(SSOConfiguration).where(SSOConfiguration.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SSO configuration not found")

    client_ip = request.client.host if request.client else None
    await log_identity_event(
        db,
        IdentityEventType.SSO_CONFIG_DELETED,
        sso_config_id=config.id,
        actor_id=current_user.id,
        actor_name=current_user.username,
        detail={"display_name": config.display_name},
        ip_address=client_ip,
    )

    await db.delete(config)
    await db.commit()
    logger.info("SSO config deleted: %s by %s", config.display_name, current_user.username)
    return None


@router.post("/configs/{config_id}/test", response_model=SSOTestConnectionResponse)
async def test_sso_connection(
    config_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Test an SSO configuration by validating the certificate and IdP metadata (ADMIN only)."""
    result = await db.execute(
        select(SSOConfiguration).where(SSOConfiguration.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SSO configuration not found")

    client_ip = request.client.host if request.client else None
    now = datetime.now(timezone.utc)

    # Validate certificate format
    cert_valid, cert_msg = validate_certificate_format(config.idp_certificate)

    # A non-empty URL proves only configuration presence. Probe the endpoint
    # so DNS/TLS/connectivity failures cannot be reported as a valid connection.
    url_ok, endpoint_message = await probe_idp_endpoint(config.idp_sso_url)

    success = cert_valid and url_ok
    message = (
        f"SSO configuration is valid; {endpoint_message}"
        if success
        else f"Validation failed: {cert_msg if not cert_valid else endpoint_message}"
    )

    config.last_test_at = now
    config.last_test_success = success
    config.last_test_error = None if success else message

    await log_identity_event(
        db,
        IdentityEventType.SSO_TEST_CONNECTION,
        sso_config_id=config.id,
        actor_id=current_user.id,
        actor_name=current_user.username,
        detail={"success": success, "message": message},
        ip_address=client_ip,
        success=success,
        error_message=None if success else message,
    )

    await db.commit()
    await db.refresh(config)

    return SSOTestConnectionResponse(
        success=success,
        message=message,
        idp_entity_id=config.idp_entity_id,
        certificate_valid=cert_valid,
    )


def _config_to_response(config: SSOConfiguration) -> SSOConfigResponse:
    """Convert a SSOConfiguration model to a response with masked certificate."""
    return SSOConfigResponse(
        id=config.id,
        display_name=config.display_name,
        provider_type=config.provider_type,
        idp_entity_id=config.idp_entity_id,
        idp_sso_url=config.idp_sso_url,
        idp_slo_url=config.idp_slo_url,
        idp_certificate_fingerprint=certificate_fingerprint(config.idp_certificate),
        sp_entity_id=config.sp_entity_id,
        sp_acs_url=config.sp_acs_url,
        audience=config.audience,
        role_mapping=config.role_mapping,
        default_role=config.default_role,
        group_attribute=config.group_attribute,
        enforcement_mode=config.enforcement_mode,
        is_active=config.is_active,
        last_test_at=config.last_test_at,
        last_test_success=config.last_test_success,
        last_test_error=config.last_test_error,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )
