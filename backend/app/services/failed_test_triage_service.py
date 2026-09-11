"""Triage-status update workflow for auto-assigned failures (migration 0088).

Every FAILED/BROKEN ``TestCase`` starts at ``triage_status =
PENDING_REVIEW``. The ``/my-failures`` inbox filters to that state, so
once the assignee (or a QA Lead) moves it forward — ``REVIEWED_APPROVED``,
``DEFECT_CREATED``, ``WONT_FIX`` — the row drops off automatically.

Authorisation contract:

* The **assignee** themselves can update the status of their own row.
  This is the common case — "I looked at this, it's a known flake".
* A **QA_LEAD or ADMIN** on the project can update the status of any
  failure in the project. Manager-level override.
* Anyone else 403s. The frontend hides the affordance for those users
  but the server enforces the same rule independently.

The service writes an audit trail directly on the row (``triage_updated_at``
+ ``triage_updated_by_user_id``) — there's no separate history table
because the existing ``TestCaseAuditLog`` already captures every
column-level change via the SQLAlchemy event listener.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import _api_key_bound_project
from app.models.postgres import (
    ProjectMember,
    TestCase,
    TestRun,
    TestStatus,
    TriageStatus,
    User,
    UserRole,
)

logger = structlog.get_logger(__name__)


_MANAGER_ROLES = (UserRole.QA_LEAD.value, UserRole.ADMIN.value)


class TriageError(Exception):
    """HTTP-style domain error so the router doesn't need per-subclass
    mapping. ``status_code`` becomes the response status."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def _actor_can_triage(
    db: AsyncSession,
    actor_user: User,
    tc: TestCase,
    project_id: uuid.UUID,
) -> bool:
    """The actor can update this failure's triage iff they are:

    * The current assignee, OR
    * Instance ADMIN (break-glass), OR
    * A QA_LEAD / ADMIN on the project.

    A project-bound API key never passes outside its own project, not even as
    the assignee, whatever its owner's role (re-audit N20).
    """
    if _api_key_bound_project(actor_user) not in (None, project_id):
        raise TriageError(403, "This API key is restricted to a different project")
    if tc.assigned_to_user_id == actor_user.id:
        return True
    if actor_user.role == UserRole.ADMIN.value:
        return True
    role_row = (
        await db.execute(
            select(ProjectMember.role).where(
                ProjectMember.user_id == actor_user.id,
                ProjectMember.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    return role_row in _MANAGER_ROLES


async def update_triage_status(
    db: AsyncSession,
    test_case_id: uuid.UUID,
    new_status: str,
    notes: Optional[str],
    actor_user: User,
) -> TestCase:
    """Move a failure to a new ``triage_status``.

    Returns the updated ``TestCase``. Caller owns the transaction — this
    function flushes for ``updated_at`` materialisation but does not
    commit.
    """
    # Validate the status value against the canonical enum. The router
    # already pattern-validates the string, but defending in depth here
    # makes the service safely callable from non-router contexts
    # (Celery tasks, scripts).
    try:
        canonical_status = TriageStatus(new_status)
    except ValueError:
        raise TriageError(422, f"Unknown triage status {new_status!r}")

    tc = (
        await db.execute(
            select(TestCase).where(TestCase.id == test_case_id)
        )
    ).scalar_one_or_none()
    if tc is None:
        raise TriageError(404, "Test case not found")

    # Triage workflow is for FAILED/BROKEN. PASSED/SKIPPED carry the
    # default PENDING_REVIEW for column not-null, but updating their
    # state via this endpoint is a contract violation.
    status_value = tc.status.value if hasattr(tc.status, "value") else tc.status
    if status_value not in (TestStatus.FAILED.value, TestStatus.BROKEN.value):
        raise TriageError(
            422, f"Cannot triage — test case status is {status_value!r}",
        )

    project_id = (
        await db.execute(
            select(TestRun.project_id).where(TestRun.id == tc.test_run_id)
        )
    ).scalar_one_or_none()
    if project_id is None:
        raise TriageError(404, "Test run not found")

    if not await _actor_can_triage(db, actor_user, tc, project_id):
        raise TriageError(
            403,
            "Only the assigned user or a QA_LEAD/ADMIN on the project "
            "can update triage status",
        )

    # No-op short-circuit: setting the same value isn't an error, but
    # we don't want a noisy audit-log entry for it either.
    current_value = (
        tc.triage_status.value if hasattr(tc.triage_status, "value") else tc.triage_status
    )
    if current_value == canonical_status.value and (notes or None) == (tc.triage_notes or None):
        return tc

    tc.triage_status = canonical_status.value
    # Notes: explicit ``None`` clears the field (e.g. when the user
    # rolls back to PENDING_REVIEW). Empty-string is treated as "clear"
    # too so the UI doesn't need a separate clear affordance.
    tc.triage_notes = (notes or None)
    tc.triage_updated_at = datetime.now(timezone.utc)
    tc.triage_updated_by_user_id = actor_user.id
    await db.flush()

    logger.info(
        "failed_test_triage_updated",
        test_case_id=str(test_case_id),
        project_id=str(project_id),
        new_status=canonical_status.value,
        had_notes=bool(notes),
        actor_user_id=str(actor_user.id),
        actor_role=actor_user.role,
    )
    return tc
