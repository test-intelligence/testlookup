"""Authentication endpoints — register, login, refresh, me, change-password."""
import logging
import uuid as _uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_active_user, oauth2_scheme
from app.core.security import (
    MFA_CHALLENGE_TOKEN_TYPE,
    MFA_ENROLLMENT_TOKEN_TYPE,
    create_access_token,
    create_mfa_token,
    decode_token,
    get_password_hash,
    verify_password,
)
from app.core.token_revocation import RevocationUnavailable, revoke_all_user_tokens, revoke_jti
from app.db.postgres import get_db
from app.models.postgres import IdentityEventType, User, UserRole
from app.services import mfa_service, ui_dismissal_service
from app.services.refresh_token_service import (
    RefreshTokenError,
    _revoke_family as _revoke_refresh_family,
    issue_refresh_token,
    rotate_refresh_token,
)
from app.models.schemas import (
    ChangePasswordRequest,
    FirstTimeResetRequest,
    MfaChallengeResponse,
    MfaEnrollmentRequiredResponse,
    RefreshRequest,
    SelfUpdateProfileRequest,
    TokenResponse,
    UserCreate,
    UIDismissalCreate,
    UIDismissalListResponse,
    UserResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    """A throwaway bcrypt hash used to equalise login timing when the supplied
    username/email doesn't exist (anti user-enumeration). Computed once."""
    import secrets as _secrets

    return get_password_hash(_secrets.token_urlsafe(16))


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)):
    """
    Self-service registration.

    New accounts are created with the QA_ENGINEER role and
    must_change_password=True so the user is prompted to set a permanent
    password on their first login.
    """
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

    user = User(
        email=payload.email,
        username=payload.username,
        full_name=payload.full_name,
        hashed_password=get_password_hash(payload.password),
        role=UserRole.QA_ENGINEER,
        must_change_password=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info("New user self-registered: user_id=%s (role=QA_ENGINEER, must_change_password=True)", user.id)
    return user


def _locked_response(until: datetime) -> HTTPException:
    """429 + Retry-After for a locked account.

    Enumeration note, stated rather than hidden: this response tells the caller
    the account exists. It is only ever reached by someone who has just typed
    the *correct* password (the lock is checked after verification, so a wrong
    password on a locked account is indistinguishable from a wrong password on
    any other account), and the pre-existing ``403 Account disabled`` branch
    already leaks the same class of fact. The alternative — a generic 401 —
    would leave a locked-out user retrying forever with no idea why, which is
    how lockout turns into a support ticket instead of a control.
    """
    retry = max(1, int((until - datetime.now(timezone.utc)).total_seconds()))
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=(
            "Account temporarily locked after repeated failed sign-in attempts. "
            f"Try again in about {max(1, retry // 60)} minute(s)."
        ),
        headers={"Retry-After": str(retry)},
    )


@router.post(
    "/login",
    response_model=Union[TokenResponse, MfaChallengeResponse, MfaEnrollmentRequiredResponse],
)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """Authenticate and return JWT access + refresh tokens.

    Three success shapes, all HTTP 200 (see ``models/schemas.py``):

    * :class:`TokenResponse` — fully authenticated.
    * :class:`MfaChallengeResponse` — password accepted, second factor owed.
    * :class:`MfaEnrollmentRequiredResponse` — password accepted, workspace
      policy requires MFA and this account has none yet.

    The last two carry interstitial tokens with their own ``type`` claim. They
    are **not** access tokens and are rejected by ``get_current_user``: see
    ``core/security.create_mfa_token`` for why a claim on a real access token
    would have been a complete bypass.
    """
    result = await db.execute(
        select(User).where(
            (User.username == form_data.username) | (User.email == form_data.username)
        ).with_for_update()
    )
    user = result.scalar_one_or_none()

    # Compare against a fixed dummy hash when the user doesn't exist so the
    # response time doesn't reveal whether the username/email is registered
    # (closes a user-enumeration timing oracle).
    password_ok = (
        verify_password(form_data.password, user.hashed_password)
        if user is not None
        else verify_password(form_data.password, _dummy_password_hash())
    )

    policy = await mfa_service.load_policy(db)
    lock_expires = mfa_service.locked_until(user) if user is not None else None

    if not user or not password_ok:
        logger.warning("Failed login attempt for: %s", form_data.username)
        # Only count against a real account, and never while it is already
        # locked — otherwise an attacker hammering a locked account would keep
        # extending the lock and the legitimate owner could never wait it out.
        # Failures against usernames that do not exist are the IP rate
        # limiter's problem; counting them would need a per-username store
        # keyed on unverified input, which is a memory-exhaustion and
        # enumeration vector on its own.
        if user is not None and lock_expires is None:
            await mfa_service.register_failed_attempt(
                db, user, policy,
                reason="password",
                ip_address=request.client.host if request.client else None,
            )
            await db.commit()  # get_db rolls back on the raise below
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account disabled",
        )

    # Checked *after* password verification on purpose — see _locked_response.
    if lock_expires is not None:
        raise _locked_response(lock_expires)

    # ── SSO enforcement check ────────────────────────────────────
    # When SSO is enforced (SSO_REQUIRED mode), only ADMIN users with the
    # fallback flag enabled can use password login. All other users must
    # authenticate through the SSO/SAML flow.
    if settings.SSO_ENABLED:
        from app.services.sso_service import is_sso_enforced, log_identity_event

        sso_enforced = await is_sso_enforced(db)
        if sso_enforced:
            is_admin = user.role == UserRole.ADMIN or str(user.role) == UserRole.ADMIN.value
            if is_admin and settings.SSO_ADMIN_FALLBACK_ENABLED:
                # Admin fallback allowed — log the event
                client_ip = request.client.host if request.client else None
                await log_identity_event(
                    db,
                    IdentityEventType.ADMIN_FALLBACK_LOGIN,
                    user_id=user.id,
                    detail={"method": "password", "reason": "admin_fallback"},
                    ip_address=client_ip,
                )
                logger.info("Admin fallback login for user_id=%s", user.id)
            else:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="SSO is required for this account. Please use the SSO login option.",
                )

    # ── Second factor ────────────────────────────────────────────
    requirement = await mfa_service.evaluate_login_requirement(db, user, policy)

    if requirement is mfa_service.LoginRequirement.BROKEN:
        # Enrolled, but the stored seed will not decrypt. ``read_secret``
        # cannot tell that apart from "never enrolled" — this is the case where
        # rounding it down to "MFA is off" would silently disable the control
        # for every enrolled user after a bad key rotation.
        logger.error("Login denied — unreadable TOTP seed for user_id=%s", user.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=mfa_service.SEED_UNREADABLE_DETAIL,
        )

    if requirement is mfa_service.LoginRequirement.CHALLENGE:
        challenge, _jti, ttl = create_mfa_token(str(user.id), MFA_CHALLENGE_TOKEN_TYPE)
        # No commit needed for the challenge itself (nothing was staged), but
        # the SSO admin-fallback event above may be pending.
        await db.commit()
        logger.info("MFA challenge issued: user_id=%s", user.id)
        return MfaChallengeResponse(challenge_token=challenge, expires_in=ttl)

    if requirement is mfa_service.LoginRequirement.ENROLL:
        enroll_token, _jti, ttl = create_mfa_token(str(user.id), MFA_ENROLLMENT_TOKEN_TYPE)
        await db.commit()
        logger.info("MFA enrollment required at login: user_id=%s", user.id)
        return MfaEnrollmentRequiredResponse(
            enrollment_token=enroll_token,
            expires_in=ttl,
            required_for_role=policy.required_for_role,
        )

    access_token = create_access_token(str(user.id))
    refresh_token = await issue_refresh_token(db, user.id)
    await mfa_service.register_successful_login(db, user)
    await db.commit()
    logger.info("User logged in: user_id=%s (must_change_password=%s)", user.id, user.must_change_password)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        must_change_password=user.must_change_password,
    )


# Mapping from URL-friendly role slug to UserRole enum
_DEV_ROLE_MAP: dict[str, UserRole] = {
    "admin":       UserRole.ADMIN,
    "qa_lead":     UserRole.QA_LEAD,
    "qa_engineer": UserRole.QA_ENGINEER,
    "tester":      UserRole.TESTER,
    "viewer":      UserRole.VIEWER,
}

# Defaults used when creating a temporary user if the seed has not run yet
_DEV_ROLE_DEFAULTS: dict[UserRole, dict] = {
    UserRole.ADMIN:       {"email": "admin@testlookup.dev",    "username": "admin",       "full_name": "Dev Admin"},
    UserRole.QA_LEAD:     {"email": "lead@testlookup.dev",     "username": "qa_lead",     "full_name": "Dev QA Lead"},
    UserRole.QA_ENGINEER: {"email": "engineer@testlookup.dev", "username": "qa_engineer", "full_name": "Dev QA Engineer"},
    UserRole.TESTER:      {"email": "tester@testlookup.dev",   "username": "tester",      "full_name": "Dev Tester"},
    UserRole.VIEWER:      {"email": "viewer@testlookup.dev",   "username": "viewer",      "full_name": "Dev Viewer"},
}


async def _get_or_create_dev_user(
    db: AsyncSession,
    target_role: UserRole,
    role_slug: str,
    username: str | None = None,
) -> User:
    if username:
        requested_username = username.strip()
        result = await db.execute(
            select(User)
            .where(User.username == requested_username)
            .where(User.is_active == True)  # noqa: E712
            .limit(1)
        )
        requested_user = result.scalar_one_or_none()
        if requested_user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Active user '{requested_username}' was not found",
            )
        # Even though dev-login is environment-gated, the username path must not
        # be an impersonation vector for real seeded/admin accounts in a dev DB.
        # Restrict it to synthetic dev accounts (the @testlookup.dev fixtures).
        if not (requested_user.email or "").lower().endswith("@testlookup.dev"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="dev-login username is restricted to synthetic @testlookup.dev accounts",
            )
        return requested_user

    result = await db.execute(
        select(User)
        .where(User.role == target_role)
        .where(User.is_active == True)  # noqa: E712
        .limit(1)
    )
    user = result.scalar_one_or_none()

    if user is not None:
        return user

    # Seed hasn't run yet — create a temporary account so the UI is accessible
    import secrets as _secrets
    defaults = _DEV_ROLE_DEFAULTS[target_role]
    user = User(
        id=_uuid.uuid4(),
        email=defaults["email"],
        username=defaults["username"],
        full_name=defaults["full_name"],
        hashed_password=get_password_hash(_secrets.token_urlsafe(32)),
        role=target_role,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info("dev-login: created temporary %s user (seed not yet run)", role_slug)
    return user


@router.post("/dev-login", response_model=TokenResponse)
async def dev_login(
    role: str = "admin",
    username: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Development-only: issue a JWT for a seeded user without credentials.

    Enabled only when APP_ENV=development AND DEV_AUTO_LOGIN_ENABLED=true.
    Returns 404 in all other environments so it is invisible in staging/prod.

    Query param:
      role — one of: admin, qa_lead, qa_engineer, tester, viewer (default: admin)
      username — optional active username to authenticate as directly

    If the requested user does not exist yet (seed not run), a temporary account
    is created on the fly so developers can always access the UI after `make dev`.
    """
    if not settings.is_development or not settings.DEV_AUTO_LOGIN_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    target_role = _DEV_ROLE_MAP.get(role.lower())
    if target_role is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown role '{role}'. Valid values: {', '.join(_DEV_ROLE_MAP)}",
        )

    user = await _get_or_create_dev_user(
        db=db,
        target_role=target_role,
        role_slug=role,
        username=username,
    )
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    await db.refresh(user)

    # dev-login skips the password, so it would also skip the second factor.
    # It is already unreachable outside APP_ENV=development, but "the dev
    # backdoor is an MFA bypass" is not a sentence worth leaving true — refuse
    # outright for an account that has deliberately enrolled a factor.
    if user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "dev-login cannot be used for an account with MFA enabled. "
                "Sign in with the password + authenticator flow."
            ),
        )

    access_token = create_access_token(str(user.id))
    refresh_token = await issue_refresh_token(db, user.id)
    await db.commit()
    logger.info("dev-login: issued token for user_id=%s (%s)", user.id, role)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/first-time-reset", status_code=204)
async def first_time_reset(
    payload: FirstTimeResetRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Forced password reset for self-registered users on first login.

    - Requires a valid JWT (user must be authenticated).
    - Only permitted when must_change_password=True on the account.
    - Does NOT require the current/registration password.
    - Clears the must_change_password flag after a successful change.
    """
    await db.execute(select(User.id).where(User.id == current_user.id).with_for_update())
    await db.refresh(current_user)
    if not current_user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password reset not required for this account. Use change-password instead.",
        )
    if payload.new_password != payload.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passwords do not match",
        )
    current_user.hashed_password = get_password_hash(payload.new_password)
    current_user.must_change_password = False
    # Revoke every access token and refresh token issued before this
    # password change so the bootstrap token used to call this endpoint
    # cannot be replayed after the password is set.
    await _revoke_refresh_family(db, current_user.id, reason="password_reset")
    try:
        await revoke_all_user_tokens(current_user.id, db)
    except RevocationUnavailable as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Token revocation store unavailable; password reset was not applied") from exc
    await db.commit()
    logger.info("First-time password reset completed: user_id=%s", current_user.id)
    return None


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Exchange a valid refresh token for a new access + refresh token pair (rotation).
    The old refresh token is not reusable after this call.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # decode_token(expected_type="refresh") already rejects a non-refresh
        # token (e.g. an access token presented here), so we only verify `sub`.
        data = decode_token(payload.refresh_token, expected_type="refresh")
        user_id: str = data.get("sub", "")
        jti: str = data.get("jti", "")
        if not user_id or not jti:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    try:
        uid = _uuid.UUID(user_id)
    except ValueError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == uid).with_for_update())
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise credentials_exception

    # Re-evaluate the MFA policy on every rotation. Without this, turning the
    # requirement on would only take effect as sessions expired — up to
    # JWT_REFRESH_TOKEN_EXPIRE_DAYS (7 days) of grandfathered sessions that
    # never present a second factor. Rejecting here sends the SPA back to
    # /login, which then returns the enrollment challenge.
    policy = await mfa_service.load_policy(db)
    requirement = await mfa_service.evaluate_login_requirement(db, user, policy)
    if requirement is mfa_service.LoginRequirement.BROKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=mfa_service.SEED_UNREADABLE_DETAIL,
            headers={"X-Refresh-Retry-Safe": "1"},
        )
    if requirement is mfa_service.LoginRequirement.ENROLL:
        logger.info("Refresh rejected — MFA enrollment now required: user_id=%s", user.id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Multi-factor authentication is now required for your role. "
                "Sign in again to enroll an authenticator."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        new_refresh = await rotate_refresh_token(db, uid, jti)
    except RefreshTokenError as exc:
        await db.commit()  # persist any family revocation from replay detection
        logger.warning("Refresh token rejected for user_id=%s: %s", user.id, exc)
        raise credentials_exception

    new_access = create_access_token(str(user.id))
    await db.commit()

    return TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        token_type="bearer",
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_active_user)):
    """Return the authenticated user's profile."""
    return current_user


@router.get("/me/dismissals", response_model=UIDismissalListResponse)
async def list_my_dismissals(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """UI prompts this user has dismissed. Read-only; no project scope — a
    dismissal is a property of the person, not of a project."""
    keys = await ui_dismissal_service.list_dismissal_keys(db, current_user.id)
    return UIDismissalListResponse(dismissed=keys)


@router.post(
    "/me/dismissals",
    response_model=UIDismissalListResponse,
    status_code=status.HTTP_201_CREATED,
)
async def dismiss_prompt(
    payload: UIDismissalCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Dismiss a UI prompt for this user. Idempotent — dismissing twice is a
    no-op and still returns 201 with the full list."""
    await ui_dismissal_service.record_dismissal(
        db, current_user.id, payload.dismissal_key
    )
    await db.commit()
    keys = await ui_dismissal_service.list_dismissal_keys(db, current_user.id)
    return UIDismissalListResponse(dismissed=keys)


@router.patch("/me", response_model=UserResponse)
async def update_me(
    payload: SelfUpdateProfileRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Self-service profile update — any authenticated user can update their own
    full_name and avatar_color.  Email and username are read-only here."""
    if payload.full_name is not None:
        current_user.full_name = payload.full_name.strip() or None
    if payload.avatar_color is not None:
        current_user.avatar_color = payload.avatar_color
    await db.commit()
    await db.refresh(current_user)
    logger.info("Profile updated for user: %s", str(current_user.id))
    return current_user


@router.post("/logout", status_code=204)
async def logout(
    current_user: User = Depends(get_current_active_user),
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
):
    """
    Server-side logout — revokes the caller's access-token jti until its
    natural expiry and revokes all live refresh tokens for the user so the
    session cannot be re-minted via /auth/refresh.
    """
    try:
        payload = decode_token(token)
        jti = payload.get("jti")
        exp = payload.get("exp")
        if jti and exp:
            remaining = int(exp) - int(datetime.now(timezone.utc).timestamp())
            await revoke_jti(str(jti), remaining)
    except JWTError:
        # Token was accepted by get_current_user but can't be decoded now?
        # Skip the jti denylist entry — the refresh-token revocation below
        # still limits the blast radius.
        pass
    except RevocationUnavailable as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Token revocation store unavailable; logout was not applied") from exc

    await _revoke_refresh_family(db, current_user.id, reason="logout")
    await db.commit()

    logger.info("User logged out: user_id=%s", current_user.id)
    return None


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    limit: int = Query(100, ge=1, le=500, description="Max users to return"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return active users for assignee dropdowns (bounded)."""
    result = await db.execute(
        select(User)
        .where(User.is_active == True)  # noqa: E712
        .order_by(User.full_name)
        .limit(limit)
    )
    return result.scalars().all()


@router.post("/change-password", status_code=204)
async def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Change the current user's password."""
    await db.execute(select(User.id).where(User.id == current_user.id).with_for_update())
    await db.refresh(current_user)
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    current_user.hashed_password = get_password_hash(payload.new_password)
    # A password change is an explicit "revoke every existing session"
    # signal — wipe refresh tokens in Postgres and set the access-token
    # cutoff in Redis so every previously-issued JWT is rejected.
    await _revoke_refresh_family(db, current_user.id, reason="password_change")
    try:
        await revoke_all_user_tokens(current_user.id, db)
    except RevocationUnavailable as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Token revocation store unavailable; password change was not applied") from exc
    await db.commit()
    logger.info("Password changed: user_id=%s", current_user.id)
    return None
