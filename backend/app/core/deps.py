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
from jose import ExpiredSignatureError, JWTError
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

# Which credential authenticated *this* request. Stashed on the loaded ``User``
# object exactly like ``_API_KEY_BOUND_PROJECT_ATTR`` above — the User row is
# loaded fresh from the request-scoped session by every auth dependency, so the
# attribute lives and dies with the request and needs no separate contextvar.
#
# It exists because the two facts are NOT the same: a *user-scoped* API key
# binds ``project_id = None``, which is indistinguishable from a JWT if you only
# look at the project binding. Downstream policy that must treat machine
# credentials differently (e.g. an MFA gate, which must never challenge a
# CI-embedded API key) needs the credential kind, not the project scope.
_CREDENTIAL_KIND_ATTR = "_testlookup_credential_kind"

CREDENTIAL_KIND_JWT = "jwt"
CREDENTIAL_KIND_API_KEY = "api_key"


@dataclass(frozen=True)
class AuthorizedTestCaseContext:
    """Server-resolved ownership for a test-case scoped operation."""

    test_case: object
    test_run: object
    run_id: uuid.UUID
    project_id: uuid.UUID

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


def _bind_credential_kind(user: User, kind: str) -> User:
    """Record which credential authenticated this request on the user object."""
    setattr(user, _CREDENTIAL_KIND_ATTR, kind)
    return user


def credential_kind(user: User) -> str | None:
    """Return the credential that authenticated the current request.

    ``"jwt"`` (:data:`CREDENTIAL_KIND_JWT`) or ``"api_key"``
    (:data:`CREDENTIAL_KIND_API_KEY`); ``None`` when the ``User`` did not come
    from one of the auth dependencies (e.g. a row loaded by a service, or a
    test fixture that overrides the dependency wholesale). ``None`` means
    "unknown", never "jwt" — callers gating on *machine credential* should test
    ``== CREDENTIAL_KIND_API_KEY``, and callers gating on *interactive human*
    should test ``== CREDENTIAL_KIND_JWT``, so an unknown falls out of both.
    """
    value = getattr(user, _CREDENTIAL_KIND_ATTR, None)
    return value if value in (CREDENTIAL_KIND_JWT, CREDENTIAL_KIND_API_KEY) else None


# ── API-key scopes (QA-R3-11) ────────────────────────────────────────────────
#
# ``api_keys.scopes`` was read in exactly one place, the streaming ingest
# dependency. Everywhere else a key declared ``["stream:write"]`` acted with
# its owner's full role: through the routes that opt in with
# ``require_role(UserRole.ADMIN, allow_project_key=True)`` a CI streaming key
# deleted runs, reset its project, purged retention and removed members, and
# ``POST /api/v1/keys`` minted it an unrestricted, never-expiring replacement
# that outlived revoking it.
#
# The rule: a key with a NON-EMPTY scope list may do only what it lists. An
# empty list is a legacy full-access key, exactly as the streaming dependency
# has always treated it.

#: Lets a scoped key use a route that opts a project-bound key in with
#: ``require_role(UserRole.ADMIN, allow_project_key=True)``: deleting the
#: project's runs, resetting it, its retention and deletion jobs, member
#: removal, release/phase deletion, compliance packs, release-gate policies.
PROJECT_ADMIN_SCOPE = "project:admin"

#: What a scoped key without :data:`PROJECT_ADMIN_SCOPE` is told on such a route.
PROJECT_ADMIN_SCOPE_DETAIL = (
    f"This API key's scopes do not include {PROJECT_ADMIN_SCOPE!r}, which this "
    "endpoint requires. Mint a key with that scope (or use a signed-in session)."
)

#: Lets a scoped key WRITE (any method but GET/HEAD/OPTIONS) on a route gated
#: below QA_LEAD: signed-in-only and QA_ENGINEER routes, and the writes of the
#: project-scoped guards on them (re-audit N32). ``project:admin`` implies it.
PROJECT_WRITE_SCOPE = "project:write"

#: What a scoped key without :data:`PROJECT_WRITE_SCOPE` is told on a write.
PROJECT_WRITE_SCOPE_DETAIL = (
    f"This API key's scopes do not include {PROJECT_WRITE_SCOPE!r} (or "
    f"{PROJECT_ADMIN_SCOPE!r}), which a write through this endpoint requires. "
    "Mint a key with that scope (or use a signed-in session)."
)

#: Lets a key use the streaming ingest endpoints (``/api/v1/stream/*``).
STREAM_WRITE_SCOPE = "stream:write"

#: The complete server-side scope vocabulary (re-audit N31). ``POST
#: /api/v1/keys`` refuses any other name with a 422, so every scope a key can
#: be minted with is enforced somewhere:
#:
#: * ``stream:write``: the streaming ingest dependency
#:   (``get_streaming_api_key_context``). A key without it is refused there.
#: * ``project:write``: every non-safe request of a scoped key that reaches
#:   ``get_current_active_user`` (all role and project guards), unless the
#:   route is marked :func:`takes_scoped_key_writes`.
#: * ``project:admin``: ``require_role`` at QA_LEAD and above (any method) and
#:   ``require_project_role`` at QA_LEAD and above (writes); implies
#:   ``project:write``.
#:
#: An EMPTY list is a legacy full-access key and passes every check above.
API_KEY_SCOPES: dict[str, str] = {
    STREAM_WRITE_SCOPE: "Stream test results (/api/v1/stream/*)",
    PROJECT_WRITE_SCOPE: "Write below the QA_LEAD role (runs, triage, suites, feedback, ...)",
    PROJECT_ADMIN_SCOPE: "Administer the key's project (QA_LEAD/ADMIN routes); implies project:write",
}

_API_KEY_GRANT_ATTR = "_testlookup_api_key_grant"


def _normalized_scopes(value) -> tuple[str, ...]:
    """``api_keys.scopes`` as a tuple of non-empty strings (JSON column: list, str or null)."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    return tuple(str(scope) for scope in value if scope)


@dataclass(frozen=True)
class ApiKeyGrant:
    """What the API key that authenticated this request was granted.

    Stashed on the loaded ``User`` like the project binding, by
    ``_validate_api_key``; ``None`` for a JWT.
    """

    key_id: uuid.UUID
    scopes: tuple[str, ...]
    expires_at: datetime | None

    @property
    def is_scoped(self) -> bool:
        return bool(self.scopes)

    def allows(self, scope: str) -> bool:
        """An unscoped (legacy) key allows everything; a scoped one only what it lists."""
        return not self.scopes or scope in self.scopes

    def allows_write(self) -> bool:
        """``project:write``, or ``project:admin`` which implies it (re-audit N32)."""
        return self.allows(PROJECT_WRITE_SCOPE) or self.allows(PROJECT_ADMIN_SCOPE)


#: Methods a scoped key without ``project:admin`` may still use on a
#: project-scoped route (QA-R4-1): reads.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _refuse_scoped_key_without_project_admin(user: User) -> None:
    """403 when the request's API key is scoped and lacks ``project:admin``.

    The one check behind every scope refusal outside streaming (QA-R3-11,
    QA-R4-1, QA-R4-2). A JWT (no grant) and a legacy key (empty scope list)
    pass: ``ApiKeyGrant.allows`` treats an empty list as full access.
    """
    grant = api_key_grant(user)
    if grant is not None and not grant.allows(PROJECT_ADMIN_SCOPE):
        # Counted like any other role shortfall: a leaked CI key probing the
        # administration surface is the burst an operator wants to see.
        _count_auth_failure("insufficient_role")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=PROJECT_ADMIN_SCOPE_DETAIL,
        )


def _is_write(request) -> bool:
    """Any method but GET/HEAD/OPTIONS. No method (a hand-built request) is a
    write: refused with a 403, not an AttributeError's 500."""
    method = getattr(request, "method", None)
    return not isinstance(method, str) or method.upper() not in _SAFE_METHODS


def _refuse_scoped_key_without_write_scope(user: User) -> None:
    """403 when the request's API key is scoped and holds neither
    ``project:write`` nor ``project:admin`` (re-audit N32)."""
    grant = api_key_grant(user)
    if grant is not None and not grant.allows_write():
        _count_auth_failure("insufficient_role")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=PROJECT_WRITE_SCOPE_DETAIL,
        )


def _refuse_scoped_key_write(request: Request, user: User, *, admin: bool = False) -> None:
    """The project-scoped guards' half of the rule: a write needs
    ``project:write``, or ``project:admin`` when ``admin`` (a guard at QA_LEAD
    and above)."""
    if not _is_write(request):
        return
    if admin:
        _refuse_scoped_key_without_project_admin(user)
    else:
        _refuse_scoped_key_without_write_scope(user)


_TAKES_SCOPED_KEY_WRITES_ATTR = "_testlookup_takes_scoped_key_writes"


def takes_scoped_key_writes(endpoint: Callable) -> Callable:
    """Mark a route handler that applies its own rules to a scoped key's writes.

    ``get_current_active_user`` refuses a scoped API key's non-safe request
    unless the key holds ``project:write`` (re-audit N32). A handler marked
    with this takes such a key anyway, because it already confines what the
    key can do: ``POST /api/v1/keys`` mints only a subset of the caller's
    scopes and expiry (a ``["stream:write"]`` CI key rotates itself), and
    ``DELETE /api/v1/keys/{key_id}`` lets a key without ``project:admin``
    revoke only itself. The set of marked handlers is pinned by
    ``tests/integration/test_scoped_key_writes_postgres.py``.
    """
    setattr(endpoint, _TAKES_SCOPED_KEY_WRITES_ATTR, True)
    return endpoint


def _route_takes_scoped_key_writes(request) -> bool:
    scope = getattr(request, "scope", None)
    endpoint = scope.get("endpoint") if isinstance(scope, dict) else None
    return getattr(endpoint, _TAKES_SCOPED_KEY_WRITES_ATTR, False) is True


def _bind_api_key_grant(user: User, grant: ApiKeyGrant | None) -> User:
    setattr(user, _API_KEY_GRANT_ATTR, grant)
    return user


def api_key_grant(user: User) -> ApiKeyGrant | None:
    """The authenticating API key's scopes and expiry, or ``None`` (a JWT, or a
    ``User`` that did not come from an auth dependency)."""
    value = getattr(user, _API_KEY_GRANT_ATTR, None)
    return value if isinstance(value, ApiKeyGrant) else None


def _enforce_api_key_project_binding(
    user: User,
    project_id: uuid.UUID | None,
    *,
    detail: str = "This API key is restricted to a different project",
) -> None:
    """403 when a project-bound API key reaches outside its own project.

    An unbound caller (a JWT, or a user-scoped key) is never refused here: role
    and membership are other checks' business. ``project_id=None`` names
    something that belongs to no single project (the system-default release
    policy, a chat session filed under no project, an unbound API key); a
    bound key is refused that too, since it is outside the one project the key
    names.
    """
    bound_project_id = _api_key_bound_project(user)
    if bound_project_id is not None and bound_project_id != project_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=detail)


def _count_auth_failure(reason: str) -> None:
    """Best-effort ``auth_failures_total`` increment. Never raises.

    The counter declared four reasons -- expired_token, invalid_token,
    insufficient_role, inactive_user -- and emitted none of them: its only call
    site was token_revocation._count, which emits two entirely different
    labels. Every dashboard or alert written against the documented vocabulary
    therefore matched zero series, permanently, while the rejections an
    operator most wants to see (a spike of expired tokens after a deploy, a
    burst of insufficient_role probing) were never counted at all.

    Mirrors token_revocation._count: telemetry must never break auth.
    """
    try:
        from app.core.metrics import auth_failures_total

        auth_failures_total.labels(reason=reason).inc()
    except Exception:  # noqa: BLE001 -- telemetry is not load-bearing
        pass


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
            _count_auth_failure("invalid_token")
            raise credentials_exception
        if payload.get("type") != "access":
            _count_auth_failure("invalid_token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type — use an access token",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except ExpiredSignatureError:
        # Caught before the generic JWTError below (it is a subclass): an
        # expired token is the ordinary end of a session, and telling it
        # apart from a malformed or forged one is the whole reason the
        # counter is labelled. Folding both into invalid_token would hide a
        # credential attack inside everyday expiry traffic.
        _count_auth_failure("expired_token")
        raise credentials_exception
    except JWTError:
        _count_auth_failure("invalid_token")
        raise credentials_exception

    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        _count_auth_failure("invalid_token")
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
            _count_auth_failure("invalid_token")
            raise credentials_exception
        if await is_token_before_cutoff(uid, iat_int):
            _count_auth_failure("invalid_token")
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
        # A well-formed token for a user row that no longer exists.
        _count_auth_failure("inactive_user")
        raise credentials_exception

    return _bind_api_key_grant(
        _bind_credential_kind(_bind_api_key_project(user, None), CREDENTIAL_KIND_JWT), None
    )


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
        _count_auth_failure("invalid_token")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    if not api_key.is_active:
        _count_auth_failure("inactive_user")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="API key is inactive")
    if api_key.expires_at and api_key.expires_at < datetime.now(timezone.utc):
        _count_auth_failure("expired_token")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="API key has expired")

    # Load the owning user
    user_result = await db.execute(select(User).where(User.id == api_key.user_id))
    user = user_result.scalar_one_or_none()
    if not user or not user.is_active:
        _count_auth_failure("inactive_user")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="API key owner account is inactive")

    # Touch last_used_at on the injected session WITHOUT committing here —
    # the auth dependency does not own this transaction. The request's get_db
    # dependency commits on success, so the timestamp persists with the
    # handler's own work instead of prematurely ending its transaction.
    api_key.last_used_at = datetime.now(timezone.utc)

    bound = _bind_credential_kind(
        _bind_api_key_project(user, api_key.project_id), CREDENTIAL_KIND_API_KEY
    )
    _bind_api_key_grant(
        bound,
        ApiKeyGrant(
            key_id=api_key.id,
            scopes=_normalized_scopes(api_key.scopes),
            expires_at=api_key.expires_at,
        ),
    )
    return ApiKeyContext(user=bound, project_id=api_key.project_id)


async def _bearer_user_or_fall_through(
    db: AsyncSession, bearer_token: str
) -> Optional[User]:
    """Resolve a bearer token, or return ``None`` to try the API-key path.

    **Only a 401 falls through.** ``get_current_user`` raises exactly three
    kinds of ``HTTPException`` (see its body):

      * **401** — signature/exp invalid, missing or unparseable ``sub``,
        non-``access`` token type, jti on the revocation denylist, ``iat``
        before the user's cutoff, or the user row is gone. All of these mean
        "this bearer token is not a usable credential", so trying the
        ``X-API-Key`` header instead is the right move — that fall-through is
        what lets a CLI send both headers and still authenticate.
      * **503** — the revocation store is unreachable, so we *cannot tell*
        whether the token was revoked. The credentials may be perfectly
        good; it is the server that cannot check them.

    Swallowing that 503 and re-raising the generic 401 below was a real
    defect: every authenticated route resolves through this dependency
    (``bootstrap.register_routers`` injects it router-wide), so a Redis
    outage arrived at the SPA as 401-everywhere, which logs the user out and
    burns a refresh — precisely the re-login loop that fail-closed revocation
    was designed to avoid (``core/token_revocation.py``). It must propagate
    unchanged, ``Retry-After`` header and all.

    Anything that is not 401 propagates, not just 503: a future 429 or 5xx
    from this path would carry the same "not a credential problem" meaning,
    and a 403 (should one ever be raised here) means "authenticated but not
    allowed" — never "try another credential". Non-``HTTPException`` errors
    (e.g. a DB failure) were never caught here and still are not.
    """
    try:
        return await get_current_user(db=db, token=bearer_token)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_401_UNAUTHORIZED:
            raise
        return None


async def get_current_user_or_api_key(
    db: AsyncSession = Depends(get_db),
    bearer_token: Optional[str] = Depends(oauth2_scheme_optional),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> User:
    """Authenticate via JWT Bearer token OR X-API-Key header.

    Tries JWT first (if Authorization header present), falls back to API key
    when — and only when — the bearer token fails with a 401. Raises 401 if
    neither credential is provided or valid.
    Returns User only (backward-compatible). Use ``get_api_key_context`` when
    you need the bound project_id, or ``credential_kind(user)`` when you need
    to know which credential authenticated the request.
    """
    # Try JWT first
    if bearer_token:
        user = await _bearer_user_or_fall_through(db, bearer_token)
        if user is not None:
            return user

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
    request: Request,
    current_user: User = Depends(get_current_user_or_api_key),
) -> User:
    """Return the current user from JWT or API key, raising 403 if disabled.

    **A scoped API key writes only with ``project:write`` (re-audit N32).**
    Scopes were enforced only by ``require_role`` at QA_LEAD+ and by the
    project guards' writes, so a ``["stream:write"]`` CI key still wrote
    through every route gated at QA_ENGINEER or merely signed in (suites,
    saved views, triage, feedback, notification preferences, AI generation)
    with its owner's role. Every role and project guard resolves through this
    dependency, so the rule lives here: a key with a NON-EMPTY scope list on
    any method but GET/HEAD/OPTIONS needs ``project:write`` or
    ``project:admin``, unless the handler is marked
    :func:`takes_scoped_key_writes`. A legacy key (empty list) and a JWT pass
    as before. The API-key ingest routes (``/api/v1/stream/*``,
    ``/api/v1/ingest*``, ``/ws/events``) authenticate through their own
    dependencies and never reach this one.
    """
    if not current_user.is_active:
        _count_auth_failure("inactive_user")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account",
        )
    if _is_write(request) and not _route_takes_scoped_key_writes(request):
        _refuse_scoped_key_without_write_scope(current_user)
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


_STREAM_WRITE_SCOPE = STREAM_WRITE_SCOPE


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
        _count_auth_failure("invalid_token")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    if not api_key.is_active:
        _count_auth_failure("inactive_user")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="API key is inactive")
    if api_key.expires_at and api_key.expires_at < datetime.now(timezone.utc):
        _count_auth_failure("expired_token")
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

    # Record the credential kind only. Deliberately NOT _bind_api_key_project:
    # this path has never bound the project onto the user object (it returns
    # project_id on the context instead), and binding it here would silently
    # change how the project-scoped guards treat a streaming request.
    _bind_credential_kind(user, CREDENTIAL_KIND_API_KEY)

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

    Fall-through to the API key happens only for a 401 from the bearer path —
    see ``_bearer_user_or_fall_through`` for why 503 must not be laundered
    into 401 here.
    """
    if bearer_token:
        user = await _bearer_user_or_fall_through(db, bearer_token)
        if user is not None:
            return user, None

    if x_api_key:
        ctx = await _validate_api_key(db, x_api_key)
        return ctx.user, ctx.project_id

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required — provide Authorization Bearer token or X-API-Key header",
        headers={"WWW-Authenticate": "Bearer"},
    )


#: What a project-bound API key is told by an ADMIN route that has not opted in
#: -- ``require_role(UserRole.ADMIN)`` and ``require_instance_admin()``, which
#: are now the same check.
PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL = (
    "This API key is bound to one project; this endpoint spans every project "
    "and needs an instance administrator"
)


def require_role(min_role: UserRole, *, allow_project_key: bool = False) -> Callable:
    """
    Return a FastAPI dependency that enforces a minimum role level.

    Usage:
        @router.post("", dependencies=[Depends(require_role(UserRole.QA_LEAD))])
        # or
        current_user: User = Depends(require_role(UserRole.QA_ENGINEER))

    **ADMIN is closed to a project-bound API key unless the route opts in
    (re-audit N20).** The role compared here is the key OWNER's, and only an
    ADMIN can bind a key to a project, so nearly every project-bound key is an
    ADMIN credential: a CI pipeline's. With the role alone deciding, a key
    leaked from one team's pipeline could create an instance administrator
    (``POST /api/v1/users``) and, logged in as it, read and write every
    tenant. So ``require_role(UserRole.ADMIN)`` refuses, with 403, a request
    whose user carries an API-key project binding: the attribute
    ``_bind_api_key_project`` sets, which ``require_instance_admin`` already
    checked. A JWT and a user-scoped key carry no binding and pass as before.

    ``allow_project_key=True`` lets such a key back in. Pass it only where the
    route itself confines the key to its own project (a
    ``require_project_access()`` / ``require_run_access()`` /
    ``require_release_access()`` guard on the path id, ``resolve_project_scope``
    on the resource's project, or an explicit
    ``_enforce_api_key_project_binding``), and list the route in the reviewed
    allow-list of ``tests/regression/test_admin_routes_refuse_project_keys.py``,
    which fails until you do.

    **QA_LEAD is closed to a project-bound key the same way (re-audit N26).**
    A bound key's owner is almost always an ADMIN, so it passed every
    ``require_role(UserRole.QA_LEAD)`` route, and many of those span every
    project: the user directory, ``POST /api/v1/projects``, the training
    export, the instance settings reads, integration probes, AI-eval. A route
    at QA_LEAD that confines the key to its own project opts in with
    ``allow_project_key=True`` and is listed in the same allow-list; one that
    does not is refused without anyone having to remember a flag.

    Below QA_LEAD nothing changes. Those roles never refused a bound key, so
    the flag would mean nothing there, and passing it is a ``ValueError``.

    **QA_LEAD and above also need the key's scopes to allow it (QA-R3-11,
    QA-R4-1, QA-R4-2).** A key with a non-empty scope list gets 403 unless the
    list holds :data:`PROJECT_ADMIN_SCOPE` (``project:admin``), bound or not,
    opted in or not. The check used to run only on opted-in routes, so an
    unbound ``["stream:write"]`` key of an ADMIN created instance
    administrators through plain ``require_role(UserRole.ADMIN)``, and a bound
    one administered its project through ``require_role(UserRole.QA_LEAD)``.
    A legacy key with an empty list stays full-access, and a JWT has no scopes
    at all. Below QA_LEAD the scopes are not read here; the project-scoped
    guards refuse such a key's writes (see ``_refuse_scoped_key_write``).
    """
    min_idx = _ROLE_ORDER.index(min_role)
    at_least_qa_lead = min_idx >= _ROLE_ORDER.index(UserRole.QA_LEAD)
    if allow_project_key and not at_least_qa_lead:
        raise ValueError(
            "allow_project_key applies only to require_role(UserRole.QA_LEAD) and "
            f"UserRole.ADMIN; {min_role.value} never refuses a project-bound API key"
        )
    refuse_project_key = at_least_qa_lead and not allow_project_key
    refuse_scoped_key = at_least_qa_lead

    async def _check(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        try:
            user_idx = _ROLE_ORDER.index(_normalize_user_role(current_user.role))
        except ValueError:
            _count_auth_failure("insufficient_role")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        if user_idx < min_idx:
            _count_auth_failure("insufficient_role")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires at least {min_role.value} role",
            )
        if refuse_project_key and _api_key_bound_project(current_user) is not None:
            # Counted like any other role shortfall: a leaked CI key probing
            # the admin surface is exactly the burst an operator wants to see.
            _count_auth_failure("insufficient_role")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL,
            )
        if refuse_scoped_key:
            _refuse_scoped_key_without_project_admin(current_user)
        return current_user

    return _check


def require_instance_admin() -> Callable:
    """ADMIN, and not through an API key bound to one project.

    Added for the admin-maintenance router (re-audit M2, QA), whose data and
    effects span every tenant. Since re-audit N20 that is what
    ``require_role(UserRole.ADMIN)`` means by default, so this is a named alias
    for it: the spelling for a route that must never opt in with
    ``allow_project_key=True``. The maintenance router's AST test pins it.
    """
    return require_role(UserRole.ADMIN)


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
        # At QA_LEAD and above a write administers the project: project:admin.
        _refuse_scoped_key_write(
            request, current_user, admin=min_idx >= _ROLE_ORDER.index(UserRole.QA_LEAD)
        )
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

    A write (any method but GET/HEAD/OPTIONS) through a scoped API key also
    needs ``project:admin`` (QA-R4-1), checked before the ADMIN bypass. The
    streaming and upload routes do not use this guard (they authenticate with
    ``get_api_key_context`` / ``get_streaming_api_key_context``), so a
    ``stream:write`` key keeps ingesting; a route-walker test pins that.
    """
    from app.models.postgres import ProjectMember

    async def _check(
        request: Request,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        _refuse_scoped_key_write(request, current_user)
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
        _refuse_scoped_key_write(request, current_user)
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


async def resolve_authorized_test_case(
    db: AsyncSession,
    current_user: User,
    test_case_id: uuid.UUID,
) -> AuthorizedTestCaseContext:
    """Resolve a test through its run and enforce tenant/API-key membership."""
    from app.models.postgres import ProjectMember, TestCase, TestRun

    result = await db.execute(
        select(TestCase, TestRun)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(TestCase.id == test_case_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Test case not found",
        )
    test_case, test_run = row
    project_id = test_run.project_id
    _enforce_api_key_project_binding(
        current_user,
        project_id,
        detail="This API key is restricted to a different project",
    )
    if _normalize_user_role(current_user.role) != UserRole.ADMIN:
        membership = await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.user_id == current_user.id,
                ProjectMember.project_id == project_id,
            )
        )
        if membership.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this test case''s project",
            )
    return AuthorizedTestCaseContext(
        test_case=test_case,
        test_run=test_run,
        run_id=test_case.test_run_id,
        project_id=project_id,
    )


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
        _refuse_scoped_key_write(request, current_user)
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

        # A project-bound caller manages only keys bound to its own project
        # (re-audit N20). Without this, the ADMIN return below let a CI key act
        # on its owner's keys for every other project, and on unbound ones.
        # Asked only for a bound caller, so every other request issues exactly
        # the one query it always did.
        if _api_key_bound_project(current_user) is not None:
            key_project_id = (await db.execute(
                select(ApiKey.project_id).where(ApiKey.id == key_uuid)
            )).scalar_one_or_none()
            _enforce_api_key_project_binding(
                current_user,
                key_project_id,
                detail=(
                    "This API key is bound to one project; it can only manage "
                    "keys bound to that project"
                ),
            )

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

        # A project-bound API key reaches only chat filed under its own project,
        # and never another project's through the ADMIN bypass below (re-audit
        # N20). A session filed under no project is refused too: it can hold
        # anything its owner asked about. No-op for an unbound caller.
        _enforce_api_key_project_binding(current_user, session.project_id)
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

        # A project-bound API key reaches only its own project's share links,
        # and never another project's through the ADMIN bypass below (re-audit
        # N20). No-op for an unbound caller.
        _enforce_api_key_project_binding(current_user, link.project_id)
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
    # compare_digest, not ==: a plain comparison short-circuits on the first
    # differing byte, which leaks the secret's prefix to a caller who can time
    # the response (re-audit H1).
    if not hmac.compare_digest(str(x_webhook_secret), str(settings.WEBHOOK_SECRET)):
        logger.warning("Webhook request with invalid secret rejected")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook secret",
        )


async def resolve_release_query_scope(
    db: AsyncSession,
    release_id: Optional[str],
    current_user: "User",
) -> Optional[str]:
    """Validate an optional ``release_id`` QUERY parameter (S4a).

    Returns the id unchanged when the caller may read it, ``None`` when no
    release was requested, and raises otherwise.

    **Why this is not ``require_release_access``.** That guard reads a PATH
    parameter and is wired as a ``Depends``. The release axis arrives as an
    optional query parameter on endpoints that already carry their own scoped
    path or query id, and the architectural authorization ratchet checks
    evidence per-ROUTE rather than per-ID — it stops at the first scoped
    parameter it can satisfy. So a route taking both ``project_id`` and
    ``release_id`` passes the ratchet with the release entirely unchecked, and
    nothing anywhere would report it.

    Called explicitly rather than as a dependency because the check must not
    run when the parameter is absent: that path has to stay byte-identical to
    pre-S4a behaviour (NFR1), including issuing no extra query.

    404 for a release that does not exist, 403 for one the caller cannot
    reach — matching ``require_release_access`` rather than inventing a second
    convention. A uniform 404 would hide existence better, but having two
    guards disagree about the same resource is the worse failure: it teaches
    readers that the codes are arbitrary.
    """
    if release_id is None:
        return None

    from app.core.release_filter import UNATTRIBUTED, is_unattributed

    if is_unattributed(release_id):
        # Names no release, so there is nothing to look up and nothing to
        # authorize: the answer is "runs this project has that no release
        # claims", and the project scoping the caller already passed still
        # applies. Normalised so every downstream comparison sees one spelling.
        return UNATTRIBUTED

    from app.models.postgres import Release

    try:
        release_uuid = uuid.UUID(str(release_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid release_id") from exc

    release = (
        await db.execute(select(Release).where(Release.id == release_uuid))
    ).scalar_one_or_none()
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")

    accessible = await get_accessible_project_ids(db, current_user)
    # ``None`` means admin — no membership restriction.
    if accessible is not None and release.project_id not in accessible:
        raise HTTPException(
            status_code=403, detail="You do not have access to this release"
        )
    return str(release_uuid)
