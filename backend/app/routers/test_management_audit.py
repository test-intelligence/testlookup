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
