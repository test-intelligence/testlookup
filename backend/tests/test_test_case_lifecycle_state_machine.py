"""Committed S0/S2 lifecycle contract tests.

These tests deliberately target the single transition owner.  They avoid the
old mock-only mistake of asserting that an arbitrary string was assigned by
pinning the complete transition table, review ownership, row locking, immutable
snapshot shape, audit inputs, and metric emission as separate invariants.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import UniqueConstraint

from app.core.metrics import test_case_transitions_total
from app.models.postgres import (
    TestCaseLifecycleState as LifecycleState,
    TestCaseVersion as VersionModel,
    UserRole,
)
from app.services import test_case_lifecycle_service as lifecycle
from app.services import test_management_metrics_service as lifecycle_metrics


EXPECTED_TRANSITIONS = {
    "request_review": {"draft": "review_requested", "rejected": "review_requested"},
    "claim_review": {"review_requested": "under_review"},
    "withdraw_review": {"review_requested": "draft"},
    "unclaim": {"under_review": "review_requested"},
    "approve": {"under_review": "approved"},
    "reject": {"under_review": "rejected"},
    "request_changes": {"under_review": "draft"},
    "activate": {"approved": "active"},
    "flag_stale": {"approved": "needs_update", "active": "needs_update"},
    "revise": {"rejected": "draft", "needs_update": "draft"},
    "deprecate": {
        "draft": "deprecated",
        "rejected": "deprecated",
        "approved": "deprecated",
        "active": "deprecated",
        "needs_update": "deprecated",
    },
    "reinstate": {"deprecated": "draft", "archived": "draft"},
    "archive": {"deprecated": "archived"},
}


def _actor(*, role: UserRole = UserRole.QA_LEAD, user_id=None):
    return SimpleNamespace(
        id=user_id or uuid.uuid4(),
        role=role,
        full_name="Lifecycle Reviewer",
        username="reviewer",
    )


def _case(status: str, *, actor=None, author_id=None, reviewer_id=None):
    actor = actor or _actor()
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        status=status,
        version=3,
        author_id=author_id or uuid.uuid4(),
        reviewer_id=reviewer_id,
        lifecycle_state_changed_at=None,
        approved_at=None,
        approved_by_id=None,
        needs_update_reason=None,
        deprecation_reason=None,
        deprecated_at=None,
        deprecated_by_id=None,
        archived_at=None,
        archived_by_id=None,
    )


def _table_as_strings() -> dict[str, dict[str, str]]:
    return {
        action.value: dict(by_source)
        for action, by_source in lifecycle.ALLOWED_TRANSITIONS.items()
    }


def test_transition_table_is_the_settled_contract_and_every_state_is_reachable():
    assert _table_as_strings() == EXPECTED_TRANSITIONS
    declared = {state.value for state in LifecycleState}
    emitted = {
        value
        for by_source in lifecycle.ALLOWED_TRANSITIONS.values()
        for value in (*by_source.keys(), *by_source.values())
    }
    assert emitted == declared


@pytest.mark.parametrize("action", list(lifecycle.LifecycleAction))
def test_every_action_has_at_least_one_transition(action):
    assert lifecycle.ALLOWED_TRANSITIONS[action], action.value


def test_allowed_actions_enforces_role_claimant_and_self_approval_rules():
    claimant = _actor(role=UserRole.QA_ENGINEER)
    under_review = _case("under_review", actor=claimant, reviewer_id=claimant.id)
    assert set(lifecycle.allowed_actions_for(under_review, claimant)) == {
        "unclaim", "approve", "reject", "request_changes",
    }

    author_claimant = _actor(role=UserRole.QA_ENGINEER)
    authored = _case(
        "under_review",
        actor=author_claimant,
        author_id=author_claimant.id,
        reviewer_id=author_claimant.id,
    )
    assert "approve" not in lifecycle.allowed_actions_for(authored, author_claimant)
    assert "reject" in lifecycle.allowed_actions_for(authored, author_claimant)

    other_reviewer = _actor(role=UserRole.QA_LEAD)
    other_actions = set(lifecycle.allowed_actions_for(under_review, other_reviewer))
    assert not ({"approve", "reject", "request_changes"} & other_actions)
    assert "unclaim" in other_actions


def test_only_leads_receive_destructive_actions():
    tester = _actor(role=UserRole.TESTER)
    lead = _actor(role=UserRole.QA_LEAD)
    active = _case("active")
    assert "deprecate" not in lifecycle.allowed_actions_for(active, tester)
    assert "deprecate" in lifecycle.allowed_actions_for(active, lead)
    assert "flag_stale" in lifecycle.allowed_actions_for(active, tester)


@pytest.mark.asyncio
async def test_flag_off_advertises_no_direct_lifecycle_actions(monkeypatch):
    from app.services import feature_flags

    actor = _actor(role=UserRole.QA_LEAD)
    case = _case("active")
    enabled = AsyncMock(return_value=False)
    monkeypatch.setattr(feature_flags, "is_enabled", enabled)

    assert await lifecycle.lifecycle_actions_for(AsyncMock(), case, actor) == []
    enabled.assert_awaited_once()


@pytest.mark.asyncio
async def test_lock_case_uses_select_for_update():
    row = _case("draft")
    captured = []

    class Result:
        def scalar_one_or_none(self):
            return row

    db = SimpleNamespace(execute=AsyncMock(side_effect=lambda stmt: captured.append(stmt) or Result()))
    assert await lifecycle._lock_case(db, row.id) is row
    assert len(captured) == 1
    assert captured[0]._for_update_arg is not None


def test_version_numbers_are_unique_per_case():
    constraints = {
        tuple(column.name for column in constraint.columns)
        for constraint in VersionModel.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("test_case_id", "version") in constraints


def test_stage_snapshot_captures_every_authored_field():
    values = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "version": 7,
        "title": "Checkout",
        "description": "description",
        "objective": "objective",
        "preconditions": "logged in",
        "steps": [{"step_number": 1, "action": "pay", "expected_result": "paid"}],
        "parameters": [{"name": "region", "value": "us"}],
        "expected_result": "success",
        "test_data": "fixture",
        "test_type": "e2e",
        "priority": "critical",
        "severity": "blocker",
        "feature_area": "payments",
        "suite_name": "regression",
        "test_suite_id": uuid.uuid4(),
        "tags": ["p0"],
        "estimated_duration_minutes": 8,
        "is_automated": True,
        "automation_status": "automated",
        "test_fingerprint": "a" * 64,
        "status": "active",
    }
    case = SimpleNamespace(**values)
    added = []
    db = SimpleNamespace(add=added.append, info={})
    actor_id = uuid.uuid4()

    snapshot = lifecycle.stage_test_case_snapshot(
        db,
        case,
        actor_id=actor_id,
        change_type="updated",
        change_summary="priority changed",
        changed_fields=["priority"],
    )

    assert added == [snapshot]
    for field, expected in values.items():
        if field in {"id", "project_id"}:
            continue
        assert getattr(snapshot, field) == expected, field
    assert snapshot.test_case_id == case.id
    assert snapshot.changed_by_id == actor_id
    assert snapshot.changed_fields == ["priority"]
    assert db.info["test_management_state_metric_projects"] == {
        values["project_id"]
    }


@pytest.mark.asyncio
async def test_unknown_and_illegal_transitions_fail_without_writes(monkeypatch):
    db = AsyncMock()
    actor = _actor()
    with pytest.raises(HTTPException) as unknown:
        await lifecycle.transition(db, uuid.uuid4(), "invented", actor)
    assert unknown.value.status_code == 422
    db.execute.assert_not_awaited()

    case = _case("draft")
    monkeypatch.setattr(lifecycle, "_lock_case", AsyncMock(return_value=case))
    with pytest.raises(HTTPException) as illegal:
        await lifecycle.transition(db, case.id, "activate", actor)
    assert illegal.value.status_code == 409
    assert case.status == "draft"
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_deprecate_requires_reason_and_lead_role_before_mutating(monkeypatch):
    db = AsyncMock()
    lead = _actor(role=UserRole.QA_LEAD)
    case = _case("active")
    lock = AsyncMock(return_value=case)
    monkeypatch.setattr(lifecycle, "_lock_case", lock)

    with pytest.raises(HTTPException) as missing:
        await lifecycle.transition(db, case.id, "deprecate", lead)
    assert missing.value.status_code == 422
    lock.assert_not_awaited()

    tester = _actor(role=UserRole.TESTER)
    with pytest.raises(HTTPException) as forbidden:
        await lifecycle.transition(db, case.id, "deprecate", tester, reason="obsolete")
    assert forbidden.value.status_code == 403
    assert case.status == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "source"),
    [
        ("flag_stale", "active"),
        ("deprecate", "active"),
        ("reinstate", "deprecated"),
        ("archive", "deprecated"),
    ],
)
async def test_every_destructive_transition_requires_a_non_blank_reason(
    monkeypatch, action, source
):
    lock = AsyncMock(return_value=_case(source))
    monkeypatch.setattr(lifecycle, "_lock_case", lock)

    with pytest.raises(HTTPException) as exc:
        await lifecycle.transition(AsyncMock(), uuid.uuid4(), action, _actor(), reason="  ")

    assert exc.value.status_code == 422
    lock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "source"),
    [
        ("deprecate", "active"),
        ("reinstate", "deprecated"),
        ("archive", "deprecated"),
    ],
)
async def test_lead_only_actions_reject_qa_engineers(monkeypatch, action, source):
    case = _case(source)
    monkeypatch.setattr(lifecycle, "_lock_case", AsyncMock(return_value=case))

    with pytest.raises(HTTPException) as exc:
        await lifecycle.transition(
            AsyncMock(),
            case.id,
            action,
            _actor(role=UserRole.QA_ENGINEER),
            reason="Governance decision",
        )

    assert exc.value.status_code == 403
    assert case.status == source


def test_admin_has_the_same_destructive_action_surface_as_qa_lead():
    for source in ("active", "deprecated", "archived"):
        case = _case(source)
        lead_actions = set(
            lifecycle.allowed_actions_for(case, _actor(role=UserRole.QA_LEAD))
        )
        admin_actions = set(
            lifecycle.allowed_actions_for(case, _actor(role=UserRole.ADMIN))
        )
        assert admin_actions == lead_actions


@pytest.mark.asyncio
async def test_decision_is_restricted_to_current_claimant(monkeypatch):
    claimant = _actor(role=UserRole.QA_ENGINEER)
    other = _actor(role=UserRole.QA_LEAD)
    case = _case("under_review", reviewer_id=claimant.id)
    review = SimpleNamespace(reviewer_id=claimant.id, status="in_progress")
    monkeypatch.setattr(lifecycle, "_lock_case", AsyncMock(return_value=case))
    monkeypatch.setattr(lifecycle, "_open_review", AsyncMock(return_value=review))

    with pytest.raises(HTTPException) as exc:
        await lifecycle.transition(AsyncMock(), case.id, "approve", other)
    assert exc.value.status_code == 403
    assert case.status == "under_review"
    assert review.status == "in_progress"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.QA_LEAD, UserRole.ADMIN])
async def test_lead_or_admin_may_unclaim_another_review_without_deciding(
    monkeypatch, role
):
    claimant = _actor(role=UserRole.QA_ENGINEER)
    actor = _actor(role=role)
    case = _case("under_review", reviewer_id=claimant.id)
    review = SimpleNamespace(
        reviewer_id=claimant.id,
        status="in_progress",
        human_notes=None,
        reviewed_at=None,
    )
    db = AsyncMock()
    monkeypatch.setattr(lifecycle, "_lock_case", AsyncMock(return_value=case))
    monkeypatch.setattr(lifecycle, "_open_review", AsyncMock(return_value=review))
    monkeypatch.setattr(lifecycle, "stage_test_case_snapshot", Mock())
    monkeypatch.setattr(lifecycle, "audit_event", AsyncMock())

    await lifecycle.transition(db, case.id, "unclaim", actor)

    assert case.status == "review_requested"
    assert case.reviewer_id is None
    assert review.status == "pending"
    assert review.reviewer_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("actor_kind", ["author", "lead"])
async def test_withdraw_permission_matches_allowed_actions_and_transition_guard(
    monkeypatch, actor_kind
):
    author = _actor(role=UserRole.TESTER)
    actor = author if actor_kind == "author" else _actor(role=UserRole.QA_LEAD)
    case = _case("review_requested", author_id=author.id)
    review = SimpleNamespace(
        reviewer_id=None,
        status="pending",
        human_notes=None,
        reviewed_at=None,
    )
    assert "withdraw_review" in lifecycle.allowed_actions_for(case, actor)

    db = AsyncMock()
    monkeypatch.setattr(lifecycle, "_lock_case", AsyncMock(return_value=case))
    monkeypatch.setattr(lifecycle, "_open_review", AsyncMock(return_value=review))
    monkeypatch.setattr(lifecycle, "stage_test_case_snapshot", Mock())
    monkeypatch.setattr(lifecycle, "audit_event", AsyncMock())

    await lifecycle.transition(db, case.id, "withdraw_review", actor)

    assert case.status == "draft"
    assert review.status == "changes_requested"
    assert review.reviewed_at is not None


@pytest.mark.asyncio
async def test_transition_stages_snapshot_audit_flush_and_metric(monkeypatch):
    actor = _actor(role=UserRole.QA_LEAD)
    case = _case("approved")
    db = SimpleNamespace(info={}, add=Mock(), flush=AsyncMock())
    snapshot = Mock()
    audit = AsyncMock()
    monkeypatch.setattr(lifecycle, "_lock_case", AsyncMock(return_value=case))
    monkeypatch.setattr(lifecycle, "stage_test_case_snapshot", snapshot)
    monkeypatch.setattr(lifecycle, "audit_event", audit)
    metric = test_case_transitions_total.labels(
        str(case.project_id), "approved", "active", UserRole.QA_LEAD.value
    )
    before = metric._value.get()

    result = await lifecycle.transition(db, case.id, "activate", actor)

    assert result.case is case
    assert case.status == "active"
    assert case.version == 4
    snapshot.assert_called_once()
    audit.assert_awaited_once()
    kwargs = audit.await_args.kwargs
    assert kwargs["transition_from"] == "approved"
    assert kwargs["transition_to"] == "active"
    assert kwargs["policy_snapshot"]["decision_requires_current_claimant"] is True
    db.flush.assert_awaited_once()
    assert metric._value.get() == before
    await lifecycle_metrics.emit_staged_test_management_metrics(db)
    assert metric._value.get() == before + 1


@pytest.mark.asyncio
async def test_archived_case_can_be_reinstated_to_editable_draft(monkeypatch):
    actor = _actor(role=UserRole.ADMIN)
    case = _case("archived")
    case.archived_at = object()
    case.archived_by_id = uuid.uuid4()
    db = AsyncMock()
    monkeypatch.setattr(lifecycle, "_lock_case", AsyncMock(return_value=case))
    monkeypatch.setattr(lifecycle, "stage_test_case_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(lifecycle, "audit_event", AsyncMock())

    await lifecycle.transition(db, case.id, "reinstate", actor, reason="feature restored")
    assert case.status == "draft"
    assert case.archived_at is None
    assert case.archived_by_id is None


@pytest.mark.asyncio
async def test_legacy_review_action_auto_claims_before_decision(monkeypatch):
    actor = _actor(role=UserRole.QA_LEAD)
    case = _case("review_requested")
    monkeypatch.setattr(lifecycle, "_lock_case", AsyncMock(return_value=case))
    decide_result = lifecycle.LifecycleTransitionResult(case=case)
    transition = AsyncMock(side_effect=[decide_result, decide_result])
    monkeypatch.setattr(lifecycle, "transition", transition)

    await lifecycle.auto_claim_and_decide(
        AsyncMock(), case.id, "approve", actor, notes="looks good"
    )

    assert transition.await_count == 2
    assert transition.await_args_list[0].args[2] == lifecycle.LifecycleAction.CLAIM_REVIEW
    assert transition.await_args_list[1].args[2] == lifecycle.LifecycleAction.APPROVE
