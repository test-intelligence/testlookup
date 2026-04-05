"""
Access Audit Service — logs user role and project membership changes.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AccessAuditLog, User

logger = logging.getLogger("services.access_audit")


async def log_access_change(
    db: AsyncSession,
    action: str,
    actor: Optional[User],
    target_user_id: Optional[uuid.UUID] = None,
    project_id: Optional[uuid.UUID] = None,
    before_value: Optional[dict] = None,
    after_value: Optional[dict] = None,
) -> None:
    """Log an access change event. Does not commit — caller manages transaction."""
    entry = AccessAuditLog(
        actor_user_id=actor.id if actor else None,
        actor_name=getattr(actor, "username", None) if actor else None,
        target_user_id=target_user_id,
        project_id=project_id,
        action=action,
        before_value=before_value,
        after_value=after_value,
    )
    db.add(entry)


async def get_access_audit_log(
    db: AsyncSession,
    target_user_id: Optional[uuid.UUID] = None,
    project_id: Optional[uuid.UUID] = None,
    limit: int = 50,
) -> list[dict]:
    """Retrieve recent access audit entries."""
    stmt = (
        select(AccessAuditLog)
        .order_by(AccessAuditLog.created_at.desc())
        .limit(limit)
    )
    if target_user_id:
        stmt = stmt.where(AccessAuditLog.target_user_id == target_user_id)
    if project_id:
        stmt = stmt.where(AccessAuditLog.project_id == project_id)

    result = await db.execute(stmt)
    return [
        {
            "id": str(e.id),
            "actor_user_id": str(e.actor_user_id) if e.actor_user_id else None,
            "actor_name": e.actor_name,
            "target_user_id": str(e.target_user_id) if e.target_user_id else None,
            "project_id": str(e.project_id) if e.project_id else None,
            "action": e.action,
            "before_value": e.before_value,
            "after_value": e.after_value,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in result.scalars().all()
    ]
