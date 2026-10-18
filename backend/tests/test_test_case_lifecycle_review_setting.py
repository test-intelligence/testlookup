"""W28 TCM-02: the per-project "Require review before Active" setting.

Owner decision OR-04: with review OFF (the default for every project) a case
moves Draft <-> Active directly; with review ON the governed flow of today
applies, plus a QA lead / admin may approve a case they wrote, with a reason,
and an approved / active case edited after its approval may be sent back to
review. A VIEWER changes nothing. Deprecate / Archive / Reinstate stay QA_LEAD
or above (the W28 contract's amendment: no widening for QA engineers).

``ALLOWED_TRANSITIONS`` itself is pinned, byte for byte, by
``test_test_case_lifecycle_state_machine.py`` (unedited); this file pins the
tables merged on top of it and every rule that depends on the setting.
"""
from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.models.postgres import TestCaseVersion as VersionRow
from app.services import test_case_lifecycle_service as lifecycle

# ── 1. the tables ────────────────────────────────────────────────────────────

REVIEW_ON = {
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
REVIEW_OFF = {
    **REVIEW_ON,
    "activate": {"approved": "active", "draft": "active", "needs_update": "active"},
    "revise": {"rejected": "draft", "needs_update": "draft", "active": "draft"},
}
REVIEW_ON_EDITED = {
    **REVIEW_ON,
    "request_review": {
        "draft": "review_requested",
        "rejected": "review_requested",
        "approved": "review_requested",
        "active": "review_requested",
    },
}


def _strings(table) -> dict[str, dict[str, str]]:
    return {action.value: dict(by_source) for action, by_source in table.items()}


def test_the_effective_tables_for_both_settings():
    on = lifecycle.transitions_for(review_required=True, edited_after_approval=False)
    off = lifecycle.transitions_for(review_required=False, edited_after_approval=False)
    edited = lifecycle.transitions_for(review_required=True, edited_after_approval=True)
    assert _strings(on) == REVIEW_ON
    assert _strings(off) == REVIEW_OFF
    assert _strings(edited) == REVIEW_ON_EDITED
    # With review off the marker is not acted on.
    assert _strings(
        lifecycle.transitions_for(review_required=False, edited_after_approval=True)
    ) == REVIEW_OFF
    assert _strings(lifecycle.ALLOWED_TRANSITIONS) == REVIEW_ON


def test_the_module_constants_are_never_mutated():
    before = (
        copy.deepcopy(lifecycle.ALLOWED_TRANSITIONS),
        copy.deepcopy(lifecycle.DIRECT_TRANSITIONS),
        copy.deepcopy(lifecycle.EDITED_AFTER_APPROVAL_TRANSITIONS),
    )
    for _ in range(2):
        for review in (True, False):
            for edited in (True, False):
                table = lifecycle.transitions_for(review_required=review, edited_after_approval=edited)
                table[lifecycle.LifecycleAction.ACTIVATE]["rejected"] = "active"  # a caller scribbling
    after = (
        lifecycle.ALLOWED_TRANSITIONS,
        lifecycle.DIRECT_TRANSITIONS,
        lifecycle.EDITED_AFTER_APPROVAL_TRANSITIONS,
    )
    assert before == after


def test_the_w28_vocabularies():
    assert lifecycle.RETIRED_STATES == ("deprecated", "archived")
    assert lifecycle.RUNNABLE_STATES == ("approved", "active")
    assert lifecycle.DEFINITION_FIELDS == ("steps", "expected_result", "preconditions")
    assert lifecycle.SELF_APPROVAL_SOURCES == ("review_requested", "under_review")
    # The amendment: the retire moves keep today's QA_LEAD floor.
    assert lifecycle.RETIRE_ROLES == lifecycle.LEAD_ROLES == {"QA_LEAD", "ADMIN"}
    assert lifecycle.EDITOR_ROLES == {"TESTER", "QA_ENGINEER", "QA_LEAD", "ADMIN"}


# ── 2. who is offered what (a literal grid) ──────────────────────────────────

_TESTER_ON = {
    "draft": ["request_review"],
    "review_requested": [],
    "under_review": [],
    "approved": ["activate", "flag_stale"],
    "active": ["flag_stale"],
    "rejected": ["request_review", "revise"],
    "needs_update": ["revise"],
    "deprecated": [],
    "archived": [],
}
_TESTER_OFF = {
    **_TESTER_ON,
    "draft": ["request_review", "activate"],
    "active": ["flag_stale", "revise"],
    "needs_update": ["activate", "revise"],
}
_ENGINEER_ON = {**_TESTER_ON, "review_requested": ["claim_review"]}
_ENGINEER_OFF = {**_TESTER_OFF, "review_requested": ["claim_review"]}
_LEAD_ON = {
    "draft": ["request_review", "deprecate"],
    "review_requested": ["claim_review", "withdraw_review"],
    "under_review": ["unclaim"],
    "approved": ["activate", "flag_stale", "deprecate"],
    "active": ["flag_stale", "deprecate"],
    "rejected": ["request_review", "revise", "deprecate"],
    "needs_update": ["revise", "deprecate"],
    "deprecated": ["reinstate", "archive"],
    "archived": ["reinstate"],
}
_LEAD_OFF = {
    **_LEAD_ON,
    "draft": ["request_review", "activate", "deprecate"],
    "active": ["flag_stale", "revise", "deprecate"],
    "needs_update": ["activate", "revise", "deprecate"],
}
_NOTHING = {state: [] for state in _TESTER_ON}

#: (role, review_required) -> state -> allowed actions, for an actor who is
#: neither the author nor the claimant (an under-review case is claimed by
#: someone else).
GRID = {
    ("VIEWER", True): _NOTHING,
    ("VIEWER", False): _NOTHING,
    ("TESTER", True): _TESTER_ON,
    ("TESTER", False): _TESTER_OFF,
    ("QA_ENGINEER", True): _ENGINEER_ON,
    ("QA_ENGINEER", False): _ENGINEER_OFF,
    ("QA_LEAD", True): _LEAD_ON,
    ("QA_LEAD", False): _LEAD_OFF,
    ("ADMIN", True): _LEAD_ON,
    ("ADMIN", False): _LEAD_OFF,
}


def _actor(role: str = "QA_LEAD", **kw) -> SimpleNamespace:
    return SimpleNamespace(id=kw.pop("id", None) or uuid.uuid4(), role=role, full_name=f"{role} person", username=role.lower(), **kw)


def _case(status: str, *, author_id=None, reviewer_id=None, marker=None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        title="Pay with a card",
        status=status,
        version=3,
        author_id=author_id or uuid.uuid4(),
        reviewer_id=reviewer_id,
        assignee_id=None,
        edited_since_approval_version=marker,
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


@pytest.mark.parametrize("state", list(_TESTER_ON))
@pytest.mark.parametrize("role, review", list(GRID))
def test_allowed_actions_grid(role, review, state):
    actor = _actor(role)
    reviewer = uuid.uuid4() if state == "under_review" else None
    case = _case(state, reviewer_id=reviewer)
    assert lifecycle.allowed_actions_for(case, actor, review_required=review) == GRID[(role, review)][state]


@pytest.mark.parametrize(
    "role, state, relation, review, expected",
    [
        # The author may withdraw their own request.
        ("TESTER", "review_requested", "author", True, ["withdraw_review"]),
        # A QA engineer claimant decides; as the author they still cannot approve.
        ("QA_ENGINEER", "under_review", "claimant", True, ["unclaim", "approve", "reject", "request_changes"]),
        ("QA_ENGINEER", "under_review", "author+claimant", True, ["unclaim", "reject", "request_changes"]),
        ("QA_ENGINEER", "review_requested", "author", True, ["claim_review", "withdraw_review"]),
        # A lead (or admin) who wrote the case may approve it, with a reason.
        ("QA_LEAD", "review_requested", "author", True, ["claim_review", "withdraw_review", "approve"]),
        ("QA_LEAD", "review_requested", "author", False, ["claim_review", "withdraw_review", "approve"]),
        ("ADMIN", "review_requested", "author", True, ["claim_review", "withdraw_review", "approve"]),
        ("QA_LEAD", "under_review", "author+claimant", True, ["unclaim", "approve", "reject", "request_changes"]),
        ("QA_LEAD", "under_review", "author", True, ["unclaim"]),  # claimed by someone else
        # Edited after approval: Request review is offered with review on only.
        ("TESTER", "active", "marker", True, ["request_review", "flag_stale"]),
        ("TESTER", "approved", "marker", True, ["request_review", "activate", "flag_stale"]),
        ("TESTER", "active", "marker", False, ["flag_stale", "revise"]),
        ("VIEWER", "active", "marker", True, []),
    ],
)
def test_allowed_actions_for_authors_claimants_and_the_marker(role, state, relation, review, expected):
    actor = _actor(role)
    author = actor.id if "author" in relation else None
    if "claimant" in relation:
        reviewer = actor.id
    elif state == "under_review":
        reviewer = uuid.uuid4()
    else:
        reviewer = None
    case = _case(state, author_id=author, reviewer_id=reviewer, marker=2 if relation == "marker" else None)
    assert lifecycle.allowed_actions_for(case, actor, review_required=review) == expected


def test_is_self_approval_needs_every_part():
    lead = _actor("QA_LEAD")
    assert lifecycle.is_self_approval(_case("review_requested", author_id=lead.id), lead)
    assert lifecycle.is_self_approval(_case("under_review", author_id=lead.id, reviewer_id=lead.id), lead)
    assert not lifecycle.is_self_approval(_case("under_review", author_id=lead.id, reviewer_id=uuid.uuid4()), lead)
    assert not lifecycle.is_self_approval(_case("review_requested"), lead)  # not the author
    assert not lifecycle.is_self_approval(_case("draft", author_id=lead.id), lead)
    engineer = _actor("QA_ENGINEER")
    assert not lifecycle.is_self_approval(_case("review_requested", author_id=engineer.id), engineer)
    # A fake without the fields is not one.
    assert not lifecycle.is_self_approval(SimpleNamespace(status="review_requested"), lead)


def test_reason_required_actions_for_this_actor():
    lead = _actor("QA_LEAD")
    assert lifecycle.reason_required_actions_for(_case("active"), lead, review_required=True) == [
        "flag_stale", "deprecate",
    ]
    assert lifecycle.reason_required_actions_for(
        _case("review_requested", author_id=lead.id), lead
    ) == ["approve"]
    tester = _actor("TESTER")
    assert lifecycle.reason_required_actions_for(_case("active"), tester, review_required=False) == ["flag_stale"]
    assert lifecycle.reason_required_actions_for(_case("draft"), tester, review_required=False) == []
    assert lifecycle.reason_required_actions_for(_case("active"), _actor("VIEWER")) == []


# ── 3. transition() under each setting ───────────────────────────────────────


class _Session:
    def __init__(self):
        self.added: list = []
        self.info: dict = {}
        self.flush = AsyncMock()

    def add(self, obj):
        self.added.append(obj)

    def versions(self):
        return [obj for obj in self.added if isinstance(obj, VersionRow)]


@pytest.fixture
def locked(monkeypatch):
    audit = AsyncMock()
    monkeypatch.setattr(lifecycle, "audit_event", audit)

    def lock(case, review=None):
        monkeypatch.setattr(lifecycle, "_lock_case", AsyncMock(return_value=case))
        monkeypatch.setattr(lifecycle, "_open_review", AsyncMock(return_value=review))
        return audit

    return lock


@pytest.mark.asyncio
async def test_review_off_a_tester_activates_a_draft_directly(locked):
    case = _case("draft")
    audit = locked(case)
    db = _Session()

    result = await lifecycle.transition(db, case.id, "activate", _actor("TESTER"), review_required=False)

    assert result.case is case and case.status == "active"
    assert case.version == 4
    (version,) = db.versions()
    assert version.status == "active" and version.change_type == "activate"
    audit.assert_awaited_once()
    snapshot = audit.await_args.kwargs["policy_snapshot"]
    assert snapshot["review_required"] is False
    assert snapshot["self_approval"] is False
    assert snapshot["decision_requires_current_claimant"] is True
    assert snapshot["lead_only_actions"] == ["archive", "deprecate", "reinstate"]
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_off_active_goes_back_to_draft(locked):
    case = _case("active")
    locked(case)
    await lifecycle.transition(_Session(), case.id, "revise", _actor("TESTER"), review_required=False)
    assert case.status == "draft"


@pytest.mark.asyncio
async def test_review_on_activate_from_draft_is_the_existing_409(locked):
    case = _case("draft")
    locked(case)
    db = _Session()
    with pytest.raises(HTTPException) as exc:
        await lifecycle.transition(db, case.id, "activate", _actor("TESTER"), review_required=True)
    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "current_state": "draft",
        "attempted_action": "activate",
        "allowed_actions": ["request_review"],
    }
    assert case.status == "draft" and db.added == []


@pytest.mark.asyncio
async def test_the_default_is_review_on(locked):
    case = _case("draft")
    locked(case)
    with pytest.raises(HTTPException) as exc:
        await lifecycle.transition(_Session(), case.id, "activate", _actor("TESTER"))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("review", [True, False])
async def test_a_viewer_is_refused_before_any_mutation(locked, review):
    case = _case("draft")
    locked(case)
    db = _Session()
    with pytest.raises(HTTPException) as exc:
        await lifecycle.transition(db, case.id, "request_review", _actor("VIEWER"), review_required=review)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Viewers cannot change test cases"
    assert case.status == "draft" and case.version == 3
    assert db.added == []
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["TESTER", "QA_ENGINEER"])
async def test_deprecate_stays_lead_only(locked, role):
    """The amendment: QA engineers are NOT given the retire moves."""
    case = _case("active")
    locked(case)
    with pytest.raises(HTTPException) as exc:
        await lifecycle.transition(
            _Session(), case.id, "deprecate", _actor(role), reason="obsolete", review_required=False
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "This action requires QA_LEAD or ADMIN"
    assert case.status == "active"


@pytest.mark.asyncio
async def test_a_lead_deprecates_with_a_reason(locked):
    case = _case("active")
    locked(case)
    await lifecycle.transition(
        _Session(), case.id, "deprecate", _actor("QA_LEAD"), reason="obsolete", review_required=False
    )
    assert case.status == "deprecated" and case.deprecation_reason == "obsolete"


# ── 4. a lead approves a case they wrote ─────────────────────────────────────


@pytest.mark.asyncio
async def test_a_lead_self_approves_from_review_requested_in_one_version(locked):
    lead = _actor("QA_LEAD")
    case = _case("review_requested", author_id=lead.id)
    review = SimpleNamespace(status="pending", reviewer_id=None, human_notes=None, reviewed_at=None)
    audit = locked(case, review)
    db = _Session()

    await lifecycle.transition(
        db, case.id, "approve", lead, reason="Reviewed with the team", review_required=True
    )

    assert case.status == "approved" and case.version == 4
    assert case.approved_by_id == lead.id and case.reviewer_id is None
    assert len(db.versions()) == 1  # no separate claim version
    assert review.status == "approved"
    assert review.reviewer_id == lead.id
    assert review.human_notes == "Reviewed with the team"
    assert review.reviewed_at is not None
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["policy_snapshot"]["self_approval"] is True
    assert audit.await_args.kwargs["policy_snapshot"]["author_cannot_approve"] == "unless_lead_with_reason"
    assert audit.await_args.kwargs["reason"] == "Reviewed with the team"


@pytest.mark.asyncio
async def test_a_self_approval_without_a_reason_is_refused_with_nothing_staged(locked):
    lead = _actor("ADMIN")
    case = _case("review_requested", author_id=lead.id)
    review = SimpleNamespace(status="pending", reviewer_id=None, human_notes=None, reviewed_at=None)
    audit = locked(case, review)
    db = _Session()
    with pytest.raises(HTTPException) as exc:
        await lifecycle.transition(db, case.id, "approve", lead, reason="  ", notes="")
    assert exc.value.status_code == 422
    assert exc.value.detail == "A reason is required to approve your own test case"
    assert case.status == "review_requested" and review.status == "pending"
    assert db.added == []
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_self_approval_accepts_the_notes_as_its_reason(locked):
    lead = _actor("QA_LEAD")
    case = _case("under_review", author_id=lead.id, reviewer_id=lead.id)
    review = SimpleNamespace(status="in_progress", reviewer_id=lead.id, human_notes=None, reviewed_at=None)
    audit = locked(case, review)
    await lifecycle.transition(_Session(), case.id, "approve", lead, notes="Checked every step")
    assert case.status == "approved"
    assert review.status == "approved" and review.human_notes == "Checked every step"
    assert audit.await_args.kwargs["reason"] == "Checked every step"


@pytest.mark.asyncio
async def test_a_qa_engineer_author_still_cannot_approve(locked):
    engineer = _actor("QA_ENGINEER")
    case = _case("under_review", author_id=engineer.id, reviewer_id=engineer.id)
    review = SimpleNamespace(status="in_progress", reviewer_id=engineer.id, human_notes=None, reviewed_at=None)
    locked(case, review)
    with pytest.raises(HTTPException) as exc:
        await lifecycle.transition(_Session(), case.id, "approve", engineer, reason="mine")
    assert exc.value.status_code == 403
    assert exc.value.detail == "A test case author cannot approve their own case"
    assert case.status == "under_review"


@pytest.mark.asyncio
async def test_a_lead_author_cannot_approve_a_review_someone_else_claimed(locked):
    lead = _actor("QA_LEAD")
    case = _case("under_review", author_id=lead.id, reviewer_id=uuid.uuid4())
    review = SimpleNamespace(status="in_progress", reviewer_id=case.reviewer_id, human_notes=None, reviewed_at=None)
    locked(case, review)
    with pytest.raises(HTTPException) as exc:
        await lifecycle.transition(_Session(), case.id, "approve", lead, reason="mine")
    assert exc.value.status_code == 403
    assert exc.value.detail == "Only the current claimant may decide"
    assert case.status == "under_review" and review.status == "in_progress"


# ── 5. the "edited after approval" marker ───────────────────────────────────


@pytest.mark.asyncio
async def test_request_review_from_active_needs_review_on_and_the_marker(locked):
    tester = _actor("TESTER")
    for review_required, marker in ((True, None), (False, 4)):
        case = _case("active", marker=marker)
        locked(case)
        with pytest.raises(HTTPException) as exc:
            await lifecycle.transition(_Session(), case.id, "request_review", tester, review_required=review_required)
        assert exc.value.status_code == 409, (review_required, marker)

    case = _case("active", marker=4)
    locked(case)
    result = await lifecycle.transition(_Session(), case.id, "request_review", tester, review_required=True)
    assert case.status == "review_requested"
    assert result.review is not None and result.review.status == "pending"
    # Leaving Approved / Active settles the marker.
    assert case.edited_since_approval_version is None


@pytest.mark.asyncio
async def test_approve_clears_the_marker(locked):
    claimant = _actor("QA_ENGINEER")
    case = _case("under_review", reviewer_id=claimant.id, marker=4)
    review = SimpleNamespace(status="in_progress", reviewer_id=claimant.id, human_notes=None, reviewed_at=None)
    locked(case, review)
    await lifecycle.transition(_Session(), case.id, "approve", claimant, notes="ok")
    assert case.status == "approved" and case.edited_since_approval_version is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role, state, action, reason, review, expected_status",
    [
        ("TESTER", "active", "revise", None, False, "draft"),
        ("QA_LEAD", "active", "deprecate", "obsolete", True, "deprecated"),
        ("TESTER", "active", "flag_stale", "stale", True, "needs_update"),
    ],
)
async def test_leaving_approved_or_active_clears_the_marker(locked, role, state, action, reason, review, expected_status):
    case = _case(state, marker=4)
    locked(case)
    await lifecycle.transition(_Session(), case.id, action, _actor(role), reason=reason, review_required=review)
    assert case.status == expected_status
    assert case.edited_since_approval_version is None


@pytest.mark.asyncio
async def test_activate_keeps_the_marker(locked):
    case = _case("approved", marker=4)
    locked(case)
    await lifecycle.transition(_Session(), case.id, "activate", _actor("TESTER"), review_required=True)
    assert case.status == "active" and case.edited_since_approval_version == 4


# ── 6. the availability contract ─────────────────────────────────────────────


def _availability(case, actor, review):
    return {
        item["action"]: (item["allowed"], item["blocked_reason"], item["requires_reason"])
        for item in lifecycle.transition_availability_for(case, actor, review_required=review)
    }


def test_availability_for_a_viewer_names_the_role_on_every_action():
    table = _availability(_case("draft"), _actor("VIEWER"), True)
    assert len(table) == len(lifecycle.LifecycleAction)
    assert {blocked for _, blocked, _ in table.values()} == {"Viewers cannot change test cases"}
    assert not any(allowed for allowed, _, _ in table.values())


def test_availability_blocked_reasons_and_requires_reason():
    tester = _actor("TESTER")
    on = _availability(_case("draft"), tester, True)
    assert on["activate"] == (False, "Review is required in this project: request a review first", False)
    assert on["request_review"] == (True, None, False)
    assert on["approve"] == (False, "Not available from draft", False)
    assert on["deprecate"] == (False, "Requires QA_LEAD or ADMIN", True)
    assert on["flag_stale"] == (False, "Not available from draft", True)

    off = _availability(_case("draft"), tester, False)
    assert off["activate"] == (True, None, False)

    needs_update = _availability(_case("needs_update"), tester, True)
    assert needs_update["activate"][1] == "Review is required in this project: request a review first"
    assert _availability(_case("active"), tester, True)["request_review"][1] == "Not available from active"

    requested = _availability(_case("review_requested"), tester, True)
    assert requested["claim_review"] == (False, "Insufficient permissions", False)
    assert requested["withdraw_review"] == (False, "Insufficient permissions", False)

    engineer = _actor("QA_ENGINEER")
    claimed_by_other = _availability(_case("under_review", reviewer_id=uuid.uuid4()), engineer, True)
    assert claimed_by_other["reject"] == (False, "Only the current claimant may perform this action", False)
    assert claimed_by_other["unclaim"] == (False, "Only the current claimant or a lead may unclaim", False)

    own = _availability(_case("under_review", author_id=engineer.id, reviewer_id=engineer.id), engineer, True)
    assert own["approve"] == (False, "Authors cannot approve their own test cases", False)

    lead = _actor("QA_LEAD")
    self_approve = _availability(_case("review_requested", author_id=lead.id), lead, True)
    assert self_approve["approve"] == (True, None, True)


# ── 7. the routes pass the project's setting ────────────────────────────────


@pytest.fixture
def case_client(monkeypatch):
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import test_management_cases as cases
    from app.routers.test_management_shared import require_case_access

    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    case = SimpleNamespace(
        **vars(_case("draft")),
        description=None, objective=None, preconditions=None, steps=None, parameters=None,
        expected_result=None, test_data=None, test_type="functional", priority="medium",
        severity="major", feature_area=None, suite_name=None, test_suite_id=None, tags=None,
        is_automated=False, automation_status="not_automated", test_fingerprint=None,
        ai_generated=False, ai_quality_score=None, ai_review_notes=None,
        estimated_duration_minutes=None, last_executed_at=None, last_execution_status=None,
        created_at=now, updated_at=now,
    )
    case.author_id = None
    state = {"review": False, "actor": _actor("TESTER")}
    review_read = AsyncMock(side_effect=lambda *_a, **_k: state["review"])
    transition = AsyncMock(return_value=lifecycle.LifecycleTransitionResult(case=case))
    monkeypatch.setattr(cases, "project_requires_case_review", review_read)
    monkeypatch.setattr(cases, "require_lifecycle_v2_enabled", AsyncMock())
    monkeypatch.setattr(cases, "transition", transition)
    monkeypatch.setattr(cases, "lifecycle_actions_for", AsyncMock(return_value=[]))
    monkeypatch.setattr(cases, "emit_staged_test_management_metrics", AsyncMock())

    app = FastAPI()
    app.include_router(cases.router, prefix="/api/v1/test-management")
    db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state["actor"]
    app.dependency_overrides[require_case_access] = lambda: case
    return TestClient(app, raise_server_exceptions=False), case, state, transition, review_read


@pytest.mark.parametrize("review", [True, False])
def test_the_transition_route_passes_the_projects_setting(case_client, review):
    http, case, state, transition, review_read = case_client
    state["review"] = review
    resp = http.post(
        f"/api/v1/test-management/cases/{case.id}/transition",
        json={"action": "activate", "expected_version": 3},
    )
    assert resp.status_code == 200, resp.text
    assert transition.await_args.kwargs["review_required"] is review
    assert transition.await_args.args[1] == case.id
    assert resp.json()["review_required"] is review
    assert str(review_read.await_args.args[1]) == str(case.project_id)


@pytest.mark.parametrize("review, activate", [(True, (False, "Review is required in this project: request a review first")), (False, (True, None))])
def test_the_allowed_transitions_route_answers_for_the_projects_setting(case_client, review, activate):
    http, case, state, _, _ = case_client
    state["review"] = review
    resp = http.get(f"/api/v1/test-management/cases/{case.id}/allowed-transitions")
    assert resp.status_code == 200, resp.text
    items = {item["action"]: item for item in resp.json()}
    assert (items["activate"]["allowed"], items["activate"]["blocked_reason"]) == activate
    assert items["deprecate"]["requires_reason"] is True
    assert items["activate"]["requires_reason"] is False
