"""
Two-run compare API — Tier 2 item 8.

Single endpoint: ``GET /api/v1/runs/compare?left={uuid}&right={uuid}``.
Both runs must be accessible to the caller; the handler enforces
tenant isolation on each side independently.
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    get_db,
    resolve_project_scope,
)
from app.models.postgres import TestRun, User
from app.models.schemas import RunCompareResponse
from app.services import run_compare_service

router = APIRouter(prefix="/api/v1/runs", tags=["Run Compare"])
logger = structlog.get_logger("routers.run_compare")


@router.get("/compare", response_model=RunCompareResponse)
async def compare_two_runs(
    left: uuid.UUID,
    right: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Compare two test runs and return per-test + aggregate deltas.

    Classifies every test whose status or duration differed between
    the two runs into buckets like ``new_failure``, ``fixed``,
    ``regressed``, ``duration_spike``. The response is sorted with
    the most urgent deltas first so the UI's default view surfaces
    regressions before wins.

    Args:
        left: UUID of the baseline ("before") run.
        right: UUID of the target ("after") run.
    """
    if left == right:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="left and right must be different run ids",
        )

    # Look up both runs' project ids and enforce scope before we do
    # the full compare — avoids leaking "run X belongs to project Y"
    # to callers who can't see either side.
    left_proj_row = await db.execute(
        select(TestRun.project_id).where(TestRun.id == left)
    )
    left_project = left_proj_row.scalar_one_or_none()
    if left_project is None:
        raise HTTPException(status_code=404, detail="Left run not found")
    await resolve_project_scope(db, current_user, str(left_project))

    right_proj_row = await db.execute(
        select(TestRun.project_id).where(TestRun.id == right)
    )
    right_project = right_proj_row.scalar_one_or_none()
    if right_project is None:
        raise HTTPException(status_code=404, detail="Right run not found")
    await resolve_project_scope(db, current_user, str(right_project))

    try:
        result = await run_compare_service.compare_runs(db, left, right)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return result
