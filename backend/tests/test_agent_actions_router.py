"""W15 AGT-01 / AGT-03: the agent-action PATCH hardened to the reviews standard (contract §2.1 test 1).

The handler is driven over a scripted session that answers each statement by
its shape (the row lock, the proposing review, the requester, the outbox, the
answer's page) and logs every add / commit / refresh in order, with the REAL
``approval_refusal``, ``act_refusal``, ``transition_action``, audit and activity
code. Only the investigator config resolution, the pipeline event and the
analytics epoch are stubbed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.deps import CREDENTIAL_KIND_API_KEY, CREDENTIAL_KIND_JWT, _bind_credential_kind
from app.models.postgres import AgentActionLedger, UserRole
from app.routers import agent_actions as router
from app.routers.agent_actions import ActionTransitionRequest

PROJECT = uuid.uuid4()
RUN = uuid.uuid4()
PIPELINE = uuid.uuid4()
REVIEW = uuid.uuid4()


def _user(kind=CREDENTIAL_KIND_JWT, *, synthetic=False, name="Lead A"):
    user = SimpleNamespace(
        id=uuid.uuid4(), role=UserRole.QA_LEAD.value, is_synthetic=synthetic, is_active=True,
        username="lead-a", full_name=name, email=None, is_service_account=False,
    )
    return _bind_credential_kind(user, kind)


def _quarantine_payload(**extra):
    payload = {
        "proposing_agent_id": "investigator", "proposing_agent_mode": "act",
        "investigation_id": str(uuid.uuid4()), "test_run_id": str(RUN),
        "test_fingerprint": "fpA1b2C3d4", "test_name": "checkout retry", "suite_name": "checkout-api",
        "title": "Quarantine checkout retry", "rationale": "Failing in this run and already flaky.",
        "risk": "low", "evidence": [],
    }
    payload.update(extra)
    return payload


def _ack_payload():
    return {"proposing_agent_id": "agent.decision_report.v1", "action_id": "rel-gate-a1", "title": "Confirm the gate"}


def _row(*, action_type="quarantine.apply", status="pending_review", pipeline=PIPELINE, payload=None, **kw):
    now = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)
    return AgentActionLedger(
        id=uuid.uuid4(), project_id=PROJECT, test_run_id=RUN, pipeline_run_id=pipeline,
        action_type=action_type, target_type="test" if action_type == "quarantine.apply" else "release_item",
        target_id="fpA1b2C3d4", status=status, approval_required=True,
        idempotency_key=f"inv:{uuid.uuid4().hex[:8]}", request_sha256="a" * 64,
        request_payload=payload if payload is not None else _quarantine_payload(),
        created_at=now, **kw,
    )


class _Result:
    def __init__(self, *, scalar=None, rows=()):
        self._scalar, self._rows = scalar, list(rows)

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return list(self._rows)


def _shape(sql: str) -> str:
    if "FROM review_requests" in sql:
        return "review"
    if "FROM agent_action_dispatch_outbox" in sql:
        return "outbox"
    if sql.startswith("SELECT agent_pipeline_runs.requested_by"):
        return "requester"
    if "LEFT OUTER JOIN test_runs" in sql:
        return "page"
    if "FROM agent_action_ledger" in sql and "FOR UPDATE" in sql:
        return "lock"
    return "unscripted"


class _Session:
    """Answers by statement shape; logs every statement, add, flush, commit and refresh in order."""

    def __init__(self, row, *, review_state="accepted", requested_by=None, relock=None):
        self.row, self.review_state, self.requested_by = row, review_state, requested_by
        self.relock = relock  # the row a SECOND lock returns (a concurrent change), default the same
        self.log: list[tuple] = []
        self.added: list = []
        self.locks = 0

    async def execute(self, stmt, *args, **kwargs):
        shape = _shape(str(stmt))
        self.log.append(("execute", shape))
        if shape == "lock":
            self.locks += 1
            if self.locks > 1 and self.relock is not None:
                return _Result(scalar=self.relock)
            return _Result(scalar=self.row)
        if shape == "review":
            return _Result(rows=[(PIPELINE, REVIEW, self.review_state)] if self.review_state else [])
        if shape == "requester":
            return _Result(scalar=self.requested_by)
        if shape == "outbox":
            return _Result(scalar=None)
        if shape == "page":
            row = self.row
            return _Result(rows=[(row, "142", "main", "investigation", self.requested_by, "Lead A", "lead-a", 1)]
                           if row is not None else [])
        raise AssertionError(f"unscripted statement: {str(stmt)[:200]}")

    def add(self, obj):
        self.added.append(obj)
        self.log.append(("add", type(obj).__name__))

    async def flush(self):
        self.log.append(("flush",))

    async def commit(self):
        self.log.append(("commit",))

    async def refresh(self, obj):
        self.log.append(("refresh",))

    def of(self, type_name):
        return [obj for obj in self.added if type(obj).__name__ == type_name]


def _investigator(mode="act", ticked=("quarantine.apply",)):
    act = SimpleNamespace(allowed_action_types=list(ticked))
    return SimpleNamespace(config=SimpleNamespace(mode=mode, extensions=SimpleNamespace(
        investigator=SimpleNamespace(act=act))))


@pytest.fixture
def live(monkeypatch):
    """The live investigator config (Act on, quarantine ticked by default), the event and the epoch."""
    state = SimpleNamespace(resolved=_investigator(), emitted=AsyncMock(), bump=AsyncMock())
    resolve = AsyncMock(side_effect=lambda *a, **k: state.resolved)
    monkeypatch.setattr("app.services.agent_action_adapters.adapter_base.resolve_for_project", resolve)
    monkeypatch.setattr("app.services.agent_action_adapters.adapter_base.get_effective_ai_config",
                        AsyncMock(return_value={}))
    monkeypatch.setattr("app.services.pipeline_event_log.emit_event", state.emitted)
    monkeypatch.setattr(router, "bump_analytics_epoch", state.bump)
    state.resolve = resolve
    return state


async def _patch(db, user, **body):
    return await router.transition_agent_action(
        project_id=PROJECT, action_id=db.row.id if db.row is not None else uuid.uuid4(),
        body=ActionTransitionRequest(**body), db=db, current_user=user, _=user,
    )


async def _refused(db, user, **body) -> tuple[int, str, str]:
    with pytest.raises(HTTPException) as exc:
        await _patch(db, user, **body)
    detail = exc.value.detail
    return exc.value.status_code, detail["code"], detail["message"]


def _wrote_nothing(db) -> bool:
    return not db.added and ("commit",) not in db.log and ("flush",) not in db.log


# ── refusals before the row is read ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_rejection_needs_a_reason_code(live):
    db = _Session(_row())
    assert (await _refused(db, _user(), status="rejected"))[:2] == (422, "reason_code_required")
    assert db.log == []


@pytest.mark.asyncio
async def test_a_reason_code_on_an_approval_is_refused(live):
    db = _Session(_row())
    code = await _refused(db, _user(), status="approved", reason_code="other")
    assert code == (422, "reason_code_on_reject_only", "reason_code applies to a rejection only.")
    assert db.log == []


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", [PIPELINE, None], ids=["ai-row", "row-without-pipeline"])
async def test_an_api_key_is_refused_for_every_row(live, pipeline):
    db = _Session(_row(pipeline=pipeline))
    status, code, message = await _refused(db, _user(CREDENTIAL_KIND_API_KEY), status="approved")
    assert (status, code) == (403, "interactive_login_required")
    assert message == "Deciding an agent action requires an interactive login; API keys are refused."
    assert db.log == []


@pytest.mark.asyncio
async def test_an_unknown_credential_kind_is_refused_too(live):
    user = SimpleNamespace(id=uuid.uuid4(), role="QA_LEAD", is_synthetic=False, username="x")
    db = _Session(_row())
    assert (await _refused(db, user, status="approved"))[1] == "interactive_login_required"


@pytest.mark.asyncio
async def test_a_synthetic_account_is_refused(live):
    db = _Session(_row())
    assert (await _refused(db, _user(synthetic=True), status="approved"))[:2] == (403, "synthetic_account")
    assert db.log == []


@pytest.mark.asyncio
async def test_another_projects_action_through_this_path_is_not_found(live):
    db = _Session(None)  # WHERE id AND project_id matched nothing
    assert (await _refused(db, _user(), status="approved"))[:2] == (404, "action_not_found")
    assert _wrote_nothing(db)


# ── the approve check (the real approval_refusal + act_refusal) ──────────────


@pytest.mark.asyncio
async def test_approving_on_a_report_still_pending_review_is_refused_and_stages_nothing(live):
    db = _Session(_row(), review_state="pending_review")
    status, code, message = await _refused(db, _user(), status="approved")
    assert (status, code) == (409, "review_not_accepted")
    assert message.startswith("Accept the AI report first")
    assert _wrote_nothing(db)  # no outbox row, no audit, no activity
    live.resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_approving_an_effect_with_act_off_is_refused(live):
    live.resolved = _investigator(mode="suggest")
    db = _Session(_row())
    status, code, message = await _refused(db, _user(), status="approved")
    assert (status, code) == (409, "act_mode_required")
    assert "Settings › AI agents › Investigator › Act" in message
    assert _wrote_nothing(db)


@pytest.mark.asyncio
async def test_approving_an_unticked_type_is_refused(live):
    live.resolved = _investigator(mode="act", ticked=("defect.promote",))
    db = _Session(_row())
    assert (await _refused(db, _user(), status="approved"))[:2] == (409, "action_type_not_allowed")
    assert _wrote_nothing(db)


@pytest.mark.asyncio
async def test_the_requester_cannot_approve_the_effects_of_their_own_investigation(live):
    requester = _user()
    db = _Session(_row(), requested_by=requester.id)
    status, code, message = await _refused(db, requester, status="approved")
    assert (status, code) == (403, "separation_of_duties")
    assert message == "The person who requested this investigation cannot approve the changes it proposes."
    assert _wrote_nothing(db)


@pytest.mark.asyncio
async def test_the_requester_may_acknowledge_a_decision_item(live):
    requester = _user()
    row = _row(action_type="decision_report_action", payload=_ack_payload())
    db = _Session(row, requested_by=requester.id)
    answer = await _patch(db, requester, status="approved")
    assert answer.status == "approved" and answer.canonical_type == "decision.acknowledge"
    live.resolve.assert_not_awaited()  # acknowledge needs no Act


@pytest.mark.asyncio
async def test_an_already_decided_action_is_not_pending(live):
    db = _Session(_row(status="rejected"))
    status, code, message = await _refused(db, _user(), status="approved")
    assert (status, code, message) == (409, "action_not_pending", "This action is already rejected.")
    assert _wrote_nothing(db)


# ── the decisions: audit + activity in the SAME transaction, ONE commit ──────


def _order(db, *entries):
    """The positions of ``entries`` in the session log (first occurrence each)."""
    return [db.log.index(entry) for entry in entries]


@pytest.mark.asyncio
async def test_a_happy_approve_stages_audit_and_activity_before_the_one_commit(live, monkeypatch):
    calls = []
    real = router.transition_action

    async def _spy(db, **kw):
        calls.append(kw)
        return await real(db, **kw)

    monkeypatch.setattr(router, "transition_action", _spy)
    db = _Session(_row(), requested_by=uuid.uuid4())  # someone else requested the investigation
    live.emitted.side_effect = lambda *a, **k: db.log.append(("event", a[1]))
    approver = _user()
    answer = await _patch(db, approver, status="approved")

    assert [c["to_status"] for c in calls] == ["approved"] and calls[0]["actor_user_id"] == approver.id
    audits, events = db.of("AccessAuditLog"), db.of("ProjectActivityEvent")
    assert [a.action for a in audits] == ["agent_action.approved"]
    assert audits[0].after_value["status"] == "approved" and "rationale" not in str(audits[0].after_value)
    assert [e.event_type for e in events] == ["agent.action_approved"]
    assert events[0].context["canonical_type"] == "quarantine.apply"
    assert len(db.of("AgentActionDispatchOutbox")) == 1  # the executor's delivery intent
    assert db.log.count(("commit",)) == 1
    audit_at, activity_at, commit_at, refresh_at, event_at = _order(
        db, ("add", "AccessAuditLog"), ("add", "ProjectActivityEvent"), ("commit",), ("refresh",),
        ("event", "agent_action_approved"),
    )
    assert audit_at < commit_at and activity_at < commit_at < refresh_at < event_at
    assert answer.status == "approved" and answer.approved_by == str(approver.id)
    live.bump.assert_not_awaited()  # nothing changed analytics data yet: the executor bumps


@pytest.mark.asyncio
async def test_a_reject_stores_redacted_notes_and_returns_and_records_none(live):
    db = _Session(_row())
    notes = "  Wrong test: mail me at someone@example.com  "
    answer = await _patch(db, _user(), status="rejected", reason_code="unsupported_claim", notes=notes)

    stored = db.row.result_payload["rejection"]
    assert stored["reason_code"] == "unsupported_claim"
    assert "someone@example.com" not in stored["notes"] and stored["notes"].startswith("Wrong test")
    audits, events = db.of("AccessAuditLog"), db.of("ProjectActivityEvent")
    assert [a.action for a in audits] == ["agent_action.rejected"]
    assert audits[0].after_value["reason_code"] == "unsupported_claim"
    assert [e.event_type for e in events] == ["agent.action_denied"]
    assert events[0].context["reason_code"] == "unsupported_claim"
    for written in (audits[0].after_value, events[0].context, events[0].summary):
        assert "Wrong test" not in str(written) and "notes" not in str(written)
    assert db.log.count(("commit",)) == 1
    assert answer.status == "rejected" and answer.rejection_reason_code == "unsupported_claim"
    assert "notes" not in answer.result_payload["rejection"]  # stored, never returned (D-11)
    assert "Wrong test" not in answer.model_dump_json()
    assert answer.outcome == "rejected" and answer.outcome_text == "Rejected by Lead A (unsupported_claim)."


@pytest.mark.asyncio
async def test_an_approved_action_not_yet_claimed_can_still_be_rejected(live):
    db = _Session(_row(status="approved"))
    answer = await _patch(db, _user(), status="rejected", reason_code="stale_data")
    assert answer.status == "rejected"


@pytest.mark.asyncio
async def test_an_executing_action_cannot_be_rejected(live):
    db = _Session(_row(status="executing"))
    code = await _refused(db, _user(), status="rejected", reason_code="other")
    assert code == (409, "action_not_pending", "This action is already executing.")
    assert _wrote_nothing(db)


@pytest.mark.asyncio
async def test_the_ignored_error_code_never_reaches_the_row(live):
    db = _Session(_row())
    await _patch(db, _user(), status="rejected", reason_code="other", error_code="executed_by_hand")
    assert db.row.error_code is None


# ── Undo (§2.3): the adapter's compare-and-set first, re-lock, then the transition ──


class _Adapter:
    action_type = "quarantine.apply"
    commits_effect = True

    def __init__(self, db, *, refuse=None):
        self.db, self.refuse, self.calls = db, refuse, []

    async def rollback(self, db, *, action, rollback_payload, actor):
        from app.services.agent_action_adapters.adapter_base import ActionOutcome, ActionRefused

        self.calls.append(rollback_payload)
        self.db.log.append(("rollback",))
        if self.refuse:
            raise ActionRefused(self.refuse)
        return ActionOutcome(result_payload={"released": True, "quarantine_request_id": "qr-1"})


def _executed(**kw):
    return _row(
        status="executed", rollback_payload={"quarantine_request_id": "qr-1"},
        result_payload={"result_link": {"kind": "quarantine", "label": "quarantined", "href": "/flaky?tab=quarantined"}},
        **kw,
    )


@pytest.fixture
def undo(live, monkeypatch):
    def _install(db, **kw):
        adapter = _Adapter(db, **kw)
        monkeypatch.setattr(router, "get_adapter", lambda action_type: adapter)
        monkeypatch.setattr(router, "load_approver", AsyncMock(return_value=None))
        live.bump.side_effect = lambda *a, **k: db.log.append(("bump",))
        return adapter
    return _install


@pytest.mark.asyncio
async def test_only_an_executed_action_can_be_undone(undo):
    db = _Session(_row(status="approved"))
    adapter = undo(db)
    code = await _refused(db, _user(), status="rolled_back")
    assert code == (409, "action_not_executed", "Only an executed action can be undone.")
    assert adapter.calls == [] and _wrote_nothing(db)


@pytest.mark.asyncio
async def test_an_acknowledgement_cannot_be_undone(undo):
    db = _Session(_row(action_type="decision_report_action", status="executed", payload=_ack_payload()))
    adapter = undo(db)
    code = await _refused(db, _user(), status="rolled_back")
    assert code == (409, "action_not_reversible", "This action cannot be undone.")
    assert adapter.calls == [] and _wrote_nothing(db)


@pytest.mark.asyncio
async def test_a_type_without_an_adapter_cannot_be_undone(live, monkeypatch):
    monkeypatch.setattr(router, "get_adapter", lambda action_type: None)
    db = _Session(_executed())
    assert (await _refused(db, _user(), status="rolled_back"))[:2] == (409, "action_not_reversible")
    assert _wrote_nothing(db)


@pytest.mark.asyncio
async def test_a_decider_no_longer_authorised_cannot_undo(undo, monkeypatch):
    from app.services.agent_action_adapters.adapter_base import ActionRefused

    db = _Session(_executed())
    adapter = undo(db)
    monkeypatch.setattr(router, "load_approver", AsyncMock(side_effect=ActionRefused("approver_not_authorized")))
    assert (await _refused(db, _user(), status="rolled_back"))[:2] == (403, "approver_not_authorized")
    assert adapter.calls == [] and _wrote_nothing(db)


@pytest.mark.asyncio
async def test_a_happy_undo_rolls_back_then_relocks_then_records_then_bumps_after_the_commit(undo):
    db = _Session(_executed())
    adapter = undo(db)
    answer = await _patch(db, _user(), status="rolled_back")

    assert adapter.calls == [{"quarantine_request_id": "qr-1"}]
    assert db.row.result_payload["rollback"]["released"] is True
    assert db.row.result_payload["result_link"]["kind"] == "quarantine"  # the executed facts are kept
    assert [a.action for a in db.of("AccessAuditLog")] == ["agent_action.rolled_back"]
    events = db.of("ProjectActivityEvent")
    assert [e.event_type for e in events] == ["agent.action_rolled_back"]
    assert events[0].context["result_kind"] == "quarantine"
    rollback_at, commit_at, bump_at, refresh_at = _order(db, ("rollback",), ("commit",), ("bump",), ("refresh",))
    relock_at = [i for i, entry in enumerate(db.log) if entry == ("execute", "lock")][1]
    assert rollback_at < relock_at < commit_at < bump_at < refresh_at
    assert db.log.count(("commit",)) == 1
    assert answer.status == "rolled_back" and answer.outcome == "rolled_back"


@pytest.mark.asyncio
async def test_an_undo_after_the_target_changed_is_refused_with_its_sentence(undo):
    db = _Session(_executed())
    undo(db, refuse="rollback_target_changed")
    status, code, message = await _refused(db, _user(), status="rolled_back")
    assert (status, code) == (409, "rollback_target_changed")
    assert message == "The target changed after the action ran: undo it on its own page."
    assert _wrote_nothing(db) and ("bump",) not in db.log


@pytest.mark.asyncio
async def test_a_concurrent_undo_loses_at_the_relock(undo):
    db = _Session(_executed(), relock=_row(status="rolled_back"))
    undo(db)
    assert (await _refused(db, _user(), status="rolled_back"))[:2] == (409, "action_not_executed")
    assert _wrote_nothing(db) and ("bump",) not in db.log
