"""The four agent-action adapters over scripted sessions (W15 AGT-03, contract §2.3 test 2).

Each target service is replaced by a recording fake BOUND to the real
function's signature (``inspect.signature(real).bind``), so a call the real
service would reject with a ``TypeError`` fails here too. The scripted session
answers each statement in order and keeps it, so a test can prove the project
filter is IN the statement (the IDOR boundary) -- the SQL semantics themselves
are proven against Postgres in ``integration/test_agent_action_executor_postgres.py``.

Every IDOR case is named ``test_<type>_idor_*``:
``test_agent_action_adapter_registry.py`` asserts one exists per registered
effectful type. Ids and fingerprints start with a letter (the persistence
sanitizer never mistakes them for phone or card numbers).
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.agent_action_adapters import (
    acknowledge_adapter,
    defect_adapter,
    quarantine_adapter,
    reassign_adapter,
)
from app.services.agent_action_adapters.adapter_base import ActionOutcome, ActionRefused
from app.services.agent_action_types import (
    DecisionAcknowledgePayload,
    DefectPromotePayload,
    FailureReassignPayload,
    QuarantineApplyPayload,
)

FINGERPRINT = "fpA1b2C3d4"


# ── Harness ──────────────────────────────────────────────────────────────────
class _Result:
    def __init__(self, value: Any = None, rows: list[Any] | None = None) -> None:
        self._value = value
        self._rows = rows if rows is not None else ([] if value is None else [value])

    def scalar_one_or_none(self) -> Any:
        return self._value

    def one_or_none(self) -> Any:
        return self._value

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class ScriptedSession:
    """Answers ``execute`` from a script, records every statement and write."""

    def __init__(self, *answers: Any) -> None:
        self._answers = [a if isinstance(a, _Result) else _Result(a) for a in answers]
        self.statements: list[Any] = []
        self.flushes = 0
        self.refreshed: list[Any] = []
        self.added: list[Any] = []

    async def execute(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        if not self._answers:
            raise AssertionError(f"unscripted statement: {stmt}")
        return self._answers.pop(0)

    async def flush(self) -> None:
        self.flushes += 1

    async def refresh(self, obj: Any) -> None:
        self.refreshed.append(obj)

    def add(self, obj: Any) -> None:
        self.added.append(obj)


def _sql(stmt: Any) -> str:
    return str(stmt)


def _params(stmt: Any) -> list[Any]:
    return list(stmt.compile().params.values())


def bound_fake(real: Any, *, result: Any = None, raises: BaseException | None = None) -> Any:
    """An async recording fake that refuses any call the real function's signature refuses."""
    signature = inspect.signature(real)
    calls: list[inspect.BoundArguments] = []

    async def fake(*args: Any, **kwargs: Any) -> Any:
        bound = signature.bind(*args, **kwargs)
        calls.append(bound)
        if raises is not None:
            raise raises
        return result(bound) if callable(result) else result

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


def _action(**overrides: Any) -> SimpleNamespace:
    base = {"id": uuid.uuid4(), "project_id": uuid.uuid4(), "status": "executing"}
    base.update(overrides)
    return SimpleNamespace(**base)


def _approver() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), username="lead2", full_name="Lead Two", role="QA_LEAD")


def _effect_fields(**overrides: Any) -> dict[str, Any]:
    base = {
        "proposing_agent_id": "investigator",
        "proposing_agent_mode": "act",
        "investigation_id": str(uuid.uuid4()),
        "test_run_id": str(uuid.uuid4()),
        "title": "Quarantine checkout retry",
        "rationale": "<img src=x onerror=alert(1)> display only",
        "risk": "low",
        "evidence": [{"type": "flaky_set", "id": FINGERPRINT}],
    }
    base.update(overrides)
    return base


def _quarantine_payload(**overrides: Any) -> QuarantineApplyPayload:
    fields = _effect_fields(test_fingerprint=FINGERPRINT, test_name="checkout retry", suite_name="Payments")
    fields.update(overrides)
    return QuarantineApplyPayload.model_validate(fields)


# ── quarantine.apply ─────────────────────────────────────────────────────────
@pytest.fixture
def q_fakes(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    from app.services import feature_flags, flaky_quarantine_service as fqs

    fakes = SimpleNamespace(
        is_enabled=bound_fake(feature_flags.is_enabled, result=True),
        propose=bound_fake(fqs.propose_quarantine),
        approve=bound_fake(fqs.approve),
        get_request=bound_fake(fqs.get_request),
        release=bound_fake(fqs.release),
    )
    _install_q(monkeypatch, fakes)
    return fakes


def _install_q(monkeypatch: pytest.MonkeyPatch, fakes: SimpleNamespace) -> None:
    monkeypatch.setattr(quarantine_adapter, "is_enabled", fakes.is_enabled)
    monkeypatch.setattr(quarantine_adapter, "propose_quarantine", fakes.propose)
    monkeypatch.setattr(quarantine_adapter, "approve", fakes.approve)
    monkeypatch.setattr(quarantine_adapter, "get_request", fakes.get_request)
    monkeypatch.setattr(quarantine_adapter, "release", fakes.release)


def _q_rearm(monkeypatch: pytest.MonkeyPatch, fakes: SimpleNamespace, **results: Any) -> SimpleNamespace:
    """Replace some of the quarantine fakes (still bound to the real signatures)."""
    from app.services import feature_flags, flaky_quarantine_service as fqs

    reals = {
        "is_enabled": feature_flags.is_enabled, "propose": fqs.propose_quarantine,
        "approve": fqs.approve, "get_request": fqs.get_request, "release": fqs.release,
    }
    for name, value in results.items():
        if isinstance(value, BaseException):
            setattr(fakes, name, bound_fake(reals[name], raises=value))
        else:
            setattr(fakes, name, bound_fake(reals[name], result=value))
    _install_q(monkeypatch, fakes)
    return fakes


def _q_row(project_id: uuid.UUID, status: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), project_id=project_id, status=status)


async def test_quarantine_preflight_reads_every_target_inside_the_project(q_fakes):
    action, approver = _action(), _approver()
    db = ScriptedSession(uuid.uuid4(), None)
    await quarantine_adapter.QuarantineApplyAdapter().preflight(
        db, action=action, payload=_quarantine_payload(), approver=approver,
    )
    flag_call = q_fakes.is_enabled.calls[0]
    assert flag_call.arguments["key"] == "flaky_auto_quarantine"
    assert flag_call.arguments["project_id"] == action.project_id
    assert flag_call.arguments["user"] is approver
    run_stmt, live_stmt = db.statements
    assert "test_runs.project_id" in _sql(run_stmt) and action.project_id in _params(run_stmt)
    assert "flaky_quarantine_requests.project_id" in _sql(live_stmt)
    assert action.project_id in _params(live_stmt) and FINGERPRINT in _params(live_stmt)


async def test_quarantine_idor_run_of_another_project_is_refused(q_fakes):
    action = _action()
    db = ScriptedSession(None)          # the run is not a run of THIS project
    with pytest.raises(ActionRefused) as refused:
        await quarantine_adapter.QuarantineApplyAdapter().preflight(
            db, action=action, payload=_quarantine_payload(), approver=_approver(),
        )
    assert refused.value.code == "target_not_in_project"
    assert action.project_id in _params(db.statements[0])
    assert q_fakes.propose.calls == [] and q_fakes.approve.calls == []


async def test_quarantine_flag_off_is_refused(q_fakes, monkeypatch):
    _q_rearm(monkeypatch, q_fakes, is_enabled=False)
    db = ScriptedSession()
    with pytest.raises(ActionRefused) as refused:
        await quarantine_adapter.QuarantineApplyAdapter().preflight(
            db, action=_action(), payload=_quarantine_payload(), approver=_approver(),
        )
    assert refused.value.code == "quarantine_disabled"
    assert db.statements == []


@pytest.mark.parametrize("status", ["APPROVED", "QUARANTINED", "RECHECK_SCHEDULED", "RE_QUARANTINED"])
async def test_quarantine_already_decided_test_is_refused(q_fakes, status):
    db = ScriptedSession(uuid.uuid4(), status)
    with pytest.raises(ActionRefused) as refused:
        await quarantine_adapter.QuarantineApplyAdapter().preflight(
            db, action=_action(), payload=_quarantine_payload(), approver=_approver(),
        )
    assert refused.value.code == "target_already_quarantined"


async def test_quarantine_execute_proposes_then_approves_as_the_approver(q_fakes, monkeypatch):
    action, approver = _action(), _approver()
    proposed = _q_row(action.project_id, "PROPOSED")
    quarantined = SimpleNamespace(id=proposed.id, project_id=action.project_id, status="QUARANTINED")
    _q_rearm(monkeypatch, q_fakes, propose=proposed, approve=quarantined)
    payload = _quarantine_payload()
    db = ScriptedSession()

    outcome = await quarantine_adapter.QuarantineApplyAdapter().execute(
        db, action=action, payload=payload, approver=approver,
    )

    propose = q_fakes.propose.calls[0].arguments
    assert propose["project_id"] == action.project_id
    assert propose["test_fingerprint"] == FINGERPRINT
    assert propose["detection_method"] == "agent_action"
    assert propose["actor"] is approver
    # Only ids reach the service: never the proposal's title / rationale text.
    assert propose["rationale"] == {
        "source": "agent_action", "action_id": str(action.id),
        "investigation_id": payload.investigation_id,
    }
    approve = q_fakes.approve.calls[0].arguments
    assert approve["db"] is db and approve["request_id"] == proposed.id and approve["actor"] is approver
    assert isinstance(outcome, ActionOutcome)
    assert outcome.rollback_payload == {"quarantine_request_id": str(proposed.id)}
    assert outcome.result_payload["quarantine_status"] == "QUARANTINED"
    assert outcome.result_link == {
        "kind": "quarantine", "id": str(proposed.id), "label": "quarantined",
        "href": "/flaky?tab=quarantined",
    }
    assert quarantine_adapter.QuarantineApplyAdapter.commits_effect is True


async def test_quarantine_idor_execute_never_approves_another_projects_request(q_fakes, monkeypatch):
    action = _action()
    _q_rearm(monkeypatch, q_fakes, propose=_q_row(uuid.uuid4(), "PROPOSED"))
    with pytest.raises(ActionRefused) as refused:
        await quarantine_adapter.QuarantineApplyAdapter().execute(
            ScriptedSession(), action=action, payload=_quarantine_payload(), approver=_approver(),
        )
    assert refused.value.code == "target_not_in_project"
    assert q_fakes.approve.calls == []


async def test_quarantine_execute_with_the_flag_turned_off_meanwhile(q_fakes, monkeypatch):
    _q_rearm(monkeypatch, q_fakes, propose=None)          # propose_quarantine's flag-off answer
    with pytest.raises(ActionRefused) as refused:
        await quarantine_adapter.QuarantineApplyAdapter().execute(
            ScriptedSession(), action=_action(), payload=_quarantine_payload(), approver=_approver(),
        )
    assert refused.value.code == "quarantine_disabled"
    assert q_fakes.approve.calls == []


async def test_quarantine_rollback_releases_the_request(q_fakes, monkeypatch):
    action, actor = _action(status="executed"), _approver()
    row = _q_row(action.project_id, "QUARANTINED")
    _q_rearm(monkeypatch, q_fakes, get_request=row,
             release=SimpleNamespace(id=row.id, project_id=action.project_id, status="RELEASED"))
    db = ScriptedSession()
    outcome = await quarantine_adapter.QuarantineApplyAdapter().rollback(
        db, action=action, rollback_payload={"quarantine_request_id": str(row.id)}, actor=actor,
    )
    release = q_fakes.release.calls[0].arguments
    assert release["db"] is db and release["request_id"] == row.id and release["actor"] is actor
    assert release["notes"] == "Agent action undone"
    assert outcome.result_payload == {"quarantine_request_id": str(row.id), "quarantine_status": "RELEASED"}


async def test_quarantine_rollback_twice_is_a_success_without_a_second_release(q_fakes, monkeypatch):
    action = _action(status="executed")
    row = _q_row(action.project_id, "RELEASED")
    _q_rearm(monkeypatch, q_fakes, get_request=row)
    outcome = await quarantine_adapter.QuarantineApplyAdapter().rollback(
        ScriptedSession(), action=action, rollback_payload={"quarantine_request_id": str(row.id)},
        actor=_approver(),
    )
    assert outcome.result_payload["quarantine_status"] == "RELEASED"
    assert q_fakes.release.calls == []


@pytest.mark.parametrize("status", ["EXPIRED", "REJECTED", "PROPOSED"])
async def test_quarantine_rollback_after_the_target_changed_is_refused(q_fakes, monkeypatch, status):
    action = _action(status="executed")
    row = _q_row(action.project_id, status)
    _q_rearm(monkeypatch, q_fakes, get_request=row)
    with pytest.raises(ActionRefused) as refused:
        await quarantine_adapter.QuarantineApplyAdapter().rollback(
            ScriptedSession(), action=action, rollback_payload={"quarantine_request_id": str(row.id)},
            actor=_approver(),
        )
    assert refused.value.code == "rollback_target_changed"
    assert q_fakes.release.calls == []


async def test_quarantine_idor_rollback_never_releases_another_projects_request(q_fakes, monkeypatch):
    action = _action(status="executed")
    row = _q_row(uuid.uuid4(), "QUARANTINED")
    _q_rearm(monkeypatch, q_fakes, get_request=row)
    with pytest.raises(ActionRefused) as refused:
        await quarantine_adapter.QuarantineApplyAdapter().rollback(
            ScriptedSession(), action=action, rollback_payload={"quarantine_request_id": str(row.id)},
            actor=_approver(),
        )
    assert refused.value.code == "target_not_in_project"
    assert q_fakes.release.calls == []


async def test_quarantine_rollback_lost_race_is_rollback_target_changed(q_fakes, monkeypatch):
    from fastapi import HTTPException

    action = _action(status="executed")
    row = _q_row(action.project_id, "QUARANTINED")
    _q_rearm(monkeypatch, q_fakes, get_request=row,
             release=HTTPException(status_code=409, detail="Cannot release from status RELEASED"))
    with pytest.raises(ActionRefused) as refused:
        await quarantine_adapter.QuarantineApplyAdapter().rollback(
            ScriptedSession(), action=action, rollback_payload={"quarantine_request_id": str(row.id)},
            actor=_approver(),
        )
    assert refused.value.code == "rollback_target_changed"


# ── failure.reassign ─────────────────────────────────────────────────────────
_CASES: dict[uuid.UUID, SimpleNamespace] = {}


@pytest.fixture
def r_fake(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.services import failed_test_reassignment_service as svc

    fake = bound_fake(svc.reassign_failure, result=_assign)
    monkeypatch.setattr(reassign_adapter, "reassign_failure", fake)
    return fake


def _assign(bound: inspect.BoundArguments) -> Any:
    # What the real service does to the (identity-mapped) row the adapter locked.
    tc = _CASES[bound.arguments["test_case_id"]]
    tc.assigned_to_user_id = bound.arguments["new_assignee_user_id"]
    return tc


def _case(status: str = "FAILED", assigned: uuid.UUID | None = None) -> SimpleNamespace:
    tc = SimpleNamespace(id=uuid.uuid4(), status=status, assigned_to_user_id=assigned, test_run_id=uuid.uuid4())
    _CASES[tc.id] = tc
    return tc


def _reassign_payload(tc: SimpleNamespace, owner: uuid.UUID) -> FailureReassignPayload:
    return FailureReassignPayload.model_validate(_effect_fields(
        title="Reassign checkout retry to the Payments owner", risk="medium",
        test_run_id=str(tc.test_run_id), test_case_id=str(tc.id),
        assignee_user_id=str(owner), suite_name="Payments", test_name="checkout retry",
    ))


def _pg() -> Any:
    from sqlalchemy.dialects import postgresql

    return postgresql.dialect()


async def test_reassign_preflight_checks_case_owner_and_assignee_inside_the_project(r_fake):
    action, owner = _action(), uuid.uuid4()
    tc = _case(assigned=uuid.uuid4())
    db = ScriptedSession(tc, owner)
    await reassign_adapter.FailureReassignAdapter().preflight(
        db, action=action, payload=_reassign_payload(tc, owner), approver=_approver(),
    )
    case_stmt, owner_stmt = db.statements
    assert "test_runs.project_id" in _sql(case_stmt) and action.project_id in _params(case_stmt)
    assert "test_suite_owners.project_id" in _sql(owner_stmt)
    assert action.project_id in _params(owner_stmt) and "Payments" in _params(owner_stmt)


async def test_reassign_idor_test_case_of_another_project_is_refused(r_fake):
    action, owner = _action(), uuid.uuid4()
    tc = _case()
    db = ScriptedSession(None)          # the case's run is not in THIS project
    with pytest.raises(ActionRefused) as refused:
        await reassign_adapter.FailureReassignAdapter().preflight(
            db, action=action, payload=_reassign_payload(tc, owner), approver=_approver(),
        )
    assert refused.value.code == "target_not_in_project"
    assert action.project_id in _params(db.statements[0])
    assert r_fake.calls == []


@pytest.mark.parametrize(
    ("status", "current_owner", "assigned_to_owner", "code"),
    [
        ("PASSED", True, False, "test_case_not_failing"),
        ("FAILED", False, False, "assignee_no_longer_owner"),
        ("BROKEN", True, True, "already_assigned"),
    ],
)
async def test_reassign_preflight_refusals(r_fake, status, current_owner, assigned_to_owner, code):
    owner = uuid.uuid4()
    tc = _case(status=status, assigned=owner if assigned_to_owner else None)
    db = ScriptedSession(tc, owner if current_owner else uuid.uuid4())
    with pytest.raises(ActionRefused) as refused:
        await reassign_adapter.FailureReassignAdapter().preflight(
            db, action=_action(), payload=_reassign_payload(tc, owner), approver=_approver(),
        )
    assert refused.value.code == code
    assert r_fake.calls == []


async def test_reassign_execute_records_the_previous_assignee(r_fake):
    action, approver, owner, previous = _action(), _approver(), uuid.uuid4(), uuid.uuid4()
    tc = _case(assigned=previous)
    db = ScriptedSession(tc, SimpleNamespace(full_name="Suite Owner", username="owner1"))
    outcome = await reassign_adapter.FailureReassignAdapter().execute(
        db, action=action, payload=_reassign_payload(tc, owner), approver=approver,
    )
    call = r_fake.calls[0].arguments
    assert call["db"] is db and call["test_case_id"] == tc.id
    assert call["new_assignee_user_id"] == owner and call["actor_user"] is approver
    assert "FOR UPDATE" in str(db.statements[0].compile(dialect=_pg()))
    assert action.project_id in _params(db.statements[0])
    facts = {
        "test_case_id": str(tc.id), "previous_assignee_user_id": str(previous),
        "assigned_to_user_id": str(owner),
    }
    assert outcome.rollback_payload == facts and outcome.result_payload == facts
    assert outcome.result_link == {
        "kind": "assignee", "id": str(owner), "label": "Suite Owner",
        "href": f"/runs/{tc.test_run_id}?tab=tests",
    }


async def test_reassign_execute_maps_the_service_refusals(monkeypatch):
    from app.services import failed_test_reassignment_service as svc

    for status_code, detail, code in [
        (403, "Reassignment requires QA_LEAD or ADMIN on this project", "approver_not_authorized"),
        (422, "Cannot reassign — test case status is 'PASSED'", "test_case_not_failing"),
        (422, "New assignee must be the test suite owner", "assignee_no_longer_owner"),
        (404, "Test case not found", "target_not_in_project"),
    ]:
        fake = bound_fake(svc.reassign_failure, raises=svc.ReassignmentError(status_code, detail))
        monkeypatch.setattr(reassign_adapter, "reassign_failure", fake)
        tc = _case()
        with pytest.raises(ActionRefused) as refused:
            await reassign_adapter.FailureReassignAdapter().execute(
                ScriptedSession(tc), action=_action(), payload=_reassign_payload(tc, uuid.uuid4()),
                approver=_approver(),
            )
        assert refused.value.code == code


async def test_reassign_idor_execute_never_calls_the_service_for_a_foreign_case(r_fake):
    tc = _case()
    with pytest.raises(ActionRefused) as refused:
        await reassign_adapter.FailureReassignAdapter().execute(
            ScriptedSession(None), action=_action(), payload=_reassign_payload(tc, uuid.uuid4()),
            approver=_approver(),
        )
    assert refused.value.code == "target_not_in_project"
    assert r_fake.calls == []


async def test_reassign_execute_refuses_a_case_assigned_meanwhile(r_fake):
    owner = uuid.uuid4()
    tc = _case(assigned=owner)
    with pytest.raises(ActionRefused) as refused:
        await reassign_adapter.FailureReassignAdapter().execute(
            ScriptedSession(tc), action=_action(), payload=_reassign_payload(tc, owner),
            approver=_approver(),
        )
    assert refused.value.code == "already_assigned"
    assert r_fake.calls == []


def _r_rollback_payload(tc: SimpleNamespace, previous: uuid.UUID | None, set_to: uuid.UUID) -> dict:
    return {
        "test_case_id": str(tc.id),
        "previous_assignee_user_id": str(previous) if previous else None,
        "assigned_to_user_id": str(set_to),
    }


@pytest.mark.parametrize("had_previous", [True, False])
async def test_reassign_rollback_restores_the_previous_assignee(r_fake, had_previous):
    action, owner = _action(status="executed"), uuid.uuid4()
    previous = uuid.uuid4() if had_previous else None
    tc = _case(assigned=owner)
    db = ScriptedSession(tc)
    outcome = await reassign_adapter.FailureReassignAdapter().rollback(
        db, action=action, rollback_payload=_r_rollback_payload(tc, previous, owner), actor=_approver(),
    )
    assert tc.assigned_to_user_id == previous          # NULL restored too
    assert db.flushes == 1
    assert "FOR UPDATE" in str(db.statements[0].compile(dialect=_pg()))
    assert action.project_id in _params(db.statements[0])
    assert outcome.result_payload["restored_assignee_user_id"] == (str(previous) if previous else None)
    assert r_fake.calls == []                          # the compare-and-set is the adapter's own


async def test_reassign_rollback_twice_is_a_success_without_a_second_write(r_fake):
    previous, owner = uuid.uuid4(), uuid.uuid4()
    tc = _case(assigned=previous)                      # the first Undo already restored it
    db = ScriptedSession(tc)
    await reassign_adapter.FailureReassignAdapter().rollback(
        db, action=_action(status="executed"), rollback_payload=_r_rollback_payload(tc, previous, owner),
        actor=_approver(),
    )
    assert tc.assigned_to_user_id == previous and db.flushes == 0


async def test_reassign_rollback_after_the_target_changed_is_refused(r_fake):
    previous, owner, somebody = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tc = _case(assigned=somebody)                      # reassigned by a person since
    db = ScriptedSession(tc)
    with pytest.raises(ActionRefused) as refused:
        await reassign_adapter.FailureReassignAdapter().rollback(
            db, action=_action(status="executed"),
            rollback_payload=_r_rollback_payload(tc, previous, owner), actor=_approver(),
        )
    assert refused.value.code == "rollback_target_changed"
    assert tc.assigned_to_user_id == somebody and db.flushes == 0


async def test_reassign_idor_rollback_of_a_foreign_case_is_refused(r_fake):
    tc = _case(assigned=uuid.uuid4())
    db = ScriptedSession(None)
    with pytest.raises(ActionRefused) as refused:
        await reassign_adapter.FailureReassignAdapter().rollback(
            db, action=_action(status="executed"),
            rollback_payload=_r_rollback_payload(tc, None, uuid.uuid4()), actor=_approver(),
        )
    assert refused.value.code == "target_not_in_project"


# ── defect.promote ───────────────────────────────────────────────────────────
@pytest.fixture
def d_fakes(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    from app.services import action_policy, defect_promotion_service, defect_triage_service
    from app.services import intelligence_snapshot_service
    from app.services.activity import service as activity_service

    fakes = SimpleNamespace(
        promote=bound_fake(defect_promotion_service.promote_cluster, result=lambda bound: {
            "defect_id": str(DEFECT_ID), "cluster_id": bound.arguments["cluster_id"],
            "approval_status": "approved", "duplicate_detected": False,
        }),
        mark_stale=bound_fake(intelligence_snapshot_service.mark_stale, result=True),
        reject_action=bound_fake(action_policy.reject_action, result=True),
        reject_pending=bound_fake(defect_triage_service.reject_pending_defect, result=_close_like_w06),
        record=bound_fake(activity_service.record),
    )
    monkeypatch.setattr(defect_adapter, "promote_cluster", fakes.promote)
    monkeypatch.setattr(defect_adapter, "mark_stale", fakes.mark_stale)
    monkeypatch.setattr(defect_adapter, "reject_action", fakes.reject_action)
    monkeypatch.setattr(defect_adapter, "reject_pending_defect", fakes.reject_pending)
    monkeypatch.setattr(defect_adapter, "record_activity", fakes.record)
    return fakes


DEFECT_ID = uuid.uuid4()


def _close_like_w06(bound: inspect.BoundArguments) -> None:
    from datetime import datetime, timezone

    from app.services import defect_status

    defect_status.apply_status(bound.arguments["defect"], defect_status.INVALID, now=datetime.now(timezone.utc))


def _defect_payload(**overrides: Any) -> DefectPromotePayload:
    fields = _effect_fields(
        title="Promote a defect for cluster checkout timeouts", risk="medium",
        failure_cluster_id=str(uuid.uuid4()), cluster_id="cl_001", cluster_label="checkout timeouts",
    )
    fields.update(overrides)
    return DefectPromotePayload.model_validate(fields)


def _defect(project_id: uuid.UUID, *, approval: str = "approved", resolution: str = "OPEN",
            jira: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=DEFECT_ID, project_id=project_id, approval_status=approval, resolution_status=resolution,
        resolved_at=None, jira_ticket_id=jira, title="checkout timeouts", release_id=None,
    )


async def test_defect_preflight_checks_run_cluster_and_open_defects_inside_the_project(d_fakes):
    action, payload = _action(), _defect_payload()
    db = ScriptedSession(uuid.uuid4(), uuid.uuid4(), None)
    await defect_adapter.DefectPromoteAdapter().preflight(db, action=action, payload=payload, approver=_approver())
    run_stmt, cluster_stmt, defect_stmt = db.statements
    assert "test_runs.project_id" in _sql(run_stmt) and action.project_id in _params(run_stmt)
    assert "failure_clusters.cluster_id" in _sql(cluster_stmt) and "cl_001" in _params(cluster_stmt)
    assert "defects.project_id" in _sql(defect_stmt) and action.project_id in _params(defect_stmt)


async def test_defect_idor_cluster_of_a_run_in_another_project_is_refused(d_fakes):
    action = _action()
    db = ScriptedSession(None)          # the run is not in THIS project: the cluster is never read
    with pytest.raises(ActionRefused) as refused:
        await defect_adapter.DefectPromoteAdapter().preflight(
            db, action=action, payload=_defect_payload(), approver=_approver(),
        )
    assert refused.value.code == "target_not_in_project"
    assert len(db.statements) == 1 and action.project_id in _params(db.statements[0])
    assert d_fakes.promote.calls == []


@pytest.mark.parametrize(("answers", "code"), [
    ((uuid.uuid4(), None), "cluster_not_found"),
    ((uuid.uuid4(), uuid.uuid4(), uuid.uuid4()), "defect_already_exists"),
])
async def test_defect_preflight_refusals(d_fakes, answers, code):
    with pytest.raises(ActionRefused) as refused:
        await defect_adapter.DefectPromoteAdapter().preflight(
            ScriptedSession(*answers), action=_action(), payload=_defect_payload(), approver=_approver(),
        )
    assert refused.value.code == code


async def test_defect_execute_promotes_internally_never_to_jira(d_fakes):
    action, approver, payload = _action(), _approver(), _defect_payload()
    db = ScriptedSession()
    outcome = await defect_adapter.DefectPromoteAdapter().execute(db, action=action, payload=payload, approver=approver)
    call = d_fakes.promote.calls[0].arguments
    assert call["run_id"] == payload.test_run_id and call["cluster_id"] == "cl_001"
    assert call["project_id"] == str(action.project_id) and call["db"] is db and call["actor"] is approver
    # No field of the request comes from the proposal's text; never a Jira key.
    assert call["request"] == {"severity": "HIGH", "project_key": None, "labels": ["automated-test", "agent-proposed"]}
    assert d_fakes.mark_stale.calls[0].arguments["run_id"] == uuid.UUID(payload.test_run_id)
    assert outcome.rollback_payload == {"defect_id": str(DEFECT_ID), "approval_status": "approved"}
    assert outcome.result_link == {
        "kind": "defect", "id": str(DEFECT_ID), "label": "defect created",
        "href": f"/defects?defect={DEFECT_ID}",
    }


async def test_defect_execute_cluster_gone_is_refused(d_fakes, monkeypatch):
    from app.services import defect_promotion_service

    monkeypatch.setattr(defect_adapter, "promote_cluster", bound_fake(
        defect_promotion_service.promote_cluster, raises=ValueError("Cluster cl_001 not found for run x"),
    ))
    with pytest.raises(ActionRefused) as refused:
        await defect_adapter.DefectPromoteAdapter().execute(
            ScriptedSession(), action=_action(), payload=_defect_payload(), approver=_approver(),
        )
    assert refused.value.code == "cluster_not_found"


_UNDO = {"defect_id": str(DEFECT_ID), "approval_status": "approved"}


async def test_defect_rollback_of_an_effective_defect_closes_it_invalid(d_fakes):
    action, actor = _action(status="executed"), _approver()
    defect = _defect(action.project_id)
    db = ScriptedSession(defect)
    outcome = await defect_adapter.DefectPromoteAdapter().rollback(db, action=action, rollback_payload=_UNDO, actor=actor)
    assert defect.resolution_status == "INVALID" and defect.resolved_at is not None
    assert "FOR UPDATE" in str(db.statements[0].compile(dialect=_pg()))
    assert action.project_id in _params(db.statements[0])
    record = d_fakes.record.calls[0].arguments
    assert record["event_type"] == "defect.updated" and record["db"] is db
    assert record["after"] == {"status": "Invalid"}
    assert d_fakes.reject_action.calls == []
    assert outcome.result_payload["resolution_status"] == "INVALID"


async def test_defect_rollback_of_a_pending_defect_rejects_it_the_w06_way(d_fakes):
    action, actor = _action(status="executed"), _approver()
    defect = _defect(action.project_id, approval="pending_review")
    db = ScriptedSession(defect)
    outcome = await defect_adapter.DefectPromoteAdapter().rollback(
        db, action=action, rollback_payload={**_UNDO, "approval_status": "pending_review"}, actor=actor,
    )
    reject = d_fakes.reject_action.calls[0].arguments
    assert reject["record_id"] == DEFECT_ID and reject["rejector_id"] == actor.id
    pending = d_fakes.reject_pending.calls[0].arguments
    assert pending["defect"] is defect and pending["reason"] == "Agent action undone"
    assert defect.resolution_status == "INVALID"
    assert outcome.result_payload["approval_status"] == "rejected"


async def test_defect_rollback_twice_is_a_success_without_a_second_write(d_fakes):
    action = _action(status="executed")
    defect = _defect(action.project_id, resolution="INVALID")
    await defect_adapter.DefectPromoteAdapter().rollback(
        ScriptedSession(defect), action=action, rollback_payload=_UNDO, actor=_approver(),
    )
    assert d_fakes.record.calls == [] and d_fakes.reject_action.calls == [] and d_fakes.reject_pending.calls == []


@pytest.mark.parametrize(("resolution", "jira"), [("IN_PROGRESS", None), ("RESOLVED", None), ("OPEN", "QA-12")])
async def test_defect_rollback_after_the_target_changed_is_refused(d_fakes, resolution, jira):
    action = _action(status="executed")
    defect = _defect(action.project_id, resolution=resolution, jira=jira)
    with pytest.raises(ActionRefused) as refused:
        await defect_adapter.DefectPromoteAdapter().rollback(
            ScriptedSession(defect), action=action, rollback_payload=_UNDO, actor=_approver(),
        )
    assert refused.value.code == "rollback_target_changed"
    assert defect.resolution_status == resolution and d_fakes.record.calls == []


async def test_defect_idor_rollback_of_a_foreign_defect_is_refused(d_fakes):
    action = _action(status="executed")
    db = ScriptedSession(None)          # the defect is not THIS project's
    with pytest.raises(ActionRefused) as refused:
        await defect_adapter.DefectPromoteAdapter().rollback(db, action=action, rollback_payload=_UNDO, actor=_approver())
    assert refused.value.code == "target_not_in_project"
    assert action.project_id in _params(db.statements[0])


# ── decision.acknowledge ─────────────────────────────────────────────────────
async def test_acknowledge_has_no_effect_and_no_undo():
    adapter = acknowledge_adapter.DecisionAcknowledgeAdapter()
    payload = DecisionAcknowledgePayload.model_validate(
        {"proposing_agent_id": "agent.decision_report.v1", "action_id": "release.blocker.1"},
    )
    db = ScriptedSession()
    assert await adapter.preflight(db, action=_action(), payload=payload, approver=_approver()) is None
    outcome = await adapter.execute(db, action=_action(), payload=payload, approver=_approver())
    assert outcome.result_payload == {"acknowledged": True}
    assert outcome.rollback_payload is None and outcome.result_link is None
    assert db.statements == [] and db.flushes == 0 and db.added == []
    with pytest.raises(ActionRefused):
        await adapter.rollback(db, action=_action(), rollback_payload={}, actor=_approver())
