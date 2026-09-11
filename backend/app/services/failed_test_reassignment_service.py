"""Reassignment of an auto-assigned failure to a different triager.

When a QA Lead receives an auto-assigned failure on the ``/my-failures``
inbox, they need to be able to delegate it to the right person —
typically:

* The **test suite owner** (per ``TestSuiteOwner``) when the suite has
  a dedicated maintainer who isn't the QA Lead.
* A **QA Engineer** project member when the QA Lead has triaged it down
  to a specific contributor.

This module enforces the authorisation contract:

* The *actor* (caller) must be a QA_LEAD or ADMIN on the failure's
  project. The user role check happens at request time — we don't
  trust the existing ``assigned_to_user_id`` to imply authority.
* The *new assignee* must be either (a) the resolved suite owner, or
  (b) a QA_ENGINEER project member. Anyone else is rejected with 422.

Out of scope:

* Reassigning to a different project's user — same as the auto-assigner,
  reassignment is project-scoped.
* Reassigning to QA_LEAD or ADMIN — the auto-assigner already covers
  that surface and adding it here would let a QA Lead bounce the
  failure to a peer indefinitely; if that's needed, do it via the
  audit-logged role-elevation flow.
"""
from __future__ import annotations

import uuid
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
    TestSuiteOwner,
    User,
    UserRole,
)

logger = structlog.get_logger(__name__)

# Roles authorised to perform a reassignment. Mirrors the assigner's
# ACTIONABLE_STATUSES philosophy — the rule is "you can move tickets if
# you're either responsible for triage or above it".
_REASSIGN_ACTOR_ROLES = (UserRole.QA_LEAD.value, UserRole.ADMIN.value)

# Roles eligible to RECEIVE a reassignment. QA_ENGINEER is the only
# membership role that opts in; the suite owner is treated as eligible
# regardless of role since they're the explicit maintainer.
_REASSIGN_ELIGIBLE_ROLES = (UserRole.QA_ENGINEER.value,)


class ReassignmentError(Exception):
    """Domain error surfaced to the router as 4xx. Carries an HTTP-style
    status_code so the router doesn't need a per-subclass mapping."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def _actor_has_authority(
    db: AsyncSession, actor_user: User, project_id: uuid.UUID,
) -> bool:
    """Return True iff the caller can reassign within ``project_id``.

    Instance-level ADMIN always passes — they're the break-glass admin.
    Otherwise, the caller's project-scoped role must be QA_LEAD or ADMIN.

    A project-bound API key never passes outside its own project, whatever its
    owner's role (re-audit N20): the ADMIN return below would otherwise hand a
    CI key every tenant's failures.
    """
    if _api_key_bound_project(actor_user) not in (None, project_id):
        raise ReassignmentError(403, "This API key is restricted to a different project")
    if actor_user.role == UserRole.ADMIN.value:
        return True
    row = (
        await db.execute(
            select(ProjectMember.role).where(
                ProjectMember.user_id == actor_user.id,
                ProjectMember.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    return row in _REASSIGN_ACTOR_ROLES


async def get_reassignment_options(
    db: AsyncSession,
    test_case_id: uuid.UUID,
    actor_user: User,
) -> dict:
    """Return the picker payload for the UI.

    Shape: ``{ suite_owner: {...} | None, qa_engineers: [{...}], project_id, suite_name }``.
    The frontend renders the suite_owner (when present) at the top of
    the picker and the qa_engineers list below. Both are de-duplicated;
    if the suite owner happens to also be a QA_ENGINEER member, they
    appear only once (under suite_owner).
    """
    tc = (
        await db.execute(
            select(
                TestCase.id,
                TestCase.suite_name,
                TestRun.project_id,
            )
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(TestCase.id == test_case_id)
        )
    ).first()
    if tc is None:
        raise ReassignmentError(404, "Test case not found")

    if not await _actor_has_authority(db, actor_user, tc.project_id):
        raise ReassignmentError(
            403, "Reassignment requires QA_LEAD or ADMIN on this project",
        )

    # Resolve the suite owner. Returns None when no explicit row exists;
    # the picker simply hides the "suite owner" entry in that case.
    suite_owner_payload: Optional[dict] = None
    if tc.suite_name:
        owner_row = (
            await db.execute(
                select(TestSuiteOwner.owner_user_id)
                .where(
                    TestSuiteOwner.project_id == tc.project_id,
                    TestSuiteOwner.suite_name == tc.suite_name,
                )
            )
        ).scalar_one_or_none()
        if owner_row is not None:
            user_row = await db.get(User, owner_row)
            if user_row is not None:
                suite_owner_payload = _user_payload(user_row)

    # QA_ENGINEER members of the project, de-duplicated against the
    # suite owner so the same name doesn't appear twice.
    engineer_rows = (
        await db.execute(
            select(User.id, User.email, User.username, User.full_name)
            .join(ProjectMember, ProjectMember.user_id == User.id)
            .where(
                ProjectMember.project_id == tc.project_id,
                ProjectMember.role.in_(_REASSIGN_ELIGIBLE_ROLES),
            )
            .order_by(User.full_name.asc(), User.email.asc())
        )
    ).all()
    suite_owner_id = (
        uuid.UUID(suite_owner_payload["user_id"]) if suite_owner_payload else None
    )
    qa_engineers = [
        {
            "user_id": str(r.id),
            "email": r.email,
            "username": r.username,
            "full_name": r.full_name,
        }
        for r in engineer_rows
        if r.id != suite_owner_id
    ]
    return {
        "project_id": str(tc.project_id),
        "suite_name": tc.suite_name,
        "suite_owner": suite_owner_payload,
        "qa_engineers": qa_engineers,
    }


async def reassign_failure(
    db: AsyncSession,
    test_case_id: uuid.UUID,
    new_assignee_user_id: uuid.UUID,
    actor_user: User,
) -> TestCase:
    """Move a FAILED/BROKEN TestCase to a new assignee.

    Enforces the full authorisation contract documented at module top.
    Caller owns the transaction (per backend/CLAUDE.md single-owner
    rule) — this function flushes but does not commit.
    """
    tc = (
        await db.execute(
            select(TestCase).where(TestCase.id == test_case_id)
        )
    ).scalar_one_or_none()
    if tc is None:
        raise ReassignmentError(404, "Test case not found")

    # The auto-assigner only ever writes FAILED/BROKEN, so reassignment
    # of anything else is a contract violation surfaced by the UI. 422
    # rather than 403 because the action isn't forbidden — it's invalid
    # for this row.
    status_value = tc.status.value if hasattr(tc.status, "value") else tc.status
    if status_value not in (TestStatus.FAILED.value, TestStatus.BROKEN.value):
        raise ReassignmentError(
            422, f"Cannot reassign — test case status is {status_value!r}",
        )

    # Resolve the failure's project_id via TestRun (TestCase doesn't
    # carry project_id directly — only test_run_id).
    project_id = (
        await db.execute(
            select(TestRun.project_id).where(TestRun.id == tc.test_run_id)
        )
    ).scalar_one_or_none()
    if project_id is None:
        raise ReassignmentError(404, "Test run not found")

    if not await _actor_has_authority(db, actor_user, project_id):
        raise ReassignmentError(
            403, "Reassignment requires QA_LEAD or ADMIN on this project",
        )

    # Validate the new assignee against the eligible set: either the
    # resolved suite owner OR a QA_ENGINEER project member.
    if not await _is_eligible_assignee(
        db, project_id, tc.suite_name, new_assignee_user_id,
    ):
        raise ReassignmentError(
            422,
            "New assignee must be the test suite owner or a QA_ENGINEER "
            "member of the project",
        )

    # No-op short-circuit: assigning to the existing owner is wasteful
    # but harmless. Return the row unchanged so the caller's UI flow
    # still navigates without firing a spurious "changed" toast.
    if tc.assigned_to_user_id == new_assignee_user_id:
        return tc

    tc.assigned_to_user_id = new_assignee_user_id
    await db.flush()
    logger.info(
        "failed_test_reassigned",
        test_case_id=str(test_case_id),
        project_id=str(project_id),
        new_assignee=str(new_assignee_user_id),
        actor_user_id=str(actor_user.id),
        actor_role=actor_user.role,
    )
    return tc


async def _is_eligible_assignee(
    db: AsyncSession,
    project_id: uuid.UUID,
    suite_name: Optional[str],
    candidate_user_id: uuid.UUID,
) -> bool:
    """Return True iff ``candidate_user_id`` is either the suite owner
    or a QA_ENGINEER project member."""
    if suite_name:
        suite_owner_id = (
            await db.execute(
                select(TestSuiteOwner.owner_user_id).where(
                    TestSuiteOwner.project_id == project_id,
                    TestSuiteOwner.suite_name == suite_name,
                )
            )
        ).scalar_one_or_none()
        if suite_owner_id == candidate_user_id:
            return True

    member_role = (
        await db.execute(
            select(ProjectMember.role).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == candidate_user_id,
            )
        )
    ).scalar_one_or_none()
    return member_role in _REASSIGN_ELIGIBLE_ROLES


def _user_payload(user: User) -> dict:
    return {
        "user_id": str(user.id),
        "email": user.email,
        "username": user.username,
        "full_name": user.full_name,
    }
