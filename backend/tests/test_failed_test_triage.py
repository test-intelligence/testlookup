"""Tests for the triage-status workflow.

Pins the two contracts:

1. **Authorisation**: the assignee or a QA_LEAD/ADMIN on the project can
   update triage status; anyone else 403s.

2. **State transitions**: any of the resolved states removes the row
   from the inbox (the router's WHERE clause; tested implicitly via
   the field write). PASSED/SKIPPED test cases can't be triaged.

DB is fully mocked.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.postgres import TestStatus, UserRole


def _scalar(value):
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=value)
    return res


def _user(*, uid=None, role=UserRole.QA_ENGINEER.value):
    return SimpleNamespace(id=uid or uuid.uuid4(), role=role)


def _test_case(*, status=TestStatus.FAILED.value, assignee=None,
               triage_status="PENDING_REVIEW", triage_notes=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        suite_name="Smoke",
        status=status,
        test_run_id=uuid.uuid4(),
        assigned_to_user_id=assignee,
        triage_status=triage_status,
        triage_notes=triage_notes,
        triage_updated_at=None,
        triage_updated_by_user_id=None,
    )


# ── update_triage_status ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_triage_rejects_unknown_status():
    from app.services.failed_test_triage_service import (
        TriageError, update_triage_status,
    )
    actor = _user()
    db = AsyncMock()

    with pytest.raises(TriageError) as exc:
        await update_triage_status(db, uuid.uuid4(), "BOGUS", None, actor)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_triage_rejects_passed_status():
    """PASSED rows aren't part of the triage workflow."""
    from app.services.failed_test_triage_service import (
        TriageError, update_triage_status,
    )
    tc = _test_case(status=TestStatus.PASSED.value)
    actor = _user()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_scalar(tc)])

    with pytest.raises(TriageError) as exc:
        await update_triage_status(db, tc.id, "REVIEWED_APPROVED", None, actor)
    assert exc.value.status_code == 422
    assert "status" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_triage_assignee_can_update_own():
    """The assignee themselves can update without a manager role."""
    from app.services.failed_test_triage_service import update_triage_status

    actor = _user(role=UserRole.QA_ENGINEER.value)
    tc = _test_case(assignee=actor.id)
    project_id = uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar(tc),
        _scalar(project_id),
        # No ProjectMember lookup — assignee shortcut hits first.
    ])
    db.flush = AsyncMock()

    result = await update_triage_status(db, tc.id, "REVIEWED_APPROVED", "looks fine", actor)
    assert result.triage_status == "REVIEWED_APPROVED"
    assert result.triage_notes == "looks fine"
    assert result.triage_updated_by_user_id == actor.id


@pytest.mark.asyncio
async def test_triage_qa_engineer_member_blocked_on_someone_elses_failure():
    """A QA_ENGINEER on the project can't update a failure assigned to
    someone else — that's the QA Lead's job."""
    from app.services.failed_test_triage_service import (
        TriageError, update_triage_status,
    )
    actor = _user(role=UserRole.QA_ENGINEER.value)
    other_assignee = uuid.uuid4()
    tc = _test_case(assignee=other_assignee)
    project_id = uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar(tc),
        _scalar(project_id),
        _scalar(UserRole.QA_ENGINEER.value),  # actor's role on project
    ])

    with pytest.raises(TriageError) as exc:
        await update_triage_status(db, tc.id, "REVIEWED_APPROVED", None, actor)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_triage_qa_lead_can_update_anyones():
    from app.services.failed_test_triage_service import update_triage_status

    actor = _user(role=UserRole.QA_ENGINEER.value)  # global role doesn't matter
    other_assignee = uuid.uuid4()
    tc = _test_case(assignee=other_assignee)
    project_id = uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar(tc),
        _scalar(project_id),
        _scalar(UserRole.QA_LEAD.value),  # actor is QA_LEAD on project
    ])
    db.flush = AsyncMock()

    result = await update_triage_status(db, tc.id, "DEFECT_CREATED", "BUG-42", actor)
    assert result.triage_status == "DEFECT_CREATED"
    assert result.triage_notes == "BUG-42"


@pytest.mark.asyncio
async def test_triage_instance_admin_bypasses_project_membership():
    from app.services.failed_test_triage_service import update_triage_status

    actor = _user(role=UserRole.ADMIN.value)
    other_assignee = uuid.uuid4()
    tc = _test_case(assignee=other_assignee)
    project_id = uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar(tc),
        _scalar(project_id),
        # Instance ADMIN bypass — no ProjectMember row read.
    ])
    db.flush = AsyncMock()

    result = await update_triage_status(db, tc.id, "WONT_FIX", "deprecated", actor)
    assert result.triage_status == "WONT_FIX"


@pytest.mark.asyncio
async def test_triage_accepts_new_automation_and_flaky_statuses():
    """The 2026-05-18 status additions: AUTOMATION_SCRIPT_ISSUE +
    FLAKY_TEST. These were added after the initial enum shipped; pin
    them so a future "tidy the enum" PR doesn't silently drop them."""
    from app.services.failed_test_triage_service import update_triage_status

    actor = _user(role=UserRole.QA_LEAD.value)
    tc = _test_case(assignee=uuid.uuid4())

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar(tc),
        _scalar(uuid.uuid4()),
        _scalar(UserRole.QA_LEAD.value),
    ])
    db.flush = AsyncMock()

    result = await update_triage_status(db, tc.id, "AUTOMATION_SCRIPT_ISSUE", None, actor)
    assert result.triage_status == "AUTOMATION_SCRIPT_ISSUE"

    # And FLAKY_TEST in a second call (fresh fixtures because the
    # AsyncMock side_effect is single-pass).
    tc2 = _test_case(assignee=uuid.uuid4())
    db2 = AsyncMock()
    db2.execute = AsyncMock(side_effect=[
        _scalar(tc2),
        _scalar(uuid.uuid4()),
        _scalar(UserRole.QA_LEAD.value),
    ])
    db2.flush = AsyncMock()
    result2 = await update_triage_status(db2, tc2.id, "FLAKY_TEST", "intermittent timeout", actor)
    assert result2.triage_status == "FLAKY_TEST"


@pytest.mark.asyncio
async def test_triage_noop_when_status_and_notes_unchanged():
    """Re-applying the same status+notes shouldn't fire a flush — keeps
    the audit log clean of phantom edits."""
    from app.services.failed_test_triage_service import update_triage_status

    actor = _user(role=UserRole.ADMIN.value)
    tc = _test_case(
        assignee=uuid.uuid4(),
        triage_status="REVIEWED_APPROVED",
        triage_notes="seen it",
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar(tc),
        _scalar(uuid.uuid4()),
    ])
    db.flush = AsyncMock()

    result = await update_triage_status(db, tc.id, "REVIEWED_APPROVED", "seen it", actor)
    assert result.triage_status == "REVIEWED_APPROVED"
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_triage_notes_empty_string_clears_field():
    """Empty-string notes are treated as 'clear' so the UI doesn't need a
    separate clear affordance."""
    from app.services.failed_test_triage_service import update_triage_status

    actor = _user(role=UserRole.ADMIN.value)
    tc = _test_case(
        assignee=uuid.uuid4(),
        triage_status="DEFECT_CREATED",
        triage_notes="BUG-1",
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar(tc),
        _scalar(uuid.uuid4()),
    ])
    db.flush = AsyncMock()

    result = await update_triage_status(db, tc.id, "REVIEWED_APPROVED", "", actor)
    assert result.triage_status == "REVIEWED_APPROVED"
    assert result.triage_notes is None
