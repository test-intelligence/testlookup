"""
Run Intelligence Snapshot Service — read-through caching.

Provides:
  - get_or_compute: returns cached snapshot or computes live then caches
  - save_snapshot: persists a computed intelligence payload
  - mark_stale: flags a snapshot as stale (triggers re-compute on next read)
  - invalidate: deletes a snapshot (used after re-analysis)

Schema:
  Snapshots are stored in run_intelligence_snapshots with:
  - schema_version: tracks payload shape evolution
  - stale: boolean flag for triggering refresh
  - fallback_used: whether the snapshot was generated without LLM

Cache strategy:
  - Read: if snapshot exists and not stale, return it directly
  - Miss: compute live via get_run_intelligence, save snapshot, return
  - Stale: return stale data immediately, trigger async refresh
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import RunIntelligenceSnapshot

logger = logging.getLogger("services.intelligence_snapshot")

CURRENT_SCHEMA_VERSION = 2


async def get_cached_snapshot(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> Optional[dict]:
    """
    Return cached intelligence payload if it exists and is fresh.
    Returns None if no cache exists or if stale.
    Returns the payload dict (not the ORM object).
    """
    result = await db.execute(
        select(RunIntelligenceSnapshot).where(
            RunIntelligenceSnapshot.run_id == run_id,
            RunIntelligenceSnapshot.stale.is_(False),
        )
    )
    snapshot = result.scalar_one_or_none()
    if snapshot and snapshot.schema_version >= CURRENT_SCHEMA_VERSION:
        return snapshot.payload
    return None


async def get_stale_snapshot(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> Optional[dict]:
    """Return a stale snapshot for immediate use while refresh is in progress."""
    result = await db.execute(
        select(RunIntelligenceSnapshot).where(
            RunIntelligenceSnapshot.run_id == run_id,
        )
    )
    snapshot = result.scalar_one_or_none()
    if snapshot:
        return snapshot.payload
    return None


async def save_snapshot(
    db: AsyncSession,
    run_id: uuid.UUID,
    payload: dict,
    fallback_used: bool = False,
) -> None:
    """Save or update a snapshot for a run."""
    result = await db.execute(
        select(RunIntelligenceSnapshot).where(
            RunIntelligenceSnapshot.run_id == run_id,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.payload = payload
        existing.schema_version = CURRENT_SCHEMA_VERSION
        existing.fallback_used = fallback_used
        existing.stale = False
        existing.generated_at = datetime.now(timezone.utc)
    else:
        db.add(RunIntelligenceSnapshot(
            run_id=run_id,
            schema_version=CURRENT_SCHEMA_VERSION,
            payload=payload,
            fallback_used=fallback_used,
            generated_at=datetime.now(timezone.utc),
        ))

    await db.commit()
    logger.debug("Snapshot saved for run %s", run_id)


async def mark_stale(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> bool:
    """Mark a snapshot as stale (triggers refresh on next read). Returns True if found.

    Stage-only: mutation is left pending on the current transaction. The
    caller (router handler) owns the commit so a stale flip can land in the
    same transaction as whatever triggered it (defect promotion, release
    override, etc.), keeping the freshness guarantee atomic.
    """
    result = await db.execute(
        select(RunIntelligenceSnapshot).where(
            RunIntelligenceSnapshot.run_id == run_id,
        )
    )
    snapshot = result.scalar_one_or_none()
    if snapshot:
        snapshot.stale = True
        return True
    return False


async def invalidate(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> bool:
    """Delete a snapshot entirely (used after re-analysis). Returns True if deleted."""
    result = await db.execute(
        delete(RunIntelligenceSnapshot).where(
            RunIntelligenceSnapshot.run_id == run_id,
        )
    )
    await db.commit()
    return result.rowcount > 0
