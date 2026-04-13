"""
Share Link Service — create, validate, revoke time-limited report share tokens.

The raw token is returned to the creator **once** and never stored; only the
SHA-256 digest lives in ``report_share_links.token_hash``. A database
compromise therefore cannot leak active share URLs.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_token
from app.models.postgres import ReportShareLink, User

logger = logging.getLogger("services.share_link")


class CreatedShareLink(NamedTuple):
    link: ReportShareLink
    raw_token: str  # shown once, never persisted


async def create_share_link(
    db: AsyncSession,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    created_by: User,
    layout: str = "executive",
    expiry_days: int = 7,
) -> CreatedShareLink:
    """Generate a unique share token, persist only its hash, return both."""
    raw_token = secrets.token_urlsafe(48)  # 64-char base64
    expires_at = datetime.now(timezone.utc) + timedelta(days=expiry_days)

    link = ReportShareLink(
        run_id=run_id,
        project_id=project_id,
        token_hash=hash_token(raw_token),
        report_layout=layout,
        created_by_id=created_by.id,
        created_by_name=created_by.username or created_by.full_name,
        expires_at=expires_at,
    )
    db.add(link)
    await db.flush()
    return CreatedShareLink(link=link, raw_token=raw_token)


async def validate_share_link(
    db: AsyncSession,
    token: str,
) -> ReportShareLink:
    """
    Look up token by its SHA-256 hash, check not revoked and not expired.
    Increments access_count, updates last_accessed_at.
    Raises ValueError if invalid/expired/revoked.
    """
    token_hash = hash_token(token)
    result = await db.execute(
        select(ReportShareLink).where(ReportShareLink.token_hash == token_hash)
    )
    link = result.scalar_one_or_none()

    if link is None:
        raise ValueError("Invalid share link")

    if link.is_revoked:
        raise ValueError("This share link has been revoked")

    if link.expires_at < datetime.now(timezone.utc):
        raise ValueError("This share link has expired")

    link.access_count = (link.access_count or 0) + 1
    link.last_accessed_at = datetime.now(timezone.utc)

    return link


async def revoke_share_link(
    db: AsyncSession,
    link_id: uuid.UUID,
) -> None:
    """Mark a share link as revoked."""
    result = await db.execute(
        select(ReportShareLink).where(ReportShareLink.id == link_id)
    )
    link = result.scalar_one_or_none()
    if link is None:
        raise ValueError("Share link not found")

    link.is_revoked = True


async def list_share_links(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> list[ReportShareLink]:
    """List all share links for a run (active + expired + revoked).

    Raw tokens are not returned — callers only see metadata (id, layout,
    expiry, revocation status). The share URL shown to the creator at
    creation time is the only place the raw token is ever exposed.
    """
    result = await db.execute(
        select(ReportShareLink)
        .where(ReportShareLink.run_id == run_id)
        .order_by(ReportShareLink.created_at.desc())
    )
    return list(result.scalars().all())
