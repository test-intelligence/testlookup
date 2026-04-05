from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import TestCaseAuditLog
from app.routers.test_management_shared import paginate_scalars


async def list_audit_logs(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    page: int,
    size: int,
    entity_type: Optional[str] = None,
    action: Optional[str] = None,
):
    query = select(TestCaseAuditLog)
    if project_id:
        query = query.where(TestCaseAuditLog.project_id == project_id)
    if entity_type:
        query = query.where(TestCaseAuditLog.entity_type == entity_type)
    if action:
        query = query.where(TestCaseAuditLog.action == action)
    return await paginate_scalars(db, query.order_by(TestCaseAuditLog.created_at.desc()), page, size)
