"""``failure.reassign``: assign a failure to its suite owner (W15 AGT-03, §0.20).

Internal and reversible (owner decision D16). The effect is
``failed_test_reassignment_service.reassign_failure`` -- stage-only, and it
re-checks the approver's project authority itself (``_actor_has_authority``).
That service loads the test case by id ALONE and never compares its project
with a caller's, so every read here goes through the test case's run
``WHERE test_runs.project_id = action.project_id`` first (the IDOR boundary).

The previous assignee may be NULL or no longer eligible, so ``reassign_failure``
cannot always put it back: the undo is this adapter's own compare-and-set --
only while the current assignee is still the one the action set.

Commit-free (``STAGE_ONLY_SERVICES``); ``commits_effect = False``.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import TestCase, TestRun, TestStatus, TestSuiteOwner, User
from app.services.agent_action_adapters.adapter_base import ActionOutcome, ActionRefused
from app.services.agent_action_types import FailureReassignPayload
from app.services.failed_test_reassignment_service import ReassignmentError, reassign_failure

_FAILING = (TestStatus.FAILED.value, TestStatus.BROKEN.value)


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _payload(payload: Any) -> FailureReassignPayload:
    if not isinstance(payload, FailureReassignPayload):
        raise ActionRefused("payload_invalid")
    return payload


def _status_word(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _refusal_for(exc: ReassignmentError) -> str:
    """The service's 4xx as one of the catalogue's codes (nothing was flushed)."""
    if exc.status_code == 403:
        return "approver_not_authorized"
    if exc.status_code == 404:
        return "target_not_in_project"
    if str(exc.detail).startswith("Cannot reassign"):
        return "test_case_not_failing"
    return "assignee_no_longer_owner"


async def _project_test_case(
    db: AsyncSession, *, test_case_id: uuid.UUID, project_id: uuid.UUID, lock: bool = False,
) -> Optional[TestCase]:
    stmt = (
        select(TestCase)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(TestCase.id == test_case_id, TestRun.project_id == project_id)
    )
    if lock:
        stmt = stmt.with_for_update(of=TestCase)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _suite_owner(db: AsyncSession, *, project_id: uuid.UUID, suite_name: str) -> Optional[uuid.UUID]:
    return (
        await db.execute(
            select(TestSuiteOwner.owner_user_id).where(
                TestSuiteOwner.project_id == project_id,
                TestSuiteOwner.suite_name == suite_name,
            )
        )
    ).scalar_one_or_none()


class FailureReassignAdapter:
    action_type = "failure.reassign"
    commits_effect = False

    async def preflight(self, db: AsyncSession, *, action: Any, payload: Any, approver: Any) -> None:
        p = _payload(payload)
        test_case_id = _as_uuid(p.test_case_id)
        assignee = _as_uuid(p.assignee_user_id)
        if test_case_id is None:
            raise ActionRefused("target_not_in_project")
        tc = await _project_test_case(db, test_case_id=test_case_id, project_id=action.project_id)
        if tc is None:
            raise ActionRefused("target_not_in_project")
        if _status_word(tc.status) not in _FAILING:
            raise ActionRefused("test_case_not_failing")
        owner = await _suite_owner(db, project_id=action.project_id, suite_name=p.suite_name)
        if assignee is None or owner is None or owner != assignee:
            raise ActionRefused("assignee_no_longer_owner")
        if tc.assigned_to_user_id == assignee:
            raise ActionRefused("already_assigned")

    async def execute(self, db: AsyncSession, *, action: Any, payload: Any, approver: Any) -> ActionOutcome:
        p = _payload(payload)
        test_case_id = _as_uuid(p.test_case_id)
        assignee = _as_uuid(p.assignee_user_id)
        if test_case_id is None or assignee is None:
            raise ActionRefused("target_not_in_project")
        # Locked inside the project: the previous assignee read here is the one
        # the service overwrites (its own SELECT returns this identity-mapped row).
        tc = await _project_test_case(
            db, test_case_id=test_case_id, project_id=action.project_id, lock=True,
        )
        if tc is None:
            raise ActionRefused("target_not_in_project")
        previous = tc.assigned_to_user_id
        if previous == assignee:
            # reassign_failure would answer a silent no-op: nothing to record or undo.
            raise ActionRefused("already_assigned")
        try:
            await reassign_failure(db, test_case_id, assignee, approver)
        except ReassignmentError as exc:
            raise ActionRefused(_refusal_for(exc)) from exc
        person = (
            await db.execute(select(User.full_name, User.username).where(User.id == assignee))
        ).one_or_none()
        label = (person.full_name or person.username) if person is not None else "assignee"
        facts = {
            "test_case_id": str(test_case_id),
            "previous_assignee_user_id": str(previous) if previous else None,
            "assigned_to_user_id": str(assignee),
        }
        return ActionOutcome(
            result_payload=dict(facts),
            rollback_payload=dict(facts),
            result_link={
                "kind": "assignee", "id": str(assignee), "label": label,
                "href": f"/runs/{tc.test_run_id}?tab=tests",
            },
        )

    async def rollback(
        self, db: AsyncSession, *, action: Any, rollback_payload: Any, actor: Any,
    ) -> ActionOutcome:
        data = rollback_payload if isinstance(rollback_payload, dict) else {}
        test_case_id = _as_uuid(data.get("test_case_id"))
        set_to = _as_uuid(data.get("assigned_to_user_id"))
        raw_previous = data.get("previous_assignee_user_id")
        previous = _as_uuid(raw_previous) if raw_previous else None
        if test_case_id is None or set_to is None or (raw_previous and previous is None):
            raise ActionRefused("rollback_target_changed")
        tc = await _project_test_case(
            db, test_case_id=test_case_id, project_id=action.project_id, lock=True,
        )
        if tc is None:
            raise ActionRefused("target_not_in_project")
        facts = {
            "test_case_id": str(test_case_id),
            "restored_assignee_user_id": str(previous) if previous else None,
        }
        if tc.assigned_to_user_id == previous:
            # Already restored (a repeated Undo after a lost commit): idempotent.
            return ActionOutcome(result_payload=facts)
        if tc.assigned_to_user_id != set_to:
            # Somebody reassigned it since: theirs wins.
            raise ActionRefused("rollback_target_changed")
        tc.assigned_to_user_id = previous
        await db.flush()
        return ActionOutcome(result_payload=facts)
