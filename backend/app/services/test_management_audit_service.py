"""Append-only audit staging for test-management mutations.

This module lives in the service layer so lifecycle and catalog services do
not depend on router modules. Callers still own the transaction boundary.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import TestCaseAuditLog, User


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
    reason: Optional[str] = None,
    policy_snapshot: Optional[dict] = None,
    transition_from: Optional[str] = None,
    transition_to: Optional[str] = None,
) -> None:
    """Stage one immutable test-management audit row."""
    db.add(
        TestCaseAuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            project_id=project_id,
            action=action,
            actor_id=actor.id,
            actor_name=actor.full_name or actor.username,
            old_values=old_values,
            new_values=new_values,
            details=details,
            reason=reason,
            policy_snapshot=policy_snapshot,
            transition_from=transition_from,
            transition_to=transition_to,
        )
    )
