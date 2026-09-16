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
  we've never seen. Key: ``auth:tokens_valid_from:{user_id}``, value: a unix
  timestamp with subsecond precision. ``get_current_user`` rejects any access
  token whose ``iat`` is not later than that cutoff. TTL matches
  ``JWT_ACCESS_TOKEN_EXPIRE_MINUTES``
  because tokens older than that max lifetime are already expired anyway.

Fail-CLOSED semantics (changed 2026-08-03)
------------------------------------------
This module used to skip the revocation check when Redis was unavailable.
That made revocation advisory: during a Redis outage a token the user had
explicitly logged out — or that a password change was meant to kill — kept
working, for up to ``JWT_ACCESS_TOKEN_EXPIRE_MINUTES`` (default **720**, i.e.
twelve hours). A security control that silently disables itself under a
condition an attacker can sometimes cause is not a control.

The read path now raises ``RevocationUnavailable`` when the denylist cannot
be consulted, and ``get_current_user`` turns that into **503 Service
Unavailable** — not 401. The honest signal is "revocation cannot be verified
right now", which is a server-side problem an operator can diagnose and fix;
a 401 would send the SPA into a re-login loop that cannot succeed and would
blame the user's credentials for a Redis outage.

Why fail-closed rather than a last-known-good snapshot: a snapshot would
have to enumerate ``auth:revoked_jti:*`` (one key per logout across the whole
fleet, unbounded in principle) on a timer, in every worker process, against
the same Redis that also carries the Celery broker and live event streams —
and it would still have to fail closed past a staleness ceiling. It buys
availability only for the pre-outage window while adding per-process
divergence, and it cannot help at all with revocations issued *during* the
outage (those writes fail too — see the write-path caveat below).

Availability trade-off, stated plainly
--------------------------------------
**A Redis outage is now an authentication outage.** ``/auth/login`` and
``/auth/refresh`` are Postgres-only and keep working — they will happily mint
tokens — but every request that carries one gets 503 until Redis returns.
Recovery is to restore Redis. An operator who consciously accepts revocation
being unenforced (e.g. a single-user air-gapped box with Redis wedged) can
set ``AUTH_REVOCATION_FAIL_OPEN=true`` in the environment and restart: that
restores the old behaviour, is env-only (no in-app toggle), and logs an error
on every use so it can never become a quiet default.

Write-path caveat (unchanged, and deliberately so)
--------------------------------------------------
``revoke_jti`` / ``revoke_all_user_tokens`` remain best-effort: with Redis
down they cannot record anything, so a logout issued *during* an outage is
not persisted and the token becomes usable again once Redis recovers. They
now log at ERROR with a metric instead of WARNING, but they do not raise —
raising would 500 ``/auth/logout`` and ``/auth/change-password``, which do
real Postgres work (refresh-family revocation) that must still commit. The
residual gap is documented in ``architecture/SECURITY.md`` §7.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog

from app.core.config import settings

logger = structlog.get_logger("core.token_revocation")


_JTI_KEY = "auth:revoked_jti:{jti}"
_USER_CUTOFF_KEY = "auth:tokens_valid_from:{user_id}"
_UPSERT_USER_CUTOFF = (
    "INSERT INTO auth_token_revocations (jti, user_id, valid_from) "
    "VALUES (:jti, :uid, clock_timestamp()) "
    "ON CONFLICT (jti) DO UPDATE SET valid_from=clock_timestamp() "
    "RETURNING valid_from"
)


async def _durable_execute(statement: str, params: dict) -> list:
    """Use Postgres as revocation authority; Redis remains only a cache."""
    from sqlalchemy import text
    from app.db.postgres import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        result = await db.execute(text(statement), params)
        rows = result.fetchall() if result.returns_rows else []
        await db.commit()
        return rows


def _durable_required() -> bool:
    return settings.APP_ENV in ("staging", "production")


class RevocationUnavailable(RuntimeError):
    """The revocation store could not be consulted for this request.

    Raised by the read path only. Callers must translate this into a 503 —
    it means "unknown", never "not revoked".
    """


def _count(reason: str) -> None:
    """Best-effort Prometheus counter — metrics must never break auth."""
    try:
        from app.core.metrics import auth_failures_total
        auth_failures_total.labels(reason=reason).inc()
    except Exception:  # noqa: BLE001 — telemetry is not load-bearing
        pass


def _unavailable(operation: str, exc: Optional[Exception] = None) -> bool:
    """Handle an unreachable revocation store on the READ path.

    Returns ``False`` (fail open) only under the explicit environment
    escape hatch; otherwise raises so the caller can answer 503.
    """
    _count("revocation_unavailable")
    if settings.AUTH_REVOCATION_FAIL_OPEN:
        logger.error(
            "token_revocation_failed_open",
            operation=operation,
            error=str(exc) if exc else "revocation store unreachable",
            detail=(
                "AUTH_REVOCATION_FAIL_OPEN=true — a revoked token may be honoured "
                "until Redis recovers. Unset this to fail closed."
            ),
        )
        return False
    logger.error(
        "token_revocation_unavailable",
        operation=operation,
        error=str(exc) if exc else "revocation store unreachable",
        detail=(
            "Revocation status cannot be verified; rejecting with 503. Restore Redis, "
            "or set AUTH_REVOCATION_FAIL_OPEN=true to accept unenforced revocation."
        ),
    )
    raise RevocationUnavailable(f"revocation store unavailable during {operation}")


async def _redis():
    """Return the Redis client or ``None`` if Redis is unavailable."""
    try:
        from app.db.redis_client import get_redis
        return get_redis()
    except Exception as exc:  # noqa: BLE001 — handled by the caller
        logger.debug("token_revocation_redis_unavailable", error=str(exc))
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
    try:
        await _durable_execute("INSERT INTO auth_token_revocations (jti, expires_at) VALUES (:jti, now() + (:ttl * interval '1 second')) ON CONFLICT (jti) DO NOTHING", {"jti": jti, "ttl": max(1, int(ttl_seconds))})
    except Exception as exc:
        logger.error("durable_revocation_unavailable", operation="revoke_jti", error=str(exc))
        if _durable_required():
            raise RevocationUnavailable("durable revocation store unavailable") from exc
    redis = await _redis()
    if redis is None:
        # Loud, not silent: the caller's logout LOOKS successful but this
        # token was never denylisted. See the write-path caveat in the
        # module docstring.
        _count("revocation_write_failed")
        logger.error(
            "token_revocation_write_failed",
            operation="revoke_jti",
            error="revocation store unreachable",
            detail="Logout did not denylist this token; it stays valid until it expires.",
        )
        return
    ttl = max(1, int(ttl_seconds))
    try:
        await redis.set(_JTI_KEY.format(jti=jti), "1", ex=ttl)
    except Exception as exc:  # noqa: BLE001
        _count("revocation_write_failed")
        logger.error("token_revocation_write_failed", operation="revoke_jti", error=str(exc))


async def is_jti_revoked(jti: str) -> bool:
    """Return True iff the jti is currently on the denylist.

    Raises ``RevocationUnavailable`` when the store cannot be consulted —
    "unknown" is not "not revoked".
    """
    if not jti:
        return False
    try:
        found = bool(await _durable_execute("SELECT 1 FROM auth_token_revocations WHERE jti=:jti AND (expires_at IS NULL OR expires_at > now())", {"jti": jti}))
        if found:
            return True
        redis = await _redis()
        if redis is None:
            return _unavailable("is_jti_revoked") if _durable_required() else False
        legacy = (await redis.get(_JTI_KEY.format(jti=jti))) is not None
        if legacy:
            # Bounded migration: import a legacy marker while its natural TTL remains.
            await _durable_execute("INSERT INTO auth_token_revocations (jti, expires_at) VALUES (:jti, now() + (:ttl * interval '1 second')) ON CONFLICT (jti) DO NOTHING", {"jti": jti, "ttl": settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60})
        return legacy
    except Exception as exc:
        logger.error("durable_revocation_unavailable", operation="is_jti_revoked", error=str(exc))
        if _durable_required():
            return _unavailable("is_jti_revoked", exc)
    redis = await _redis()
    if redis is None:
        return _unavailable("is_jti_revoked")
    try:
        val = await redis.get(_JTI_KEY.format(jti=jti))
        return val is not None
    except Exception as exc:  # noqa: BLE001
        return _unavailable("is_jti_revoked", exc)


async def revoke_all_user_tokens(user_id: uuid.UUID, db=None) -> None:
    """
    Revoke every access token previously issued to ``user_id`` by writing a
    cutoff marker. Any token whose ``iat`` is earlier than this marker will
    be rejected by ``is_token_before_cutoff``.

    The TTL matches the access-token max lifetime so the marker self-prunes
    once it can no longer possibly invalidate a still-live token.
    """
    cutoff = datetime.now(timezone.utc).timestamp()
    try:
        params = {"jti": f"cutoff:{user_id}", "uid": user_id}
        if db is not None:
            from sqlalchemy import text
            result = await db.execute(text(_UPSERT_USER_CUTOFF), params)
            result.scalar_one()
        else:
            rows = await _durable_execute(_UPSERT_USER_CUTOFF, params)
            cutoff = rows[0][0].timestamp()
    except Exception as exc:
        logger.error("durable_revocation_unavailable", operation="revoke_all_user_tokens", error=str(exc))
        if _durable_required():
            raise RevocationUnavailable("durable revocation store unavailable") from exc
    # A caller-owned transaction can still roll back. Publishing its cutoff to
    # Redis here would let the legacy-cache migration path resurrect a cutoff
    # whose password change never committed. PostgreSQL is queried first on
    # every read, so caller-transaction writes need no pre-commit cache copy.
    if db is not None:
        return
    redis = await _redis()
    if redis is None:
        _count("revocation_write_failed")
        logger.error(
            "token_revocation_write_failed",
            operation="revoke_all_user_tokens",
            user_id=str(user_id),
            error="revocation store unreachable",
            detail="Password change did not write a cutoff; existing access tokens stay valid.",
        )
        return
    ttl = max(60, settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60)
    try:
        await redis.set(
            _USER_CUTOFF_KEY.format(user_id=str(user_id)),
            str(cutoff),
            ex=ttl,
        )
    except Exception as exc:  # noqa: BLE001
        _count("revocation_write_failed")
        logger.error(
            "token_revocation_write_failed",
            operation="revoke_all_user_tokens",
            user_id=str(user_id),
            error=str(exc),
        )


async def is_token_before_cutoff(user_id: uuid.UUID, token_iat: Optional[float]) -> bool:
    """
    Return True iff the user has a revocation cutoff and ``token_iat`` is not
    later than it (i.e., the token cannot be proved to postdate the cutoff).

    ``token_iat`` is the unix timestamp from the JWT's ``iat`` claim. When
    ``None`` (legacy tokens issued before we added ``iat``) we conservatively
    treat them as invalid if any cutoff exists for the user — legacy tokens
    will naturally phase out within one access-token lifetime.

    Raises ``RevocationUnavailable`` when the store cannot be consulted.
    """
    try:
        rows = await _durable_execute("SELECT valid_from FROM auth_token_revocations WHERE jti=:jti AND user_id=:uid", {"jti": f"cutoff:{user_id}", "uid": user_id})
        cutoff_value = rows[0][0] if rows else None
        if cutoff_value is not None:
            return token_iat is None or token_iat <= cutoff_value.timestamp()
        # During the bounded migration window, honor legacy Redis markers too.
        redis = await _redis()
        if redis is None:
            return False
        legacy = await redis.get(_USER_CUTOFF_KEY.format(user_id=str(user_id)))
        if legacy is None:
            return False
        if isinstance(legacy, bytes):
            legacy = legacy.decode()
        precise_cutoff = float(legacy)
        await _durable_execute("INSERT INTO auth_token_revocations (jti, user_id, valid_from) VALUES (:jti, :uid, to_timestamp(:cutoff)) ON CONFLICT (jti) DO NOTHING", {"jti": f"cutoff:{user_id}", "uid": user_id, "cutoff": precise_cutoff})
        return token_iat is None or token_iat <= precise_cutoff
    except Exception as exc:
        logger.error("durable_revocation_unavailable", operation="is_token_before_cutoff", error=str(exc))
        if _durable_required():
            return _unavailable("is_token_before_cutoff", exc)
    # Redis fallback is used only while an older installation is migrating.
    redis = await _redis()
    if redis is None:
        return _unavailable("is_token_before_cutoff")
    try:
        cutoff_str = await redis.get(_USER_CUTOFF_KEY.format(user_id=str(user_id)))
        if cutoff_str is None:
            return False
        if isinstance(cutoff_str, bytes):
            cutoff_str = cutoff_str.decode()
        # A cutoff we cannot parse is also "cannot verify" — the store is in a
        # state we can't reason about, so it takes the 503 path rather than
        # being rounded down to "not revoked".
        cutoff = float(cutoff_str)
        if token_iat is None:
            return True  # legacy token, any cutoff invalidates it
        return token_iat <= cutoff
    except Exception as exc:  # noqa: BLE001
        return _unavailable("is_token_before_cutoff", exc)
