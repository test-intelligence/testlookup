"""SSO router — admin configuration CRUD, SP-initiated login, SAML ACS, OIDC login + callback (W20 ADM-08b)."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import require_role
from app.services.auth_session_tokens import issue_access_jwt
from app.services.refresh_token_service import issue_refresh_token
from app.db.postgres import get_db
from app.core.token_revocation import RevocationUnavailable, revoke_all_user_tokens
from app.models.postgres import (
    FederatedIdentity,
    IdentityEventType,
    SSOConfiguration,
    SSOProviderType,
    User,
    UserRole,
)
from app.models.schemas import (
    SSOConfigCreate,
    SSOConfigResponse,
    SSOConfigUpdate,
    SSOExchangeRequest,
    SSOLoginResponse,
    SSORolePreviewRequest,
    SSORolePreviewResponse,
    SSOTestConnectionResponse,
    UserResponse,
)
from app.services.sso_service import (
    build_authn_request,
    certificate_expiration,
    certificate_fingerprint,
    claim_assertion_id,
    claim_sso_login,
    enforce_saml_security,
    get_active_sso_config,
    jit_provision_or_link,
    log_identity_event,
    parse_saml_response,
    preview_role_from_groups,
    probe_idp_endpoint,
    remember_saml_request,
    safe_relay_state,
    saml_redirect_url,
    stash_sso_login,
    validate_certificate_format,
    validate_saml_issuer,
)
from app.services import oidc_service, saml_slo
from app.services.refresh_token_service import _revoke_family as _revoke_refresh_family
from app.services.secret_service import expire_secret, has_secret, read_secret, store_secret

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sso", tags=["SSO / SAML"])

# Upper bound on an inbound base64 SAMLResponse before we decode/parse it.
_MAX_SAML_RESPONSE_BYTES = 1_000_000

# W20 ADM-08b: the OIDC client secret lives in secret_refs under this scope, keyed by the config id.
OIDC_SECRET_SCOPE = "sso_oidc"
_OIDC_CODE_MAX = 4096


def _is_oidc(config: SSOConfiguration) -> bool:
    return getattr(config, "provider_type", None) == SSOProviderType.OIDC

# ── Public endpoints (no JWT required) ───────────────────────────────────────


@router.get("/metadata", response_model=dict)
async def get_sp_metadata(db: AsyncSession = Depends(get_db)):
    """Return SP metadata for configuring the IdP.

    W20 ADM-08c: the single-logout URL is advertised only while the active SAML
    configuration has an IdP SLO URL (``/sso/slo`` answers 404 otherwise, and
    advertising it then sent IdPs to that 404). IdP-initiated SLO only, so no
    SP signing key is published.
    """
    metadata: dict = {
        "entity_id": settings.SAML_SP_ENTITY_ID,
        "acs_url": f"{settings.SAML_BASE_URL}/api/v1/sso/acs",
        "name_id_format": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
    }
    if settings.SSO_ENABLED:
        config = await get_active_sso_config(db)
        if config is not None and not _is_oidc(config) and config.idp_slo_url:
            metadata["single_logout_url"] = saml_slo.slo_url()
            metadata["single_logout_bindings"] = [
                "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
                "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            ]
    return metadata


@router.get("/slo", operation_id="saml_single_logout_redirect")
async def saml_single_logout_redirect_binding(request: Request, db: AsyncSession = Depends(get_db)):
    """W20 ADM-08c: IdP-initiated single logout, HTTP-Redirect binding (see ``saml_single_logout``)."""
    return await saml_single_logout(request, db)


# Two routes, not one api_route(methods=[GET, POST]): that shares one operationId between the methods and the
# generated reference picked either name from run to run.
@router.post("/slo", operation_id="saml_single_logout_post")
async def saml_single_logout_post_binding(request: Request, db: AsyncSession = Depends(get_db)):
    """W20 ADM-08c: IdP-initiated single logout, HTTP-POST binding (see ``saml_single_logout``)."""
    return await saml_single_logout(request, db)


async def saml_single_logout(request: Request, db: AsyncSession):
    """W20 ADM-08c: IdP-initiated single logout (HTTP-Redirect or HTTP-POST binding).

    A LogoutRequest signed by the configured IdP ends the NameID's TestLookup sessions — every refresh token
    revoked and an access-token cutoff written, so the next API call goes to the sign-in page — and the browser
    goes back to the IdP with a Success LogoutResponse. Anything unsigned, signed by another key, from another
    issuer, stale or replayed is refused with 400 and changes nothing (no identity event: anyone can send one).
    """
    if not settings.SSO_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SSO is not enabled")
    config = await get_active_sso_config(db)
    if config is None or _is_oidc(config) or not config.idp_slo_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Single logout is not configured")

    if request.method == "POST":
        form = await request.form()
        relay_state = form.get("RelayState")
        verify = dict(binding="post", form_saml_request=str(form.get("SAMLRequest") or ""))
    else:
        relay_state = request.query_params.get("RelayState")
        verify = dict(binding="redirect", raw_query=request.url.query)
    try:
        claims = saml_slo.verify_logout_request(idp_certificate_pem=config.idp_certificate or "", **verify)
        saml_slo.check_logout_request(claims, idp_entity_id=config.idp_entity_id)
        if not await claim_assertion_id(f"slo:{claims['id']}", saml_slo.REPLAY_TTL_SECONDS):
            raise ValueError("the LogoutRequest was already processed (replay)")
    except ValueError as exc:
        logger.warning("SAML LogoutRequest refused: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The logout request was refused.")

    fed = (await db.execute(
        select(FederatedIdentity).where(
            FederatedIdentity.sso_config_id == config.id,
            FederatedIdentity.external_id == claims["name_id"],
        )
    )).scalar_one_or_none()
    if fed is not None:
        await _revoke_refresh_family(db, fed.user_id, reason="saml_slo")
        try:
            await revoke_all_user_tokens(fed.user_id, db)
        except RevocationUnavailable as exc:
            await db.rollback()
            raise HTTPException(status_code=503, detail="Token revocation store unavailable; try again") from exc
        await log_identity_event(
            db,
            IdentityEventType.SSO_LOGOUT,
            user_id=fed.user_id,
            sso_config_id=config.id,
            detail={"name_id": claims["name_id"], "request_id": claims["id"],
                    "session_indexes": claims["session_indexes"][:10]},
            ip_address=request.client.host if request.client else None,
        )
        await db.commit()
        logger.info("SAML single logout ended the sessions of user %s", fed.user_id)
    else:
        # Nothing to end here (never signed in through this IdP): still a Success for the IdP's chain.
        logger.info("SAML single logout for an unknown NameID; nothing to end")

    return RedirectResponse(
        url=saml_slo.logout_response_url(
            idp_slo_url=config.idp_slo_url,
            in_response_to=claims["id"],
            sp_entity_id=config.sp_entity_id,
            relay_state=str(relay_state) if relay_state else None,
        ),
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/login-url", response_model=dict)
async def get_sso_login_url(next: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """
    The IdP URL that starts an SP-initiated login: the IdP SSO URL carrying a
    SAML AuthnRequest (HTTP-Redirect binding) and, when ``next`` is an
    app-relative path, a RelayState the browser returns to after sign-in.
    """
    if not settings.SSO_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SSO is not enabled")

    config = await get_active_sso_config(db)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active SSO configuration found",
        )

    if _is_oidc(config):
        try:
            oidc_url = await oidc_service.start_login(config, next_path=safe_relay_state(next))
        except oidc_service.OIDCError as exc:
            logger.warning("OIDC login could not start: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The identity provider could not be reached. Contact your administrator.",
            )
        return {"redirect_url": oidc_url, "request_id": None, "provider_type": SSOProviderType.OIDC.value}

    # SP-initiated: redirect to IdP SSO URL. Persist the minted request id
    # (single-use, short TTL) so the ACS can bind the IdP's response to this
    # request via InResponseTo and reject replays / unsolicited responses.
    request_id = "_" + uuid.uuid4().hex
    await remember_saml_request(request_id)
    # A SAML row always has these (ck_sso_config_fields_by_provider); the columns are NULL-able for OIDC rows.
    idp_sso_url = config.idp_sso_url or ""
    authn_request = build_authn_request(
        request_id=request_id,
        destination=idp_sso_url,
        acs_url=config.sp_acs_url or "",
        sp_entity_id=config.sp_entity_id or "",
    )
    redirect_url = saml_redirect_url(idp_sso_url, authn_request, safe_relay_state(next))

    return {
        "redirect_url": redirect_url,
        "request_id": request_id,
        "sp_entity_id": config.sp_entity_id,
        "provider_type": SSOProviderType.SAML.value,
    }


@router.get("/oidc/login")
async def oidc_login(next: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """W20 ADM-08b: start an OIDC sign-in — 302 to the IdP's authorization endpoint (state, nonce, PKCE S256)."""
    if not settings.SSO_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SSO is not enabled")
    config = await get_active_sso_config(db)
    if config is None or not _is_oidc(config):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active OIDC configuration found")
    try:
        url = await oidc_service.start_login(config, next_path=safe_relay_state(next))
    except oidc_service.OIDCError as exc:
        logger.warning("OIDC login could not start: %s", exc)
        return RedirectResponse(url=f"{settings.public_base_url}/login?sso_error=1", status_code=status.HTTP_302_FOUND)
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@router.get("/oidc/callback")
async def oidc_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """W20 ADM-08b: the IdP sends the browser back here with ``code`` + ``state``.

    The state is burned first (single use), the code exchanged with the client secret and the PKCE verifier,
    the ID token validated (signature / iss / aud / exp / nonce), then the user is JIT-provisioned or linked
    and handed into the app through the one-time-code fragment. Every refusal goes back to ``/login`` with a
    generic notice; the reason stays in the server log and the identity-event audit.
    """
    if not settings.SSO_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SSO is not enabled")
    client_ip = request.client.host if request.client else None
    config = await get_active_sso_config(db)

    async def refuse(reason: str, *, audit: bool = True, **detail: object) -> RedirectResponse:
        logger.warning("OIDC sign-in refused: %s", reason)
        # identity_events has no retention clock: a refusal anyone can trigger with a made-up state is a log line,
        # not an event (see IdentityEventType). Only a login this server started (its state burned) is audited.
        if config is not None and audit:
            await log_identity_event(
                db,
                IdentityEventType.SSO_LOGIN_FAILED,
                sso_config_id=config.id,
                detail={"error": reason, "provider_type": "OIDC", **detail},
                ip_address=client_ip,
                success=False,
                error_message=reason[:500],
            )
            await db.commit()
        return RedirectResponse(url=f"{settings.public_base_url}/login?sso_error=1", status_code=status.HTTP_303_SEE_OTHER)

    if config is None or not _is_oidc(config):
        return await refuse("no active OIDC configuration", audit=False)
    # Burn the state before anything else, so an error answer cannot be replayed either.
    parked = await oidc_service.consume_state(state)
    if parked is None:
        return await refuse("state unknown, already used or expired", audit=False)
    if error:
        return await refuse(f"identity provider returned error {error[:100]!r}")
    if parked.get("config_id") != str(config.id):
        return await refuse("the active SSO configuration changed during the sign-in")
    if not code or len(code) > _OIDC_CODE_MAX:
        return await refuse("missing authorization code")
    client_secret = await read_secret(db, OIDC_SECRET_SCOPE, str(config.id))
    if not client_secret:
        return await refuse("no client secret is stored for this configuration")

    try:
        doc = await oidc_service.discover(config.oidc_issuer or "")
        tokens = await oidc_service.exchange_code(
            doc,
            code=code,
            code_verifier=parked["code_verifier"],
            redirect_uri=parked["redirect_uri"],
            client_id=config.oidc_client_id or "",
            client_secret=client_secret,
        )
        claims = await oidc_service.validate_with_jwks(
            tokens["id_token"], doc,
            issuer=config.oidc_issuer or "", client_id=config.oidc_client_id or "", nonce=parked["nonce"],
        )
        email, attributes, email_verified = oidc_service.claims_to_attributes(claims, config.oidc_groups_claim)
    except oidc_service.OIDCError as exc:
        return await refuse(str(exc))

    try:
        user, is_new = await jit_provision_or_link(
            db=db,
            config=config,
            name_id=claims["sub"],
            attributes=attributes,
            ip_address=client_ip,
            email_verified=email_verified,
        )
    except ValueError as exc:
        return await refuse(str(exc), sub=claims["sub"])
    if not user.is_active:
        return await refuse("account is deactivated", sub=claims["sub"])

    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    await db.refresh(user)
    access_token = await issue_access_jwt(db, str(user.id))
    refresh_token = await issue_refresh_token(db, user.id)
    await log_identity_event(
        db,
        IdentityEventType.SSO_LOGIN,
        user_id=user.id,
        sso_config_id=config.id,
        detail={"sub": claims["sub"], "email": email, "is_new_user": is_new, "provider_type": "OIDC"},
        ip_address=client_ip,
    )
    await db.commit()
    logger.info("OIDC login successful for user: %s (new=%s)", user.username, is_new)

    signed_in = SSOLoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse.model_validate(user),
        is_new_user=is_new,
    )
    handoff = await stash_sso_login(signed_in.model_dump(mode="json"))
    fragment = f"code={quote(handoff)}"
    if parked.get("next"):
        fragment += f"&next={quote(parked['next'], safe='')}"
    return RedirectResponse(url=f"{settings.public_base_url}/sso/callback#{fragment}", status_code=status.HTTP_303_SEE_OTHER)


def _wants_browser_redirect(request: Request) -> bool:
    """The IdP's form POST comes from the user's browser (it asks for HTML); an
    API client or test asks for JSON and keeps getting JSON."""
    return "text/html" in (request.headers.get("accept") or "").lower()


def _acs_failure(browser: bool, status_code: int, detail: str) -> HTTPException:
    """A refusal: JSON for an API client; for a browser, back to the sign-in
    page with a generic notice (the specific reason stays in the server log)."""
    if browser:
        return HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail=detail,
            headers={"Location": f"{settings.public_base_url}/login?sso_error=1"},
        )
    return HTTPException(status_code=status_code, detail=detail)


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

    The POST comes from the browser. It answers with a 303 into the app
    (``/sso/callback``) carrying a one-time code in the URL fragment, which the
    app exchanges for the tokens (``POST /sso/exchange``); a refusal goes back
    to ``/login``. A client that asks for JSON gets the tokens as before.
    """
    browser = _wants_browser_redirect(request)
    if not settings.SSO_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SSO is not enabled")

    config = await get_active_sso_config(db)
    if config is None or _is_oidc(config):
        raise _acs_failure(browser, status.HTTP_400_BAD_REQUEST, "No active SAML configuration")

    # Parse form data (SAML POST binding)
    form = await request.form()
    saml_response_b64 = form.get("SAMLResponse")
    if not saml_response_b64:
        raise _acs_failure(browser, status.HTTP_400_BAD_REQUEST, "Missing SAMLResponse in form data")
    # Bound the untrusted payload before base64-decoding + XML parsing on this
    # public endpoint (cheap-DoS guard). Real assertions are a few KB; 1 MB is
    # generous headroom for large signed multi-attribute responses.
    if len(str(saml_response_b64)) > _MAX_SAML_RESPONSE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="SAMLResponse too large",
        )
    # Where the app lands afterwards. It came back through the IdP, so it is
    # checked again: an app-relative path or nothing.
    relay_state = safe_relay_state(form.get("RelayState"))

    client_ip = request.client.host if request.client else None

    # Fail closed: an active config must carry a non-empty IdP certificate or
    # we cannot verify the signature. Refuse rather than downgrade to an
    # unsigned parse.
    if not config.idp_certificate or not config.idp_certificate.strip():
        logger.error("SAML ACS rejected: active SSO config %s has no IdP certificate", config.id)
        raise _acs_failure(
            browser, status.HTTP_400_BAD_REQUEST, "SSO is misconfigured. Contact your administrator."
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
        raise _acs_failure(browser, status.HTTP_401_UNAUTHORIZED, "SAML assertion validation failed.")

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
        raise _acs_failure(browser, status.HTTP_400_BAD_REQUEST, str(exc))

    if not user.is_active:
        raise _acs_failure(browser, status.HTTP_403_FORBIDDEN, "Account is deactivated")

    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    await db.refresh(user)

    # Issue tokens
    access_token = await issue_access_jwt(db, str(user.id))
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

    signed_in = SSOLoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse.model_validate(user),
        is_new_user=is_new,
    )
    if not browser:
        return signed_in

    # Into the app. The fragment never reaches a server log or a Referer.
    code = await stash_sso_login(signed_in.model_dump(mode="json"))
    fragment = f"code={quote(code)}"
    if relay_state:
        fragment += f"&next={quote(relay_state, safe='')}"
    return RedirectResponse(
        url=f"{settings.public_base_url}/sso/callback#{fragment}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/exchange", response_model=SSOLoginResponse)
async def exchange_sso_code(payload: SSOExchangeRequest):
    """The tokens for an SSO sign-in the ACS completed, for its one-time code.

    Public, like the ACS: the code is the credential. It works once, within a
    minute of the IdP's response.
    """
    if not settings.SSO_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SSO is not enabled")
    signed_in = await claim_sso_login(payload.code)
    if signed_in is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This sign-in link has expired or was already used. Sign in again.",
        )
    return SSOLoginResponse(**signed_in)


@router.get("/status", response_model=dict)
async def get_sso_status(db: AsyncSession = Depends(get_db)):
    """Public endpoint to check if SSO is enabled and available."""
    if not settings.SSO_ENABLED:
        return {"sso_enabled": False, "has_active_config": False, "enforcement_mode": None,
                "provider_type": None, "display_name": None}

    config = await get_active_sso_config(db)
    return {
        "sso_enabled": True,
        "has_active_config": config is not None,
        "enforcement_mode": config.enforcement_mode if config else None,
        # W20 ADM-08b: the login page labels its button "Sign in with <display_name>" for either type.
        "provider_type": config.provider_type if config else None,
        "display_name": config.display_name if config else None,
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
    return [await _config_response(db, c) for c in configs]


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
    return await _config_response(db, config)


@router.post("/configs", response_model=SSOConfigResponse, status_code=201)
async def create_sso_config(
    payload: SSOConfigCreate,
    request: Request,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Create a new SSO configuration (ADMIN only)."""
    is_oidc = payload.provider_type == SSOProviderType.OIDC
    if is_oidc:
        _check_oidc_issuer(payload.oidc_issuer or "")
    else:
        cert_valid, cert_msg = validate_certificate_format(payload.idp_certificate or "")
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
        id=uuid.uuid4(),
        display_name=payload.display_name,
        provider_type=payload.provider_type,
        idp_entity_id=None if is_oidc else payload.idp_entity_id,
        idp_sso_url=None if is_oidc else payload.idp_sso_url,
        idp_slo_url=None if is_oidc else payload.idp_slo_url,
        idp_certificate=None if is_oidc else payload.idp_certificate,
        sp_entity_id=None if is_oidc else payload.sp_entity_id,
        sp_acs_url=None if is_oidc else payload.sp_acs_url,
        audience=None if is_oidc else payload.audience,
        oidc_issuer=payload.oidc_issuer.rstrip("/") if is_oidc and payload.oidc_issuer else None,
        oidc_client_id=payload.oidc_client_id if is_oidc else None,
        oidc_scopes=payload.oidc_scopes if is_oidc else None,
        oidc_groups_claim=payload.oidc_groups_claim if is_oidc else None,
        role_mapping=payload.role_mapping or {},
        default_role=payload.default_role,
        group_attribute=payload.group_attribute,
        enforcement_mode=payload.enforcement_mode,
    )
    db.add(config)
    # The app's sessions do not autoflush: insert the config before the audit row that references it
    # (unrelated tables, so the unit of work may otherwise insert identity_events first → FK violation).
    await db.flush()
    if is_oidc and payload.oidc_client_secret:
        await store_secret(db, OIDC_SECRET_SCOPE, str(config.id), payload.oidc_client_secret, actor_id=current_user.id)

    client_ip = request.client.host if request.client else None
    await log_identity_event(
        db,
        IdentityEventType.SSO_CONFIG_CREATED,
        sso_config_id=config.id,
        actor_id=current_user.id,
        actor_name=current_user.username,
        detail={"display_name": config.display_name, "provider_type": SSOProviderType(config.provider_type).value},
        ip_address=client_ip,
    )

    await db.commit()
    await db.refresh(config)
    logger.info("SSO config created: %s by %s", config.display_name, current_user.username)
    return await _config_response(db, config)


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
    new_secret = update_data.pop("oidc_client_secret", None)

    # W20 ADM-08b: the fields this provider type cannot do without may change, never become empty
    # (the CHECK constraint would refuse it at commit; say which field instead).
    required = (
        ("oidc_issuer", "oidc_client_id") if _is_oidc(config)
        else ("idp_entity_id", "idp_sso_url", "idp_certificate", "sp_entity_id", "sp_acs_url")
    )
    for name in required:
        if name in update_data and not update_data[name]:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{name} cannot be empty")
    if update_data.get("oidc_issuer"):
        _check_oidc_issuer(update_data["oidc_issuer"])
        update_data["oidc_issuer"] = update_data["oidc_issuer"].rstrip("/")

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

    # There is one system-level SAML login policy. Switching the active IdP is
    # therefore a replacement operation, not an additive toggle. The partial
    # unique index is the final race guard for concurrent admin requests.
    deactivated_count = 0
    if update_data.get("is_active") is True and not config.is_active:
        activation_result = await db.execute(
            update(SSOConfiguration)
            .where(
                SSOConfiguration.id != config.id,
                SSOConfiguration.is_active.is_(True),
            )
            .values(is_active=False)
        )
        deactivated_count = activation_result.rowcount or 0

    changes: dict[str, object] = {}
    for field, value in update_data.items():
        old_val = getattr(config, field, None)
        if old_val != value:
            changes[field] = {"old": str(old_val) if field != "idp_certificate" else "(masked)", "new": str(value) if field != "idp_certificate" else "(masked)"}
            setattr(config, field, value)
    if new_secret and _is_oidc(config):
        await store_secret(db, OIDC_SECRET_SCOPE, str(config.id), new_secret, actor_id=current_user.id)
        changes["oidc_client_secret"] = {"old": "(masked)", "new": "(masked)"}
    if deactivated_count:
        changes["deactivated_other_configs"] = deactivated_count

    if update_data.get("is_active") is True:
        try:
            # Flush the policy swap before recording its audit event so a
            # concurrent activation conflict is identified precisely.
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Another SSO configuration was activated concurrently; retry",
            ) from exc

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
    return await _config_response(db, config)


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

    if _is_oidc(config):
        await expire_secret(db, OIDC_SECRET_SCOPE, str(config.id))
    await db.delete(config)
    await db.commit()
    logger.info("SSO config deleted: %s by %s", config.display_name, current_user.username)
    return None


@router.post("/configs/{config_id}/preview-role", response_model=SSORolePreviewResponse)
async def preview_sso_role(
    config_id: uuid.UUID,
    payload: SSORolePreviewRequest,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """W20 ADM-08a: the role a user in these IdP groups would get (ADMIN only; pure, writes nothing).

    The posted ``role_mapping`` / ``default_role`` preview an unsaved edit; otherwise the stored ones answer.
    """
    config = (await db.execute(select(SSOConfiguration).where(SSOConfiguration.id == config_id))).scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SSO configuration not found")
    valid_roles = {r.value for r in UserRole}
    mapping = payload.role_mapping if payload.role_mapping is not None else (config.role_mapping or {})
    for group_name, role_value in mapping.items():
        if role_value not in valid_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid role '{role_value}' in role_mapping for group '{group_name}'",
            )
    default = payload.default_role or config.default_role
    if default not in valid_roles:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid default role '{default}'")
    groups = [g.strip() for g in payload.groups if g and g.strip()]
    return preview_role_from_groups(groups, mapping, UserRole(default))


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

    cert_valid: Optional[bool]
    if _is_oidc(config):
        # W20 ADM-08b: discovery (fresh, not cached) + JWKS prove the IdP answers and names its endpoints;
        # the stored secret is checked for presence (only a real sign-in proves it).
        cert_valid = None
        try:
            doc = await oidc_service.discover(config.oidc_issuer or "", use_cache=False)
            jwks = await oidc_service.fetch_jwks(doc["jwks_uri"], force=True)
            success = await has_secret(db, OIDC_SECRET_SCOPE, str(config.id))
            message = f"OIDC provider reachable; discovery OK, {len(jwks.get('keys') or [])} signing key(s)"
            if not success:
                message = f"Validation failed: {message}; no client secret is stored"
        except oidc_service.OIDCError as exc:
            success = False
            message = f"Validation failed: {exc}"
    else:
        # Validate certificate format
        cert_valid, cert_msg = validate_certificate_format(config.idp_certificate or "")

        # A non-empty URL proves only configuration presence. Probe the endpoint
        # so DNS/TLS/connectivity failures cannot be reported as a valid connection.
        url_ok, endpoint_message = await probe_idp_endpoint(config.idp_sso_url or "")

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
        idp_entity_id=config.oidc_issuer if _is_oidc(config) else config.idp_entity_id,
        certificate_valid=cert_valid,
        certificate_expires_at=(
            expiry.isoformat()
            if config.idp_certificate and (expiry := certificate_expiration(config.idp_certificate))
            else None
        ),
    )


def _check_oidc_issuer(issuer: str) -> None:
    """An issuer is an absolute http(s) URL with no query or fragment (OIDC Discovery §2)."""
    parts = urlsplit(issuer)
    if parts.scheme not in ("https", "http") or not parts.netloc or parts.query or parts.fragment:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="oidc_issuer must be an absolute https URL without a query or fragment",
        )


async def _config_response(db: AsyncSession, config: SSOConfiguration) -> SSOConfigResponse:
    secret_set = _is_oidc(config) and await has_secret(db, OIDC_SECRET_SCOPE, str(config.id))
    return _config_to_response(config, oidc_client_secret_set=secret_set)


def _config_to_response(config: SSOConfiguration, *, oidc_client_secret_set: bool = False) -> SSOConfigResponse:
    """Convert a SSOConfiguration model to a response with masked certificate (and never the OIDC secret)."""
    return SSOConfigResponse(
        id=config.id,
        display_name=config.display_name,
        provider_type=config.provider_type,
        idp_entity_id=config.idp_entity_id,
        idp_sso_url=config.idp_sso_url,
        idp_slo_url=config.idp_slo_url,
        idp_certificate_fingerprint=certificate_fingerprint(config.idp_certificate) if config.idp_certificate else "",
        sp_entity_id=config.sp_entity_id,
        sp_acs_url=config.sp_acs_url,
        audience=config.audience,
        oidc_issuer=config.oidc_issuer,
        oidc_client_id=config.oidc_client_id,
        oidc_scopes=config.oidc_scopes,
        oidc_groups_claim=config.oidc_groups_claim,
        oidc_client_secret_set=oidc_client_secret_set,
        oidc_redirect_uri=oidc_service.callback_url() if _is_oidc(config) else None,
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
