"""``release.decided`` is sent, not just offered (regression).

The defect
----------
``webhook_service.SUPPORTED_EVENTS`` listed ``release.decided``, and the webhook
API accepted subscriptions to it, but no code path ever called ``emit_event``
with it. A subscriber got a success response and then never received a delivery.
A release decision is written in two places, and both now emit after their commit:

* ``ReleaseRiskAgent._persist_decision`` (``trigger="agent"``);
* ``POST /api/v1/release-readiness/{run_id}/override`` (``trigger="override"``).

What is pinned
--------------
* an agent decision stages exactly one delivery, after the decision's commit,
  published through the real ``deliver_webhook`` signature and carrying the
  payload a receiver is promised; a retried write stages nothing more;
* a decision whose commit fails is never announced;
* an override emits after the route's commit, marked ``overridden``, with no
  reviewer or overrider identity; each override is its own delivery;
* while ``REVIEW_GATE_ENFORCED`` is on, an unreviewed AI decision leaves as
  ``PENDING_REVIEW`` with the model's value in ``draft_recommendation``, and the
  webhook and the release-readiness response apply one rule;
* every catalog event has a producer, so the next declared-but-never-sent event
  fails here instead of in a customer's receiver.
"""
from __future__ import annotations

import ast
import inspect
import itertools
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.models.postgres import ReleaseDecision
from app.models.schemas import ReleaseCouncilOverrideRequest, ReleaseCouncilResponse
from app.services import release_decision_webhook as emitter
from app.services import report_distribution_policy as policy
from app.services import webhook_service
from app.services.review_envelope import ReviewEnvelope

RUN = uuid.uuid4()
PROJECT = uuid.uuid4()
PIPELINE = uuid.uuid4()
CREATED = datetime(2026, 9, 12, 18, 30, tzinfo=timezone.utc)
UPDATED = datetime(2026, 9, 12, 19, 5, tzinfo=timezone.utc)
REVIEW_ID = str(uuid.uuid4())
REVIEWED_AT = "2026-09-12T20:00:00+00:00"

AGENT_DECISION = {
    "recommendation": "GO",
    "risk_score": 14,
    "blocking_issues": ["checkout-api p95 latency regressed 12%"],
    "conditions_for_go": ["re-run the payments suite on staging"],
    "reasoning": "low composite risk",
}


class _Result:
    def __init__(self, *, rows=None, scalar=None, first=None):
        self._rows = list(rows or [])
        self._scalar = scalar
        self._first = first

    def scalar_one_or_none(self):
        return self._scalar

    def first(self):
        return self._first

    def all(self):
        return list(self._rows)

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


class _Session:
    """An ``AsyncSessionLocal()`` stand-in that records its commit in ``log``."""

    def __init__(self, execute, *, log, name, fail_commit=False, on_add=None):
        self._execute = execute
        self._log = log
        self._name = name
        self._fail_commit = fail_commit
        self._on_add = on_add

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, statement, *_args):
        return await self._execute(statement)

    def add(self, row):
        if self._on_add is not None:
            self._on_add(row)

    async def flush(self):
        return None

    async def refresh(self, _row):
        return None

    async def commit(self):
        if self._fail_commit:
            raise RuntimeError("could not serialize access due to concurrent update")
        self._log.append(f"{self._name}_commit")


# First row of a multi-VALUES insert binds plain column names, later rows ``<col>_m<n>``.
_MULTI_PARAM = re.compile(r"^(?P<column>.+)_m(?P<index>\d+)$")


class _WebhookTable:
    """Plays PostgreSQL for ``emit_event``: the subscription scan, then the real
    ``INSERT ... ON CONFLICT (delivery_key) DO NOTHING``, whose rows are read out
    of the compiled statement, so the insert being checked is the real one."""

    def __init__(self, log):
        self.log = log
        self.subscription = SimpleNamespace(
            id=uuid.uuid4(), project_id=PROJECT, enabled=True, events=["release.decided"]
        )
        self.rows: dict[str, dict] = {}

    def session(self):
        return _Session(self._execute, log=self.log, name="webhook")

    async def _execute(self, statement):
        if not getattr(statement, "is_insert", False):
            return _Result(rows=[self.subscription])
        params = statement.compile(dialect=postgresql.dialect()).params
        staged: dict[int, dict] = {}
        for key, value in params.items():
            match = _MULTI_PARAM.match(key)
            index, column = (int(match["index"]), match["column"]) if match else (0, key)
            staged.setdefault(index, {})[column] = value
        inserted = []
        for row in staged.values():
            if row["delivery_key"] in self.rows:
                continue
            self.rows[row["delivery_key"]] = row
            self.log.append("stage")
            inserted.append(SimpleNamespace(id=row["id"], subscription_id=row["subscription_id"]))
        return _Result(rows=inserted)


@pytest.fixture
def world(monkeypatch):
    from app.core.config import settings
    from app.worker import tasks as worker_tasks

    log: list[str] = []
    table = _WebhookTable(log)
    published: list[dict] = []
    task_signature = inspect.signature(worker_tasks.deliver_webhook.run)

    def _delay(*args, **kwargs):
        # A renamed or added required task argument fails here, not in a worker.
        task_signature.bind(*args, **kwargs)
        published.append(kwargs)

    state = SimpleNamespace(log=log, table=table, published=published, review="pending_review", decision=None)

    async def _envelope_for(db, run_id, workflow_type=None, ai_generated=True):
        assert workflow_type == "deep"
        if state.review == "accepted":
            return ReviewEnvelope(True, "accepted", "accepted", REVIEW_ID, REVIEWED_AT)
        return ReviewEnvelope(True, state.review, state.review, REVIEW_ID)

    async def _envelope_for_subject(db, pipeline_run_id, ai_generated=True):
        assert pipeline_run_id == PIPELINE
        if state.review == "accepted":
            return ReviewEnvelope(True, "accepted", "accepted", REVIEW_ID, REVIEWED_AT)
        return ReviewEnvelope(True, state.review, state.review, REVIEW_ID)

    async def _decision_lookup(_statement):
        found = (state.decision, PROJECT) if state.decision is not None else None
        return _Result(first=found)

    async def _council(run_id, db, baseline_diff=None):
        d = state.decision
        return ReleaseCouncilResponse(
            run_id=str(run_id),
            recommendation=d.recommendation,
            risk_score=d.risk_score,
            blocking_issues=d.blocking_issues or [],
            conditions_for_go=d.conditions_for_go or [],
            reasoning=d.reasoning,
            human_override=d.human_override,
            overridden_by=str(d.overridden_by) if d.overridden_by else None,
        )

    monkeypatch.setattr(webhook_service, "AsyncSessionLocal", table.session)
    monkeypatch.setattr(webhook_service, "_post_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(worker_tasks.deliver_webhook, "delay", _delay)
    monkeypatch.setattr(
        emitter, "AsyncSessionLocal", lambda: _Session(_decision_lookup, log=log, name="emitter")
    )
    monkeypatch.setattr(emitter, "get_release_council", _council)
    monkeypatch.setattr(policy, "review_envelope_for_run", _envelope_for)
    monkeypatch.setattr(policy, "review_envelope_for_pipeline_subject", _envelope_for_subject)
    monkeypatch.setattr(policy, "_project_allows_drafts", AsyncMock(return_value=False))
    monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", False)
    state.enforce = lambda on: monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", on)
    return state


async def _persist_agent_decision(monkeypatch, world, *, fail_commit=False):
    from app.agents import release_risk_agent as agent_module

    def _on_add(row):
        # What PostgreSQL fills in on INSERT; the emitter reads the row back.
        row.created_at = CREATED
        row.updated_at = CREATED
        world.decision = row

    async def _no_existing_row(_statement):
        return _Result(scalar=None)

    session = _Session(
        _no_existing_row, log=world.log, name="decision", fail_commit=fail_commit, on_add=_on_add
    )
    monkeypatch.setattr(agent_module, "AsyncSessionLocal", lambda: session)
    agent = agent_module.ReleaseRiskAgent.__new__(agent_module.ReleaseRiskAgent)
    await agent._persist_decision(
        str(RUN), str(PIPELINE), dict(AGENT_DECISION), input_snapshot={"pass_rate": 0.97}
    )


def _only_delivery(world) -> dict:
    (row,) = world.table.rows.values()
    return row


# ── The agent's write ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_agent_decision_stages_exactly_one_delivery_after_its_commit(monkeypatch, world):
    await _persist_agent_decision(monkeypatch, world)

    row = _only_delivery(world)
    assert world.log == ["decision_commit", "stage", "webhook_commit"]
    assert row["event_type"] == "release.decided"
    assert row["subscription_id"] == world.table.subscription.id
    assert row["run_id"] == RUN
    assert row["event_payload"] == {
        "run_id": str(RUN),
        "project_id": str(PROJECT),
        "pipeline_run_id": str(PIPELINE),
        "trigger": "agent",
        "recommendation": "GO",
        "draft_recommendation": None,
        "risk_score": 14,
        "blocking_issues": ["checkout-api p95 latency regressed 12%"],
        "conditions_for_go": ["re-run the payments suite on staging"],
        "synthesized": False,
        "overridden": False,
        "requires_human_review": True,
        "review": {"state": "pending_review", "review_id": REVIEW_ID, "reviewed_at": None},
        "review_gate_enforced": False,
        "created_at": CREATED.isoformat(),
        "updated_at": CREATED.isoformat(),
    }
    json.dumps(row["event_payload"])  # it is sent as a JSON body
    assert world.published == [{"delivery_id": str(row["id"])}]

    # A retried write for the same pipeline run is the same event.
    assert await emitter.emit_release_decided(RUN, trigger=emitter.TRIGGER_AGENT) == 0
    assert len(world.table.rows) == 1
    assert len(world.published) == 1


@pytest.mark.asyncio
async def test_a_decision_that_fails_to_commit_is_never_announced(monkeypatch, world):
    with pytest.raises(RuntimeError, match="could not serialize"):
        await _persist_agent_decision(monkeypatch, world, fail_commit=True)

    assert world.table.rows == {}
    assert world.published == []


# ── The human override ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_override_is_announced_after_the_route_commits(monkeypatch, world):
    from app.routers import release_readiness as route

    reason = "accepting the known payments flake for this hotfix"
    overrider = SimpleNamespace(id=uuid.uuid4(), username="dana.qa", full_name="Dana Q")
    world.enforce(True)  # an override is a person's decision; the gate leaves it alone
    world.decision = ReleaseDecision(
        test_run_id=RUN,
        pipeline_run_id=PIPELINE,
        recommendation="GO",
        risk_score=71,
        blocking_issues=["payments suite 3 failures"],
        conditions_for_go=[],
        human_override=reason,
        overridden_by=overrider.id,
        original_recommendation="NO_GO",
        original_risk_score=71,
        override_audit=[{"actor_id": str(overrider.id), "actor_name": "dana.qa", "reason": reason}],
        created_at=CREATED,
        updated_at=UPDATED,
    )
    council = SimpleNamespace(recommendation="GO")
    monkeypatch.setattr(route, "apply_override", AsyncMock(return_value=council))
    monkeypatch.setattr(
        "app.services.intelligence_snapshot_service.mark_stale", AsyncMock(return_value=None)
    )

    async def _unused(_statement):
        raise AssertionError("the route's own session runs no query here")

    monkeypatch.setattr(
        route, "AsyncSessionLocal", lambda: _Session(_unused, log=world.log, name="override")
    )
    body = ReleaseCouncilOverrideRequest(override_recommendation="GO", reason=reason)

    assert await route.override_release_decision(
        run_id=RUN, body=body, current_user=overrider, _=overrider
    ) is council

    assert world.log == ["override_commit", "stage", "webhook_commit"]
    payload = _only_delivery(world)["event_payload"]
    assert payload["trigger"] == "override"
    assert payload["overridden"] is True
    assert payload["recommendation"] == "GO"
    assert payload["draft_recommendation"] is None
    assert payload["review_gate_enforced"] is True
    assert payload["created_at"] == CREATED.isoformat()
    assert payload["updated_at"] == UPDATED.isoformat()
    sent = json.dumps(payload)
    for identity in (str(overrider.id), "dana.qa", "Dana Q", reason):
        assert identity not in sent

    # A second override is a second decision, and a second delivery.
    world.decision.override_audit = [*world.decision.override_audit, {"reason": "re-confirmed"}]
    await route.override_release_decision(run_id=RUN, body=body, current_user=overrider, _=overrider)
    assert len(world.table.rows) == 2
    assert len(world.published) == 2


# ── The human-review gate (E8.4) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_while_enforced_an_unreviewed_ai_decision_leaves_as_pending_review(monkeypatch, world):
    world.enforce(True)
    await _persist_agent_decision(monkeypatch, world)

    payload = _only_delivery(world)["event_payload"]
    assert payload["recommendation"] == "PENDING_REVIEW"
    assert payload["draft_recommendation"] == "GO"
    assert payload["review"] == {"state": "pending_review", "review_id": REVIEW_ID, "reviewed_at": None}
    assert payload["review_gate_enforced"] is True


@pytest.mark.asyncio
async def test_while_enforced_an_accepted_ai_decision_leaves_unchanged(monkeypatch, world):
    world.enforce(True)
    world.review = "accepted"
    await _persist_agent_decision(monkeypatch, world)

    payload = _only_delivery(world)["event_payload"]
    assert payload["recommendation"] == "GO"
    assert payload["draft_recommendation"] is None
    assert payload["review"] == {"state": "accepted", "review_id": REVIEW_ID, "reviewed_at": REVIEWED_AT}


@pytest.mark.asyncio
async def test_webhook_retry_rechecks_review_and_restores_the_original_decision(monkeypatch):
    """A queued payload is a source artifact, not a frozen authorization decision."""
    stored = {
        "run_id": str(RUN),
        "project_id": str(PROJECT),
        "pipeline_run_id": str(PIPELINE),
        "recommendation": "PENDING_REVIEW",
        "draft_recommendation": "GO",
        "risk_score": 14,
        "blocking_issues": ["latency regression"],
        "conditions_for_go": ["rerun staging"],
        "synthesized": False,
        "overridden": False,
        "requires_human_review": True,
        "review": {"state": "pending_review", "review_id": REVIEW_ID, "reviewed_at": None},
        "review_gate_enforced": True,
    }
    pending = dict(stored)
    accepted = {
        **stored,
        "recommendation": "GO",
        "draft_recommendation": None,
        "review": {"state": "accepted", "review_id": REVIEW_ID, "reviewed_at": REVIEWED_AT},
    }
    gate = AsyncMock(side_effect=[(pending, object()), (accepted, object())])
    monkeypatch.setattr(policy, "gate_release_decided_delivery", gate)
    delivery = SimpleNamespace(
        event_type="release.decided",
        run_id=RUN,
        event_payload=stored,
    )

    first = await webhook_service._refresh_review_gated_payload(None, delivery)
    second = await webhook_service._refresh_review_gated_payload(None, delivery)

    assert first["recommendation"] == "PENDING_REVIEW"
    assert second["recommendation"] == "GO"
    assert gate.await_count == 2
    for call in gate.await_args_list:
        source = call.args[1]
        assert source["recommendation"] == "GO"
        assert "draft_recommendation" not in source
        assert "requires_human_review" not in source
        assert "review" not in source
        assert "review_gate_enforced" not in source
        assert call.kwargs["pipeline_run_id"] == PIPELINE


@pytest.mark.asyncio
async def test_release_webhook_honours_the_project_draft_setting(monkeypatch, world):
    world.enforce(True)
    monkeypatch.setattr(policy, "_project_allows_drafts", AsyncMock(return_value=True))

    payload, decision = await policy.gate_release_decided_delivery(
        None,
        {
            "project_id": str(PROJECT),
            "recommendation": "GO",
            "blocking_issues": ["draft blocker"],
        },
        run_id=RUN,
        synthesized=False,
        human_override=None,
        pipeline_run_id=PIPELINE,
        project_id=PROJECT,
    )

    assert decision.reason == policy.PROJECT_ALLOWS_DRAFTS
    assert payload["recommendation"] == "GO"
    assert payload["draft_recommendation"] is None
    assert payload["draft_watermark"] == policy.DRAFT_WATERMARK


@pytest.mark.asyncio
async def test_release_webhook_reports_would_refuse_while_enforcement_is_off(world):
    world.enforce(False)

    payload, decision = await policy.gate_release_decided_delivery(
        None,
        {"project_id": str(PROJECT), "recommendation": "NO_GO"},
        run_id=RUN,
        synthesized=False,
        human_override=None,
        pipeline_run_id=PIPELINE,
        project_id=PROJECT,
    )

    assert decision.reason == policy.WOULD_REFUSE
    assert decision.audit_action == "ai_report.distribution_would_refuse"
    assert payload["recommendation"] == "NO_GO"
    assert "draft_watermark" not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize("review", ["rejected", "superseded"])
async def test_terminal_review_never_leaves_in_release_webhook_content(world, review):
    world.enforce(True)
    world.review = review

    payload = await policy.gate_release_decided_payload(
        None,
        {
            "recommendation": "NO_GO",
            "original_recommendation": "GO",
            "blocking_issues": ["rejected blocker narrative"],
            "conditions_for_go": ["rejected condition narrative"],
            "reasoning": "rejected reasoning narrative",
        },
        run_id=RUN,
        synthesized=False,
        human_override=None,
    )

    assert payload["recommendation"] == "PENDING_REVIEW"
    assert payload["draft_recommendation"] is None
    assert payload["blocking_issues"] == []
    assert payload["conditions_for_go"] == []
    assert payload["reasoning"] is None
    assert payload["original_recommendation"] is None


@pytest.mark.asyncio
async def test_release_webhook_uses_its_exact_pipeline_review(monkeypatch, world):
    world.enforce(True)

    async def _wrong_newer_review(*_args, **_kwargs):
        return ReviewEnvelope(True, "accepted", "accepted", str(uuid.uuid4()), REVIEWED_AT)

    monkeypatch.setattr(policy, "review_envelope_for_run", _wrong_newer_review)
    world.review = "rejected"
    payload = await policy.gate_release_decided_payload(
        None,
        {
            "recommendation": "NO_GO",
            "blocking_issues": ["pipeline-specific blocker"],
            "conditions_for_go": ["pipeline-specific condition"],
        },
        run_id=RUN,
        synthesized=False,
        human_override=None,
        pipeline_run_id=PIPELINE,
    )

    assert payload["review"]["state"] == "rejected"
    assert payload["recommendation"] == "PENDING_REVIEW"
    assert payload["draft_recommendation"] is None
    assert payload["blocking_issues"] == []
    assert payload["conditions_for_go"] == []


@pytest.mark.asyncio
async def test_the_webhook_and_the_release_readiness_response_apply_one_rule(world):
    for enforced, review, override in itertools.product((False, True), ("pending_review", "accepted"), (None, "why")):
        world.enforce(enforced)
        world.review = review
        council = ReleaseCouncilResponse(
            run_id=str(RUN), recommendation="NO_GO", risk_score=80, human_override=override
        )
        response = await policy.apply_release_review_gate(None, council, run_id=RUN)
        payload = await policy.gate_release_decided_payload(
            None, {"recommendation": "NO_GO"}, run_id=RUN, synthesized=False, human_override=override
        )
        case = (enforced, review, override)
        assert payload["recommendation"] == response.recommendation, case
        assert payload["draft_recommendation"] == response.draft_recommendation, case


# ── The class of bug ─────────────────────────────────────────────────────────

APP = Path(__file__).resolve().parents[1] / "app"

#: Offered in the catalog with no producer yet. Remove an entry when its producer
#: lands; the test fails if a listed event gains a producer and stays listed.
KNOWN_UNSENT = {"defect.promoted"}


def _emitted_event_literals() -> set[str]:
    emitted: set[str] = set()
    for path in APP.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            first = node.args[0]
            if name == "emit_event" and isinstance(first, ast.Constant) and isinstance(first.value, str):
                emitted.add(first.value)
    return emitted


def test_every_catalog_event_has_a_producer():
    unsent = set(webhook_service.SUPPORTED_EVENTS) - _emitted_event_literals()
    assert unsent == KNOWN_UNSENT
