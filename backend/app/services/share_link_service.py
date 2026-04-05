"""
Share Link Service — create, validate, revoke time-limited report share tokens.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import ReportShareLink, User

logger = logging.getLogger("services.share_link")


async def create_share_link(
    db: AsyncSession,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    created_by: User,
    layout: str = "executive",
    expiry_days: int = 7,
) -> ReportShareLink:
    """Generate a unique share token and persist the link."""
    token = secrets.token_urlsafe(48)  # 64-char base64
    expires_at = datetime.now(timezone.utc) + timedelta(days=expiry_days)

    link = ReportShareLink(
        run_id=run_id,
        project_id=project_id,
        token=token,
        report_layout=layout,
        created_by_id=created_by.id,
        created_by_name=created_by.username or created_by.full_name,
        expires_at=expires_at,
    )
    db.add(link)
    await db.flush()
    return link


async def validate_share_link(
    db: AsyncSession,
    token: str,
) -> ReportShareLink:
    """
    Look up token, check not revoked and not expired.
    Increments access_count, updates last_accessed_at.
    Raises ValueError if invalid/expired/revoked.
    """
    result = await db.execute(
        select(ReportShareLink).where(ReportShareLink.token == token)
    )
    link = result.scalar_one_or_none()

    if link is None:
        raise ValueError("Invalid share link")

    if link.is_revoked:
        raise ValueError("This share link has been revoked")

    if link.expires_at < datetime.now(timezone.utc):
        raise ValueError("This share link has expired")

    # Update access tracking
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
    """List all share links for a run (active + expired + revoked)."""
    result = await db.execute(
        select(ReportShareLink)
        .where(ReportShareLink.run_id == run_id)
        .order_by(ReportShareLink.created_at.desc())
    )
    return list(result.scalars().all())
