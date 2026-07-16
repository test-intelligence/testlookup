"""Commit attribution router (thin HTTP layer) — Epic 8 US-8.1/US-8.2.

Endpoints (both project/run-guarded via ``require_run_access``):

  GET /api/v1/runs/{run_id}/commit-range
      The resolved commit range for a run: base/head commits + the commits
      landed since the last green run, with an honest ``available`` flag and
      the acquisition ``source`` (connector | supplied | unavailable). Lazily
      resolves once if no row exists yet (e.g. runs finalized before Epic 8).

  GET /api/v1/runs/{run_id}/suspects?cluster_id=|fingerprint=
      Deterministic suspect ranking for a newly-failing cluster/test — ranked
      commits with the overlapping-files rationale and the monorepo caveat.

All logic lives in ``services/commit_attribution_service``.
"""
import uuid
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.core.deps import require_run_access
from app.db.postgres import AsyncSessionLocal, get_db
from app.models.postgres import TestRun
from app.services import commit_attribution_service as svc

logger = structlog.get_logger("routers.commit_attribution")

router = APIRouter(prefix="/api/v1/runs", tags=["Commit Attribution"])


async def _load_run(db: Any, run_id: uuid.UUID) -> TestRun:
    result = await db.execute(select(TestRun).where(TestRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


async def _lazy_resolve(run_id: uuid.UUID) -> None:
    """One-shot resolve on a dedicated write session so the GET handler's own
    transaction stays read-only (mirrors the run_intelligence cache-populate
    pattern). The service stages the row; this write session owns the commit.
    Best-effort — a resolution failure must not fail the read."""
    try:
        async with AsyncSessionLocal() as write_db:
            await svc.resolve_commit_range(write_db, run_id)
            await write_db.commit()
    except Exception as exc:
        logger.warning("commit_range lazy resolve failed", run_id=str(run_id), error=str(exc))


@router.get("/{run_id}/commit-range")
async def get_run_commit_range(
    run_id: uuid.UUID,
    db: Any = Depends(get_db),
    _: Any = Depends(require_run_access()),
):
    """Return the commit range associated with a run (US-8.1).

    If no range has been resolved yet, attempts a one-shot resolution
    (supplied wins → connector → unavailable) in a dedicated write session so
    this GET stays read-only, then returns the freshly-resolved range.
    """
    run = await _load_run(db, run_id)
    result = await svc.get_commit_range(db, run)
    if not result.get("available") and result.get("source") in (None, svc.SOURCE_UNAVAILABLE):
        await _lazy_resolve(run_id)
        run = await _load_run(db, run_id)
        result = await svc.get_commit_range(db, run)
    return result


@router.get("/{run_id}/suspects")
async def get_run_suspects(
    run_id: uuid.UUID,
    cluster_id: Optional[str] = Query(
        default=None, description="Failure cluster id (e.g. cl_001) to attribute",
    ),
    fingerprint: Optional[str] = Query(
        default=None, description="Test fingerprint to attribute (single test)",
    ),
    db: Any = Depends(get_db),
    _: Any = Depends(require_run_access()),
):
    """Return ranked suspect commits for a newly-failing cluster/test (US-8.2).

    Honest ``available: False`` when there's no commit range for the run.
    Ranking is deterministic + inspectable (per-commit overlapping files).
    """
    run = await _load_run(db, run_id)
    # Resolve the range on demand so suspects work even before finalize ran
    # the connector path (mirrors the commit-range endpoint's lazy resolve).
    existing = await svc.get_commit_range(db, run)
    if not existing.get("available") and existing.get("source") in (None, svc.SOURCE_UNAVAILABLE):
        await _lazy_resolve(run_id)
    return await svc.rank_suspects(
        db, run, cluster_id=cluster_id, fingerprint=fingerprint,
    )
