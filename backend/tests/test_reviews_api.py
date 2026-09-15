"""E8.2: the human review gate API.

Pins section 8.3's enforcement: API keys and synthetic accounts cannot decide,
a rejection needs a reason, only a pending review can be settled, the requester
cannot review their own run, and the run moves ``completed -> passed | failed``
through the state machine or the decision is refused. Also: non-members get
404, reviewer identity stays out of responses, and notes are redacted.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.deps import CREDENTIAL_KIND_API_KEY, CREDENTIAL_KIND_JWT, _bind_credential_kind
from app.models.postgres import REVIEW_REASON_CODES, UserRole
from app.services import review_request_service as svc

PROJECT = uuid.uuid4()


def _user(kind=CREDENTIAL_KIND_JWT, *, role=UserRole.QA_LEAD.value, synthetic=False):
    user = SimpleNamespace(id=uuid.uuid4(), role=role, is_synthetic=synthetic, username="qa-lead")
    return _bind_credential_kind(user, kind)


def _review(**kw):
    base = dict(
        id=uuid.uuid4(), project_id=PROJECT, kind="report", subject_type="pipeline_run",
        subject_id="", pipeline_run_id=uuid.uuid4(), test_run_id=uuid.uuid4(),
        workflow_type="offline", state="pending_review", requested_by=None,
        reviewed_by=None, reviewed_at=None, reason_code=None, notes=None,
        evidence_bundle_sha256=None, superseded_by=None,
        created_at=datetime.now(timezone.utc), ai_disclaimer_version=svc.AI_DISCLAIMER_VERSION,
    )
    base.update(kw)
    review = SimpleNamespace(**base)
    review.subject_id = review.subject_id or str(review.pipeline_run_id)
    return review


class _DB:
    def __init__(self, row=None):
        self.row = row
        self.flushed = False
        self.committed = False

    async def execute(self, _stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: self.row)

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.committed = True


@pytest.fixture
def transitions(monkeypatch):
    calls: list[dict] = []

    async def _guarded(db, run_id, *, expected, to, error=None, **_kw):
        calls.append({"run_id": run_id, "expected": expected.value, "to": to.value, "error": error})
        return to

    monkeypatch.setattr("app.services.workflow_run_state.guarded_transition", _guarded)
    return calls


# ── settle_review: the refusals, in order ────────────────────────────────────


@pytest.mark.asyncio
async def test_an_api_key_cannot_decide_whatever_its_owners_role(transitions):
    with pytest.raises(svc.ReviewDecisionRefused) as exc:
        await svc.settle_review(_DB(), review=_review(), reviewer=_user(CREDENTIAL_KIND_API_KEY, role="admin"),
                                decision="accepted")
    assert (exc.value.status_code, exc.value.code) == (403, "interactive_login_required")
    assert transitions == []


@pytest.mark.asyncio
async def test_an_unknown_credential_kind_is_refused_too(transitions):
    """``credential_kind`` returns None for a user that did not come through
    auth; that must not read as a login."""
    user = SimpleNamespace(id=uuid.uuid4(), role="qa_lead", is_synthetic=False)
    with pytest.raises(svc.ReviewDecisionRefused) as exc:
        await svc.settle_review(_DB(), review=_review(), reviewer=user, decision="accepted")
    assert exc.value.code == "interactive_login_required"


@pytest.mark.asyncio
async def test_a_synthetic_account_cannot_decide(transitions):
    with pytest.raises(svc.ReviewDecisionRefused) as exc:
        await svc.settle_review(_DB(), review=_review(), reviewer=_user(synthetic=True), decision="accepted")
    assert (exc.value.status_code, exc.value.code) == (403, "synthetic_account")


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [None, "", "looked_wrong"])
async def test_a_rejection_needs_a_known_reason_code(transitions, reason):
    with pytest.raises(svc.ReviewDecisionRefused) as exc:
        await svc.settle_review(_DB(), review=_review(), reviewer=_user(), decision="rejected", reason_code=reason)
    assert (exc.value.status_code, exc.value.code) == (422, "reason_code_required")
    assert transitions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["accepted", "rejected", "superseded"])
async def test_only_a_pending_review_can_be_settled(transitions, state):
    with pytest.raises(svc.ReviewDecisionRefused) as exc:
        await svc.settle_review(_DB(), review=_review(state=state), reviewer=_user(), decision="accepted")
    assert (exc.value.status_code, exc.value.code) == (409, "review_not_pending")
    assert transitions == []


@pytest.mark.asyncio
async def test_the_requester_cannot_review_their_own_run(transitions):
    reviewer = _user()
    with pytest.raises(svc.ReviewDecisionRefused) as exc:
        await svc.settle_review(_DB(), review=_review(requested_by=reviewer.id), reviewer=reviewer,
                                decision="accepted")
    assert (exc.value.status_code, exc.value.code) == (403, "separation_of_duties")


@pytest.mark.asyncio
async def test_a_run_that_moved_on_refuses_the_decision_and_writes_nothing(monkeypatch):
    from app.services.workflow_run_state import TransitionLost

    async def _lost(*_a, **_kw):
        raise TransitionLost("another writer moved it")

    monkeypatch.setattr("app.services.workflow_run_state.guarded_transition", _lost)
    review, db = _review(), _DB()
    with pytest.raises(svc.ReviewDecisionRefused) as exc:
        await svc.settle_review(db, review=review, reviewer=_user(), decision="accepted")
    assert (exc.value.status_code, exc.value.code) == (409, "run_not_awaiting_review")
    assert review.state == "pending_review" and review.reviewed_at is None
    assert db.flushed is False


# ── settle_review: what a decision does ──────────────────────────────────────


@pytest.mark.asyncio
async def test_accept_moves_the_run_completed_to_passed(transitions):
    from app.core.metrics import review_requests_total

    review, reviewer = _review(), _user()
    metric = review_requests_total.labels(state="accepted")
    before = metric._value.get()
    await svc.settle_review(_DB(), review=review, reviewer=reviewer, decision="accepted")

    assert metric._value.get() == before + 1
    assert transitions == [{"run_id": review.pipeline_run_id, "expected": "completed", "to": "passed", "error": None}]
    assert review.state == "accepted"
    assert review.reviewed_by == reviewer.id and review.reviewed_at is not None
    assert review.reason_code is None


@pytest.mark.asyncio
async def test_reject_moves_the_run_to_failed_with_the_review_rejected_code(transitions):
    from app.core.metrics import review_requests_total

    review = _review()
    metric = review_requests_total.labels(state="rejected")
    before = metric._value.get()
    await svc.settle_review(_DB(), review=review, reviewer=_user(), decision="rejected",
                            reason_code="missing_evidence")

    assert metric._value.get() == before + 1
    assert transitions[0]["to"] == "failed"
    assert transitions[0]["error"] == "review_rejected: missing_evidence"
    assert review.state == "rejected" and review.reason_code == "missing_evidence"


@pytest.mark.asyncio
async def test_notes_are_redacted_before_they_are_stored(transitions):
    review = _review()
    await svc.settle_review(_DB(), review=review, reviewer=_user(), decision="accepted",
                            notes="looks right; contact me at jane.doe@example.com, password=hunter2")
    assert "hunter2" not in review.notes
    assert "jane.doe@example.com" not in review.notes


@pytest.mark.asyncio
async def test_blank_notes_are_stored_as_none(transitions):
    review = _review()
    await svc.settle_review(_DB(), review=review, reviewer=_user(), decision="accepted", notes="   ")
    assert review.notes is None


@pytest.mark.asyncio
async def test_a_review_whose_run_was_deleted_still_settles(transitions):
    review = _review(pipeline_run_id=None, subject_id="gone")
    await svc.settle_review(_DB(), review=review, reviewer=_user(), decision="accepted")
    assert transitions == [] and review.state == "accepted"


def test_the_api_reason_codes_match_the_database_vocabulary():
    from typing import get_args

    from app.routers.reviews import ReasonCode

    assert tuple(get_args(ReasonCode)) == REVIEW_REASON_CODES


def test_reject_without_a_reason_code_is_a_validation_error():
    from app.routers.reviews import RejectReviewRequest

    with pytest.raises(ValidationError):
        RejectReviewRequest()
    with pytest.raises(ValidationError):
        RejectReviewRequest(reason_code="looked_wrong")


# ── the router ───────────────────────────────────────────────────────────────


@pytest.fixture
def recorders(monkeypatch):
    from app.routers import reviews

    audit, activity, events = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(reviews, "log_access_change", audit)
    monkeypatch.setattr(reviews, "record_activity", activity)
    monkeypatch.setattr("app.services.pipeline_event_log.emit_event", events)
    return SimpleNamespace(audit=audit, activity=activity, events=events)


@pytest.mark.asyncio
async def test_accept_audits_records_activity_and_commits(transitions, recorders):
    from app.routers.reviews import AcceptReviewRequest, accept_review

    review, reviewer = _review(), _user()
    db = _DB(review)
    out = await accept_review(review.id, AcceptReviewRequest(notes="ok"), db=db, current_user=reviewer)

    assert db.committed is True
    assert out.state == "accepted" and out.reviewed is True
    audit = recorders.audit.await_args.kwargs
    assert audit["action"] == "ai_review.accepted"
    assert "notes" not in str(audit["after_value"]), "free-text notes must never reach the audit trail"
    assert recorders.activity.await_args.kwargs["event_type"] == "review.accepted"
    recorders.events.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_response_never_names_the_reviewer(transitions, recorders):
    from app.routers.reviews import accept_review

    review, reviewer = _review(), _user()
    out = await accept_review(review.id, None, db=_DB(review), current_user=reviewer)

    payload = out.model_dump()
    assert "reviewed_by" not in payload
    assert str(reviewer.id) not in str(payload)


@pytest.mark.asyncio
async def test_a_refused_decision_commits_nothing_and_records_nothing(transitions, recorders):
    from app.routers.reviews import accept_review

    review = _review(state="accepted")
    db = _DB(review)
    with pytest.raises(HTTPException) as exc:
        await accept_review(review.id, None, db=db, current_user=_user())

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "review_not_pending"
    assert db.committed is False
    recorders.audit.assert_not_awaited()
    recorders.activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_reject_carries_the_reason_through(transitions, recorders):
    from app.routers.reviews import RejectReviewRequest, reject_review

    review = _review()
    out = await reject_review(review.id, RejectReviewRequest(reason_code="contradiction"),
                              db=_DB(review), current_user=_user())
    assert out.state == "rejected" and out.reason_code == "contradiction"
    assert recorders.activity.await_args.kwargs["context"]["reason_code"] == "contradiction"


@pytest.mark.asyncio
async def test_a_missing_review_is_404(recorders):
    from app.routers.reviews import accept_review

    with pytest.raises(HTTPException) as exc:
        await accept_review(uuid.uuid4(), None, db=_DB(None), current_user=_user())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_an_event_log_outage_does_not_undo_a_committed_decision(transitions, recorders):
    from app.routers.reviews import accept_review

    recorders.events.side_effect = RuntimeError("mongo down")
    review = _review()
    db = _DB(review)
    out = await accept_review(review.id, None, db=db, current_user=_user())
    assert db.committed is True and out.state == "accepted"


# ── require_review_access ────────────────────────────────────────────────────


class _AccessDB:
    def __init__(self, project_id, member):
        self._results = [project_id, member]

    async def execute(self, _stmt):
        value = self._results.pop(0)
        return SimpleNamespace(scalar_one_or_none=lambda: value)


def _request(review_id):
    return SimpleNamespace(path_params={"review_id": str(review_id)})


@pytest.mark.asyncio
async def test_access_a_member_passes():
    from app.routers.reviews import require_review_access

    user = _user(role=UserRole.QA_ENGINEER.value)
    out = await require_review_access()(_request(uuid.uuid4()), db=_AccessDB(PROJECT, uuid.uuid4()), current_user=user)
    assert out is user


@pytest.mark.asyncio
async def test_access_a_non_member_gets_404_not_403():
    from app.routers.reviews import require_review_access

    with pytest.raises(HTTPException) as exc:
        await require_review_access()(_request(uuid.uuid4()), db=_AccessDB(PROJECT, None),
                                      current_user=_user(role=UserRole.QA_ENGINEER.value))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_access_an_unknown_review_is_404():
    from app.routers.reviews import require_review_access

    with pytest.raises(HTTPException) as exc:
        await require_review_access()(_request(uuid.uuid4()), db=_AccessDB(None, None), current_user=_user())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_access_an_admin_bypasses_membership_once_the_review_exists():
    from app.routers.reviews import require_review_access

    admin = _user(role=UserRole.ADMIN.value)
    out = await require_review_access()(_request(uuid.uuid4()), db=_AccessDB(PROJECT, None), current_user=admin)
    assert out is admin


@pytest.mark.asyncio
async def test_access_a_malformed_id_is_400():
    from app.routers.reviews import require_review_access

    with pytest.raises(HTTPException) as exc:
        await require_review_access()(SimpleNamespace(path_params={"review_id": "nope"}),
                                      db=_AccessDB(None, None), current_user=_user())
    assert exc.value.status_code == 400


def test_accept_and_reject_require_qa_lead_and_the_review_guard():
    import inspect

    from app.routers import reviews

    for name in ("accept_review", "reject_review"):
        src = inspect.getsource(getattr(reviews, name))
        assert "require_role(UserRole.QA_LEAD)" in src, name
        assert "require_review_access()" in src, name
