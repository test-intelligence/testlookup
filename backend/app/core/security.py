"""JWT authentication and security utilities."""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, cast

import bcrypt
from jose import jwt

from app.core.config import settings


# bcrypt hashes at most 72 bytes. Versions before 5.0 truncated silently;
# 5.0 raises ValueError instead ("password cannot be longer than 72 bytes").
# Left unhandled that is a 500 on *unauthenticated* /auth/login for any
# password over 72 bytes — and on register / change-password / first-time
# reset. We truncate explicitly, which:
#   * closes the 500, and
#   * preserves the pre-5.0 semantics, so a credential created when bcrypt
#     truncated silently still verifies. Rejecting instead would lock those
#     users out of their own accounts.
# Bytes, not characters: a multi-byte character may be cut mid-sequence, which
# is fine — the value only has to be deterministic, never decoded back.
_BCRYPT_MAX_BYTES = 72


def _bcrypt_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(_bcrypt_bytes(plain_password), hashed_password.encode("utf-8"))


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(_bcrypt_bytes(password), bcrypt.gensalt()).decode("utf-8")


def hash_token(raw: str) -> str:
    """SHA-256 hex digest — used for share-link tokens and refresh-token jtis."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_access_token(subject: Any, expires_delta: Optional[timedelta] = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (
        expires_delta or timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    # ``iat`` enables bulk revocation (password change, account compromise):
    # any token whose iat precedes the user's ``tokens_valid_from`` marker
    # is rejected without having to enumerate individual jtis. See
    # app/core/token_revocation.py.
    payload = {
        "sub": str(subject),
        "iat": now,
        "exp": expire,
        "type": "access",
        "jti": uuid.uuid4().hex,
    }
    return cast(str, jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM))


def create_refresh_token(subject: Any, jti: Optional[str] = None) -> tuple[str, str, datetime]:
    """Return (encoded_token, jti, expires_at).

    Callers must persist a `RefreshTokenRecord` using ``hash_token(jti)`` so
    rotation and replay detection can be enforced at refresh time.
    """
    expire = datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    if jti is None:
        jti = secrets.token_urlsafe(24)
    payload = {"sub": str(subject), "exp": expire, "type": "refresh", "jti": jti}
    token = cast(str, jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM))
    return token, jti, expire


def decode_token(token: str, expected_type: Optional[str] = None) -> dict[Any, Any]:
    """Decode and validate a JWT token. Raises JWTError on failure.

    When ``expected_type`` is given (e.g. ``"access"`` / ``"refresh"``), the
    token's ``type`` claim must match or a ``JWTError`` is raised — pushing the
    access-vs-refresh confused-deputy check into the decode layer as
    defense-in-depth (callers may still re-check for clearer error messages).
    """
    from jose import JWTError

    payload = cast(
        dict[Any, Any],
        jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]),
    )
    if expected_type is not None and payload.get("type") != expected_type:
        raise JWTError(f"Unexpected token type: expected {expected_type!r}")
    return payload
