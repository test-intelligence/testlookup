from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import User
from app.models.schemas import AuditLogListResponse, AuditLogResponse
from app.routers.test_management_shared import row
from app.services.test_management_query_service import list_audit_logs

router = APIRouter()


@router.get("/audit", response_model=AuditLogListResponse)
async def get_audit_log(
    project_id: Optional[uuid.UUID] = None,
    entity_type: str | None = None,
    action: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # F-042: this check ran ONLY in the ``not project_id`` branch, so naming a
    # project skipped it entirely — the guard fired only where there was
    # nothing to guard. Third recurrence of the class (F-033 digests, F-040
    # chat). ``resolve_project_scope`` 403s a non-admin naming a project they
    # do not belong to; the empty return below preserves today's behaviour for
    # a non-admin who names no project at all.
    from app.core.deps import resolve_project_scope  # noqa: PLC0415

    scoped_project_id, allowed = await resolve_project_scope(
        db, current_user, str(project_id) if project_id else None
    )
    if scoped_project_id is None and allowed is not None:
        return {"items": [], "total": 0, "page": page, "size": size, "pages": 0}
    items, total, pages = await list_audit_logs(
        db,
        project_id=project_id,
        page=page,
        size=size,
        entity_type=entity_type,
        action=action,
    )
    return {
        "items": [row(entry, AuditLogResponse) for entry in items],
        "total": total,
        "page": page,
        "size": size,
        "pages": pages,
    }
