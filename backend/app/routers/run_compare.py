"""
Two-run compare API — Tier 2 item 8.

Single endpoint: ``GET /api/v1/runs/compare?left={uuid}&right={uuid}``.
Both runs must be accessible to the caller; the handler enforces
tenant isolation on each side independently.
"""
from __future__ import annotations

import uuid
import asyncio
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    get_db,
    resolve_project_scope,
)
from app.models.postgres import TestRun, User
from app.models.schemas import RunCompareResponse
from app.services import run_compare_ai_service, run_compare_service

router = APIRouter(prefix="/api/v1/runs", tags=["Run Compare"])
logger = structlog.get_logger("routers.run_compare")

_AI_REPORT_TIMEOUT_SECONDS = 60


@router.get("/compare", response_model=RunCompareResponse)
async def compare_two_runs(
    left: uuid.UUID,
    right: uuid.UUID,
    suite_name: Optional[str] = Query(None, description="Optional suite scope; matched case-insensitively"),
    include_ai_report: bool = Query(True, description="Attach cached or generated AI comparison report"),
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

    selection = None
    if suite_name:
        selection = {
            "mode": "explicit",
            "scope": "suite",
            "suite_name": suite_name,
            "selection_reason": "User explicitly selected two historical runs.",
            "project_id": right_project,
            "branch": None,
            "branch_mismatch": False,
            "release_name": None,
        }

    try:
        result = await run_compare_service.compare_runs(
            db,
            left,
            right,
            suite_name=suite_name,
            selection=selection,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if suite_name:
        result["selection"]["branch"] = result["right"]["branch"]
        result["selection"]["branch_mismatch"] = result["left"]["branch"] != result["right"]["branch"]

    if include_ai_report:
        result["ai_report"] = await _resolve_ai_report(
            db,
            result=result,
            project_id=right_project,
            left_run_id=left,
            right_run_id=right,
            suite_name=suite_name,
            user_id=current_user.id,
        )
    return result


@router.get("/compare/latest", response_model=RunCompareResponse)
async def compare_latest_suite_runs(
    suite_name: str = Query(..., min_length=1, description="Suite name; matched case-insensitively"),
    project_id: Optional[uuid.UUID] = Query(None),
    include_ai_report: bool = Query(True, description="Attach cached or generated AI comparison report"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Compare the latest completed run for a suite against the previous latest
    completed run for the same suite and branch.
    """
    scoped_project_id, allowed_project_ids = await resolve_project_scope(
        db,
        current_user,
        str(project_id) if project_id else None,
    )
    effective_project_id = scoped_project_id
    if effective_project_id is None:
        if allowed_project_ids is not None and len(allowed_project_ids) == 1:
            effective_project_id = next(iter(allowed_project_ids))
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="project_id is required when multiple projects are accessible",
            )

    try:
        previous, latest = await run_compare_service.resolve_latest_suite_pair(
            db,
            project_id=effective_project_id,
            suite_name=suite_name,
        )
        selection = {
            "mode": "latest_vs_previous",
            "scope": "suite",
            "suite_name": suite_name,
            "selection_reason": "Latest completed suite run compared with the previous completed run on the same branch.",
            "project_id": effective_project_id,
            "branch": latest.branch,
            "branch_mismatch": previous.branch != latest.branch,
            "release_name": None,
        }
        result = await run_compare_service.compare_runs(
            db,
            previous.id,
            latest.id,
            suite_name=suite_name,
            selection=selection,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if include_ai_report:
        result["ai_report"] = await _resolve_ai_report(
            db,
            result=result,
            project_id=effective_project_id,
            left_run_id=previous.id,
            right_run_id=latest.id,
            suite_name=suite_name,
            user_id=current_user.id,
        )
    return result


async def _resolve_ai_report(
    db: AsyncSession,
    *,
    result: dict,
    project_id: uuid.UUID,
    left_run_id: uuid.UUID,
    right_run_id: uuid.UUID,
    suite_name: Optional[str],
    user_id: uuid.UUID,
) -> dict:
    cached = await run_compare_ai_service.get_cached_report(
        db,
        project_id=project_id,
        left_run_id=left_run_id,
        right_run_id=right_run_id,
        suite_name=suite_name,
    )
    if cached:
        return cached

    try:
        return await asyncio.wait_for(
            run_compare_ai_service.generate_and_save_report(
                db,
                project_id=project_id,
                left_run_id=left_run_id,
                right_run_id=right_run_id,
                suite_name=suite_name,
                compare_payload=result,
                created_by_user_id=user_id,
            ),
            timeout=_AI_REPORT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        await db.rollback()
        await run_compare_ai_service.mark_queued(
            db,
            project_id=project_id,
            left_run_id=left_run_id,
            right_run_id=right_run_id,
            suite_name=suite_name,
            compare_payload=result,
            created_by_user_id=user_id,
        )
        try:
            from app.worker.tasks import generate_run_compare_report
            generate_run_compare_report.delay(
                project_id=str(project_id),
                left_run_id=str(left_run_id),
                right_run_id=str(right_run_id),
                suite_name=suite_name,
            )
        except Exception as exc:
            logger.warning("run_compare_report_queue_failed", error=str(exc))
        return {
            "status": "queued",
            "executive_summary": "",
            "markdown_report": "",
            "risk_level": "LOW",
            "key_differences": [],
            "new_risks": [],
            "resolved_risks": [],
            "duration_concerns": [],
            "recommended_actions": [],
            "confidence": 0,
            "confidence_reason": "",
            "fallback_used": False,
            "message": "AI comparison report is taking longer than 60 seconds. It has been queued and will be saved here when ready.",
        }
