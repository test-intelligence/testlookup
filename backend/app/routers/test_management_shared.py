from __future__ import annotations

import uuid
from typing import Optional

import structlog
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import TestCaseAuditLog, User

logger = structlog.get_logger(__name__)


async def audit_event(
    db: AsyncSession,
    entity_type: str,
    entity_id: uuid.UUID,
    project_id: Optional[uuid.UUID],
    action: str,
    actor: User,
    old_values: Optional[dict] = None,
    new_values: Optional[dict] = None,
    details: Optional[str] = None,
) -> None:
    log = TestCaseAuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        project_id=project_id,
        action=action,
        actor_id=actor.id,
        actor_name=actor.full_name or actor.username,
        old_values=old_values,
        new_values=new_values,
        details=details,
    )
    db.add(log)


def row(model_instance, schema_class):
    """Map ORM instance to Pydantic schema using from_attributes."""
    return schema_class.model_validate(model_instance)


async def get_or_404(db: AsyncSession, model, entity_id, detail: str):
    instance = await db.get(model, entity_id)
    if not instance:
        raise HTTPException(status_code=404, detail=detail)
    return instance


async def paginate_scalars(db: AsyncSession, query, page: int, size: int):
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    result = await db.execute(query.offset((page - 1) * size).limit(size))
    return result.scalars().all(), total, -(-total // size)


def apply_model_updates(model_instance, values: dict) -> None:
    for field, value in values.items():
        setattr(model_instance, field, value)
