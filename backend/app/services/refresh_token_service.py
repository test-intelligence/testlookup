"""
Refresh token issuance, rotation, and replay detection.

Every refresh token carries a random ``jti`` claim. Only the SHA-256 hex
digest of the jti is persisted (``RefreshTokenRecord.jti_hash``). On refresh:

  1. Look up the presented jti's hash.
  2. If the record is missing, expired, or revoked → 401.
  3. If the record has already been rotated, treat it as a **replay**:
     revoke the entire token family for the owner, raise
     ``RefreshTokenReplayError`` (the caller then also ends every access token
     of the owner) and return 401.
  4. Otherwise, rotate: mark the old record ``rotated_to_id`` and create a
     new record for the freshly issued jti.

This gives us:
  - **Hashed at rest** — raw jti is never stored.
  - **Rotation** — old tokens invalidate on use.
  - **Replay detection** — stolen tokens used after the legitimate client
    refreshes trigger family-wide revocation.
  - **Revocation** — ``revoked_at`` lets us invalidate specific records.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_refresh_token, hash_token
from app.models.postgres import RefreshTokenRecord

logger = logging.getLogger(__name__)


class RefreshTokenError(ValueError):
    """Raised when a refresh token fails rotation/replay checks."""


class RefreshTokenReplayError(RefreshTokenError):
    """An already-rotated refresh token was presented again: treat it as stolen.

    The caller must also end the user's access tokens
    (``token_revocation.revoke_all_user_tokens``): revoking the refresh family
    alone leaves every access token already issued valid until it expires.
    """


async def issue_refresh_token(db: AsyncSession, user_id: uuid.UUID) -> str:
    """Create a new refresh token and persist its server-side record."""
    encoded, jti, expires_at = create_refresh_token(str(user_id))
    record = RefreshTokenRecord(
        user_id=user_id,
        jti_hash=hash_token(jti),
        expires_at=expires_at,
    )
    db.add(record)
    await db.flush()
    return encoded


async def rotate_refresh_token(
    db: AsyncSession,
    user_id: uuid.UUID,
    presented_jti: str,
) -> str:
    """Validate the presented jti, rotate it, and issue a replacement.

    Raises ``RefreshTokenError`` on any validation failure. Callers should
    treat every failure as 401 Unauthorized.
    """
    jti_hash = hash_token(presented_jti)
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(RefreshTokenRecord).where(RefreshTokenRecord.jti_hash == jti_hash)
    )
    record: Optional[RefreshTokenRecord] = result.scalar_one_or_none()

    if record is None:
        raise RefreshTokenError("Unknown refresh token")

    if record.user_id != user_id:
        raise RefreshTokenError("Token subject mismatch")

    if record.expires_at <= now:
        raise RefreshTokenError("Refresh token expired")

    if record.rotated_to_id is not None:
        # Replay of a rotated token — the legitimate client already moved
        # on. Treat as compromise and revoke everything for this user.
        await _revoke_family(db, user_id, reason="replay")
        logger.warning(
            "Refresh token replay detected user_id=%s old_record=%s",
            user_id,
            record.id,
        )
        raise RefreshTokenReplayError("Refresh token replay detected")

    if record.revoked_at is not None:
        # A token that was revoked without being rotated (logout, password
        # change) and is presented again, e.g. by a browser tab left open.
        # Refuse it and revoke the family defensively, but it is not a theft
        # signal: the caller does not end the user's access tokens for it.
        await _revoke_family(db, user_id)
        raise RefreshTokenError("Refresh token has been revoked")

    # Happy path: issue a new record and link the old one.
    new_encoded, new_jti, new_expires_at = create_refresh_token(str(user_id))
    new_record = RefreshTokenRecord(
        user_id=user_id,
        jti_hash=hash_token(new_jti),
        expires_at=new_expires_at,
    )
    db.add(new_record)
    await db.flush()

    record.rotated_to_id = new_record.id
    record.revoked_at = now
    return new_encoded


async def _revoke_family(
    db: AsyncSession,
    user_id: uuid.UUID,
    reason: str = "revoked",
) -> None:
    """Revoke every live refresh token for a user (replay defence)."""
    now = datetime.now(timezone.utc)
    await db.execute(
        update(RefreshTokenRecord)
        .where(
            RefreshTokenRecord.user_id == user_id,
            RefreshTokenRecord.revoked_at.is_(None),
        )
        .values(
            revoked_at=now,
            replay_detected=(reason == "replay"),
        )
    )
