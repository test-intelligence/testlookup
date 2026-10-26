"""Password-reset tokens: issue, self-service request, redeem (W17b ADM-02).

Anti-enumeration is the design constraint. ``POST /auth/password/forgot``
answers the same 202 for every address and does the account lookup, the
per-mailbox limit and the send in :func:`process_forgot_request`, scheduled as
a background task *after* the response — so neither the body nor the latency
says whether an account exists.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import PasswordResetToken, User, UserRole
from app.services.account_links_service import (
    FORGOT_PER_EMAIL_PER_HOUR,
    PASSWORD_RESET_LIFETIME,
    RESET_PATH,
    send_link_email,
    smtp_enabled,
)
from app.services.notification.email_templates import render_password_reset_email
from app.services.one_time_tokens import fragment_link, hash_ip, new_token

logger = logging.getLogger(__name__)

FORGOT_ACCEPTED_DETAIL = "If an account exists for that address, a reset link is on its way."


async def create_password_reset_token(
    db: AsyncSession,
    user: User,
    *,
    issued_by: Optional[User] = None,
    request_ip: Optional[str] = None,
) -> tuple[str, datetime]:
    """Stage a new token for ``user`` and kill every older unused one. Caller commits.

    Returns ``(raw token, expires_at)``; only the hash is written.
    """
    now = datetime.now(timezone.utc)
    await db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    raw, token_hash = new_token()
    expires_at = now + PASSWORD_RESET_LIFETIME
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
            issued_by_user_id=issued_by.id if issued_by else None,
            request_ip_hash=hash_ip(request_ip),
        )
    )
    await db.flush()
    return raw, expires_at


async def email_password_reset(to_email: str, link: str, *, issued_by_admin: bool) -> bool:
    minutes = int(PASSWORD_RESET_LIFETIME.total_seconds() // 60)
    html_body = render_password_reset_email(link, minutes, issued_by_admin)
    text_body = (
        "Reset your TestLookup password with this link (it works once and expires in "
        f"{minutes} minutes):\n\n{link}\n\nIf you did not ask for it, ignore this email."
    )
    return await send_link_email(to_email, "Reset your TestLookup password", html_body, text_body)


async def _within_mailbox_limit(email: str) -> bool:
    """Per-address ceiling (``FORGOT_PER_EMAIL_PER_HOUR``). Fails CLOSED when Redis is down:
    a missed reset mail is a nuisance; an unthrottled mailbox flood is abuse."""
    key = "pwreset:forgot:" + hashlib.sha256(email.lower().encode("utf-8")).hexdigest()
    try:
        from app.db.redis_client import get_redis

        redis = get_redis()
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 3600)
        return int(count) <= FORGOT_PER_EMAIL_PER_HOUR
    except Exception as exc:  # noqa: BLE001
        logger.warning("forgot_password_limiter_unavailable: %s", type(exc).__name__)
        return False


async def _self_service_allowed(db: AsyncSession, user: User) -> bool:
    if not user.is_active:
        return False
    if settings.SSO_ENABLED:
        from app.services.sso_service import is_sso_enforced

        if await is_sso_enforced(db):
            is_admin = user.role == UserRole.ADMIN or str(user.role) == UserRole.ADMIN.value
            if not (is_admin and settings.SSO_ADMIN_FALLBACK_ENABLED):
                return False
    return True


async def process_forgot_request(email: str, request_ip: Optional[str]) -> None:
    """Background half of ``/auth/password/forgot``. Silent on every skip; never raises."""
    from app.db.postgres import AsyncSessionLocal

    try:
        if not await smtp_enabled():
            logger.info("forgot_password_skipped: reason=smtp_off")
            return
        if not await _within_mailbox_limit(email):
            logger.info("forgot_password_skipped: reason=rate_limited")
            return
        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(func.lower(User.email) == email.lower()))
            ).scalar_one_or_none()
            if user is None or not await _self_service_allowed(db, user):
                logger.info("forgot_password_skipped: reason=no_eligible_account")
                return
            raw, _expires = await create_password_reset_token(db, user, request_ip=request_ip)
            await db.commit()
            user_id, to_email = user.id, user.email
        sent = await email_password_reset(to_email, fragment_link(RESET_PATH, raw), issued_by_admin=False)
        logger.info("forgot_password_processed: user_id=%s email_sent=%s", user_id, sent)
    except Exception as exc:  # noqa: BLE001 - a background task must not surface
        logger.warning("forgot_password_failed: %s", type(exc).__name__)
