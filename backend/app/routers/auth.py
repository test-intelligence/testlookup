"""Authentication endpoints — register, login, refresh, me, change-password."""
import logging
import uuid as _uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_active_user, oauth2_scheme
from app.core.security import (
    create_access_token,
    decode_token,
    get_password_hash,
    verify_password,
)
from app.core.token_revocation import revoke_all_user_tokens, revoke_jti
from app.db.postgres import get_db
from app.models.postgres import IdentityEventType, User, UserRole
from app.services.refresh_token_service import (
    RefreshTokenError,
    _revoke_family as _revoke_refresh_family,
    issue_refresh_token,
    rotate_refresh_token,
)
from app.models.schemas import (
    ChangePasswordRequest,
    FirstTimeResetRequest,
    RefreshRequest,
    SelfUpdateProfileRequest,
    TokenResponse,
    UserCreate,
    UserResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


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
    logger.info("New user self-registered: %s (role=QA_ENGINEER, must_change_password=True)", user.username)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """Authenticate and return JWT access + refresh tokens."""
    result = await db.execute(
        select(User).where(
            (User.username == form_data.username) | (User.email == form_data.username)
        )
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.hashed_password):
        logger.warning("Failed login attempt for: %s", form_data.username)
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
                logger.info("Admin fallback login for: %s", user.username)
            else:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="SSO is required for this account. Please use the SSO login option.",
                )

    access_token = create_access_token(str(user.id))
    refresh_token = await issue_refresh_token(db, user.id)
    await db.commit()
    logger.info("User logged in: %s (must_change_password=%s)", user.username, user.must_change_password)

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

    access_token = create_access_token(str(user.id))
    refresh_token = await issue_refresh_token(db, user.id)
    await db.commit()
    logger.info("dev-login: issued token for %s (%s)", user.username, role)

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
    await db.commit()
    await revoke_all_user_tokens(current_user.id)
    logger.info("First-time password reset completed for user: %s", current_user.username)
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
        data = decode_token(payload.refresh_token)
        if data.get("type") != "refresh":
            raise credentials_exception
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

    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise credentials_exception

    try:
        new_refresh = await rotate_refresh_token(db, uid, jti)
    except RefreshTokenError as exc:
        await db.commit()  # persist any family revocation from replay detection
        logger.warning("Refresh token rejected for %s: %s", user.username, exc)
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

    await _revoke_refresh_family(db, current_user.id, reason="logout")
    await db.commit()

    logger.info("User logged out: %s", current_user.username)
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
    await db.commit()
    await revoke_all_user_tokens(current_user.id)
    logger.info("Password changed for user: %s", current_user.username)
    return None
