"""Value Metrics endpoints — operational value dashboard for customers."""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_accessible_project_ids, get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import User
from app.services.value_metrics_service import get_methodology, get_value_metrics

router = APIRouter(prefix="/api/v1/value-metrics", tags=["Value Metrics"])


def _project_in_scope(project_id: str, accessible: set) -> bool:
    """True when ``project_id`` is one the caller may access."""
    try:
        return uuid.UUID(str(project_id)) in accessible
    except (ValueError, TypeError):
        return False


@router.get("")
async def get_metrics(
    project_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
    months: int = Query(6, ge=1, le=24),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return operational value metrics for a project (or all) over a time
    window, including the US-12.1 engineer-hours-saved model (``months``
    bounds the monthly trend)."""
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None:
        # Non-admin: verify the provided project_id too — otherwise a caller
        # could read any tenant's value metrics via ?project_id=<foreign-uuid>.
        if not project_id or not _project_in_scope(project_id, accessible):
            return {}
    pid = uuid.UUID(project_id) if project_id else None
    return await get_value_metrics(db, project_id=pid, days=days, months=months)


@router.get("/methodology")
async def get_methodology_page(
    current_user: User = Depends(get_current_active_user),
):
    """US-12.1: the hours-saved model documented — legs, formulas, caveats,
    defaults, research anchors. Static content; login-only, not
    project-scoped."""
    return get_methodology()


@router.get("/by-team")
async def get_metrics_by_team(
    project_id: str = Query(...),
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Tier 2 item 11 — partition value metrics by owning team.

    Uses ``ServiceOwnershipRule`` rows to bucket tests + defects by
    team. Requires a ``project_id`` because ownership rules are
    project-scoped. Returns a per-team rollup with test count, pass
    rate, defect count, MTTR hours, and estimated minutes saved so
    the dashboard can render per-team tiles without any further
    transformation.
    """
    from app.core.deps import resolve_project_scope
    await resolve_project_scope(db, current_user, project_id)
    from app.services.team_value_metrics_service import get_team_value_metrics
    pid = uuid.UUID(project_id)
    return await get_team_value_metrics(db, project_id=pid, days=days)


@router.get("/export")
async def export_metrics(
    project_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Export value metrics as a downloadable JSON report."""
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None and (not project_id or not _project_in_scope(project_id, accessible)):
        return Response(
            content='{"report_type": "value_metrics"}',
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=value-metrics-{days}d.json"},
        )
    import json
    pid = uuid.UUID(project_id) if project_id else None
    metrics = await get_value_metrics(db, project_id=pid, days=days)
    content = json.dumps({"report_type": "value_metrics", **metrics}, indent=2, default=str)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=value-metrics-{days}d.json"},
    )
