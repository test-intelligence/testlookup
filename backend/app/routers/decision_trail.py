"""
Decision trail API — Tier 0B.

Single endpoint that returns the aggregated AI decision audit for a
test run so enterprise QA leads can see "why did the AI recommend X"
without grepping logs.

Tenant isolation is enforced via ``resolve_project_scope`` against the
underlying ``TestRun.project_id``.
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, get_db, resolve_project_scope
from app.models.postgres import TestRun, User
from app.models.schemas import DecisionTrailResponse
from app.services import decision_trail_service

router = APIRouter(prefix="/api/v1/runs", tags=["Decision Trail"])
logger = structlog.get_logger("routers.decision_trail")


@router.get(
    "/{run_id}/decision-trail",
    response_model=DecisionTrailResponse,
    summary="AI decision trail for a test run",
)
async def get_decision_trail(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> DecisionTrailResponse:
    """
    Return the full AI decision trail for a test run.

    The trail contains:
      - Pipeline run metadata (status, started_at, completed_at, cost).
      - Per-stage summary with decision_log, analysis_mode, fallback reason.
      - Per-test routing showing which engine ran and any confidence
        adjustments or retries.
      - Workflow router events (fast-path skips, specialist stage picks).

    Access control: the caller must have access to the run's project.
    """
    # Resolve the run's project and enforce scope. We look the project up
    # directly from TestRun so a user can't probe run IDs for other tenants.
    run_row = await db.execute(
        select(TestRun.project_id).where(TestRun.id == run_id)
    )
    project_id = run_row.scalar_one_or_none()
    if project_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Test run not found",
        )

    # ``resolve_project_scope`` raises 403 when the user is not a member and
    # 400 on bad UUIDs. ADMIN passes through unconstrained.
    await resolve_project_scope(db, current_user, str(project_id))

    trail = await decision_trail_service.build_trail(db, run_id)
    if trail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Test run not found",
        )
    return trail
