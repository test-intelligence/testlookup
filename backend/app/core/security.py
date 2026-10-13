"""JWT authentication and security utilities."""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, cast

import bcrypt
import jwt

from app.core.config import settings

# PyJWT, not python-jose (GHSA-3qf3-8w2g-rqmx: jose <= 3.5.0 has an unfixed
# algorithm-confusion bypass and the project is unmaintained). Callers catch
# these two names from HERE rather than from the JWT library, so a library
# swap stays a one-file change:
#   * ``JWTError`` is PyJWT's base class, so it still covers every decode
#     failure python-jose's ``JWTError`` covered (bad signature, malformed
#     token, bad claim, and a key PyJWT refuses to use as an HMAC secret).
#   * ``ExpiredSignatureError`` is a subclass of it, as it was in jose, so a
#     handler that catches it before ``JWTError`` keeps working.
# scripts/quality_gate.py (deps.no-python-jose) keeps jose from coming back.
JWTError = jwt.PyJWTError
ExpiredSignatureError = jwt.ExpiredSignatureError


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


def create_access_token(
    subject: Any,
    expires_delta: Optional[timedelta] = None,
    *,
    issued_at: Optional[datetime] = None,
) -> str:
    now = issued_at or datetime.now(timezone.utc)
    expire = now + (
        expires_delta or timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    # Preserve subsecond precision. A whole-second ``iat`` cannot distinguish
    # a replacement token from a revocation cutoff created earlier in that
    # same second, which makes an immediate post-reset sign-in unusable.
    payload = {
        "sub": str(subject),
        "iat": now.timestamp(),
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


# ── MFA interstitial tokens ──────────────────────────────────────────────────
#
# Between "password verified" and "second factor verified" the caller needs a
# credential, but it MUST NOT be an access token. ``get_current_user`` trusts
# any token whose ``type`` claim is ``"access"`` — and the router-wide
# dependency in ``bootstrap.register_routers`` puts that check on essentially
# every route — so an access token carrying an ``mfa_pending`` marker would be
# a complete bypass of the second factor for anyone who simply ignored the
# marker. There is no marker we could add that the 300-odd existing handlers
# would honour.
#
# Instead these tokens carry their own ``type``. ``decode_token`` rejects a
# type mismatch *at the decode layer*, so presenting one of these as a bearer
# token fails inside ``get_current_user`` before any user is loaded — the same
# defence that already separates access from refresh tokens.
MFA_CHALLENGE_TOKEN_TYPE = "mfa_challenge"      # password OK, awaiting TOTP
MFA_ENROLLMENT_TOKEN_TYPE = "mfa_enroll"        # password OK, policy requires enrollment


def create_mfa_token(
    subject: Any,
    token_type: str,
    *,
    issued_at: Optional[datetime] = None,
) -> tuple[str, str, int]:
    """Mint a short-lived MFA interstitial token.

    Returns ``(encoded_token, jti, expires_in_seconds)``. ``token_type`` must be
    :data:`MFA_CHALLENGE_TOKEN_TYPE` or :data:`MFA_ENROLLMENT_TOKEN_TYPE`; any
    other value is a programming error and raises, because the whole security
    property here rests on the type claim never being ``"access"``.
    """
    if token_type not in (MFA_CHALLENGE_TOKEN_TYPE, MFA_ENROLLMENT_TOKEN_TYPE):
        raise ValueError(f"Not an MFA interstitial token type: {token_type!r}")
    now = issued_at or datetime.now(timezone.utc)
    ttl = max(30, int(settings.MFA_CHALLENGE_TTL_SECONDS))
    expire = now + timedelta(seconds=ttl)
    jti = uuid.uuid4().hex
    payload = {
        "sub": str(subject),
        "iat": now.timestamp(),
        "exp": expire,
        "type": token_type,
        "jti": jti,
    }
    token = cast(
        str, jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    )
    return token, jti, ttl


def decode_token(token: str, expected_type: Optional[str] = None) -> dict[Any, Any]:
    """Decode and validate a JWT token. Raises JWTError on failure.

    When ``expected_type`` is given (e.g. ``"access"`` / ``"refresh"``), the
    token's ``type`` claim must match or a ``JWTError`` is raised — pushing the
    access-vs-refresh confused-deputy check into the decode layer as
    defense-in-depth (callers may still re-check for clearer error messages).

    ``algorithms`` is always the single configured algorithm: the token's own
    ``alg`` header never chooses how it is verified.

    ``iat`` is minted from PostgreSQL's clock (``auth_session_tokens``), not
    this process's. PyJWT rejects an ``iat`` later than "now" by default, so
    with zero leeway a database clock a fraction of a second ahead of the API
    pod would 401 a freshly issued token. python-jose never checked that, and
    ``exp`` (which IS checked) is what bounds a token's life, so this keeps
    jose's rule instead: ``iat``, when present, must be a number. The options
    dict is built per call, never shared (PyJWT has mutated a caller's options
    in place before: GHSA-gvp8-978c-rx2q).
    """
    payload = cast(
        dict[Any, Any],
        jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_iat": False},
        ),
    )
    if "iat" in payload:
        iat = payload["iat"]
        if isinstance(iat, bool) or not isinstance(iat, (int, float)):
            raise jwt.InvalidIssuedAtError("Issued At claim (iat) must be a number")
    if expected_type is not None and payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"Unexpected token type: expected {expected_type!r}")
    return payload
