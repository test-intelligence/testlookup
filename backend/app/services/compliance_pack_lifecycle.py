"""Early release and deletion for compliance packs (S4).

A pack is generated with ``retention_expires_at = now + 7 years`` and the
nightly purge already deletes packs past it. Until now that window was
enforced only on the way OUT: nothing could retire a pack sooner, and nothing
stopped a deletion ignoring the window entirely, because no delete path
existed.

**Two steps, and the split is the guard.** ``retire_early`` moves
``retention_expires_at`` to now; the existing purge then collects the pack on
its next sweep, using the object-then-row ordering it already gets right (the
object is deleted first, so a failed object delete leaves the row for the next
sweep to retry). ``deletion_blockers`` refuses an immediate delete while the
pack is still inside its window — retire it first, deliberately, and the
refusal names why.

**On holds.** The epic's criterion is "refused while a hold covers it", but
legal holds are S9 and no hold table exists. A hold check written now would be
a branch nothing could ever trigger — the same defect this epic catalogues
elsewhere. The retention window is a real, already-enforced constraint and is
the guard until S9 adds the hold check alongside it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import select

from app.models.postgres import CompliancePack

logger = structlog.get_logger(__name__)


def deletion_blockers(pack: Any, *, now: Optional[datetime] = None) -> list[str]:
    """Reasons this pack must not be deleted right now, or an empty list.

    Every blocker is collected rather than returning on the first, so clearing
    one does not reveal another on the next attempt.

    S9 adds the hold check here. It is deliberately absent rather than stubbed:
    an ``if hold: ...`` against a table that does not exist reads as a working
    guard while never firing once.
    """
    now = now or datetime.now(timezone.utc)
    blockers: list[str] = []

    expires = getattr(pack, "retention_expires_at", None)
    if expires is not None:
        # Naive timestamps come back from some drivers; compare in UTC.
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires > now:
            blockers.append(
                f"retention window runs to {expires.isoformat()} — retire the "
                "pack early first if it really should go now"
            )

    return blockers


def retire_early(
    pack: Any, *, now: Optional[datetime] = None, reason: str = ""
) -> datetime:
    """Bring a pack's retention window forward to now. Stages; caller commits.

    Deliberately does NOT delete. The nightly purge already deletes packs past
    their window, and duplicating that here would mean two implementations of
    the object-then-row ordering — the sequence that makes a failed object
    delete retryable rather than an orphaned row.
    """
    stamped = now or datetime.now(timezone.utc)
    pack.retention_expires_at = stamped
    logger.info(
        "compliance_pack_retired_early",
        pack_id=str(getattr(pack, "id", "")),
        project_id=str(getattr(pack, "project_id", "")),
        reason=(reason or "")[:500],
    )
    return stamped


async def get_pack_for_write(db, pack_id: uuid.UUID):
    """Load a pack for a mutating operation. Read-only; caller owns the session."""
    return (
        await db.execute(select(CompliancePack).where(CompliancePack.id == pack_id))
    ).scalar_one_or_none()
