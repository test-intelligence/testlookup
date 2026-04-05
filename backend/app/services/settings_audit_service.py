"""
Settings Audit Service — logs who changed what configuration and when.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import SettingsAuditLog, User

logger = logging.getLogger("services.settings_audit")


async def log_settings_change(
    db: AsyncSession,
    setting_key: str,
    action: str,
    actor: Optional[User],
    changed_fields: Optional[list[str]] = None,
) -> None:
    """Log a settings change event."""
    entry = SettingsAuditLog(
        setting_key=setting_key,
        action=action,
        actor_id=actor.id if actor else None,
        actor_name=getattr(actor, "username", None) or getattr(actor, "full_name", None) if actor else None,
        changed_fields=changed_fields,
    )
    db.add(entry)
    # Don't commit — caller manages the transaction


async def get_settings_audit_log(
    db: AsyncSession,
    setting_key: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Retrieve recent audit entries, optionally filtered by setting key."""
    stmt = (
        select(SettingsAuditLog)
        .order_by(SettingsAuditLog.created_at.desc())
        .limit(limit)
    )
    if setting_key:
        stmt = stmt.where(SettingsAuditLog.setting_key == setting_key)

    result = await db.execute(stmt)
    entries = result.scalars().all()

    return [
        {
            "id": str(e.id),
            "setting_key": e.setting_key,
            "action": e.action,
            "actor_id": str(e.actor_id) if e.actor_id else None,
            "actor_name": e.actor_name,
            "changed_fields": e.changed_fields,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]
