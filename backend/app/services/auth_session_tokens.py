"""Issue session JWTs from PostgreSQL's clock while the caller holds the user lock."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, create_mfa_token


async def _database_issued_at(db: AsyncSession) -> datetime:
    """Return statement time from the same database that stores revocation cutoffs."""
    result = await db.execute(select(func.clock_timestamp()))
    issued_at = result.scalar_one()
    if not isinstance(issued_at, datetime) or issued_at.tzinfo is None:
        raise RuntimeError("PostgreSQL clock_timestamp() did not return an aware datetime")
    return issued_at


async def issue_access_jwt(
    db: AsyncSession,
    subject: Any,
    expires_delta: Optional[timedelta] = None,
) -> str:
    issued_at = await _database_issued_at(db)
    return create_access_token(subject, expires_delta, issued_at=issued_at)


async def issue_mfa_jwt(
    db: AsyncSession,
    subject: Any,
    token_type: str,
) -> tuple[str, str, int]:
    issued_at = await _database_issued_at(db)
    return create_mfa_token(subject, token_type, issued_at=issued_at)
