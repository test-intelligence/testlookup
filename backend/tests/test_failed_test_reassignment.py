"""Tests for the failure-reassignment service + endpoint.

Pins the two contracts:

1. **Authorisation**: only QA_LEAD or ADMIN on the failure's project can
   reassign. Instance-ADMIN passes regardless of project membership.

2. **Eligibility**: the new assignee must be EITHER the resolved test
   suite owner OR a QA_ENGINEER project member. Anything else 422s —
   including another QA_LEAD (the auto-assigner already covers that
   surface via pool distribution).

Plus a happy-path test for ``get_reassignment_options`` so the picker
payload stays stable. DB is fully mocked.
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


def _first(value):
    res = MagicMock()
    res.first = MagicMock(return_value=value)
    return res


def _all(rows):
    res = MagicMock()
    res.all = MagicMock(return_value=rows)
    return res


def _user(*, uid=None, role=UserRole.QA_LEAD.value):
    return SimpleNamespace(id=uid or uuid.uuid4(), role=role)


def _test_case(*, suite_name="Smoke", status=TestStatus.FAILED.value, assignee=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        suite_name=suite_name,
        status=status,
        test_run_id=uuid.uuid4(),
        assigned_to_user_id=assignee,
    )


# ── reassign_failure ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reassign_rejects_non_qa_lead_actor():
    """A QA_ENGINEER trying to reassign gets 403, even if they're a member
    of the project."""
    from app.services.failed_test_reassignment_service import (
        ReassignmentError, reassign_failure,
    )

    tc = _test_case()
    actor = _user(role=UserRole.QA_ENGINEER.value)
    project_id = uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar(tc),                  # SELECT TestCase
        _scalar(project_id),           # SELECT TestRun.project_id
        _scalar(UserRole.QA_ENGINEER.value),  # SELECT ProjectMember.role
    ])

    with pytest.raises(ReassignmentError) as exc:
        await reassign_failure(db, tc.id, uuid.uuid4(), actor)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_reassign_rejects_passed_status():
    """The auto-assigner only ever touches FAILED/BROKEN; a reassign for a
    PASSED row is a contract violation surfaced as 422."""
    from app.services.failed_test_reassignment_service import (
        ReassignmentError, reassign_failure,
    )

    tc = _test_case(status=TestStatus.PASSED.value)
    actor = _user(role=UserRole.QA_LEAD.value)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_scalar(tc)])

    with pytest.raises(ReassignmentError) as exc:
        await reassign_failure(db, tc.id, uuid.uuid4(), actor)
    assert exc.value.status_code == 422
    assert "status" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_reassign_rejects_ineligible_new_assignee():
    """A new assignee who is neither the suite owner nor a QA_ENGINEER
    fails the 422 validation. Test: target user is a different QA_LEAD
    (which the auto-assigner already handles via the pool)."""
    from app.services.failed_test_reassignment_service import (
        ReassignmentError, reassign_failure,
    )

    tc = _test_case(suite_name="Smoke")
    actor = _user(role=UserRole.QA_LEAD.value)
    project_id = uuid.uuid4()
    new_assignee = uuid.uuid4()  # another QA_LEAD, not an engineer

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar(tc),                              # SELECT TestCase
        _scalar(project_id),                       # TestRun.project_id
        _scalar(UserRole.QA_LEAD.value),          # actor's ProjectMember.role
        _scalar(None),                             # no TestSuiteOwner row
        _scalar(UserRole.QA_LEAD.value),          # candidate's ProjectMember.role — not eligible
    ])

    with pytest.raises(ReassignmentError) as exc:
        await reassign_failure(db, tc.id, new_assignee, actor)
    assert exc.value.status_code == 422
    assert "suite owner" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_reassign_accepts_qa_engineer_member():
    from app.services.failed_test_reassignment_service import reassign_failure

    tc = _test_case(suite_name="Smoke")
    actor = _user(role=UserRole.QA_LEAD.value)
    project_id = uuid.uuid4()
    qa_engineer_id = uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar(tc),
        _scalar(project_id),
        _scalar(UserRole.QA_LEAD.value),
        _scalar(None),                            # no TestSuiteOwner row
        _scalar(UserRole.QA_ENGINEER.value),     # candidate is a QA_ENGINEER → eligible
    ])
    db.flush = AsyncMock()

    result = await reassign_failure(db, tc.id, qa_engineer_id, actor)
    assert result.assigned_to_user_id == qa_engineer_id
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_reassign_accepts_suite_owner():
    """When the candidate IS the suite owner, eligibility short-circuits
    without consulting their ProjectMember.role — the suite owner is the
    authoritative maintainer regardless of role."""
    from app.services.failed_test_reassignment_service import reassign_failure

    tc = _test_case(suite_name="Smoke")
    actor = _user(role=UserRole.QA_LEAD.value)
    project_id = uuid.uuid4()
    suite_owner_id = uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar(tc),
        _scalar(project_id),
        _scalar(UserRole.QA_LEAD.value),
        _scalar(suite_owner_id),    # suite owner = candidate → eligible
    ])
    db.flush = AsyncMock()

    result = await reassign_failure(db, tc.id, suite_owner_id, actor)
    assert result.assigned_to_user_id == suite_owner_id


@pytest.mark.asyncio
async def test_reassign_instance_admin_bypasses_project_membership():
    """Instance ADMIN doesn't need a ProjectMember row to reassign."""
    from app.services.failed_test_reassignment_service import reassign_failure

    tc = _test_case(suite_name="Smoke")
    actor = _user(role=UserRole.ADMIN.value)
    project_id = uuid.uuid4()
    qa_engineer_id = uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar(tc),
        _scalar(project_id),
        # No ProjectMember.role check — instance ADMIN short-circuits.
        _scalar(None),                              # no TestSuiteOwner
        _scalar(UserRole.QA_ENGINEER.value),       # candidate eligibility
    ])
    db.flush = AsyncMock()

    result = await reassign_failure(db, tc.id, qa_engineer_id, actor)
    assert result.assigned_to_user_id == qa_engineer_id


@pytest.mark.asyncio
async def test_reassign_to_existing_owner_is_noop():
    """Idempotent: setting the same owner doesn't flush."""
    from app.services.failed_test_reassignment_service import reassign_failure

    existing = uuid.uuid4()
    tc = _test_case(suite_name="Smoke", assignee=existing)
    actor = _user(role=UserRole.QA_LEAD.value)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar(tc),
        _scalar(uuid.uuid4()),
        _scalar(UserRole.QA_LEAD.value),
        _scalar(None),
        _scalar(UserRole.QA_ENGINEER.value),
    ])
    db.flush = AsyncMock()

    result = await reassign_failure(db, tc.id, existing, actor)
    assert result.assigned_to_user_id == existing
    db.flush.assert_not_awaited()


# ── get_reassignment_options ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_options_returns_owner_plus_engineers_deduped():
    """Picker payload: suite owner is rendered separately from the
    engineers list; if the suite owner happens to also be a QA_ENGINEER
    member, they only appear once (under suite_owner)."""
    from app.services.failed_test_reassignment_service import (
        get_reassignment_options,
    )

    tc_id = uuid.uuid4()
    project_id = uuid.uuid4()
    suite_owner_id = uuid.uuid4()
    other_eng_id = uuid.uuid4()
    actor = _user(role=UserRole.QA_LEAD.value)

    suite_owner_user = SimpleNamespace(
        id=suite_owner_id,
        email="owner@example.com",
        username="owner",
        full_name="Suite Owner",
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        # SELECT TestCase + project_id
        _first(SimpleNamespace(id=tc_id, suite_name="Smoke", project_id=project_id)),
        # actor authority — QA_LEAD ProjectMember
        _scalar(UserRole.QA_LEAD.value),
        # SELECT TestSuiteOwner.owner_user_id
        _scalar(suite_owner_id),
        # SELECT QA_ENGINEER members (include the suite owner; should dedupe)
        _all([
            SimpleNamespace(id=suite_owner_id, email="owner@example.com", username="owner", full_name="Suite Owner"),
            SimpleNamespace(id=other_eng_id,  email="eng@example.com",   username="eng",   full_name="Engineer"),
        ]),
    ])
    # ``db.get(User, owner_id)`` for the suite-owner payload hydration.
    db.get = AsyncMock(return_value=suite_owner_user)

    result = await get_reassignment_options(db, tc_id, actor)
    assert result["suite_owner"]["user_id"] == str(suite_owner_id)
    eng_ids = [e["user_id"] for e in result["qa_engineers"]]
    # Suite owner deduped out of the engineers list.
    assert str(suite_owner_id) not in eng_ids
    assert str(other_eng_id) in eng_ids


@pytest.mark.asyncio
async def test_get_options_rejects_non_qa_lead_actor():
    from app.services.failed_test_reassignment_service import (
        ReassignmentError, get_reassignment_options,
    )

    tc_id = uuid.uuid4()
    project_id = uuid.uuid4()
    actor = _user(role=UserRole.QA_ENGINEER.value)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _first(SimpleNamespace(id=tc_id, suite_name=None, project_id=project_id)),
        _scalar(UserRole.QA_ENGINEER.value),
    ])

    with pytest.raises(ReassignmentError) as exc:
        await get_reassignment_options(db, tc_id, actor)
    assert exc.value.status_code == 403
