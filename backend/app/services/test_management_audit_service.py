"""Append-only audit staging for test-management mutations.

This module lives in the service layer so lifecycle and catalog services do
not depend on router modules. Callers still own the transaction boundary.
"""
from __future__ import annotations

import contextvars
import functools
import uuid
from typing import Any, Awaitable, Callable, Optional, TypeVar

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
    """Stage one immutable test-management audit row (and its activity row)."""
    row_id = uuid.uuid4()
    db.add(
        TestCaseAuditLog(
            id=row_id,
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
    await _mirror_to_activity(
        db, entity_type, entity_id, project_id, action, actor, row_id,
        new_values=new_values, transition_from=transition_from, transition_to=transition_to,
    )


_SUMMARISED: contextvars.ContextVar[bool] = contextvars.ContextVar("tm_activity_summarised", default=False)
_F = TypeVar("_F", bound=Callable[..., Awaitable[Any]])


def activity_summarised_elsewhere(func: _F) -> _F:
    """Decorate a bulk path that records ONE summary activity row itself
    (``test_case.bulk_changed``, ``test_case.imported``): inside it the
    per-case audit rows are still written, but not mirrored into the feed,
    so a 500-case bulk edit is one feed row, not 501."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        token = _SUMMARISED.set(True)
        try:
            return await func(*args, **kwargs)
        finally:
            _SUMMARISED.reset(token)

    return wrapper  # type: ignore[return-value]


#: W21 ACT-01 slice (c): audit actions that mean a case came into being.
_CASE_CREATED = frozenset({"created", "imported", "ai_generated"})
#: Lifecycle actions with a name of their own in the feed. ``deprecate`` is
#: what ``DELETE /test-management/cases/{id}`` does (cases are never hard
#: deleted), so it reads as the delete.
_CASE_NAMED = {
    "approve": "test_case.approved",
    "generation_accepted": "test_case.approved",
    "reject": "test_case.rejected",
    "generation_rejected": "test_case.rejected",
    "ai_reviewed": "test_case.reviewed",
    "request_changes": "test_case.reviewed",
    "deprecate": "test_case.deleted",
}


def _activity_event(
    entity_type: str, action: str, transition_from: Optional[str], transition_to: Optional[str],
) -> Optional[str]:
    if entity_type == "test_case":
        if action in _CASE_CREATED:
            return "test_case.created"
        if action == "updated":
            return "test_case.updated"
        if action in _CASE_NAMED:
            return _CASE_NAMED[action]
        if transition_to and transition_to != transition_from:
            return "test_case.status_changed"
        return None
    if entity_type == "test_plan":
        if action in ("created", "ai_generated"):
            return "test_plan.created"
        if action == "updated":
            return "test_plan.updated"
    return None


async def _mirror_to_activity(
    db: AsyncSession,
    entity_type: str,
    entity_id: uuid.UUID,
    project_id: Optional[uuid.UUID],
    action: str,
    actor: User,
    source_id: uuid.UUID,
    *,
    new_values: Optional[dict],
    transition_from: Optional[str],
    transition_to: Optional[str],
) -> None:
    """W21 ACT-01 slice (c): the project feed's copy of a test-management audit row.

    Outcome mode on the caller's session: the audit row and its activity row
    commit (or roll back) together. ``source_table`` / ``source_id`` point at
    the audit row, so the feed and the case history never disagree.
    """
    event_type = _activity_event(entity_type, action, transition_from, transition_to)
    if event_type is None or project_id is None or _SUMMARISED.get():
        return
    from app.models.postgres import ManagedTestCase, TestPlan  # noqa: PLC0415
    from app.services.activity.service import ActorRef, record as record_activity  # noqa: PLC0415

    model = ManagedTestCase if entity_type == "test_case" else TestPlan
    label = None
    try:
        obj = await db.get(model, entity_id)
        label = getattr(obj, "title", None) or getattr(obj, "name", None)
    except Exception:  # a label lookup never blocks the mutation
        label = None
    changed = ", ".join(sorted((new_values or {}).keys())) or action.replace("_", " ")
    await record_activity(
        db,
        project_id=project_id,
        event_type=event_type,
        actor=ActorRef.from_user(actor),
        entity_id=entity_id,
        entity_label=label or str(entity_id)[:8],
        context={"changed": changed, "status": transition_to or "", "action": action},
        source_table="test_case_audit_logs",
        source_id=source_id,
    )
