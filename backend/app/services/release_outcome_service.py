"""Append-only release incident and rollback outcomes for G5 drift (E9.9)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Release, ReleaseOutcome


async def record_outcome(
    db: AsyncSession,
    *,
    release: Release,
    outcome_kind: str,
    reason: str,
    marked_by_user_id: uuid.UUID | None,
) -> ReleaseOutcome:
    """Stage one human outcome; the owning router commits it with its audit event."""
    row = ReleaseOutcome(
        release_id=release.id,
        project_id=release.project_id,
        outcome_kind=outcome_kind,
        reason=reason.strip(),
        marked_by_user_id=marked_by_user_id,
    )
    db.add(row)
    await db.flush()
    return row


async def list_release_outcomes(
    db: AsyncSession,
    *,
    release_id: uuid.UUID,
    limit: int = 50,
) -> list[ReleaseOutcome]:
    """Return newest-first human outcomes for a release detail view."""
    rows = await db.execute(
        select(ReleaseOutcome)
        .where(ReleaseOutcome.release_id == release_id)
        .order_by(ReleaseOutcome.marked_at.desc(), ReleaseOutcome.id.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def list_project_outcomes_between(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> list[ReleaseOutcome]:
    """Supply G5 with incident and rollback facts for one comparison window."""
    rows = await db.execute(
        select(ReleaseOutcome)
        .where(
            ReleaseOutcome.project_id == project_id,
            ReleaseOutcome.marked_at >= start,
            ReleaseOutcome.marked_at < end,
        )
        .order_by(ReleaseOutcome.marked_at.asc(), ReleaseOutcome.id.asc())
    )
    return list(rows.scalars().all())


__all__ = [
    "list_project_outcomes_between",
    "list_release_outcomes",
    "record_outcome",
]
