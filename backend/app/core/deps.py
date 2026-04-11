"""Dependencies for FastAPI (authentication, authorisation, webhook security)."""
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_token
from app.db.postgres import get_db
from app.models.postgres import ApiKey, User, UserRole

logger = logging.getLogger(__name__)

# P3-2: Redis cache TTL for project membership lookups (seconds)
_MEMBERSHIP_CACHE_TTL = 300  # 5 minutes

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    scheme_name="JWT",
)

# CLI-5: Optional OAuth2 scheme for dual auth (JWT OR API key)
oauth2_scheme_optional = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    scheme_name="JWT",
    auto_error=False,
)

# Role hierarchy — higher index = more privileged
_ROLE_ORDER: list[UserRole] = [
    UserRole.VIEWER,
    UserRole.TESTER,
    UserRole.QA_ENGINEER,
    UserRole.QA_LEAD,
    UserRole.ADMIN,
]


def _normalize_user_role(value: UserRole | str) -> UserRole:
    """Convert a stored role string to a UserRole enum.

    After migration 0045 cleaned legacy ``'UserRole.X'`` values, this is a
    simple str → enum conversion.  The ``UserRole.`` prefix guard is retained
    only as a safety net for any un-migrated rows.
    """
    if isinstance(value, UserRole):
        return value
    raw_value = str(value).strip()
    if raw_value.startswith("UserRole."):
        raw_value = raw_value.split(".", 1)[1]
    return UserRole(raw_value)


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    token: str = Depends(oauth2_scheme),
) -> User:
    """Validate JWT access token and return the matching User row."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_token(token)
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type — use an access token",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except JWTError:
        raise credentials_exception

    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Return the current user, raising 403 if the account is disabled."""
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account",
        )
    return current_user


# ── CLI-5: Dual auth (JWT OR API Key) ────────────────────────────────────────


@dataclass
class ApiKeyContext:
    """Internal result of API key validation — carries project scope."""
    user: User
    project_id: uuid.UUID | None  # None for user-scoped keys


async def _validate_api_key(db: AsyncSession, raw_key: str) -> ApiKeyContext:
    """Validate an API key from the X-API-Key header and return context."""
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    result = await db.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash)
    )
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    if not api_key.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="API key is inactive")
    if api_key.expires_at and api_key.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="API key has expired")

    # Load the owning user
    user_result = await db.execute(select(User).where(User.id == api_key.user_id))
    user = user_result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="API key owner account is inactive")

    # Update last_used_at
    api_key.last_used_at = datetime.now(timezone.utc)
    await db.commit()

    return ApiKeyContext(user=user, project_id=api_key.project_id)


async def get_current_user_or_api_key(
    db: AsyncSession = Depends(get_db),
    bearer_token: Optional[str] = Depends(oauth2_scheme_optional),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> User:
    """Authenticate via JWT Bearer token OR X-API-Key header.

    Tries JWT first (if Authorization header present), falls back to API key.
    Raises 401 if neither is provided or valid.
    Returns User only (backward-compatible). Use ``get_api_key_context`` when
    you need the bound project_id.
    """
    # Try JWT first
    if bearer_token:
        try:
            return await get_current_user(db=db, token=bearer_token)
        except HTTPException:
            pass  # Fall through to API key

    # Try API key
    if x_api_key:
        ctx = await _validate_api_key(db, x_api_key)
        return ctx.user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required — provide Authorization Bearer token or X-API-Key header",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_api_key_context(
    db: AsyncSession = Depends(get_db),
    bearer_token: Optional[str] = Depends(oauth2_scheme_optional),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> tuple[User, uuid.UUID | None]:
    """Authenticate via JWT or API key and return (user, bound_project_id).

    Returns:
        (user, None) for JWT auth or user-scoped API key.
        (user, project_uuid) for project-scoped API key.

    Callers must enforce the project_id constraint when non-None.
    """
    if bearer_token:
        try:
            user = await get_current_user(db=db, token=bearer_token)
            return user, None
        except HTTPException:
            pass

    if x_api_key:
        ctx = await _validate_api_key(db, x_api_key)
        return ctx.user, ctx.project_id

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required — provide Authorization Bearer token or X-API-Key header",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_role(min_role: UserRole) -> Callable:
    """
    Return a FastAPI dependency that enforces a minimum role level.

    Usage:
        @router.post("", dependencies=[Depends(require_role(UserRole.QA_LEAD))])
        # or
        current_user: User = Depends(require_role(UserRole.QA_ENGINEER))
    """
    min_idx = _ROLE_ORDER.index(min_role)

    async def _check(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        try:
            user_idx = _ROLE_ORDER.index(_normalize_user_role(current_user.role))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        if user_idx < min_idx:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires at least {min_role.value} role",
            )
        return current_user

    return _check


def require_project_role(min_role: UserRole) -> Callable:
    """
    Dependency that checks project-level role from project_members table,
    falling back to the user's global role when no project membership exists.

    The project_id is read from path params. If no project_id in path,
    falls back to global role check.
    """
    from app.models.postgres import ProjectMember  # local import to avoid circular

    min_idx = _ROLE_ORDER.index(min_role)

    async def _check(
        request: Request,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        project_id_str = request.path_params.get("project_id")
        if project_id_str:
            try:
                project_uuid = uuid.UUID(project_id_str)
                result = await db.execute(
                    select(ProjectMember).where(
                        ProjectMember.user_id == current_user.id,
                        ProjectMember.project_id == project_uuid,
                    )
                )
                membership = result.scalar_one_or_none()
                if membership:
                    effective_role = _normalize_user_role(membership.role)
                else:
                    effective_role = _normalize_user_role(current_user.role)
            except ValueError:
                effective_role = _normalize_user_role(current_user.role)
        else:
            effective_role = _normalize_user_role(current_user.role)

        try:
            user_idx = _ROLE_ORDER.index(effective_role)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

        if user_idx < min_idx:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires at least {min_role.value} role for this project",
            )
        return current_user

    return _check


# ── Project / run-scoped access (BL-01: tenant isolation) ────────────────────

async def get_accessible_project_ids(
    db: AsyncSession,
    user: User,
) -> set[uuid.UUID] | None:
    """
    Return the set of project IDs this user can access, or None if the user
    is ADMIN (meaning unrestricted access).

    Non-admin users can only access projects they are a member of.

    P3-2: Results are cached in Redis for 5 minutes to avoid a DB query on
    every request.  Invalidated by ``invalidate_membership_cache()``.
    """
    from app.models.postgres import ProjectMember

    if _normalize_user_role(user.role) == UserRole.ADMIN:
        return None  # ADMIN sees everything

    # P3-2: Check Redis cache first
    cache_key = f"membership:{user.id}"
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        cached = await redis.get(cache_key)
        if cached is not None:
            return {uuid.UUID(pid) for pid in json.loads(cached)}
    except Exception:
        pass  # Redis unavailable — fall through to DB

    result = await db.execute(
        select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
    )
    project_ids = {row[0] for row in result.all()}

    # Store in Redis cache
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        await redis.set(
            cache_key,
            json.dumps([str(pid) for pid in project_ids]),
            ex=_MEMBERSHIP_CACHE_TTL,
        )
    except Exception:
        pass  # Cache write failure is non-blocking

    return project_ids


async def invalidate_membership_cache(user_id: uuid.UUID) -> None:
    """Invalidate the cached project membership set for a user.

    Call after adding/removing/updating project members.
    """
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        await redis.delete(f"membership:{user_id}")
    except Exception:
        pass  # Best-effort invalidation


def require_project_access(project_id_param: str = "project_id"):
    """
    Dependency that verifies the current user has access to a project.
    ADMIN bypasses. Non-members get 403.
    """
    from app.models.postgres import ProjectMember

    async def _check(
        request: Request,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        if _normalize_user_role(current_user.role) == UserRole.ADMIN:
            return current_user

        project_id_str = request.path_params.get(project_id_param)
        if not project_id_str:
            return current_user

        try:
            project_uuid = uuid.UUID(project_id_str)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid project ID")

        result = await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.user_id == current_user.id,
                ProjectMember.project_id == project_uuid,
            )
        )
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this project",
            )
        return current_user

    return _check


def require_run_access():
    """
    Dependency that resolves a run's project and checks membership.
    Reads `run_id` from path params, verifies project access.
    """
    from app.models.postgres import ProjectMember, TestRun

    async def _check(
        request: Request,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        if _normalize_user_role(current_user.role) == UserRole.ADMIN:
            return current_user

        run_id_str = request.path_params.get("run_id")
        if not run_id_str:
            return current_user

        try:
            run_uuid = uuid.UUID(run_id_str)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid run ID")

        result = await db.execute(
            select(TestRun.project_id).where(TestRun.id == run_uuid)
        )
        project_id = result.scalar_one_or_none()
        if not project_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test run not found")

        membership = await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.user_id == current_user.id,
                ProjectMember.project_id == project_id,
            )
        )
        if not membership.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this run's project",
            )
        return current_user

    return _check


async def verify_webhook_secret(
    x_webhook_secret: str = Header(..., alias="X-Webhook-Secret"),
) -> None:
    """Validate the shared webhook secret header sent by MinIO."""
    if x_webhook_secret != settings.WEBHOOK_SECRET:
        logger.warning("Webhook request with invalid secret rejected")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook secret",
        )
