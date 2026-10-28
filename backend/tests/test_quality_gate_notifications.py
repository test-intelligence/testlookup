"""W09 NTF-01: "Quality gate failed" is staged on a recorded transition INTO NO_GO.

What is pinned here, without a database:

1. ``is_transition_into_no_go`` (None counts as "not NO_GO");
2. both gate services read the previous verdict only when recording, stage only
   on a transition into NO_GO, and a preview (``record=False``) reads nothing;
3. a stage that fails (a fake session without ``begin_nested``, a staging error)
   never changes ``evaluate_release``'s answer and logs one warning;
4. the title / body builders: names verbatim, clipping, ``+N more``, the empty case;
5. ``app_link``'s allow-list (whole-value equality), the preference normaliser;
6. the override handler: an override INTO NO_GO stages under its ordinal,
   before the commit; out of NO_GO, or to GO, stages nothing;
7. the real notification email over a gate row: no ``None%``, a hostile name escaped;
8. ``stage_agent_decision_no_go``'s outcome table, through the REAL
   ``release_review_projection`` (only the envelope lookup is faked);
9. the decision-report critic enqueues ``notify_release_decision_no_go`` on
   ``default`` with arguments that bind to the REAL task signature, and a broker
   failure never fails ``_persist``.

The PostgreSQL behaviour (recipients, de-duplication, the history link) runs in
``tests/integration/test_quality_gate_notifications_postgres.py``.
"""
from __future__ import annotations

import inspect
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from app.models.postgres import NotificationEventType
from app.services.notification import gate_notifications as gn

RELEASE_ID = uuid.UUID("11111111-2222-4333-8444-555555555555")
PHASE_ID = uuid.UUID("66666666-7777-4888-9999-aaaaaaaaaaaa")
PROJECT_ID = uuid.UUID("bbbbbbbb-cccc-4ddd-8eee-ffffffffffff")
RUN_ID = uuid.UUID("12345678-1234-4234-8234-123456789abc")
FP = "9f2c41d07ab3e815"
RAW_REASON = f"checkout-e2e::{FP} is FAILED"


# ── fakes ────────────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, value=None, rows=None, first=None):
        self._value = value
        self._rows = rows or []
        self._first = first

    def scalar_one_or_none(self):
        return self._value

    def first(self):
        return self._first

    def all(self):
        return self._rows


class _GateSession:
    """Answers the gate services' and the stage's reads by the table they name."""

    def __init__(self, *, release=None, phase=None, nested=True, project_name="Checkout"):
        self.release = release
        self.phase = phase
        self.project_name = project_name
        self.statements: list[str] = []
        if nested:
            self.begin_nested = self._begin_nested
        self.savepoints = 0

    @asynccontextmanager
    async def _begin_nested(self):
        self.savepoints += 1
        yield self

    async def execute(self, stmt, *args, **kwargs):
        sql = " ".join(str(stmt).split())
        self.statements.append(sql)
        if "FROM release_phases" in sql:
            return _Result(self.phase)
        if "JOIN projects" in sql and "FROM releases" in sql:
            return _Result(first=(self.release, self.project_name) if self.release else None)
        if "FROM releases" in sql:
            return _Result(self.release)
        if "FROM test_cases" in sql:
            return _Result(rows=[(FP, "test_pay")])
        return _Result()

    def add(self, obj):
        pass

    async def flush(self):
        pass


def _release(name="2026.10"):
    return SimpleNamespace(id=RELEASE_ID, project_id=PROJECT_ID, name=name, baseline_release_id=None)


def _phase():
    return SimpleNamespace(id=PHASE_ID, name="Staging", project_id=PROJECT_ID, exit_criteria=None)


@pytest.fixture
def gate(monkeypatch):
    """Both gate services with the rollup, defects, policy and the decision table faked."""
    from app.services import release_gate_service as release_gate
    from app.services import release_phase_gate_service as phase_gate

    state = SimpleNamespace(verdict="NO_GO", reasons=[RAW_REASON], previous=None, made=[], staged=[])

    async def build_rollup(*args, **kwargs):
        return SimpleNamespace(
            denominator=10, evidence_count=10, run_ids=[], status_counts={}, attribution_mix={},
            incomplete_runs={},
        )

    monkeypatch.setattr(release_gate.rollup_svc, "build_rollup", build_rollup)
    monkeypatch.setattr(release_gate.rollup_svc, "decide", lambda rollup: (state.verdict, list(state.reasons)))
    monkeypatch.setattr(release_gate.rollup_svc, "summarise", lambda rollup: {})
    monkeypatch.setattr(
        release_gate.defect_svc, "defects_for_release",
        AsyncMock(return_value={"verdict": "GO", "reasons": []}),
    )
    monkeypatch.setattr(
        phase_gate.policy_resolution, "resolve_effective_document",
        AsyncMock(return_value={"document": {}, "sources": {}, "layers": [], "effective_level": "project"}),
    )

    async def current_decision(db, release_id, *, phase_id=None):
        return None if state.previous is None else SimpleNamespace(verdict=state.previous)

    async def record_decision(db, release_id, verdict, **kwargs):
        made = SimpleNamespace(id=uuid.uuid4(), verdict=verdict, blocking_reasons=kwargs.get("blocking_reasons"))
        state.made.append(made)
        return made

    async def stage(db, **kwargs):
        state.staged.append(kwargs)
        return True

    monkeypatch.setattr(release_gate.decisions, "current_decision", current_decision)
    monkeypatch.setattr(release_gate.decisions, "record_decision", record_decision)
    monkeypatch.setattr(gn, "stage_gate_no_go", stage)
    state.release_gate = release_gate
    state.phase_gate = phase_gate
    return state


# ── 1. the transition rule ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "previous, verdict, expected",
    [
        (None, "NO_GO", True),
        ("GO", "NO_GO", True),
        ("NOT_EVALUATED", "NO_GO", True),
        ("CONDITIONAL_GO", "NO_GO", True),
        ("NO_GO", "NO_GO", False),
        ("NO_GO", "GO", False),
        ("GO", "GO", False),
        (None, "NOT_EVALUATED", False),
    ],
)
def test_is_transition_into_no_go(previous, verdict, expected):
    assert gn.is_transition_into_no_go(previous, verdict) is expected


def test_the_delivery_scopes():
    decision_id = uuid.UUID("abcdefab-cdef-4abc-8def-abcdefabcdef")
    assert gn.gate_delivery_scope(decision_id) == f"quality-gate-failed:gate:{decision_id}:v1"
    assert gn.run_delivery_scope(RUN_ID) == f"quality-gate-failed:run:{RUN_ID}:agent:v1"
    assert gn.run_delivery_scope(RUN_ID, override_ordinal=2) == f"quality-gate-failed:run:{RUN_ID}:override:2:v1"
    assert gn.EVENT is NotificationEventType.QUALITY_GATE_FAILED


# ── 2. the gate services stage only on a transition ─────────────────────────


@pytest.mark.asyncio
async def test_evaluate_release_does_not_stage_a_repeated_no_go(gate):
    gate.previous = "NO_GO"
    result = await gate.release_gate.evaluate_release(_GateSession(release=_release()), RELEASE_ID)
    assert result["verdict"] == "NO_GO" and result["decision_id"] == str(gate.made[0].id)
    assert gate.staged == []


@pytest.mark.asyncio
@pytest.mark.parametrize("previous", ["GO", "NOT_EVALUATED", None])
async def test_evaluate_release_stages_a_transition_with_the_new_decision(gate, previous):
    gate.previous = previous
    await gate.release_gate.evaluate_release(_GateSession(release=_release()), RELEASE_ID)
    (made,) = gate.made
    assert gate.staged == [{"release_id": RELEASE_ID, "decision": made, "previous_verdict": previous}]


@pytest.mark.asyncio
async def test_evaluate_release_does_not_stage_go(gate):
    gate.previous, gate.verdict, gate.reasons = "NO_GO", "GO", []
    await gate.release_gate.evaluate_release(_GateSession(release=_release()), RELEASE_ID)
    assert gate.staged == []


@pytest.mark.asyncio
async def test_a_release_preview_reads_no_previous_verdict_and_stages_nothing(gate, monkeypatch):
    forbidden = AsyncMock(side_effect=AssertionError("a preview must not read or stage"))
    monkeypatch.setattr(gate.release_gate.decisions, "current_decision", forbidden)
    monkeypatch.setattr(gate.release_gate.decisions, "record_decision", forbidden)
    monkeypatch.setattr(gn, "stage_gate_no_go", forbidden)
    result = await gate.release_gate.evaluate_release(_GateSession(release=_release()), RELEASE_ID, record=False)
    assert result["recorded"] is False and result["decision_id"] is None
    forbidden.assert_not_awaited()


@pytest.mark.asyncio
async def test_evaluate_phase_does_not_stage_a_repeated_no_go(gate):
    gate.previous = "NO_GO"
    await gate.phase_gate.evaluate_phase(_GateSession(phase=_phase()), RELEASE_ID, PHASE_ID)
    assert gate.staged == []


@pytest.mark.asyncio
async def test_evaluate_phase_stages_a_transition_with_the_phase(gate):
    gate.previous = "GO"
    phase = _phase()
    await gate.phase_gate.evaluate_phase(_GateSession(phase=phase), RELEASE_ID, PHASE_ID)
    (made,) = gate.made
    assert gate.staged == [
        {"release_id": RELEASE_ID, "decision": made, "previous_verdict": "GO", "phase": phase}
    ]


@pytest.mark.asyncio
async def test_evaluate_phase_reads_the_phases_own_previous_verdict(gate, monkeypatch):
    asked: list = []

    async def current_decision(db, release_id, *, phase_id=None):
        asked.append(phase_id)
        return SimpleNamespace(verdict="GO")

    monkeypatch.setattr(gate.phase_gate.decisions, "current_decision", current_decision)
    await gate.phase_gate.evaluate_phase(_GateSession(phase=_phase()), RELEASE_ID, PHASE_ID)
    assert asked == [PHASE_ID]


@pytest.mark.asyncio
async def test_a_phase_preview_reads_no_previous_verdict_and_stages_nothing(gate, monkeypatch):
    forbidden = AsyncMock(side_effect=AssertionError("a preview must not read or stage"))
    monkeypatch.setattr(gate.phase_gate.decisions, "current_decision", forbidden)
    monkeypatch.setattr(gate.phase_gate.decisions, "record_decision", forbidden)
    monkeypatch.setattr(gn, "stage_gate_no_go", forbidden)
    result = await gate.phase_gate.evaluate_phase(_GateSession(phase=_phase()), RELEASE_ID, PHASE_ID, record=False)
    assert result["recorded"] is False and result["decision_id"] is None
    forbidden.assert_not_awaited()


# ── 3. a failing stage never fails the gate ─────────────────────────────────


#: The real coroutine, taken when this module is imported (before any fixture patches it).
_REAL_STAGE_GATE_NO_GO = gn.stage_gate_no_go


@pytest.fixture
def real_stage(gate, monkeypatch):
    """The gate fixture with the REAL ``stage_gate_no_go`` put back."""
    monkeypatch.setattr(gn, "stage_gate_no_go", _REAL_STAGE_GATE_NO_GO)
    return gate


@pytest.mark.asyncio
async def test_a_session_without_savepoints_leaves_the_decision_intact(real_stage):
    real_stage.previous = "GO"
    with structlog.testing.capture_logs() as logs:
        result = await real_stage.release_gate.evaluate_release(
            _GateSession(release=_release(), nested=False), RELEASE_ID,
        )
    (made,) = real_stage.made
    assert result["verdict"] == "NO_GO" and result["decision_id"] == str(made.id)
    assert [(e["event"], e["error_type"]) for e in logs if e["event"] == "quality_gate_notification_failed"] == [
        ("quality_gate_notification_failed", "AttributeError")
    ]


@pytest.mark.asyncio
async def test_a_staging_error_leaves_the_decision_intact(real_stage, monkeypatch):
    real_stage.previous = "GO"
    monkeypatch.setattr(gn.manager, "stage_scoped_preference_deliveries", AsyncMock(side_effect=RuntimeError("boom")))
    session = _GateSession(release=_release())
    with structlog.testing.capture_logs() as logs:
        result = await real_stage.release_gate.evaluate_release(session, RELEASE_ID)
    (made,) = real_stage.made
    assert result["verdict"] == "NO_GO" and result["decision_id"] == str(made.id)
    assert session.savepoints == 1
    warnings = [e for e in logs if e["event"] == "quality_gate_notification_failed"]
    assert [(e["release_id"], e["error_type"]) for e in warnings] == [(str(RELEASE_ID), "RuntimeError")]


@pytest.mark.asyncio
async def test_the_stage_translates_the_reasons_and_stages_one_scoped_entry(real_stage, monkeypatch):
    real_stage.previous = "GO"
    real_stage.reasons = [RAW_REASON, "defect DEF-1 is CRITICAL"]
    staged = AsyncMock()
    monkeypatch.setattr(gn.manager, "stage_scoped_preference_deliveries", staged)
    session = _GateSession(release=_release())
    await real_stage.release_gate.evaluate_release(session, RELEASE_ID)
    (made,) = real_stage.made
    (call,) = staged.await_args_list
    assert call.args == (session,)
    kwargs = call.kwargs
    assert kwargs["project_id"] == PROJECT_ID and kwargs["run_id"] is None
    assert kwargs["events"] == [NotificationEventType.QUALITY_GATE_FAILED]
    assert kwargs["delivery_scope"] == f"quality-gate-failed:gate:{made.id}:v1"
    title, body = kwargs["build_title_fn"](NotificationEventType.QUALITY_GATE_FAILED)
    assert title == "Quality gate failed: 2026.10 — test_pay (checkout-e2e) is FAILED"
    assert body == "• test_pay (checkout-e2e) is FAILED\n• defect DEF-1 is CRITICAL"
    meta = kwargs["metadata"]
    assert meta["app_path"] == f"/releases/{RELEASE_ID}"
    assert meta["release_id"] == str(RELEASE_ID) and meta["decision_id"] == str(made.id)
    assert meta["previous_verdict"] == "GO" and meta["verdict"] == "NO_GO"
    assert meta["project_name"] == "Checkout" and meta["phase_id"] is None
    assert not {"pass_rate", "total_tests", "failed_tests"} & set(meta)
    assert session.savepoints == 1


@pytest.mark.asyncio
async def test_a_phase_title_names_the_phase(real_stage, monkeypatch):
    real_stage.previous = None
    staged = AsyncMock()
    monkeypatch.setattr(gn.manager, "stage_scoped_preference_deliveries", staged)
    await real_stage.phase_gate.evaluate_phase(
        _GateSession(phase=_phase(), release=_release()), RELEASE_ID, PHASE_ID,
    )
    title, _body = staged.await_args.kwargs["build_title_fn"](gn.EVENT)
    assert title == "Quality gate failed: 2026.10 · Staging — test_pay (checkout-e2e) is FAILED"
    meta = staged.await_args.kwargs["metadata"]
    assert (meta["phase_id"], meta["phase_name"]) == (str(PHASE_ID), "Staging")


# ── 4. the builders ──────────────────────────────────────────────────────────


def _gate_message(**overrides):
    values = dict(
        release_id=RELEASE_ID, release_name="2026.10", project_name="Checkout",
        decision_id=uuid.UUID(int=7), previous_verdict="GO", reasons=["a is FAILED"], total_reasons=1,
    )
    values.update(overrides)
    return gn.build_gate_message(**values)


def test_a_hostile_release_name_is_stored_verbatim():
    hostile = "<img src=x onerror=alert(1)>"
    title, _body, meta = _gate_message(release_name=hostile)
    assert title == f"Quality gate failed: {hostile} — a is FAILED"
    assert meta["release_name"] == hostile


def test_the_title_clips_at_200_and_each_reason_at_300():
    long_reason = "r" * 400
    title, body, meta = _gate_message(reasons=[long_reason], total_reasons=1)
    assert len(title) == 200 and title.endswith("…")
    assert body == "• " + "r" * 299 + "…"
    assert meta["blocking_reasons"] == ["r" * 299 + "…"]


def test_five_reasons_render_three_and_the_rest_as_a_count():
    reasons = [f"t{i} is FAILED" for i in range(5)]
    _title, body, meta = _gate_message(reasons=reasons[:3], total_reasons=5)
    assert body == "• t0 is FAILED\n• t1 is FAILED\n• t2 is FAILED\n+2 more"
    assert meta["blocking_reasons"] == reasons[:3]


def test_no_reasons_render_the_plain_sentence():
    title, body, meta = _gate_message(reasons=[], total_reasons=0)
    assert title == "Quality gate failed: 2026.10"
    assert body == "The gate recorded NO_GO."
    assert meta["blocking_reasons"] == []


def test_the_run_builder():
    title, body, meta = gn.build_run_message(
        run_id=RUN_ID, project_name="Checkout", build_number="142", issues=["pay is broken", "b", "c", "d"],
        trigger="override",
    )
    assert title == "Quality gate failed: Checkout build 142 — pay is broken"
    assert body == "• pay is broken\n• b\n• c\n+1 more"
    assert meta["app_path"] == f"/release-gate/{RUN_ID}"
    assert meta["trigger"] == "override" and meta["run_id"] == str(RUN_ID) and meta["build_number"] == "142"
    assert not {"pass_rate", "total_tests", "failed_tests"} & set(meta)
    assert gn.build_run_message(
        run_id=RUN_ID, project_name="Checkout", build_number="", issues=[], trigger="agent",
    )[:2] == ("Quality gate failed: Checkout", "The gate recorded NO_GO.")


# ── 5. the link allow-list, the event normaliser ────────────────────────────

GOOD_UUID = "12345678-1234-4234-8234-123456789abc"


@pytest.mark.parametrize(
    "metadata, expected",
    [
        ({"app_path": f"/releases/{GOOD_UUID}"}, f"/releases/{GOOD_UUID}"),
        ({"app_path": f"/release-gate/{GOOD_UUID}"}, f"/release-gate/{GOOD_UUID}"),
        ({"app_path": "/releases/x"}, None),
        ({"app_path": f"https://example.com/releases/{GOOD_UUID}"}, None),
        ({"app_path": "//example.com"}, None),
        ({"app_path": f"//example.com/releases/{GOOD_UUID}"}, None),
        ({"app_path": "/settings"}, None),
        ({"app_path": f"/releases/{GOOD_UUID.upper()}"}, None),
        ({"app_path": f"/releases/{GOOD_UUID}/"}, None),
        ({"app_path": f"/releases/{GOOD_UUID}\n"}, None),
        ({"app_path": f"/runs/{GOOD_UUID}"}, None),
        # W21d ACT-02: a comment mention links to its test case; near misses stay text.
        ({"app_path": f"/test-management?case={GOOD_UUID}"}, f"/test-management?case={GOOD_UUID}"),
        ({"app_path": "/test-management?case=x"}, None),
        ({"app_path": f"/test-management?case={GOOD_UUID}&tab=comments"}, None),
        ({"app_path": f"/test-management?case={GOOD_UUID.upper()}"}, None),
        ({"app_path": f"/test-management?case={GOOD_UUID}/"}, None),
        ({"app_path": f"/test-management/?case={GOOD_UUID}"}, None),
        ({"app_path": f"/test-management?test={GOOD_UUID}"}, None),
        ({"app_path": f"https://example.com/test-management?case={GOOD_UUID}"}, None),
        ({"app_path": 123}, None),
        ({"app_path": [f"/releases/{GOOD_UUID}"]}, None),
        ({"app_path": None}, None),
        ({}, None),
        (None, None),
        ("not a mapping", None),
    ],
)
def test_app_link(metadata, expected):
    assert gn.app_link(metadata) == expected


@pytest.mark.parametrize(
    "values, expected",
    [
        (["flaky_test_detected", "run_failed", "test.newly_flaky"], ["test.newly_flaky", "run_failed"]),
        (["flaky_test_detected"], ["test.newly_flaky"]),
        (["run_failed", "run_failed", "quality_gate_failed"], ["run_failed", "quality_gate_failed"]),
        (["test.newly_flaky", "flaky_test_detected"], ["test.newly_flaky"]),
        ([], []),
    ],
)
def test_normalise_notification_events(values, expected):
    from app.models.schemas import normalise_notification_events

    assert normalise_notification_events(values) == expected


def test_a_preference_stores_the_alias_as_test_newly_flaky():
    from app.models.schemas import NotificationPreferenceCreate

    pref = NotificationPreferenceCreate(channel="email", events=["flaky_test_detected", "test.newly_flaky"])
    assert pref.events == ["test.newly_flaky"]


# ── 6. the override handler ──────────────────────────────────────────────────


class _OverrideSession(_GateSession):
    def __init__(self, journal):
        super().__init__()
        self.journal = journal

    async def execute(self, stmt, *args, **kwargs):
        sql = " ".join(str(stmt).split())
        self.statements.append(sql)
        if "JOIN projects" in sql:
            return _Result(first=("142", "Checkout"))
        return _Result(PROJECT_ID)  # run -> project

    async def commit(self):
        self.journal.append("commit")


def _council(recommendation, *befores):
    audit = [
        {"timestamp": f"2026-10-16T00:00:0{i}+00:00", "before_recommendation": before, "before_risk_score": 40,
         "after_recommendation": recommendation, "reason": "lead decision"}
        for i, before in enumerate(befores)
    ]
    from app.models.schemas import ReleaseCouncilResponse

    return ReleaseCouncilResponse(
        run_id=str(RUN_ID), recommendation=recommendation, risk_score=80,
        blocking_issues=["pay is broken"], original_recommendation=befores[0], override_audit=audit,
    )


async def _override(monkeypatch, council, target):
    from app.models.schemas import ReleaseCouncilOverrideRequest
    from app.routers import release_readiness

    journal: list = []
    monkeypatch.setattr(release_readiness, "apply_override", AsyncMock(return_value=council))
    monkeypatch.setattr(release_readiness, "record_activity", AsyncMock(side_effect=lambda *a, **k: journal.append("activity")))
    monkeypatch.setattr("app.services.intelligence_snapshot_service.stage_invalidate", AsyncMock())
    monkeypatch.setattr("app.services.release_decision_webhook.emit_release_decided", AsyncMock())
    staged = AsyncMock(side_effect=lambda *a, **k: journal.append("stage"))
    monkeypatch.setattr(gn.manager, "stage_scoped_preference_deliveries", staged)
    db = _OverrideSession(journal)
    lead = SimpleNamespace(id=uuid.uuid4(), username="lead", role="qa_lead")
    await release_readiness.override_release_decision(
        run_id=RUN_ID,
        body=ReleaseCouncilOverrideRequest(override_recommendation=target, reason="lead decision"),
        current_user=lead, _=lead, db=db,
    )
    return journal, staged


@pytest.mark.asyncio
async def test_an_override_into_no_go_stages_under_its_ordinal_before_the_commit(monkeypatch):
    journal, staged = await _override(monkeypatch, _council("NO_GO", "GO"), "NO_GO")
    assert journal == ["activity", "stage", "commit"]
    (call,) = staged.await_args_list
    assert call.kwargs["delivery_scope"] == f"quality-gate-failed:run:{RUN_ID}:override:1:v1"
    assert call.kwargs["run_id"] == RUN_ID and call.kwargs["project_id"] == PROJECT_ID
    title, _body = call.kwargs["build_title_fn"](gn.EVENT)
    assert title == "Quality gate failed: Checkout build 142 — pay is broken"
    assert call.kwargs["metadata"]["app_path"] == f"/release-gate/{RUN_ID}"
    assert call.kwargs["metadata"]["trigger"] == "override"


@pytest.mark.asyncio
async def test_a_second_override_into_no_go_uses_the_next_ordinal(monkeypatch):
    _journal, staged = await _override(monkeypatch, _council("NO_GO", "NO_GO", "CONDITIONAL_GO"), "NO_GO")
    assert staged.await_args.kwargs["delivery_scope"] == f"quality-gate-failed:run:{RUN_ID}:override:2:v1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "council, target",
    [(("GO", "NO_GO"), "GO"), (("NO_GO", "NO_GO"), "NO_GO"), (("CONDITIONAL_GO", "GO"), "CONDITIONAL_GO")],
    ids=["no-go-to-go", "from-no-go", "go-to-conditional"],
)
async def test_an_override_out_of_or_not_into_no_go_stages_nothing(monkeypatch, council, target):
    journal, staged = await _override(monkeypatch, _council(*council), target)
    staged.assert_not_awaited()
    assert journal == ["activity", "commit"]


def test_the_stage_precedes_the_commit_in_the_handler_source():
    from app.routers import release_readiness

    src = inspect.getsource(release_readiness.override_release_decision)
    assert src.index("gate_notifications.stage_override_no_go(") < src.index("await db.commit()")


# ── 7. the real email over a gate row ───────────────────────────────────────


def test_the_notification_email_escapes_the_release_name_and_invents_no_stats():
    from app.services.notification import email_service

    hostile = "<img src=x onerror=alert(1)>"
    title, body, meta = _gate_message(release_name=hostile, reasons=["pay is FAILED"], total_reasons=1)
    html = email_service._build_html(title, body, gn.EVENT.value, meta)
    assert "None%" not in html and "None" not in html.split("<!-- Footer -->")[0]
    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "Pass rate" not in html and "Total tests" not in html
    plain = email_service._build_plain(title, body, meta, gn.EVENT.value)
    assert "None" not in plain


# ── 8. the agent path's outcome table ───────────────────────────────────────


class _AgentSession:
    def __init__(self, decision):
        self.decision = decision

    async def execute(self, stmt, *args, **kwargs):
        sql = " ".join(str(stmt).split())
        if "FROM release_decisions" in sql:
            return _Result(self.decision)
        if "JOIN projects" in sql:
            return _Result(first=(PROJECT_ID, "142", "Checkout"))
        return _Result()


PIPELINE = uuid.UUID("99999999-8888-4777-8666-555555555555")


def _decision(**overrides):
    values = dict(pipeline_run_id=PIPELINE, human_override=None, recommendation="NO_GO")
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def agent(monkeypatch):
    from app.core.config import settings
    from app.services import release_council_service, report_distribution_policy
    from app.services.review_envelope import ReviewEnvelope

    state = SimpleNamespace(
        council=SimpleNamespace(recommendation="NO_GO", synthesized=False, human_override=None,
                                blocking_issues=["pay is broken"]),
        envelope=ReviewEnvelope(ai_generated=True, state="pending", message="Pending review"),
        staged=AsyncMock(),
    )
    monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", False)
    monkeypatch.setattr(release_council_service, "get_release_council", AsyncMock(side_effect=lambda *a, **k: state.council))

    async def envelope(*args, **kwargs):
        return state.envelope

    monkeypatch.setattr(report_distribution_policy, "review_envelope_for_pipeline_subject", envelope)
    monkeypatch.setattr(report_distribution_policy, "review_envelope_for_run", envelope)
    monkeypatch.setattr(gn.manager, "stage_scoped_preference_deliveries", state.staged)
    state.settings = settings
    return state


async def _agent(decision, pipeline_run_id=str(PIPELINE)):
    return await gn.stage_agent_decision_no_go(
        _AgentSession(decision), run_id=str(RUN_ID), pipeline_run_id=pipeline_run_id,
        evidence_bundle_sha256="e" * 64,
    )


@pytest.mark.asyncio
async def test_agent_outcome_no_decision(agent):
    assert await _agent(None) == "no_decision"


@pytest.mark.asyncio
async def test_agent_outcome_pipeline_mismatch(agent):
    assert await _agent(_decision(), pipeline_run_id=str(uuid.uuid4())) == "pipeline_mismatch"


@pytest.mark.asyncio
async def test_agent_outcome_overridden(agent):
    assert await _agent(_decision(human_override="a lead decided")) == "overridden"


@pytest.mark.asyncio
async def test_agent_outcome_no_council(agent):
    agent.council = None
    assert await _agent(_decision()) == "no_council"


@pytest.mark.asyncio
async def test_agent_outcome_withheld_while_the_review_gate_is_enforced(agent, monkeypatch):
    monkeypatch.setattr(agent.settings, "REVIEW_GATE_ENFORCED", True)
    assert await _agent(_decision()) == "withheld"
    agent.staged.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_outcome_an_accepted_review_is_not_withheld(agent, monkeypatch):
    from app.services.review_envelope import ReviewEnvelope

    monkeypatch.setattr(agent.settings, "REVIEW_GATE_ENFORCED", True)
    agent.envelope = ReviewEnvelope(ai_generated=True, state="accepted", message="Accepted")
    assert await _agent(_decision()) == "staged"


@pytest.mark.asyncio
async def test_agent_outcome_not_no_go(agent):
    agent.council.recommendation = "GO"
    assert await _agent(_decision()) == "not_no_go"
    agent.staged.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_outcome_staged_once_per_run(agent):
    assert await _agent(_decision()) == "staged"
    (call,) = agent.staged.await_args_list
    assert call.kwargs["delivery_scope"] == f"quality-gate-failed:run:{RUN_ID}:agent:v1"
    assert call.kwargs["run_id"] == RUN_ID and call.kwargs["project_id"] == PROJECT_ID
    assert call.kwargs["metadata"]["trigger"] == "agent"
    assert call.kwargs["metadata"]["app_path"] == f"/release-gate/{RUN_ID}"
    assert call.kwargs["build_title_fn"](gn.EVENT)[0] == "Quality gate failed: Checkout build 142 — pay is broken"


def test_every_outcome_is_declared():
    assert gn.AGENT_OUTCOMES == (
        "no_decision", "pipeline_mismatch", "overridden", "no_council", "withheld", "not_no_go", "staged",
    )


# ── 9. the critic enqueues the task on default ──────────────────────────────


def _critic(monkeypatch):
    from app.agents import decision_report_critic_agent as critic_module
    from app.agents.decision_report_critic_agent import DecisionReportCriticAgent
    from app.services import release_decision_webhook

    collection = type("Collection", (), {"update_one": AsyncMock(return_value=None)})()

    class _Mongo:
        def __getitem__(self, _name):
            return collection

    monkeypatch.setattr(critic_module, "get_mongo_db", lambda: _Mongo())
    monkeypatch.setattr(
        critic_module, "publish_decision_report",
        AsyncMock(return_value={
            "report_id": "report-1", "report_version": 1, "supersedes_report_id": None,
            "status": "published", "generated_at": "2026-10-16T00:00:00+00:00",
            "evidence_bundle_sha256": "e" * 64,
        }),
    )
    monkeypatch.setattr(release_decision_webhook, "emit_release_decided", AsyncMock(return_value=0))
    return DecisionReportCriticAgent.__new__(DecisionReportCriticAgent)


def _critic_state():
    return {"test_run_id": str(RUN_ID), "pipeline_run_id": str(PIPELINE), "project_id": str(PROJECT_ID),
            "build_number": "142"}


@pytest.mark.asyncio
async def test_the_critic_enqueues_the_notification_task_on_default(monkeypatch):
    from app.worker import tasks

    enqueue = MagicMock()
    monkeypatch.setattr(tasks.notify_release_decision_no_go, "apply_async", enqueue)
    agent = _critic(monkeypatch)
    await agent._persist(_critic_state(), {"verification": {"status": "passed"}}, "# report")

    (call,) = enqueue.call_args_list
    assert call.kwargs["queue"] == "default"
    args, kwargs = call.kwargs.get("args", ()), call.kwargs.get("kwargs") or {}
    task = tasks.notify_release_decision_no_go
    # Celery's own argument check (what apply_async runs before sending), and the function's.
    task.__header__(*args, **kwargs)
    bound = inspect.signature(task.run).bind(*args, **kwargs)
    assert bound.arguments == {
        "run_id": str(RUN_ID), "pipeline_run_id": str(PIPELINE), "evidence_bundle_sha256": "e" * 64,
    }
    assert task.queue == "default"


@pytest.mark.asyncio
async def test_a_broker_failure_never_fails_the_critic(monkeypatch):
    from app.worker import tasks

    monkeypatch.setattr(
        tasks.notify_release_decision_no_go, "apply_async", MagicMock(side_effect=ConnectionError("broker down")),
    )
    agent = _critic(monkeypatch)
    with structlog.testing.capture_logs() as logs:
        await agent._persist(_critic_state(), {"verification": {"status": "passed"}}, "# report")
    assert [e.get("error_type") for e in logs if e["event"] == "release_decision_notification_not_queued"] == [
        "ConnectionError"
    ]
