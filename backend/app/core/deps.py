"""Dependencies for FastAPI (authentication, authorisation, webhook security)."""
import hashlib
import hmac
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
_API_KEY_BOUND_PROJECT_ATTR = "_testlookup_api_key_project_id"

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


def _bind_api_key_project(user: User, project_id: uuid.UUID | None) -> User:
    """Attach request-local API-key project scope to the loaded user object."""
    setattr(user, _API_KEY_BOUND_PROJECT_ATTR, project_id)
    return user


def _api_key_bound_project(user: User) -> uuid.UUID | None:
    value = getattr(user, _API_KEY_BOUND_PROJECT_ATTR, None)
    return value if isinstance(value, uuid.UUID) else None


def _enforce_api_key_project_binding(
    user: User,
    project_id: uuid.UUID,
    *,
    detail: str = "This API key is restricted to a different project",
) -> None:
    bound_project_id = _api_key_bound_project(user)
    if bound_project_id is not None and bound_project_id != project_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=detail)


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    token: str = Depends(oauth2_scheme),
) -> User:
    """Validate JWT access token and return the matching User row.

    In addition to signature/exp validation, this also consults the token
    revocation store so explicit logouts and password changes actually
    invalidate existing tokens before their natural expiry. See
    ``app/core/token_revocation.py`` for the revocation scopes.
    """
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

    # Revocation checks. Fail-CLOSED when the revocation store is unreachable:
    # "we cannot verify whether this token was revoked" is not "it wasn't".
    # The answer is 503, not 401 — the credentials may be perfectly good; it is
    # the server that cannot check them, and a 401 would put the SPA in a
    # re-login loop that cannot succeed. See token_revocation.py for the
    # availability trade-off and the AUTH_REVOCATION_FAIL_OPEN escape hatch.
    from app.core.token_revocation import (
        RevocationUnavailable,
        is_jti_revoked,
        is_token_before_cutoff,
    )
    jti = payload.get("jti")
    iat = payload.get("iat")
    iat_int: Optional[int] = None
    if isinstance(iat, (int, float)):
        iat_int = int(iat)
    try:
        if jti and await is_jti_revoked(str(jti)):
            raise credentials_exception
        if await is_token_before_cutoff(uid, iat_int):
            raise credentials_exception
    except RevocationUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Token revocation status cannot be verified right now "
                "(revocation store unavailable). This is a server-side outage, "
                "not a credential problem — retry shortly."
            ),
            headers={"Retry-After": "5"},
        )

    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception

    return _bind_api_key_project(user, None)


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

    # Touch last_used_at on the injected session WITHOUT committing here —
    # the auth dependency does not own this transaction. The request's get_db
    # dependency commits on success, so the timestamp persists with the
    # handler's own work instead of prematurely ending its transaction.
    api_key.last_used_at = datetime.now(timezone.utc)

    return ApiKeyContext(user=_bind_api_key_project(user, api_key.project_id), project_id=api_key.project_id)


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


async def get_current_active_user(
    current_user: User = Depends(get_current_user_or_api_key),
) -> User:
    """Return the current user from JWT or API key, raising 403 if disabled."""
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account",
        )
    return current_user


@dataclass
class StreamingApiKeyContext:
    """Context for the API-key-only streaming ingest endpoint.

    Distinct from ``ApiKeyContext`` because streaming has stricter requirements:
    project-scoped key + ``stream:write`` scope. Carries the api_key id/name so
    the server can label auto-created live sessions with a recognisable client.
    """
    user: User
    project_id: uuid.UUID
    api_key_id: uuid.UUID
    api_key_name: str


_STREAM_WRITE_SCOPE = "stream:write"


async def get_streaming_api_key_context(
    db: AsyncSession = Depends(get_db),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> StreamingApiKeyContext:
    """Authenticate the streaming ingest endpoint using an API key only.

    Enforces three rules beyond the standard ``_validate_api_key`` checks:
      1. JWT auth is **not** accepted — streaming is API-key only so the same
         key can be safely embedded in CI configuration.
      2. The API key must be project-scoped — the server derives ``project_id``
         from the key, so the client never sends it.
      3. The API key must declare the ``stream:write`` scope. Legacy keys with
         an empty/null scopes list are treated as full-access (backwards
         compatible with keys minted before scope enforcement landed).
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Streaming requires an X-API-Key header",
        )

    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    api_key = (
        await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
    ).scalar_one_or_none()
    if not api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    if not api_key.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="API key is inactive")
    if api_key.expires_at and api_key.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="API key has expired")
    if api_key.project_id is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Streaming requires a project-scoped API key. Re-issue the key with a project binding.",
        )

    scopes = api_key.scopes or []
    if scopes and _STREAM_WRITE_SCOPE not in scopes:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"API key lacks the {_STREAM_WRITE_SCOPE!r} scope",
        )

    user = (
        await db.execute(select(User).where(User.id == api_key.user_id))
    ).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="API key owner account is inactive",
        )

    # See _validate_api_key: touch the timestamp but let get_db own the commit.
    api_key.last_used_at = datetime.now(timezone.utc)

    return StreamingApiKeyContext(
        user=user,
        project_id=api_key.project_id,
        api_key_id=api_key.id,
        api_key_name=api_key.name,
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

    bound_project_id = _api_key_bound_project(user)
    if bound_project_id is not None:
        return {bound_project_id}

    if _normalize_user_role(user.role) == UserRole.ADMIN:
        return None  # ADMIN sees everything

    # P3-2: Check Redis cache first. A persistent Redis failure would silently
    # cascade into every request hitting the DB; log the first occurrence per
    # process so ops can see the dependency degrade instead of only noticing
    # the tail-latency symptom.
    cache_key = f"membership:{user.id}"
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        cached = await redis.get(cache_key)
        if cached is not None:
            verified = _verify_membership_cache(user.id, cached)
            if verified is not None:
                return verified
            # Signature missing/invalid → treat as a cache miss and fall
            # through to the DB (the authoritative source). Defence-in-depth
            # for tenant isolation: even if Redis is writable by an attacker
            # (or a stale/plain legacy entry survives a deploy), a poisoned or
            # forged ``membership:*`` value cannot widen a user's project scope
            # — it just fails the HMAC and is ignored. See the signing helpers.
            _warn_membership_cache_tampered(user.id)
    except Exception as exc:
        _warn_membership_cache_degraded("read", exc)

    result = await db.execute(
        select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
    )
    project_ids = {row[0] for row in result.all()}

    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        await redis.set(
            cache_key,
            _sign_membership_cache(user.id, project_ids),
            ex=_MEMBERSHIP_CACHE_TTL,
        )
    except Exception as exc:
        _warn_membership_cache_degraded("write", exc)

    return project_ids


def _membership_signature(user_id, payload: str) -> str:
    """HMAC-SHA256 of the cached membership payload, keyed by APP_SECRET_KEY and
    bound to the user id so a valid blob can't be replayed under another user's
    cache key."""
    msg = f"{user_id}:{payload}".encode("utf-8")
    key = (settings.APP_SECRET_KEY or "").encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _sign_membership_cache(user_id, project_ids: set) -> str:
    """Serialise + sign a user's accessible project-id set for Redis storage."""
    payload = json.dumps(sorted(str(pid) for pid in project_ids))
    return json.dumps({"p": payload, "s": _membership_signature(user_id, payload)})


def _verify_membership_cache(user_id, cached):
    """Return the cached project-id set iff the value carries a valid HMAC for
    this user; ``None`` on any decode/format error or signature mismatch (the
    caller then treats it as a miss and re-reads from the DB)."""
    try:
        if isinstance(cached, (bytes, bytearray)):
            cached = cached.decode("utf-8")
        blob = json.loads(cached)
        payload = blob["p"]
        sig = blob["s"]
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    if not isinstance(sig, str) or not hmac.compare_digest(sig, _membership_signature(user_id, payload)):
        return None
    try:
        return {uuid.UUID(pid) for pid in json.loads(payload)}
    except (ValueError, TypeError):
        return None


# Throttle noisy warnings — log once per hour per op if Redis is down.
_last_cache_warn_ts: dict[str, float] = {}


def _warn_membership_cache_degraded(op: str, exc: Exception) -> None:
    import time as _time
    now = _time.monotonic()
    last = _last_cache_warn_ts.get(op, 0.0)
    if now - last < 3600:
        return
    _last_cache_warn_ts[op] = now
    logger.warning(
        "membership cache %s failed — falling back to DB on every request: %s",
        op, exc,
    )


def _warn_membership_cache_tampered(user_id) -> None:
    """A ``membership:*`` entry failed HMAC verification — either tampering or a
    legacy/plain value from before signing. Throttled like the degraded warner;
    security-relevant, so it's logged rather than silently swallowed."""
    import time as _time
    now = _time.monotonic()
    last = _last_cache_warn_ts.get("tamper", 0.0)
    if now - last < 3600:
        return
    _last_cache_warn_ts["tamper"] = now
    logger.warning(
        "membership cache entry failed signature verification for user=%s — "
        "ignoring cache and reading membership from DB (possible tampering or "
        "a pre-signing legacy value)", user_id,
    )


async def resolve_project_scope(
    db: AsyncSession,
    user: User,
    requested_project_id: Optional[str],
) -> tuple[Optional[uuid.UUID], Optional[set[uuid.UUID]]]:
    """
    Resolve the effective project scope for a query, enforcing tenant isolation.

    Returns a tuple ``(project_id, allowed_project_ids)``:

    - ADMIN, no request  → ``(None, None)``               — unrestricted
    - ADMIN, specific    → ``(UUID, None)``               — unrestricted, pinned to one project
    - Non-admin, no req  → ``(None, {member_ids})``       — scoped to user's memberships
    - Non-admin, specific & allowed → ``(UUID, None)``    — pinned, access verified
    - Non-admin, specific & denied  → raises HTTP 403
    - Non-admin with zero memberships → ``(None, set())`` — caller returns empty results

    The two return slots are mutually exclusive: callers filter by ``project_id``
    when set, otherwise by ``allowed_project_ids.in_(...)`` when non-None.
    """
    parsed: Optional[uuid.UUID] = None
    if requested_project_id:
        try:
            parsed = uuid.UUID(requested_project_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid project ID",
            )

    accessible = await get_accessible_project_ids(db, user)
    # ADMIN: accessible is None, no restriction
    if accessible is None:
        return parsed, None

    # Non-admin
    if parsed is not None:
        if parsed not in accessible:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this project",
            )
        return parsed, None

    # Non-admin, no specific project → scope to membership set
    return None, accessible


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
        bound_project_id = _api_key_bound_project(current_user)
        if bound_project_id is None and _normalize_user_role(current_user.role) == UserRole.ADMIN:
            return current_user

        project_id_str = request.path_params.get(project_id_param)
        if not project_id_str:
            return current_user

        try:
            project_uuid = uuid.UUID(project_id_str)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid project ID")

        _enforce_api_key_project_binding(current_user, project_uuid)
        if _normalize_user_role(current_user.role) == UserRole.ADMIN:
            return current_user

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
        bound_project_id = _api_key_bound_project(current_user)
        if bound_project_id is None and _normalize_user_role(current_user.role) == UserRole.ADMIN:
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

        _enforce_api_key_project_binding(
            current_user,
            project_id,
            detail="This API key is restricted to a different project",
        )
        if _normalize_user_role(current_user.role) == UserRole.ADMIN:
            return current_user

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


def _make_project_scoped_guard(
    model_getter: Callable,
    id_param: str,
    guard_name: str,
    not_found_detail: str,
    deny_detail: str,
):
    """Factory for "load a project-scoped resource and check membership" guards.

    The three resource-specific wrappers (``require_release_access`` etc.)
    share the same three-step logic:

      1. Extract a UUID path param named ``id_param``.
      2. ``SELECT project_id FROM <table> WHERE id = :pid`` to find the
         owning project.
      3. Check ``ProjectMember`` for the current user against that project.
         ADMIN bypasses.

    ``model_getter`` is a callable that returns the SQLAlchemy model class,
    so we can lazy-import the model inside and avoid circular imports at
    module load time.

    We override ``_check.__qualname__`` with the caller's ``guard_name`` so
    the architectural ratchet (which identifies guards by qualname substring
    match) can tell the three wrappers apart.
    """
    from app.models.postgres import ProjectMember

    async def _check(
        request: Request,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        bound_project_id = _api_key_bound_project(current_user)
        if bound_project_id is None and _normalize_user_role(current_user.role) == UserRole.ADMIN:
            return current_user

        raw = request.path_params.get(id_param)
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"{guard_name} wired on route missing {{{id_param}}}",
            )
        try:
            resource_uuid = uuid.UUID(raw)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {id_param}")

        model = model_getter()
        result = await db.execute(
            select(model.project_id).where(model.id == resource_uuid)
        )
        project_id = result.scalar_one_or_none()
        if project_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=not_found_detail)

        _enforce_api_key_project_binding(current_user, project_id)
        if _normalize_user_role(current_user.role) == UserRole.ADMIN:
            return current_user

        membership = await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.user_id == current_user.id,
                ProjectMember.project_id == project_id,
            )
        )
        if membership.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=deny_detail)
        return current_user

    # Make the guard identifiable by name in the architectural ratchet,
    # which inspects ``call.__qualname__`` for substring matches.
    _check.__qualname__ = f"{guard_name}.<locals>._check"
    return _check


def require_release_access():
    """Verify the caller is a member of the project that owns ``{release_id}``."""
    def _model():
        from app.models.postgres import Release
        return Release

    return _make_project_scoped_guard(
        _model,
        id_param="release_id",
        guard_name="require_release_access",
        not_found_detail="Release not found",
        deny_detail="You do not have access to this release",
    )


def require_knowledge_source_access():
    """Verify the caller is a member of the project that owns ``{source_id}``."""
    def _model():
        from app.models.postgres import KnowledgeSource
        return KnowledgeSource

    return _make_project_scoped_guard(
        _model,
        id_param="source_id",
        guard_name="require_knowledge_source_access",
        not_found_detail="Knowledge source not found",
        deny_detail="You do not have access to this knowledge source",
    )


def require_generation_batch_access():
    """Verify the caller is a member of the project that owns ``{batch_id}``."""
    def _model():
        from app.models.postgres import GenerationBatch
        return GenerationBatch

    return _make_project_scoped_guard(
        _model,
        id_param="batch_id",
        guard_name="require_generation_batch_access",
        not_found_detail="Generation batch not found",
        deny_detail="You do not have access to this generation batch",
    )


def require_live_session_access():
    """Verify the caller is a member of the project that owns ``{session_id}``.

    :class:`LiveSession` carries ``project_id`` directly, so the generic
    project-scoped factory fits it cleanly.
    """
    def _model():
        from app.models.postgres import LiveSession
        return LiveSession

    return _make_project_scoped_guard(
        _model,
        id_param="session_id",
        guard_name="require_live_session_access",
        not_found_detail="Live session not found",
        deny_detail="You do not have access to this live session",
    )


def require_api_key_owner():
    """Resolve an :class:`ApiKey` by ``{key_id}`` and verify caller ownership.

    API keys are owned by a single user via ``user_id``; ADMIN bypasses.
    Unlike the project-scoped guards this one does not need ``ProjectMember``
    resolution — the owner check is a single-column comparison.
    """
    from app.models.postgres import ApiKey

    async def _check(
        request: Request,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        raw = request.path_params.get("key_id")
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="require_api_key_owner wired on route missing {key_id}",
            )
        try:
            key_uuid = uuid.UUID(raw)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid key_id")

        owner_id = (await db.execute(
            select(ApiKey.user_id).where(ApiKey.id == key_uuid)
        )).scalar_one_or_none()
        if owner_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

        if _normalize_user_role(current_user.role) == UserRole.ADMIN:
            return current_user
        if owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not own this API key",
            )
        return current_user

    return _check


def require_session_access():
    """Resolve a chat session by ``{session_id}`` and verify ownership.

    Returns the loaded :class:`ChatSession` so the handler does not need to
    re-query. Creator-only semantics: the owning user (and ADMIN) may read,
    delete, and post to a session; no one else, even fellow project members.

    Unlike the older ``require_*_access`` helpers this dependency returns the
    resource instead of the current user, so handlers can consume it directly:

        async def delete_session(
            session: ChatSession = Depends(require_session_access()),
            db: AsyncSession = Depends(get_db),
        ):
            ...
    """
    from app.models.postgres import ChatSession

    async def _check(
        request: Request,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
    ) -> "ChatSession":
        raw = request.path_params.get("session_id")
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="require_session_access wired on non-session route",
            )
        try:
            session_uuid = uuid.UUID(raw)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid session ID")

        result = await db.execute(select(ChatSession).where(ChatSession.id == session_uuid))
        session = result.scalar_one_or_none()
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

        is_admin = _normalize_user_role(current_user.role) == UserRole.ADMIN
        if not is_admin and session.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this chat session",
            )
        return session

    return _check


def require_link_access():
    """Resolve a :class:`ReportShareLink` by ``{link_id}`` and verify access.

    Access is granted to the link's creator, any member of the owning
    project, and ADMIN. Returns the loaded link so the handler does not
    need to re-query.
    """
    from app.models.postgres import ProjectMember, ReportShareLink

    async def _check(
        request: Request,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
    ) -> "ReportShareLink":
        raw = request.path_params.get("link_id")
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="require_link_access wired on non-link route",
            )
        try:
            link_uuid = uuid.UUID(raw)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid link ID")

        result = await db.execute(select(ReportShareLink).where(ReportShareLink.id == link_uuid))
        link = result.scalar_one_or_none()
        if link is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share link not found")

        is_admin = _normalize_user_role(current_user.role) == UserRole.ADMIN
        if is_admin or link.created_by_id == current_user.id:
            return link

        membership = await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.user_id == current_user.id,
                ProjectMember.project_id == link.project_id,
            )
        )
        if not membership.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this share link",
            )
        return link

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
