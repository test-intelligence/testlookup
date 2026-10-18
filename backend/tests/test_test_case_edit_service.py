"""W28 TCM-01: editing a test case saves a version and never changes its status.

Before W28 any PATCH of an approved / active case ran ``transition(FLAG_STALE,
reason="edited_after_approval")``: one typo fix took the case out of Active
and out of new plans (owner decision OR-04: "editing never changes the
status"). These tests drive the REAL ``update_managed_test_case`` with a
hand-built session and pin, per contract §2.1:

* the status is unchanged, the version is bumped once, one snapshot is staged
  with ``change_type`` (``updated`` by default) and no transition is awaited;
* the "edited after approval" marker (review on, approved / active, a real
  change of steps / expected result / preconditions, not already set), and
  that a draft edit reads nothing new (no project read);
* the Owner (``assignee_id``): validated like create's, snapshotted, cleared by
  an explicit null, no read when omitted;
* the version source (``person`` for a JWT actor, ``api`` for an API key), on
  the edit AND the lifecycle snapshot;
* ``case_response`` survives a hand-built session (the review read answers a
  mock, which is not ``True``).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core import deps
from app.models.postgres import TestCaseVersion as VersionRow
from app.models.schemas import ManagedTestCaseUpdate
from app.services import test_case_lifecycle_service as lifecycle
from app.services import test_management_service as svc

STEPS = [{"step_number": 1, "action": "Open checkout", "expected_result": "The cart shows"}]


def _case(status: str = "active", **overrides) -> SimpleNamespace:
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    values = dict(
        id=uuid.uuid4(), project_id=uuid.uuid4(), title="Pay with a card",
        description=None, objective=None, preconditions="Signed in",
        steps=[dict(step) for step in STEPS], parameters=None,
        expected_result="Paid", test_data=None, test_type="functional",
        priority="medium", severity="major", feature_area=None, suite_name=None,
        test_suite_id=None, tags=["checkout"], estimated_duration_minutes=None,
        is_automated=False, automation_status="not_automated",
        test_fingerprint=None, status=status, version=4, author_id=uuid.uuid4(),
        assignee_id=None, reviewer_id=None, edited_since_approval_version=None,
        ai_generated=False, ai_quality_score=None, ai_review_notes=None,
        last_executed_at=None, last_execution_status=None,
        created_at=now, updated_at=now,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _actor(role: str = "TESTER") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role=role, full_name="Tess Tester", username="tess")


def _result(value):
    return SimpleNamespace(scalar_one_or_none=lambda: value)


class _Session:
    """add / info / flush, and ``execute`` answering a script (or failing loudly)."""

    def __init__(self, *answers, get=None):
        self.added: list = []
        self.info: dict = {}
        self.flush = AsyncMock()
        self.execute = AsyncMock(side_effect=[_result(a) for a in answers] or AssertionError("no read expected"))
        self.get = AsyncMock(return_value=get)

    def add(self, obj):
        self.added.append(obj)


@pytest.fixture
def edit(monkeypatch):
    """Patch the case load, the audit row and the lifecycle; return a runner."""
    transition = AsyncMock()
    audit = AsyncMock()
    monkeypatch.setattr(svc, "transition", transition)
    monkeypatch.setattr(svc, "audit_event", audit)

    async def run(case, db, actor=None, **fields):
        monkeypatch.setattr(svc, "get_test_case_or_404", AsyncMock(return_value=case))
        change_type = fields.pop("change_type", None)
        payload = ManagedTestCaseUpdate(expected_version=case.version, **fields)
        kwargs = {"change_type": change_type} if change_type else {}
        return await svc.update_managed_test_case(db, case.id, payload, actor or _actor(), **kwargs)

    run.transition = transition  # type: ignore[attr-defined]
    run.audit = audit  # type: ignore[attr-defined]
    return run


def _versions(db: _Session) -> list[VersionRow]:
    return [obj for obj in db.added if isinstance(obj, VersionRow)]


# ── 2. no downgrade ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "approved", "draft", "needs_update", "rejected"])
async def test_an_edit_keeps_the_status_and_saves_one_version(edit, status):
    case = _case(status)
    db = _Session(False) if status in ("active", "approved") else _Session()

    returned = await edit(case, db, title="Pay with a saved card")

    assert returned is case
    assert case.status == status
    assert case.version == 5
    (version,) = _versions(db)
    assert version.change_type == "updated"
    assert version.version == 5 and version.status == status
    assert version.title == "Pay with a saved card"
    assert version.change_summary == "Updated to v5"
    edit.transition.assert_not_awaited()
    edit.audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_change_type_keyword_names_the_version(edit):
    case = _case("draft")
    db = _Session()
    await edit(case, db, priority="high", change_type="bulk_updated")
    (version,) = _versions(db)
    assert version.change_type == "bulk_updated"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["deprecated", "archived"])
async def test_a_retired_case_still_refuses_an_edit(edit, status):
    case = _case(status)
    db = _Session()
    with pytest.raises(HTTPException) as exc:
        await edit(case, db, title="Brought back")
    assert exc.value.status_code == 409
    assert exc.value.detail == f"Cannot edit a {status} test case"
    assert db.added == [] and case.version == 4


# ── 3. the "edited after approval" marker ───────────────────────────────────


NEW_STEPS = [
    {"step_number": 1, "action": "Open checkout", "expected_result": "The cart shows"},
    {"step_number": 2, "action": "Pay", "expected_result": "Paid"},
]


@pytest.mark.asyncio
async def test_review_on_a_steps_edit_of_an_active_case_sets_the_marker(edit):
    case = _case("active")
    db = _Session(True)  # project_requires_case_review answers True
    await edit(case, db, steps=NEW_STEPS)
    assert case.edited_since_approval_version == 5
    assert case.status == "active"
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("field, value", [("expected_result", "Paid and receipted"), ("preconditions", "A saved card")])
async def test_review_on_the_other_definition_fields_mark_too(edit, field, value):
    case = _case("approved")
    db = _Session(True)
    await edit(case, db, **{field: value})
    assert case.edited_since_approval_version == 5


@pytest.mark.asyncio
async def test_a_metadata_edit_never_marks_and_reads_nothing(edit):
    case = _case("active")
    db = _Session()  # any read fails the test
    await edit(case, db, tags=["checkout", "regression"], priority="high", title="Renamed")
    assert case.edited_since_approval_version is None
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_steps_resent_equal_mark_nothing(edit):
    case = _case("active")
    db = _Session()
    await edit(case, db, steps=[dict(step) for step in STEPS])
    assert case.edited_since_approval_version is None
    db.execute.assert_not_awaited()
    assert case.version == 5  # still a version: the PATCH named a field


@pytest.mark.asyncio
async def test_review_off_never_marks(edit):
    case = _case("active")
    db = _Session(False)  # the read answers false
    await edit(case, db, steps=NEW_STEPS)
    assert case.edited_since_approval_version is None
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_marker_already_set_is_kept_and_not_re_read(edit):
    case = _case("active", edited_since_approval_version=3)
    db = _Session()
    await edit(case, db, steps=NEW_STEPS)
    assert case.edited_since_approval_version == 3
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_draft_edit_reads_nothing_new(edit):
    case = _case("draft")
    db = _Session()
    await edit(case, db, steps=NEW_STEPS)
    assert case.edited_since_approval_version is None
    db.execute.assert_not_awaited()
    db.get.assert_not_awaited()


# ── 4. the Owner ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_member_becomes_the_owner_and_the_version_keeps_it(edit):
    member = SimpleNamespace(id=uuid.uuid4(), is_active=True, role="TESTER")
    case = _case("draft")
    db = _Session(member.id, get=member)  # the membership read answers the user id
    await edit(case, db, assignee_id=member.id)
    assert case.assignee_id == member.id
    (version,) = _versions(db)
    assert version.assignee_id == member.id
    # The audit row stores the id as text (the column is JSON).
    assert edit.audit.await_args.kwargs["new_values"] == {"assignee_id": str(member.id)}


@pytest.mark.asyncio
async def test_a_non_member_is_refused_and_nothing_is_staged(edit):
    stranger = SimpleNamespace(id=uuid.uuid4(), is_active=True, role="TESTER")
    case = _case("draft")
    db = _Session(None, get=stranger)  # no membership row
    with pytest.raises(HTTPException) as exc:
        await edit(case, db, assignee_id=stranger.id)
    assert exc.value.status_code == 422
    assert exc.value.detail == "The assignee must be an active member of this project."
    assert db.added == []
    assert case.assignee_id is None and case.version == 4
    edit.audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_explicit_null_unassigns_without_a_read(edit):
    owner = uuid.uuid4()
    case = _case("draft", assignee_id=owner)
    db = _Session()
    await edit(case, db, assignee_id=None)
    assert case.assignee_id is None
    db.get.assert_not_awaited()
    (version,) = _versions(db)
    assert version.assignee_id is None


@pytest.mark.asyncio
async def test_an_omitted_owner_is_kept_without_a_read(edit):
    owner = uuid.uuid4()
    case = _case("draft", assignee_id=owner)
    db = _Session()
    await edit(case, db, title="Renamed")
    assert case.assignee_id == owner
    db.get.assert_not_awaited()
    (version,) = _versions(db)
    assert version.assignee_id == owner


# ── 5. masked parameters (the service half; the pure half is test_test_case_parameters) ──


@pytest.mark.asyncio
async def test_an_edit_keeps_a_masked_value_it_was_not_given_and_audits_no_secret(edit):
    case = _case(
        "draft",
        parameters=[
            {"name": "token", "value": "s3cr3t", "masked": True},
            {"name": "region", "value": "eu", "masked": False},
        ],
    )
    db = _Session()
    await edit(
        case,
        db,
        parameters=[
            {"name": "token", "value": None, "masked": True},
            {"name": "region", "value": "us", "masked": False},
        ],
    )
    assert case.parameters[0]["value"] == "s3cr3t"
    assert case.parameters[1]["value"] == "us"
    (version,) = _versions(db)
    assert version.parameters[0]["value"] == "s3cr3t"  # the snapshot is the stored truth
    audited = edit.audit.await_args.kwargs["new_values"]["parameters"]
    assert audited[0]["value"] is None and audited[0]["masked"] is True
    assert "s3cr3t" not in repr(edit.audit.await_args)


# ── 6. the version source ────────────────────────────────────────────────────


def _api_key_actor(role: str = "TESTER") -> SimpleNamespace:
    actor = _actor(role)
    deps._bind_credential_kind(actor, deps.CREDENTIAL_KIND_API_KEY)
    return actor


def _jwt_actor(role: str = "TESTER") -> SimpleNamespace:
    actor = _actor(role)
    deps._bind_credential_kind(actor, deps.CREDENTIAL_KIND_JWT)
    return actor


def test_version_source_reads_the_credential_kind():
    assert lifecycle.version_source(_jwt_actor()) == "person"
    assert lifecycle.version_source(_api_key_actor()) == "api"
    assert lifecycle.version_source(_actor()) == "person"  # unknown = a person
    assert lifecycle.VERSION_SOURCES == ("person", "ai", "api")


@pytest.mark.asyncio
@pytest.mark.parametrize("make_actor, expected", [(_jwt_actor, "person"), (_api_key_actor, "api")])
async def test_the_edit_snapshot_records_its_source(edit, make_actor, expected):
    case = _case("draft")
    db = _Session()
    await edit(case, db, make_actor(), title="Renamed")
    (version,) = _versions(db)
    assert version.source == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("make_actor, expected", [(_jwt_actor, "person"), (_api_key_actor, "api")])
async def test_the_transition_snapshot_records_its_source(monkeypatch, make_actor, expected):
    case = _case("approved")
    monkeypatch.setattr(lifecycle, "_lock_case", AsyncMock(return_value=case))
    monkeypatch.setattr(lifecycle, "audit_event", AsyncMock())
    db = _Session()
    await lifecycle.transition(db, case.id, "activate", make_actor())
    (version,) = _versions(db)
    assert version.source == expected
    assert version.change_type == "activate"


# ── 7. case_response with a hand-built session ──────────────────────────────


@pytest.mark.asyncio
async def test_case_response_reads_review_off_from_an_async_mock(monkeypatch):
    from app.routers import test_management_cases as cases
    from app.services import feature_flags

    monkeypatch.setattr(feature_flags, "is_enabled", AsyncMock(return_value=True))
    case = _case("draft", author_id=None)
    response = await cases.case_response(AsyncMock(), case, _actor("TESTER"))

    assert response.review_required is False
    # Review off: Draft -> Active is offered directly.
    assert "activate" in response.allowed_actions
    assert response.edited_after_approval is False


@pytest.mark.asyncio
async def test_case_response_reads_review_off_from_the_fake_session(monkeypatch):
    from app.routers import test_management_cases as cases
    from app.services import feature_flags
    from tests.integration.conftest import FakeAsyncSession

    monkeypatch.setattr(feature_flags, "is_enabled", AsyncMock(return_value=True))
    case = _case("active", author_id=None, edited_since_approval_version=5)
    response = await cases.case_response(FakeAsyncSession(), case, _actor("QA_LEAD"))

    assert response.review_required is False
    # A marker left from a review-on period is hidden while review is off.
    assert response.edited_since_approval_version == 5
    assert response.edited_after_approval is False
    assert "deprecate" in response.reason_required_actions
    assert set(response.reason_required_actions) <= set(response.allowed_actions)


@pytest.mark.asyncio
async def test_case_response_with_review_on_shows_the_marker(monkeypatch):
    from app.routers import test_management_cases as cases
    from app.services import feature_flags

    monkeypatch.setattr(feature_flags, "is_enabled", AsyncMock(return_value=True))
    case = _case("active", author_id=None, edited_since_approval_version=5)
    response = await cases.case_response(
        AsyncMock(), case, _actor("TESTER"), {}, review_required=True
    )
    assert response.review_required is True
    assert response.edited_after_approval is True
    assert "request_review" in response.allowed_actions


@pytest.mark.asyncio
async def test_case_response_advertises_nothing_with_the_flag_off(monkeypatch):
    from app.routers import test_management_cases as cases
    from app.services import feature_flags

    monkeypatch.setattr(feature_flags, "is_enabled", AsyncMock(return_value=False))
    response = await cases.case_response(
        AsyncMock(), _case("active", author_id=None), _actor("QA_LEAD"), {}, review_required=False
    )
    assert response.allowed_actions == []
    assert response.reason_required_actions == []
