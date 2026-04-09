"""Value Metrics endpoints — operational value dashboard for customers."""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_accessible_project_ids, get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import User
from app.services.value_metrics_service import get_value_metrics

router = APIRouter(prefix="/api/v1/value-metrics", tags=["Value Metrics"])


@router.get("")
async def get_metrics(
    project_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return operational value metrics for a project (or all) over a time window."""
    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            return {}
    pid = uuid.UUID(project_id) if project_id else None
    return await get_value_metrics(db, project_id=pid, days=days)


@router.get("/export")
async def export_metrics(
    project_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Export value metrics as a downloadable JSON report."""
    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
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
