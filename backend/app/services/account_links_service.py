"""Invitation + password-reset links: lifetimes, status, and best-effort email (W17b ADM-01/02).

The token rules (hash at rest, fragment links) live in ``one_time_tokens``; this
module holds what the two routers share on top of them. Mail is always
best-effort: a send failure turns into ``email_sent=False`` and the caller still
returns the copyable link, never a 500 — the copy-link path is how an offline
deployment (no SMTP) onboards anyone at all.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models.postgres import UserInvitation

logger = logging.getLogger(__name__)

INVITATION_LIFETIME = timedelta(days=7)
PASSWORD_RESET_LIFETIME = timedelta(minutes=60)

#: Self-service reset requests per mailbox per hour (ADM-02): one address cannot be flooded.
FORGOT_PER_EMAIL_PER_HOUR = 3

INVITE_PATH = "/accept-invite"
RESET_PATH = "/reset-password/confirm"


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def invitation_status(invitation: UserInvitation, now: Optional[datetime] = None) -> str:
    """``used`` > ``revoked`` > ``expired`` > ``pending``."""
    now = now or datetime.now(timezone.utc)
    if invitation.is_used or invitation.used_at is not None:
        return "used"
    if invitation.revoked_at is not None:
        return "revoked"
    expires = _aware(invitation.expires_at)
    if expires is not None and expires <= now:
        return "expired"
    return "pending"


def invitation_redeemable(invitation: Optional[UserInvitation]) -> bool:
    return invitation is not None and invitation_status(invitation) == "pending"


async def smtp_enabled() -> bool:
    """True when mail can be delivered at all. Never raises (a broken config reads as off)."""
    from app.services.notification.email_service import get_smtp_config

    try:
        cfg = await get_smtp_config()
    except Exception as exc:  # noqa: BLE001 - a broken SMTP secret must not break sign-in pages
        logger.warning("smtp_config_unreadable: %s", type(exc).__name__)
        return False
    return bool(cfg.get("enabled"))


async def send_link_email(to_email: str, subject: str, html_body: str, text_body: str) -> bool:
    """Send one link mail. True only when it was handed to the relay.

    Never logs the body (it holds the raw token) and never raises.
    """
    from app.services.notification.email_service import get_smtp_config, send_html_email

    try:
        cfg = await get_smtp_config()
        if not cfg.get("enabled"):
            return False
        await send_html_email(to_email, subject, html_body, text_body=text_body, smtp_cfg=cfg)
        return True
    except Exception as exc:  # noqa: BLE001 - best effort by contract
        logger.warning("link_email_failed: subject=%s error=%s", subject, type(exc).__name__)
        return False
