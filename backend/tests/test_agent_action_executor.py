"""The real ``execute_agent_action`` over scripted sessions (W15 AGT-03, contract §2.3 test 1).

``AsyncSessionLocal`` is replaced by a WORLD: every session it opens answers
the ledger lock with the one row, the review lookup, the approver's user and
membership rows, and records each statement, staged object and commit on one
shared TIMELINE -- so a test can prove the claim was committed BEFORE the
adapter's ``execute`` was awaited, and which rows each commit carried.

Real code throughout: the executor, ``load_approver``, ``act_refusal`` (only
its config resolver is scripted), ``log_access_change``, ``record_activity``
(strict under pytest) and, for the acknowledge, the REAL registry and adapter.
The effectful adapters are recording fakes (their own tests:
``test_agent_action_adapters.py``). The SQL semantics -- FOR UPDATE, a second
session seeing the claim -- are proven in
``integration/test_agent_action_executor_postgres.py``.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional

import pytest

import app.services.agent_action_ledger_service as service
from app.models.postgres import AccessAuditLog
from app.services.agent_action_adapters import adapter_base
from app.services.agent_action_adapters.adapter_base import ActionOutcome, ActionRefused
from app.services.agent_action_types import ACTION_TYPES
from app.services.canonical_json import stable_json_sha256

FINGERPRINT = "fpA1b2C3d4"


# ── The world ────────────────────────────────────────────────────────────────
class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class World:
    def __init__(self, row: SimpleNamespace, *, review: Any, approver: SimpleNamespace,
                 member: bool = True) -> None:
        self.row = row
        self.review = review
        self.approver = approver
        self.member = member
        self.sessions: list[WorldSession] = []
        self.timeline: list[tuple[Any, ...]] = []

    def __call__(self) -> "WorldSession":
        session = WorldSession(self, len(self.sessions))
        self.sessions.append(session)
        return session

    @property
    def commits(self) -> list[tuple[Any, ...]]:
        return [event for event in self.timeline if event[0] == "commit"]

    def committed(self, kind: type) -> list[Any]:
        return [obj for event in self.commits for obj in event[3] if isinstance(obj, kind)]

    def statements(self) -> list[str]:
        return [sql for session in self.sessions for sql in session.statements]


class WorldSession:
    def __init__(self, world: World, index: int) -> None:
        self.world = world
        self.index = index
        self.statements: list[str] = []
        self.raw_statements: list[Any] = []
        self.staged: list[Any] = []

    async def __aenter__(self) -> "WorldSession":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        self.staged = []          # leaving without a commit discards what was staged
        return False

    async def execute(self, stmt: Any) -> _Result:
        sql = str(stmt)
        self.statements.append(sql)
        self.raw_statements.append(stmt)
        if "FROM agent_action_ledger" in sql:
            return _Result(self.world.row)
        if "FROM review_requests" in sql:
            return _Result(self.world.review)
        if "FROM project_members" in sql:
            return _Result(uuid.uuid4() if self.world.member else None)
        if "FROM users" in sql:
            return _Result(self.world.approver)
        raise AssertionError(f"unscripted statement: {sql}")

    def add(self, obj: Any) -> None:
        self.staged.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.world.timeline.append(("commit", self.index, self.world.row.status, list(self.staged)))
        self.staged = []


class RecordingAdapter:
    def __init__(self, world: World, *, action_type: str = "quarantine.apply", commits_effect: bool = False,
                 preflight_raises: Optional[BaseException] = None, raises: Optional[BaseException] = None) -> None:
        self.world = world
        self.action_type = action_type
        self.commits_effect = commits_effect
        self.preflight_raises = preflight_raises
        self.raises = raises
        self.calls: list[str] = []

    async def preflight(self, db: Any, *, action: Any, payload: Any, approver: Any) -> None:
        self.calls.append("preflight")
        if self.preflight_raises is not None:
            raise self.preflight_raises

    async def execute(self, db: Any, *, action: Any, payload: Any, approver: Any) -> ActionOutcome:
        self.calls.append("execute")
        self.world.timeline.append(("execute", action.status, approver.id))
        if self.raises is not None:
            raise self.raises
        return ActionOutcome(
            result_payload={"quarantine_request_id": "qrA1", "quarantine_status": "QUARANTINED"},
            rollback_payload={"quarantine_request_id": "qrA1"},
            result_link={"kind": "quarantine", "id": "qrA1", "label": "quarantined", "href": "/flaky?tab=quarantined"},
        )

    async def rollback(self, db: Any, *, action: Any, rollback_payload: Any, actor: Any) -> ActionOutcome:
        raise AssertionError("the executor never rolls back")


def _quarantine_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "proposing_agent_id": "investigator", "proposing_agent_mode": "act",
        "investigation_id": str(uuid.uuid4()), "test_run_id": str(uuid.uuid4()),
        "test_fingerprint": FINGERPRINT, "test_name": "checkout retry", "suite_name": "Payments",
        "title": "Quarantine checkout retry", "rationale": "Failing here and already flaky.",
        "risk": "low", "evidence": [{"type": "flaky_set", "id": FINGERPRINT}],
    }
    payload.update(overrides)
    return payload


def _row(*, action_type: str = "quarantine.apply", payload: Optional[dict] = None, status: str = "approved",
         pipeline: bool = True) -> SimpleNamespace:
    request = payload if payload is not None else _quarantine_payload()
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4(), test_run_id=uuid.uuid4(),
        pipeline_run_id=uuid.uuid4() if pipeline else None, action_type=action_type,
        target_type="test", target_id=FINGERPRINT, status=status, approval_required=True,
        idempotency_key="inv:k1", request_payload=request, request_sha256=stable_json_sha256(request),
        approved_by=uuid.uuid4(), approved_at=datetime.now(timezone.utc), execution_started_at=None,
        execution_completed_at=None, error_code=None, result_payload=None, rollback_payload=None,
    )


def _approver(row: SimpleNamespace, role: str = "QA_LEAD", **overrides: Any) -> SimpleNamespace:
    base = {"id": row.approved_by, "role": role, "is_active": True, "is_synthetic": False,
            "username": "lead2", "full_name": "Lead Two"}
    base.update(overrides)
    return SimpleNamespace(**base)


def _act_config(mode: str = "act", ticked: tuple[str, ...] = ("quarantine.apply",)) -> SimpleNamespace:
    act = SimpleNamespace(allowed_action_types=list(ticked))
    return SimpleNamespace(config=SimpleNamespace(mode=mode, extensions=SimpleNamespace(
        investigator=SimpleNamespace(act=act))))


class Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.monkeypatch = monkeypatch
        self.config = _act_config()
        self.resolved: list[tuple[Any, ...]] = []
        self.bumps: list[Any] = []
        self.events: list[tuple[str, str, dict]] = []
        self.webhooks: list[str] = []

        async def resolve(db: Any, project_id: Any, agent_id: str, **kwargs: Any) -> Any:
            self.resolved.append((project_id, agent_id))
            return self.config

        async def effective(**kwargs: Any) -> dict:
            return {}

        async def bump(project_id: Any) -> None:
            self.bumps.append(project_id)

        async def emit(pipeline_run_id: str, event_type: str, **kwargs: Any) -> None:
            self.events.append((pipeline_run_id, event_type, kwargs.get("detail") or {}))

        async def webhook(defect_id: Any) -> int:
            self.webhooks.append(str(defect_id))
            return 1

        from app.services import cache_service, defect_promoted_webhook, pipeline_event_log

        monkeypatch.setattr(adapter_base, "resolve_for_project", resolve)
        monkeypatch.setattr(adapter_base, "get_effective_ai_config", effective)
        monkeypatch.setattr(cache_service, "bump_analytics_epoch", bump)
        monkeypatch.setattr(pipeline_event_log, "emit_event", emit)
        monkeypatch.setattr(defect_promoted_webhook, "emit_defect_promoted", webhook)

    def world(self, row: SimpleNamespace, *, review: Any = "accepted", approver: Any = None,
              member: bool = True, real_adapter: bool = False, **adapter_kwargs: Any) -> World:
        """``review="accepted"`` answers an accepted review id; ``None`` = no accepted review."""
        world = World(
            row, review=uuid.uuid4() if review == "accepted" else review,
            approver=approver if approver is not None else _approver(row), member=member,
        )
        self.monkeypatch.setattr(service, "AsyncSessionLocal", world)
        adapter: Optional[RecordingAdapter] = None
        if not real_adapter:
            spec = ACTION_TYPES.get(row.action_type, ACTION_TYPES["quarantine.apply"])
            adapter = RecordingAdapter(world, action_type=spec.action_type, **adapter_kwargs)
            self.monkeypatch.setattr(service, "get_adapter", lambda action_type: adapter)
        world.adapter = adapter  # type: ignore[attr-defined]
        return world

    async def run(self, world: World) -> dict[str, Any]:
        return await service.execute_agent_action(project_id=world.row.project_id, action_id=world.row.id)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(monkeypatch)


def _audits(world: World) -> list[AccessAuditLog]:
    return world.committed(AccessAuditLog)


def _assert_refused(world: World, outcome: dict, code: str) -> None:
    assert outcome == {"status": "failed", "action_id": str(world.row.id), "reason": code}
    assert world.row.status == "failed" and world.row.error_code == code
    assert world.row.execution_completed_at is not None
    assert len(world.commits) == 1, "a refusal is ONE commit"
    assert [e for e in world.timeline if e[0] == "execute"] == [], "a refusal never reaches the effect"
    (audit,) = _audits(world)
    assert audit.action == "agent_action.failed" and audit.after_value["error_code"] == code


# ── The happy path ───────────────────────────────────────────────────────────
async def test_happy_path_commits_the_claim_before_the_effect_then_finishes_executed(harness):
    from app.models.postgres import ProjectActivityEvent as ActivityEvent

    row = _row()
    world = harness.world(row)

    outcome = await harness.run(world)

    assert outcome == {"status": "executed", "action_id": str(row.id)}
    claim, finish = world.commits
    execute_at = world.timeline.index(next(e for e in world.timeline if e[0] == "execute"))
    # The claim is durable (committed, status executing) BEFORE execute is awaited.
    assert world.timeline.index(claim) < execute_at < world.timeline.index(finish)
    assert claim[2] == "executing" and claim[3] == []
    assert world.timeline[execute_at] == ("execute", "executing", row.approved_by)
    assert claim[1] != finish[1], "the effect runs on its own session"
    # The finish commit carries the audit row and the activity row.
    assert finish[2] == "executed"
    (audit,) = [o for o in finish[3] if isinstance(o, AccessAuditLog)]
    assert audit.action == "agent_action.executed" and audit.actor_user_id == row.approved_by
    assert audit.after_value == {
        "action_id": str(row.id), "canonical_type": "quarantine.apply", "status": "executed",
        "result_kind": "quarantine", "target_id": FINGERPRINT,
    }
    (event,) = [o for o in finish[3] if isinstance(o, ActivityEvent)]
    assert event.event_type == "agent.action_executed"
    assert event.actor_type == "agent" and event.actor_name == "Investigator"   # D-14
    assert event.entity_label == "Quarantine checkout retry"
    assert row.result_payload["quarantine_request_id"] == "qrA1"
    assert row.result_payload["result_link"]["href"] == "/flaky?tab=quarantined"
    assert row.rollback_payload == {"quarantine_request_id": "qrA1"}
    assert row.error_code is None and row.execution_completed_at is not None
    # After the finish commit: the analytics epoch and the pipeline event.
    assert harness.bumps == [row.project_id]
    assert harness.events == [(str(row.pipeline_run_id), "agent_action_executed",
                               {"action_id": str(row.id), "action_type": "quarantine.apply"})]
    # The LIVE investigator config was read on the claim session.
    assert harness.resolved == [(row.project_id, "investigator")]


async def test_the_alias_acknowledges_with_an_accepted_review_and_no_act(harness):
    """decision_report_action → decision.acknowledge through the REAL registry and adapter."""
    payload = {
        "proposing_agent_id": "agent.decision_report.v1", "action_id": "release.blocker.1",
        "title": "Investigate blocker", "owner": "release_owner", "rationale": "failure",
        "evidence": [{"type": "decision_evidence", "id": "abc"}], "risk": "high",
        "required_permission": "release_review",
    }
    row = _row(action_type="decision_report_action", payload=payload)
    harness.config = _act_config(mode="shadow", ticked=())
    world = harness.world(row, real_adapter=True)

    outcome = await harness.run(world)

    assert outcome["status"] == "executed"
    assert row.result_payload == {"acknowledged": True, "result_link": None}
    assert row.rollback_payload is None
    assert harness.resolved == [], "acknowledge needs no Act: the config is never read"
    assert harness.bumps == [], "no effect, no analytics bump"
    (audit,) = [a for a in _audits(world) if a.action == "agent_action.executed"]
    assert audit.after_value["canonical_type"] == "decision.acknowledge"


# ── Steps 1-8: every refusal is failed + its code, ONE commit, no effect ─────
async def test_a_second_dispatch_of_a_claimed_action_does_nothing(harness):
    row = _row(status="executing")
    world = harness.world(row)
    outcome = await harness.run(world)
    assert outcome == {"status": "already_claimed", "action_id": str(row.id)}
    assert world.commits == [] and world.adapter.calls == []
    assert row.status == "executing"


@pytest.mark.parametrize(("status", "answer"), [
    ("executed", "already_executed"), ("pending_review", "not_executable"),
    ("failed", "not_executable"), ("rolled_back", "not_executable"),
])
async def test_only_an_approved_row_is_executable(harness, status, answer):
    world = harness.world(_row(status=status))
    outcome = await harness.run(world)
    assert outcome["status"] == answer
    assert world.commits == [] and world.adapter.calls == []


async def test_step2_an_unregistered_type_has_no_executor(harness):
    world = harness.world(_row(action_type="jira_ticket_creation", payload={"summary": "x"}))
    _assert_refused(world, await harness.run(world), "action_executor_not_configured")


async def test_step3_a_proposing_report_not_accepted_is_refused(harness):
    world = harness.world(_row(), review=None)
    _assert_refused(world, await harness.run(world), "review_not_accepted")
    review_sql = next(s for s in world.statements() if "FROM review_requests" in s)
    assert "review_requests.state" in review_sql and "review_requests.project_id" in review_sql


@pytest.mark.parametrize(("config", "code"), [
    (_act_config(mode="suggest"), "act_mode_required"),          # Act switched off after approval
    (_act_config(mode="act", ticked=()), "action_type_not_allowed"),  # the type unticked since
])
async def test_step4_the_live_act_config_wins_over_the_approval(harness, config, code):
    harness.config = config
    world = harness.world(_row())
    _assert_refused(world, await harness.run(world), code)


async def test_step5_a_payload_edited_after_hashing_is_tampered(harness):
    row = _row()
    row.request_payload = {**row.request_payload, "test_fingerprint": "fpOther9z"}   # hash unchanged
    world = harness.world(row)
    _assert_refused(world, await harness.run(world), "payload_tampered")


async def test_step5_a_payload_that_is_not_the_types_model_is_invalid(harness):
    payload = _quarantine_payload(extra_field="not in the model")      # re-hashed: not a tamper
    world = harness.world(_row(payload=payload))
    _assert_refused(world, await harness.run(world), "payload_invalid")


async def test_step5_a_proposer_outside_the_types_proposers_is_refused(harness):
    payload = _quarantine_payload(proposing_agent_id="agent.decision_report.v1")
    world = harness.world(_row(payload=payload))
    _assert_refused(world, await harness.run(world), "proposer_not_permitted")


@pytest.mark.parametrize(("approver_overrides", "member", "case"), [
    ({"role": "QA_ENGINEER"}, True, "demoted after approval"),
    ({}, False, "removed from the project"),
    ({"is_active": False}, True, "deactivated"),
    ({"is_synthetic": True}, True, "synthetic"),
])
async def test_step6_an_approver_no_longer_authorised_is_refused(harness, approver_overrides, member, case):
    row = _row()
    world = harness.world(row, approver=_approver(row, **approver_overrides), member=member)
    _assert_refused(world, await harness.run(world), "approver_not_authorized")


async def test_step7_a_preflight_refusal_is_recorded_with_its_code(harness):
    world = harness.world(_row(), preflight_raises=ActionRefused("target_not_in_project"))
    _assert_refused(world, await harness.run(world), "target_not_in_project")
    assert world.adapter.calls == ["preflight"]
    (audit,) = _audits(world)
    assert audit.actor_user_id == world.row.approved_by


async def test_step8_an_external_effect_never_runs_offline(harness, monkeypatch):
    from dataclasses import replace

    external = replace(ACTION_TYPES["quarantine.apply"], side_effect="external")
    monkeypatch.setattr(service, "resolve_action_type", lambda raw: external)
    monkeypatch.setattr(service.settings, "AI_OFFLINE_MODE", True)
    world = harness.world(_row())
    _assert_refused(world, await harness.run(world), "offline_mode_blocked")
    assert world.adapter.calls == ["preflight"]


# ── Step 10: a failure after the claim is recorded, never retried ────────────
def _assert_failed_after_claim(world: World, outcome: dict, code: str) -> None:
    assert outcome == {"status": "failed", "action_id": str(world.row.id), "reason": code}
    claim, finish = world.commits
    assert claim[2] == "executing" and finish[2] == "failed"
    assert len({claim[1], finish[1]}) == 2 and finish[1] == len(world.sessions) - 1, "recorded on session 3"
    assert world.row.error_code == code
    (audit,) = [o for o in finish[3] if isinstance(o, AccessAuditLog)]
    assert audit.action == "agent_action.failed" and audit.actor_user_id == world.row.approved_by
    assert len(world.sessions) == 3


async def test_an_exception_from_a_committing_adapter_is_outcome_unknown(harness):
    world = harness.world(_row(), commits_effect=True, raises=RuntimeError("release lost its connection"))
    _assert_failed_after_claim(world, await harness.run(world), "outcome_unknown")
    assert harness.bumps == [] and harness.events == []


async def test_a_refusal_from_a_committing_adapter_is_outcome_unknown_too(harness):
    # propose_quarantine answers None for a flag turned off OR a swallowed error after its own commit.
    world = harness.world(_row(), commits_effect=True, raises=ActionRefused("quarantine_disabled"))
    _assert_failed_after_claim(world, await harness.run(world), "outcome_unknown")


async def test_an_exception_from_a_stage_only_adapter_is_execution_error(harness):
    world = harness.world(_row(action_type="failure.reassign", payload=_reassign_payload()),
                          raises=RuntimeError("deadlock detected"))
    harness.config = _act_config(ticked=("failure.reassign",))
    _assert_failed_after_claim(world, await harness.run(world), "execution_error")
    # Nothing the failed effect staged reached a commit: only the claim and the failure.
    assert all(not isinstance(o, AccessAuditLog) or o.action == "agent_action.failed"
               for _, _, _, staged in world.commits for o in staged)


async def test_a_race_refused_by_a_stage_only_adapter_keeps_its_code(harness):
    world = harness.world(_row(action_type="failure.reassign", payload=_reassign_payload()),
                          raises=ActionRefused("assignee_no_longer_owner"))
    harness.config = _act_config(ticked=("failure.reassign",))
    _assert_failed_after_claim(world, await harness.run(world), "assignee_no_longer_owner")


async def test_a_worker_killed_after_the_claim_leaves_executing_for_the_reaper(harness):
    # CancelledError is a BaseException: exactly what a SIGKILL'd / time-limited task leaves.
    world = harness.world(_row(), commits_effect=True, raises=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await harness.run(world)
    assert world.row.status == "executing" and len(world.commits) == 1
    again = await harness.run(world)
    assert again == {"status": "already_claimed", "action_id": str(world.row.id)}
    assert world.adapter.calls.count("execute") == 1, "never retried"


async def test_a_claim_taken_over_before_the_effect_does_nothing(harness, monkeypatch):
    world = harness.world(_row())
    real_lock = service._lock_claimed

    async def lose(db: Any, claim: Any) -> Any:
        world.row.execution_started_at = datetime(2001, 1, 1, tzinfo=timezone.utc)   # someone else's fence
        return await real_lock(db, claim)

    monkeypatch.setattr(service, "_lock_claimed", lose)
    outcome = await harness.run(world)
    assert outcome == {"status": "claim_lost", "action_id": str(world.row.id)}
    assert "execute" not in world.adapter.calls and len(world.commits) == 1


def _reassign_payload() -> dict[str, Any]:
    payload = _quarantine_payload(
        title="Reassign checkout retry to the Payments owner", risk="medium",
        test_case_id=str(uuid.uuid4()), assignee_user_id=str(uuid.uuid4()),
    )
    payload.pop("test_fingerprint")
    return payload


# ── The decision report's frozen mode (§0.5 / §0.14) ─────────────────────────
async def test_report_proposals_carry_the_frozen_mode_only_when_given():
    from unittest.mock import AsyncMock, MagicMock

    def _db() -> MagicMock:
        db = MagicMock()
        empty = MagicMock()
        empty.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=empty)
        db.flush = AsyncMock()
        return db

    item = {"action_id": "release.blocker.1", "idempotency_key": "release:blocker:1", "title": "Investigate"}
    ids = {"project_id": uuid.uuid4(), "test_run_id": uuid.uuid4(), "pipeline_run_id": uuid.uuid4()}
    with_mode, without = _db(), _db()
    await service.persist_report_action_proposals(
        with_mode, **ids, proposed_actions=[item], proposing_agent_mode="suggest",
    )
    await service.persist_report_action_proposals(without, **ids, proposed_actions=[item])
    assert _ledger_row(with_mode).request_payload["proposing_agent_mode"] == "suggest"
    assert "proposing_agent_mode" not in _ledger_row(without).request_payload


@pytest.mark.parametrize(("metadata", "expected"), [
    ({"workflow_agent_configs": {"agent.decision_report.v1": {"mode": "suggest"}}}, "suggest"),
    ({"workflow_agent_configs": {"agent.decision_report.v1": {"mode": "act"}}}, "act"),
    ({"workflow_agent_configs": {"agent.decision_report.v1": {"mode": "autonomous"}}}, None),
    ({"workflow_agent_configs": {}}, None),
    ({}, None),
    (None, None),
])
async def test_the_pipeline_passes_the_decision_reports_frozen_mode(monkeypatch, metadata, expected):
    from unittest.mock import AsyncMock

    from app.agents import workflow
    from app.services import agent_action_ledger_service, review_request_service, run_downstream_outbox
    from tests.test_finalize_creates_review_request import _pipeline, _Session, _stage

    pipeline = _pipeline()
    pipeline.execution_metadata = metadata
    persist = AsyncMock(return_value=1)
    monkeypatch.setattr(workflow, "AsyncSessionLocal", lambda: _Session(pipeline, [_stage("summary")]))
    monkeypatch.setattr(review_request_service, "stage_run_review_request", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_action_ledger_service, "persist_report_action_proposals", persist)
    monkeypatch.setattr(run_downstream_outbox, "stage_ai_summary_notification_operation", AsyncMock())
    final_state = {
        "project_id": str(uuid.uuid4()), "test_run_id": str(pipeline.test_run_id),
        "decision_intelligence": {"proposed_actions": [{"action_id": "a1", "idempotency_key": "k1"}]},
    }

    await workflow._mark_pipeline_done(str(pipeline.id), success=True, error=None, final_state=final_state)

    persist.assert_awaited_once()
    assert persist.await_args.kwargs["proposing_agent_mode"] == expected


def _ledger_row(db):
    """The ledger row the service added (W21 ACT-01: an agent's proposal also adds its activity row)."""
    from app.models.postgres import AgentActionLedger

    return [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], AgentActionLedger)][-1]
