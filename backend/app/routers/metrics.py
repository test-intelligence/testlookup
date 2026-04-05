"""Dashboard metrics endpoints."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import get_db
from app.services.metrics_service import get_dashboard_summary, get_trend_data

router = APIRouter(prefix="/api/v1/metrics", tags=["Metrics"])


@router.get("/summary")
async def dashboard_summary(
    project_id: str | None = None,
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    """Return aggregated KPI metrics for the Executive Dashboard."""
    return await get_dashboard_summary(db, project_id, days)


@router.get("/trends")
async def trend_data(
    project_id: str | None = None,
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    """Return daily pass/fail/skip breakdown for trend charts."""
    data = await get_trend_data(db, project_id, days)
    return {"data": data, "period_days": days}
