"""Dashboard metrics endpoints."""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_accessible_project_ids, get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import User
from app.services.metrics_service import get_dashboard_summary, get_trend_data

router = APIRouter(prefix="/api/v1/metrics", tags=["Metrics"])


def _project_in_scope(project_id: str, accessible: set) -> bool:
    """True when ``project_id`` is one the caller may access."""
    try:
        return uuid.UUID(str(project_id)) in accessible
    except (ValueError, TypeError):
        return False


@router.get("/summary")
async def dashboard_summary(
    project_id: str | None = None,
    days: int = Query(7, ge=1, le=90),
    suite_name: str | None = Query(None, min_length=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return aggregated KPI metrics for the Executive Dashboard."""
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None:
        # Non-admin: must request a project they're a member of. Without
        # verifying the *provided* project_id a caller could read any tenant's
        # KPIs via ?project_id=<other-tenant-uuid> (the service trusts it).
        if not project_id or not _project_in_scope(project_id, accessible):
            return {}
    return await get_dashboard_summary(db, project_id, days, suite_name=suite_name)


@router.get("/trends")
async def trend_data(
    project_id: str | None = None,
    days: int = Query(7, ge=1, le=90),
    suite_name: str | None = Query(None, min_length=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return daily pass/fail/skip breakdown for trend charts."""
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None:
        # Non-admin: only own-project trends (see dashboard_summary).
        if not project_id or not _project_in_scope(project_id, accessible):
            return {"data": [], "period_days": days}
    data = await get_trend_data(db, project_id, days, suite_name=suite_name)
    return {"data": data, "period_days": days}
