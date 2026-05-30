"""
Access-token revocation — Redis-backed denylist for JWT access tokens.

Why this exists
---------------
Before this module, ``POST /api/v1/auth/logout`` was a no-op (client-side
only), and ``/auth/change-password`` / ``/auth/first-time-reset`` left
existing access tokens valid for up to ``JWT_ACCESS_TOKEN_EXPIRE_MINUTES``
after the password change. A stolen token kept working after a legitimate
user tried to invalidate it — a meaningful security gap.

Two revocation scopes
---------------------
* **Per-jti denylist** — explicit logout revokes exactly one token. Used by
  ``/auth/logout``. Key: ``auth:revoked_jti:{jti}``. TTL is set to the
  remaining lifetime of the token so Redis auto-expires it.

* **Per-user cutoff** — password change / compromise response revokes
  **every** access token issued before the cutoff timestamp, including ones
  we've never seen. Key: ``auth:tokens_valid_from:{user_id}``, value: unix
  seconds. ``get_current_user`` rejects any access token whose ``iat`` is
  earlier than that cutoff. TTL matches ``JWT_ACCESS_TOKEN_EXPIRE_MINUTES``
  because tokens older than that max lifetime are already expired anyway.

Fail-open semantics
-------------------
If Redis is unavailable, the revocation check is skipped rather than
denying every request — the service should not become un-authable on a
cache outage. The trade-off: a brief window where a revoked token might
work during a Redis failure. Refresh tokens still get revoked in Postgres
via the existing family-revocation mechanism, so the compromise window is
bounded by ``JWT_ACCESS_TOKEN_EXPIRE_MINUTES`` regardless.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


_JTI_KEY = "auth:revoked_jti:{jti}"
_USER_CUTOFF_KEY = "auth:tokens_valid_from:{user_id}"


async def _redis():
    """Return the Redis client or ``None`` if Redis is unavailable."""
    try:
        from app.db.redis_client import get_redis
        return get_redis()
    except Exception as exc:  # noqa: BLE001 — best-effort cache
        logger.debug("Redis unavailable for token revocation: %s", exc)
        return None


async def revoke_jti(jti: str, ttl_seconds: int) -> None:
    """
    Mark a single access-token jti as revoked until it would have expired.

    ``ttl_seconds`` must be the remaining lifetime of the token (``exp - now``)
    so Redis auto-clears the entry exactly when the token would have expired
    naturally. Non-positive TTLs are clamped to 1 second so the key is still
    written (belt-and-braces — technically unreachable because an expired
    token would already fail signature/exp validation).
    """
    if not jti:
        return
    redis = await _redis()
    if redis is None:
        return
    ttl = max(1, int(ttl_seconds))
    try:
        await redis.set(_JTI_KEY.format(jti=jti), "1", ex=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to revoke jti %s: %s", jti, exc)


async def is_jti_revoked(jti: str) -> bool:
    """Return True iff the jti is currently on the denylist."""
    if not jti:
        return False
    redis = await _redis()
    if redis is None:
        return False
    try:
        val = await redis.get(_JTI_KEY.format(jti=jti))
        return val is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to check jti revocation for %s: %s", jti, exc)
        return False


async def revoke_all_user_tokens(user_id: uuid.UUID) -> None:
    """
    Revoke every access token previously issued to ``user_id`` by writing a
    cutoff marker. Any token whose ``iat`` is earlier than this marker will
    be rejected by ``is_token_before_cutoff``.

    The TTL matches the access-token max lifetime so the marker self-prunes
    once it can no longer possibly invalidate a still-live token.
    """
    redis = await _redis()
    if redis is None:
        return
    now = int(datetime.now(timezone.utc).timestamp())
    ttl = max(60, settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60)
    try:
        await redis.set(
            _USER_CUTOFF_KEY.format(user_id=str(user_id)),
            str(now),
            ex=ttl,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to revoke all tokens for user %s: %s", user_id, exc)


async def is_token_before_cutoff(user_id: uuid.UUID, token_iat: Optional[int]) -> bool:
    """
    Return True iff the user has a revocation cutoff and ``token_iat`` is
    earlier than it (i.e., the token was issued before the cutoff and must
    be rejected).

    ``token_iat`` is the unix timestamp from the JWT's ``iat`` claim. When
    ``None`` (legacy tokens issued before we added ``iat``) we conservatively
    treat them as invalid if any cutoff exists for the user — legacy tokens
    will naturally phase out within one access-token lifetime.
    """
    redis = await _redis()
    if redis is None:
        return False
    try:
        cutoff_str = await redis.get(_USER_CUTOFF_KEY.format(user_id=str(user_id)))
        if cutoff_str is None:
            return False
        if isinstance(cutoff_str, bytes):
            cutoff_str = cutoff_str.decode()
        cutoff = int(cutoff_str)
        if token_iat is None:
            return True  # legacy token, any cutoff invalidates it
        return token_iat < cutoff
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to check revocation cutoff for user %s: %s", user_id, exc)
        return False
