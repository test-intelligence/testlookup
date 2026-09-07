"""TOTP multi-factor authentication — enrollment, verification, recovery codes.

Registered as a **public** router: ``/mfa/verify`` and the forced-enrollment
path carry an interstitial MFA token, not an access token, so the router-wide
``get_current_user_or_api_key`` dependency in ``bootstrap.register_routers``
would reject them before the handler ran. Every endpoint that does need a real
session declares it explicitly.

Handlers own their transactions (``await db.commit()``); the service layer
stages only.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_active_user
from app.core.security import (
    MFA_CHALLENGE_TOKEN_TYPE,
    MFA_ENROLLMENT_TOKEN_TYPE,
    create_access_token,
    decode_token,
    verify_password,
)
from app.core.token_revocation import RevocationUnavailable, is_jti_revoked, revoke_jti
from app.db.postgres import get_db
from app.models.postgres import IdentityEventType, User
from app.models.schemas import (
    MfaDisableRequest,
    MfaEnrollConfirmRequest,
    MfaEnrollConfirmResponse,
    MfaEnrollStartRequest,
    MfaEnrollStartResponse,
    MfaRecoveryCodesRequest,
    MfaRecoveryCodesResponse,
    MfaStatusResponse,
    MfaVerifyRequest,
    TokenResponse,
)
from app.services import mfa_service
from app.services.refresh_token_service import issue_refresh_token
from app.services.sso_service import log_identity_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth/mfa", tags=["Authentication"])


_INVALID_TOKEN = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired MFA token. Start over from the login screen.",
    headers={"WWW-Authenticate": "Bearer"},
)

_SEED_BROKEN = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail=mfa_service.SEED_UNREADABLE_DETAIL,
)


def _client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


async def _require_interactive_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    """A real browser/CLI session — never an API key.

    Enrolling, disabling, or reissuing recovery codes changes the account's
    second factor. A CI-embedded API key must not be able to strip the very
    control it is exempt from. Written as ``== CREDENTIAL_KIND_JWT`` so an
    unknown credential kind (``None``) is refused rather than assumed
    interactive.

    Note for future integration tests: ``tests/integration/conftest.py``'s
    ``auth_as`` overrides ``get_current_active_user`` wholesale, which leaves
    the credential kind unset and therefore trips this guard. That is the
    fail-safe direction working as intended — such a test must set the
    credential kind on its user object rather than loosen this check.
    """
    if not mfa_service.mfa_gate_applies(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "MFA settings can only be changed from an interactive session. "
                "Sign in with your password instead of an API key."
            ),
        )
    return current_user


async def _user_from_mfa_token(
    db: AsyncSession, token: str, expected_type: str
) -> User:
    """Resolve an interstitial MFA token to its user.

    ``decode_token(expected_type=...)`` is what makes a challenge token
    unusable as an access token and vice versa — the type claim is checked
    before anything is loaded.
    """
    try:
        payload = decode_token(token, expected_type=expected_type)
    except JWTError:
        raise _INVALID_TOKEN

    subject = payload.get("sub")
    jti = payload.get("jti")
    if not subject or not jti:
        raise _INVALID_TOKEN

    # Single-use. Reuses the access-token denylist rather than inventing a
    # second store, which means it inherits the audited fail-closed semantics
    # (503 when Redis cannot be consulted) and the AUTH_REVOCATION_FAIL_OPEN
    # escape hatch, instead of adding a second, differently-behaving one.
    try:
        if await is_jti_revoked(str(jti)):
            raise _INVALID_TOKEN
    except RevocationUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "MFA token status cannot be verified right now (revocation store "
                "unavailable). This is a server-side outage — retry shortly."
            ),
            headers={"Retry-After": "5"},
        )

    import uuid as _uuid

    try:
        uid = _uuid.UUID(str(subject))
    except ValueError:
        raise _INVALID_TOKEN

    user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise _INVALID_TOKEN
    setattr(user, "_mfa_token_jti", str(jti))
    setattr(user, "_mfa_token_exp", payload.get("exp"))
    return user


async def _consume_mfa_token(user: User) -> None:
    """Burn the interstitial token so it cannot be replayed."""
    jti = getattr(user, "_mfa_token_jti", None)
    exp = getattr(user, "_mfa_token_exp", None)
    if not jti:
        return
    remaining = settings.MFA_CHALLENGE_TTL_SECONDS
    if isinstance(exp, (int, float)):
        remaining = int(exp) - int(datetime.now(timezone.utc).timestamp())
    try:
        await revoke_jti(str(jti), remaining)
    except RevocationUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Token revocation store unavailable; retry the MFA operation",
            headers={"Retry-After": "5"},
        ) from exc


def _assert_not_locked(user: User) -> None:
    until = mfa_service.locked_until(user)
    if until is None:
        return
    retry = max(1, int((until - datetime.now(timezone.utc)).total_seconds()))
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Account temporarily locked after repeated failed attempts.",
        headers={"Retry-After": str(retry)},
    )


async def _issue_session(db: AsyncSession, user: User) -> TokenResponse:
    access_token = create_access_token(str(user.id))
    refresh_token = await issue_refresh_token(db, user.id)
    await mfa_service.register_successful_login(db, user)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        must_change_password=user.must_change_password,
    )


async def _resolve_enrolling_user(
    db: AsyncSession,
    enrollment_token: Optional[str],
    session_user: Optional[User],
) -> tuple[User, bool]:
    """Return ``(user, forced)`` for an enrollment call.

    Two entry points share these endpoints: a signed-in user turning MFA on
    voluntarily, and a user whose login stopped at "policy requires MFA and you
    have none" and who therefore holds an enrollment token but no session.
    """
    if enrollment_token:
        user = await _user_from_mfa_token(db, enrollment_token, MFA_ENROLLMENT_TOKEN_TYPE)
        return user, True
    if session_user is not None:
        return session_user, False
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sign in first, or supply the enrollment_token issued at login.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _optional_session_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    """Resolve a bearer token if one was sent, without requiring it.

    ``OAuth2PasswordBearer(auto_error=False)`` would do this, but it would also
    accept the API-key fall-through path. Enrollment must be interactive, so we
    resolve the bearer token directly and apply the same JWT-only rule.
    """
    header = request.headers.get("Authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    from app.core.deps import get_current_user

    try:
        user = await get_current_user(db=db, token=token)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return None
        raise
    if not user.is_active:
        return None
    return user


# ── Enrollment ───────────────────────────────────────────────────────────────


@router.post("/enroll/start", response_model=MfaEnrollStartResponse)
async def start_enrollment(
    payload: MfaEnrollStartRequest,
    request: Request,
    session_user: Optional[User] = Depends(_optional_session_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a TOTP seed and provisioning URI. Does **not** enable MFA.

    The seed is stored immediately so ``/enroll/confirm`` has something to
    verify against, but ``users.mfa_enabled`` stays false until a code proves
    the authenticator actually holds it. An abandoned enrollment therefore
    changes nothing about how the account authenticates.
    """
    user, _forced = await _resolve_enrolling_user(db, payload.enrollment_token, session_user)
    if user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA is already enabled. Disable it first to enroll a new device.",
        )

    secret, uri = await mfa_service.start_enrollment(db, user)
    await log_identity_event(
        db,
        IdentityEventType.MFA_ENROLL_STARTED,
        user_id=user.id,
        ip_address=_client_ip(request),
    )
    await db.commit()
    logger.info("MFA enrollment started: user_id=%s", user.id)
    return MfaEnrollStartResponse(
        secret=secret,
        otpauth_uri=uri,
        issuer=settings.MFA_ISSUER_NAME,
        account_name=user.username or user.email,
        digits=mfa_service.TOTP_DIGITS,
        period_seconds=mfa_service.TOTP_INTERVAL_SECONDS,
    )


@router.post("/enroll/confirm", response_model=MfaEnrollConfirmResponse)
async def confirm_enrollment(
    payload: MfaEnrollConfirmRequest,
    request: Request,
    session_user: Optional[User] = Depends(_optional_session_user),
    db: AsyncSession = Depends(get_db),
):
    """Verify a code against the staged seed, then enable MFA.

    Recovery codes are minted in the same unit of work as the flag flip — an
    account must never reach ``mfa_enabled=True`` without a way back in. They
    are returned once and only digests are kept.
    """
    user, forced = await _resolve_enrolling_user(db, payload.enrollment_token, session_user)
    if user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA is already enabled for this account.",
        )

    codes = await mfa_service.confirm_enrollment(db, user, payload.code)
    if codes is None:
        await db.commit()  # nothing staged, but keep the session clean
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That code did not match. Check your authenticator and try again.",
        )

    await log_identity_event(
        db,
        IdentityEventType.MFA_ENABLED,
        user_id=user.id,
        detail={"forced_by_policy": forced},
        ip_address=_client_ip(request),
    )

    tokens: Optional[TokenResponse] = None
    if forced:
        # The enrollment token was the only credential in play; completing
        # enrollment is what completes the login.
        tokens = await _issue_session(db, user)
        await _consume_mfa_token(user)

    await db.commit()
    logger.info("MFA enabled: user_id=%s (forced=%s)", user.id, forced)
    return MfaEnrollConfirmResponse(recovery_codes=codes, tokens=tokens)


# ── Verification (login second leg) ──────────────────────────────────────────


@router.post("/verify", response_model=TokenResponse)
async def verify_mfa(
    payload: MfaVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Exchange a challenge token plus a second factor for a real session."""
    user = await _user_from_mfa_token(db, payload.challenge_token, MFA_CHALLENGE_TOKEN_TYPE)
    _assert_not_locked(user)

    policy = await mfa_service.load_policy(db)
    ip = _client_ip(request)

    if not user.mfa_enabled:
        # MFA was turned off between the challenge and the verify. The
        # challenge alone is not a credential, so the honest answer is "start
        # over" rather than silently issuing a session.
        raise _INVALID_TOKEN

    used_recovery = False
    accepted = False

    if payload.recovery_code:
        used_recovery = await mfa_service.consume_recovery_code(db, user, payload.recovery_code)
        accepted = used_recovery
    elif payload.code:
        loaded = await mfa_service.load_totp_secret(db, user)
        if not loaded.usable:
            # Enrolled but the seed will not decrypt. Never fall through to
            # "MFA is off" — deny and make the operator fix it.
            await db.commit()
            raise _SEED_BROKEN
        step = mfa_service.verify_totp(loaded.secret or "", payload.code, user.mfa_last_used_step)
        if step is not None:
            user.mfa_last_used_step = step
            accepted = True
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either code or recovery_code.",
        )

    if not accepted:
        locked = await mfa_service.register_failed_attempt(
            db, user, policy, reason="mfa_verify", ip_address=ip
        )
        await log_identity_event(
            db,
            IdentityEventType.MFA_VERIFY_FAILED,
            user_id=user.id,
            detail={"method": "recovery_code" if payload.recovery_code else "totp"},
            ip_address=ip,
            success=False,
        )
        await db.commit()
        if locked is not None:
            _assert_not_locked(user)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid verification code.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    tokens = await _issue_session(db, user)
    await log_identity_event(
        db,
        IdentityEventType.MFA_RECOVERY_CODE_USED if used_recovery
        else IdentityEventType.MFA_VERIFY_SUCCESS,
        user_id=user.id,
        detail={
            "method": "recovery_code" if used_recovery else "totp",
            "recovery_codes_remaining": (
                await mfa_service.count_unused_recovery_codes(db, user)
                if used_recovery else None
            ),
        },
        ip_address=ip,
    )
    await db.commit()
    await _consume_mfa_token(user)
    logger.info("MFA verified: user_id=%s recovery=%s", user.id, used_recovery)
    return tokens


# ── Self-service management ──────────────────────────────────────────────────


async def _verify_second_factor(
    db: AsyncSession, user: User, code: Optional[str], recovery_code: Optional[str]
) -> bool:
    """Check a live second factor for a management action."""
    if recovery_code:
        return await mfa_service.consume_recovery_code(db, user, recovery_code)
    if not code:
        return False
    loaded = await mfa_service.load_totp_secret(db, user)
    if not loaded.usable:
        raise _SEED_BROKEN
    step = mfa_service.verify_totp(loaded.secret or "", code, user.mfa_last_used_step)
    if step is None:
        return False
    user.mfa_last_used_step = step
    return True


@router.post("/disable", status_code=204)
async def disable_mfa(
    payload: MfaDisableRequest,
    request: Request,
    current_user: User = Depends(_require_interactive_user),
    db: AsyncSession = Depends(get_db),
):
    """Turn MFA off. Requires the password **and** a live second factor."""
    if not current_user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA is not enabled for this account.",
        )
    if not verify_password(payload.password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Password is incorrect"
        )

    # Policy is checked *before* the second factor so a user who is not allowed
    # to disable MFA is not asked to burn a recovery code finding that out.
    policy = await mfa_service.load_policy(db)
    if mfa_service.policy_requires_mfa(current_user, policy) and not await mfa_service.is_sso_managed(
        db, current_user
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Workspace policy requires MFA for your role, so it cannot be "
                "disabled. Ask an administrator to change the policy or reset "
                "your device."
            ),
        )

    if not await _verify_second_factor(db, current_user, payload.code, payload.recovery_code):
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid authenticator code or recovery code is required to disable MFA.",
        )

    await mfa_service.disable_mfa(db, current_user)
    await log_identity_event(
        db,
        IdentityEventType.MFA_DISABLED,
        user_id=current_user.id,
        actor_id=current_user.id,
        actor_name=current_user.username,
        detail={"initiated_by": "self"},
        ip_address=_client_ip(request),
    )
    await db.commit()
    logger.info("MFA disabled: user_id=%s", current_user.id)
    return None


@router.post("/recovery-codes", response_model=MfaRecoveryCodesResponse)
async def reissue_recovery_codes(
    payload: MfaRecoveryCodesRequest,
    request: Request,
    current_user: User = Depends(_require_interactive_user),
    db: AsyncSession = Depends(get_db),
):
    """Replace the recovery-code set. The previous codes stop working."""
    if not current_user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA is not enabled for this account.",
        )
    if not verify_password(payload.password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Password is incorrect"
        )
    if not await _verify_second_factor(db, current_user, payload.code, payload.recovery_code):
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid authenticator code or recovery code is required.",
        )

    codes = await mfa_service.issue_recovery_codes(db, current_user)
    await log_identity_event(
        db,
        IdentityEventType.MFA_RECOVERY_CODES_REISSUED,
        user_id=current_user.id,
        actor_id=current_user.id,
        actor_name=current_user.username,
        ip_address=_client_ip(request),
    )
    await db.commit()
    logger.info("MFA recovery codes reissued: user_id=%s", current_user.id)
    return MfaRecoveryCodesResponse(recovery_codes=codes)


@router.get("/status", response_model=MfaStatusResponse)
async def mfa_status(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Current MFA state for the caller.

    Readable with an API key (unlike the mutating endpoints) so a CI job can
    report on its owner's posture without being able to change it.
    """
    policy = await mfa_service.load_policy(db)
    sso_managed = await mfa_service.is_sso_managed(db, current_user)
    unreadable = False
    if current_user.mfa_enabled:
        unreadable = not (await mfa_service.load_totp_secret(db, current_user)).usable
    return MfaStatusResponse(
        enabled=bool(current_user.mfa_enabled),
        enrolled_at=current_user.mfa_enrolled_at,
        recovery_codes_remaining=await mfa_service.count_unused_recovery_codes(db, current_user),
        required_by_policy=mfa_service.policy_requires_mfa(current_user, policy) and not sso_managed,
        sso_managed=sso_managed,
        secret_unreadable=unreadable,
    )
