"""
Release Readiness Router.

Exposes go/no-go release decisions produced by ReleaseRiskAgent, enriched with
Release Council context (cluster insights, baseline diff, override audit trail).
"""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.deps import get_current_active_user, require_role
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import User, UserRole
from app.models.schemas import (
    ReleaseCouncilOverrideRequest,
    ReleaseCouncilResponse,
)
from app.services.release_council_service import apply_override, get_release_council

logger = logging.getLogger("routers.release_readiness")

router = APIRouter(prefix="/api/v1/release-readiness", tags=["Release Readiness"])


# ── Legacy compact response (kept for backward compatibility) ────────────────

class ReleaseDecisionResponse(BaseModel):
    run_id: str
    recommendation: str
    risk_score: int
    blocking_issues: list[str]
    conditions_for_go: list[str]
    reasoning: Optional[str]
    human_override: Optional[str]
    pass_rate: Optional[float] = None
    build_number: Optional[str] = None


@router.get("/{run_id}", response_model=ReleaseCouncilResponse)
async def get_release_decision(
    run_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
):
    """
    Retrieve the release readiness decision with full council context:
    dimension scores, linked cluster insights, baseline diff, open defects,
    and override audit trail.
    """
    async with AsyncSessionLocal() as db:
        council = await get_release_council(run_id, db)
        if not council:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No release decision found. Trigger deep investigation first.",
            )
        return council


@router.post("/{run_id}/override", response_model=ReleaseCouncilResponse)
async def override_release_decision(
    run_id: uuid.UUID,
    body: ReleaseCouncilOverrideRequest,
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    """
    Override the AI release decision (QA Lead only).
    Records the override in an immutable audit trail with before/after values.
    """
    if body.override_recommendation not in ("GO", "NO_GO", "CONDITIONAL_GO"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="override_recommendation must be GO, NO_GO, or CONDITIONAL_GO",
        )

    if not body.reason or not body.reason.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Override reason is required.",
        )

    async with AsyncSessionLocal() as db:
        council = await apply_override(
            run_id=run_id,
            override_recommendation=body.override_recommendation,
            reason=body.reason.strip(),
            actor=current_user,
            db=db,
        )
        if not council:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No release decision found for this run.",
            )
        # BL-03: Mark intelligence snapshot stale after release override
        try:
            from app.services.intelligence_snapshot_service import mark_stale
            await mark_stale(db, run_id)
        except Exception:
            pass  # Non-blocking
        return council
