"""Dashboard metrics endpoints."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_accessible_project_ids, get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import User
from app.services.commit_attribution_service import get_tia_readiness
from app.services.metrics_service import get_dashboard_summary, get_trend_data

router = APIRouter(prefix="/api/v1/metrics", tags=["Metrics"])


def _project_in_scope(project_id: str, accessible: set) -> bool:
    """True when ``project_id`` is one the caller may access."""
    try:
        return uuid.UUID(str(project_id)) in accessible
    except (ValueError, TypeError):
        return False


def _require_valid_project_id(project_id: str | None) -> str | None:
    """Reject a malformed ``project_id`` before it reaches a UUID column.

    ``ALL_PROJECTS_ID`` ("all") is a **frontend-only** sentinel; if it ever
    reaches the API it must not be treated as an id. Non-admins were already
    covered by accident — ``_project_in_scope`` returns False for a non-UUID,
    so they got an empty payload. But ``get_accessible_project_ids`` returns
    ``None`` for an ADMIN, which SKIPS that scope check entirely, so the raw
    string reached the query layer as a UUID comparison and produced a **500**.
    The bug was therefore role-dependent and invisible to non-admin testing.

    ``None`` stays valid — it means "all projects" for a caller allowed to see
    them. Mirrors ``/api/v1/runs``, which answers 400 "Invalid project_id".
    """
    if project_id is None:
        return None
    try:
        uuid.UUID(str(project_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid project_id — expected a UUID")
    return project_id


@router.get("/summary")
async def dashboard_summary(
    project_id: str | None = None,
    days: int = Query(7, ge=1, le=90),
    suite_name: str | None = Query(None, min_length=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return aggregated KPI metrics for the Executive Dashboard."""
    project_id = _require_valid_project_id(project_id)
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
    project_id = _require_valid_project_id(project_id)
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None:
        # Non-admin: only own-project trends (see dashboard_summary).
        if not project_id or not _project_in_scope(project_id, accessible):
            return {"data": [], "period_days": days}
    data = await get_trend_data(db, project_id, days, suite_name=suite_name)
    return {"data": data, "period_days": days}


@router.get("/tia-readiness")
async def tia_readiness(
    project_id: str = Query(
        ..., description="Project to measure — readiness is never a fleet average",
    ),
    days: int = Query(90, ge=7, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Is this project's commit-range corpus big enough to train test-impact
    analysis on yet? (Epic 10 go/no-go.)

    Counts the runs whose commit range actually resolved AND carries
    per-commit changed files, the span they cover, and the distinct paths
    seen. Returns ``available: false`` with a concrete
    ``insufficient_data_reason`` until every published threshold is met —
    the same honesty contract as the value-metrics headline gate.

    ``project_id`` is REQUIRED: readiness is a claim about one project's own
    change/failure history, so there is no meaningful all-projects rollup.
    """
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None and not _project_in_scope(project_id, accessible):
        # Non-admin asking about a project they can't see — same shape as an
        # empty project rather than a 403 that confirms the project exists.
        return {
            "project_id": project_id,
            "available": False,
            "insufficient_data_reason": "no commit ranges have been resolved for this project yet",
        }
    try:
        pid = uuid.UUID(project_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="project_id must be a UUID")
    return await get_tia_readiness(db, pid, days=days)
