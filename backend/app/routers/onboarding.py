"""Onboarding wizard endpoints — guided setup and adoption tracking."""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, require_project_access, require_role
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.services.onboarding_service import (
    auto_detect_progress,
    complete_step,
    get_onboarding_status,
    get_usage_events,
    restore_step,
    skip_step,
    track_event,
)

logger = logging.getLogger("routers.onboarding")

router = APIRouter(prefix="/api/v1/onboarding", tags=["Onboarding"])


class StepAction(BaseModel):
    step_key: str


class TrackEventRequest(BaseModel):
    event_name: str
    project_id: Optional[str] = None
    payload: Optional[dict] = None


@router.get("/{project_id}/status")
async def get_status(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
):
    """Get onboarding progress for a project."""
    try:
        return await get_onboarding_status(project_id, db)
    except Exception as exc:
        logger.error("Onboarding status failed: %s", exc, exc_info=True)
        return {
            "project_id": str(project_id), "steps": [], "completed_count": 0,
            "total_count": 0, "progress_pct": 0, "is_complete": False,
        }


@router.post("/{project_id}/detect")
async def detect_progress(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
):
    """Auto-detect and update onboarding progress from existing data."""
    try:
        result = await auto_detect_progress(project_id, db)
        await db.commit()
        return result
    except Exception as exc:
        await db.rollback()
        logger.error("Onboarding detect failed: %s", exc, exc_info=True)
        return {
            "project_id": str(project_id), "steps": [], "completed_count": 0,
            "total_count": 0, "progress_pct": 0, "is_complete": False,
        }


@router.post("/{project_id}/complete")
async def mark_step_complete(
    project_id: uuid.UUID,
    body: StepAction,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
):
    """Mark an onboarding step as completed."""
    try:
        result = await complete_step(project_id, body.step_key, current_user.id, db)
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Complete step failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update step") from exc


@router.post("/{project_id}/skip")
async def mark_step_skipped(
    project_id: uuid.UUID,
    body: StepAction,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_access()),
):
    """Mark an onboarding step as skipped."""
    try:
        result = await skip_step(project_id, body.step_key, db)
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Skip step failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to skip step") from exc


@router.post("/{project_id}/restore")
async def mark_step_restored(
    project_id: uuid.UUID,
    body: StepAction,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_access()),
):
    """Un-skip an onboarding step, returning it to pending so it can be redone."""
    try:
        result = await restore_step(project_id, body.step_key, db)
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Restore step failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to restore step") from exc


@router.post("/track")
async def track_usage_event(
    body: TrackEventRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Track a product usage event for adoption analytics."""
    project_uuid = None
    if body.project_id:
        try:
            project_uuid = uuid.UUID(body.project_id)
        except ValueError:
            pass
    await track_event(db, body.event_name, user_id=current_user.id, project_id=project_uuid, payload=body.payload)
    # Preserve fire-and-forget semantics: if the commit itself fails,
    # log it but still report "tracked" since usage analytics shouldn't
    # surface errors to the caller.
    try:
        await db.commit()
    except Exception as exc:
        logger.warning("Failed to commit usage event %s: %s", body.event_name, exc)
        await db.rollback()
    return {"status": "tracked"}


@router.get("/events")
async def list_usage_events(
    event_name: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """List recent product usage events for analytics. Requires ADMIN.

    F-044: this served **instance-wide** analytics to any authenticated caller.
    ``get_usage_events`` applies no project filter when none is named, and the
    handler bound the user to ``_`` — so it discarded the identity it would
    have needed to scope by. A zero-membership VIEWER read another user's
    ``user_id``, an inaccessible ``project_id``, and the raw ``event_payload``.

    ADMIN-only rather than membership-scoped: this is cross-project analytics
    by nature, mirroring ``/audit-dashboard/export``, and it has no consumer —
    no frontend, CLI, MCP or SDK caller references it.
    """
    pid = None
    if project_id:
        try:
            pid = uuid.UUID(project_id)
        except ValueError:
            pass
    return await get_usage_events(db, event_name=event_name, project_id=pid, limit=limit)
