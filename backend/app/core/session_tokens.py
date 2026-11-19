"""Access-token checks for routes that cannot use the FastAPI auth dependencies.

The live stream endpoints (``GET /api/v1/stream/sse/{project_id}`` and the
``/ws/live/{project_id}`` WebSocket) receive their JWT outside the
``Authorization`` header, so they cannot depend on ``get_current_user``. Each
used to re-implement part of it by hand and both left out the revocation
checks; the SSE route did not even look at the token type.

``authenticate_access_token`` is the single place those routes get a user from
a raw token. It applies the same checks, in the same order, that
``app.core.deps.get_current_user`` and ``get_current_active_user`` apply to a
JWT:

1. signature, expiry and ``type == "access"`` (a refresh or MFA token is refused);
2. a usable ``sub`` claim;
3. the jti denylist (logout) and the per-user cutoff (password change, admin
   reset). When the revocation store cannot answer, the token is refused as
   "cannot verify right now" (fail closed), exactly like the HTTP path's 503;
4. the user exists and is active;
5. the account is not on a temporary password.

If ``get_current_user`` gains a check, add it here too. The parity test in
``tests/regression/test_live_stream_token_checks.py`` pins the calls those two
dependencies make so that a new one cannot be added without this module being
looked at.

Project membership is NOT checked here: the callers keep their own check.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import token_revocation
from app.core.security import ExpiredSignatureError, JWTError, decode_token
from app.models.postgres import User

# Reasons a token is refused. Stable strings: callers and tests match on them.
REASON_INVALID_TOKEN = "invalid_token"
REASON_EXPIRED_TOKEN = "expired_token"
REASON_WRONG_TOKEN_TYPE = "wrong_token_type"
REASON_REVOKED_TOKEN = "revoked_token"
REASON_REVOCATION_UNAVAILABLE = "revocation_unavailable"
REASON_USER_NOT_FOUND = "user_not_found"
REASON_INACTIVE_USER = "inactive_user"
REASON_CHANGE_REQUIRED = "password_change_required"


class SessionTokenRefused(Exception):
    """The token cannot open or keep a session.

    ``reason`` is one of the ``REASON_*`` constants. ``status_code`` is the
    HTTP status the dependency path would have answered (401, 403 or 503) and
    ``detail`` is a message safe to show the caller.
    """

    def __init__(self, reason: str, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.status_code = status_code
        self.detail = detail


def _count(reason: str) -> None:
    """Feed the same ``auth_failures_total`` counter the HTTP path feeds."""
    from app.core.deps import _count_auth_failure

    _count_auth_failure(reason)


def _refused(reason: str = REASON_INVALID_TOKEN, *, metric: str = "invalid_token") -> SessionTokenRefused:
    _count(metric)
    return SessionTokenRefused(reason, 401, "Could not validate credentials")


def _wrong_type_or_invalid(token: str) -> str:
    """Name the refusal for a token ``decode_token(expected_type="access")`` rejected.

    Used only to choose the message: the token has already been refused.
    """
    try:
        payload = decode_token(token)
    except Exception:  # noqa: BLE001 - any failure means "not a valid token"
        return REASON_INVALID_TOKEN
    if payload.get("type") != "access":
        return REASON_WRONG_TOKEN_TYPE
    return REASON_INVALID_TOKEN


async def authenticate_access_token(token: str, db: AsyncSession) -> tuple[User, float]:
    """Return ``(active user, token expiry)`` for a raw access token, or raise.

    The expiry is the ``exp`` claim as a unix timestamp: a stream opened with
    this token must end when it passes.

    Raises :class:`SessionTokenRefused` for every case ``get_current_user`` /
    ``get_current_active_user`` would refuse a JWT. See the module docstring.
    """
    try:
        # expected_type makes a refresh, mfa_challenge or mfa_enroll token a
        # JWTError here, before anything else is read from it.
        payload: dict[Any, Any] = decode_token(token, expected_type="access")
    except ExpiredSignatureError:
        raise _refused(REASON_EXPIRED_TOKEN, metric="expired_token") from None
    except JWTError:
        raise _refused(_wrong_type_or_invalid(token)) from None
    except Exception:  # noqa: BLE001 - e.g. a non-string token; never a 500
        raise _refused() from None

    user_id = payload.get("sub")
    if not user_id:
        raise _refused()
    try:
        uid = uuid.UUID(str(user_id))
    except ValueError:
        raise _refused() from None

    # Revocation. Fail closed when the store is unreachable: "we cannot check
    # whether this token was revoked" is not "it was not revoked".
    jti = payload.get("jti")
    iat = payload.get("iat")
    iat_value: Optional[float] = float(iat) if isinstance(iat, (int, float)) else None
    try:
        if jti and await token_revocation.is_jti_revoked(str(jti)):
            raise _refused(REASON_REVOKED_TOKEN)
        if await token_revocation.is_token_before_cutoff(uid, iat_value):
            raise _refused(REASON_REVOKED_TOKEN)
    except token_revocation.RevocationUnavailable as exc:
        raise SessionTokenRefused(
            REASON_REVOCATION_UNAVAILABLE,
            503,
            token_revocation.unavailable_detail(exc),
        ) from exc

    found = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    if found is None:
        raise _refused(REASON_USER_NOT_FOUND, metric="inactive_user")
    if not found.is_active:
        _count("inactive_user")
        raise SessionTokenRefused(REASON_INACTIVE_USER, 403, "Inactive user account")
    if getattr(found, "must_change_password", False):
        # Same rule as deps.refuse_until_password_changed for a JWT. A live
        # stream is never one of the password-change paths.
        raise SessionTokenRefused(
            REASON_CHANGE_REQUIRED,
            403,
            "This account is on a temporary password. Change it, sign in again, "
            "then reopen the live stream.",
        )

    exp_raw = payload.get("exp")
    expires_at = float(exp_raw) if isinstance(exp_raw, (int, float)) else 0.0
    return found, expires_at
