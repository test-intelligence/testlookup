"""Unified Audit Dashboard router — cross-table queries, tenant observability, export (OPS-04)."""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_accessible_project_ids, require_project_access, require_role
from app.db.postgres import get_db
from app.models.postgres import User, UserRole

logger = logging.getLogger("routers.audit_dashboard")

router = APIRouter(prefix="/api/v1/audit-dashboard", tags=["Audit Dashboard"])


@router.get("/events")
async def list_audit_events(
    project_id: Optional[uuid.UUID] = None,
    category: Optional[str] = None,
    actor_id: Optional[uuid.UUID] = None,
    days: int = Query(default=30, ge=1, le=365),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """
    Unified audit event query across all audit tables (QA_LEAD+).

    Tenant isolation: non-admin users only see events for their projects.
    """
    from app.services.audit_dashboard_service import AUDIT_CATEGORIES, query_unified_audit

    # Validate category
    if category and category not in AUDIT_CATEGORIES:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid category. Valid: {', '.join(AUDIT_CATEGORIES.keys())}",
        )

    # Tenant isolation: non-admin users restricted to their projects
    if project_id is None:
        accessible = await get_accessible_project_ids(db, current_user)
        # If user is not admin and no project_id specified, they get no cross-project events
        # Admin (accessible=None) gets everything
        if accessible is not None and len(accessible) == 0:
            return {"total": 0, "items": []}

    return await query_unified_audit(
        db, project_id=project_id, category=category, actor_id=actor_id,
        days=days, page=page, page_size=page_size, redact=True,
    )


@router.get("/categories")
async def list_categories(
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    """List available audit event categories."""
    from app.services.audit_dashboard_service import AUDIT_CATEGORIES

    return [{"key": k, "description": v} for k, v in AUDIT_CATEGORIES.items()]


@router.get("/observability/{project_id}")
async def get_project_observability(
    project_id: uuid.UUID,
    days: int = Query(default=7, ge=1, le=90),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_access()),
):
    """Get tenant-scoped observability metrics for a project (QA_LEAD+)."""
    from app.services.audit_dashboard_service import get_tenant_observability

    return await get_tenant_observability(db, project_id, days)


@router.get("/export")
async def export_audit_events(
    project_id: Optional[uuid.UUID] = None,
    category: Optional[str] = None,
    days: int = Query(default=30, ge=1, le=365),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Export audit events as CSV with redaction applied (ADMIN only)."""
    from app.services.audit_dashboard_service import export_audit_csv

    csv_content = await export_audit_csv(db, project_id, category, days)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=audit-export-{days}d.csv"},
    )
