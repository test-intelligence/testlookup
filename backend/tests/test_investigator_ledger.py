"""Agent-runs ledger, auto-triggers, and compliance section (AI-3 core).

Pins:

* every investigation outcome writes an AgentRun ledger row with
  ``actions_taken == []`` (shadow/suggest are observation-only) and mirrors
  a durable ``agent_run_recorded`` event to the Mongo stream;
* trigger-time gating in the service (policy disabled / one-active-per-run /
  max_runs_per_day) raises the typed errors the router maps to HTTP;
* ``maybe_auto_trigger`` NEVER raises into the host path and respects the
  gates;
* the auto-trigger hooks are wired (transition engine ``newly_failing`` +
  release-gate NO_GO) inside their own try/except;
* compliance packs gain the self-guarding ``agent_activity`` section and
  ship ``agent_activity.json``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from app.models.postgres import AgentConfig, AgentInvestigation, AgentRun, TestRun  # noqa: E402
from app.services import agent_investigation_service as svc  # noqa: E402

PROJECT_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _investigation(**over):
    row = AgentInvestigation(
        id=uuid.uuid4(),
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        status="completed",
        mode="shadow",
        triggered_by="auto:newly_failing",
        budget={"max_llm_calls": 30, "max_tokens": 60000, "max_seconds": 300},
        spend={"llm_calls": 1, "tokens": 500, "cost_usd": 0.0, "seconds": 4.2},
        hypotheses=[],
    )
    for k, v in over.items():
        setattr(row, k, v)
    return row


class _StageDB:
    def __init__(self):
        self.added = []
        self.flushed = False

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()


# ─────────────────────────── Ledger writes ───────────────────────────


@pytest.mark.asyncio
async def test_record_agent_run_writes_entry_and_mirrors_mongo(monkeypatch):
    mirrored = []

    async def _capture_emit(pipeline_run_id, event_type, **kwargs):
        mirrored.append((pipeline_run_id, event_type, kwargs))

    import app.services.pipeline_event_log as pel

    monkeypatch.setattr(pel, "emit_event", _capture_emit)

    inv = _investigation()
    db = _StageDB()
    entry = await svc.record_agent_run(
        db, inv,
        status="completed",
        summary="primary_cause=infra (confidence 85) — infra dominates",
        actions_proposed=["Check runner health"],
        tokens=500, cost_usd=0.0, duration_ms=4200,
        prompt_registry_digest="deadbeef1234",
    )
    assert entry in db.added and db.flushed
    assert entry.agent_id == "investigator"
    assert entry.actions_taken == []  # ALWAYS empty this slice
    assert entry.actions_proposed == ["Check runner health"]
    assert entry.mode == "shadow" and entry.trigger == "auto:newly_failing"
    assert entry.details_path == f"/investigations/{inv.id}"

    # Durable Mongo mirror carries the full ledger payload.
    ((stream_id, event_type, kwargs),) = mirrored
    assert stream_id == str(inv.id)
    assert event_type == "agent_run_recorded"
    detail = kwargs["detail"]
    assert detail["status"] == "completed"
    assert detail["actions_taken"] == []
    assert detail["prompt_registry_digest"] == "deadbeef1234"


@pytest.mark.asyncio
async def test_record_agent_run_survives_mongo_outage(monkeypatch):
    async def _broken_emit(*args, **kwargs):
        raise RuntimeError("mongo down")

    import app.services.pipeline_event_log as pel

    monkeypatch.setattr(pel, "emit_event", _broken_emit)
    db = _StageDB()
    entry = await svc.record_agent_run(
        db, _investigation(), status="failed", summary="boom",
    )
    assert entry in db.added  # PG ledger write still landed


def test_serialize_agent_run_shape():
    entry = AgentRun(
        id=uuid.uuid4(), agent_id="investigator", project_id=PROJECT_ID,
        run_id=None, mode="shadow", trigger="manual", status="cancelled",
        summary="s", actions_proposed=[], actions_taken=[],
        tokens=0, cost_usd=0.0, duration_ms=10,
        prompt_registry_digest=None, details_path=None,
        created_at=datetime.now(timezone.utc),
    )
    data = svc.serialize_agent_run(entry)
    assert data["run_id"] is None
    assert data["created_at"] is not None
    assert isinstance(data["cost_usd"], float)


# ─────────────────────────── Trigger-time gates ───────────────────────────


class _GateDB(_StageDB):
    def __init__(self, *, policy=None, active=None, today_count=0):
        super().__init__()
        self._policy = policy
        self._active = active
        self._today_count = today_count

    async def execute(self, stmt):
        text = str(stmt).lower()

        class _R:
            def __init__(self, value):
                self._value = value

            def scalar_one_or_none(self):
                return self._value

            def scalar(self):
                return self._value

        if "agent_configs" in text:
            return _R(self._policy)
        if "count(" in text:
            return _R(self._today_count)
        if "agent_investigations" in text:
            return _R(self._active)
        return _R(None)


def _test_run():
    return TestRun(id=RUN_ID, project_id=PROJECT_ID, build_number="b-1")


def _policy(*, enabled=True, mode="shadow", budgets=None, shadow_runs_completed=0):
    return AgentConfig(
        id=uuid.uuid4(), project_id=PROJECT_ID, agent_id="investigator",
        enabled=enabled, mode=mode, config={"extensions": {"investigator": {
            "budgets": budgets or {}, "shadow_runs_completed": shadow_runs_completed,
            "promotion_note": None,
        }}}, config_version=1,
    )


@pytest.mark.asyncio
async def test_start_investigation_policy_disabled_raises():
    policy = _policy(enabled=False)
    with pytest.raises(svc.InvestigationPolicyDisabled):
        await svc.start_investigation(_GateDB(policy=policy), _test_run())


@pytest.mark.asyncio
async def test_start_investigation_one_active_per_run_raises():
    active = _investigation(status="running")
    with pytest.raises(svc.InvestigationAlreadyActive) as exc:
        await svc.start_investigation(_GateDB(active=active), _test_run())
    assert exc.value.investigation_id == active.id


@pytest.mark.asyncio
async def test_start_investigation_max_runs_per_day_raises():
    with pytest.raises(svc.InvestigationDailyBudgetExceeded) as exc:
        await svc.start_investigation(_GateDB(today_count=10), _test_run())
    assert exc.value.max_runs_per_day == 10


@pytest.mark.asyncio
async def test_start_investigation_inherits_policy_mode_and_budgets():
    policy = _policy(
        mode="suggest",
        budgets={"max_runs_per_day": 3, "max_llm_calls_per_run": 7,
                 "max_tokens_per_run": 1000, "max_seconds_per_run": 60},
        shadow_runs_completed=4,
    )
    db = _GateDB(policy=policy, today_count=2)
    inv = await svc.start_investigation(db, _test_run(), triggered_by="auto:gate_no_go")
    assert inv.mode == "suggest"
    assert inv.triggered_by == "auto:gate_no_go"
    assert inv.budget == {
        "max_llm_calls": 7,
        "max_tokens": 1000,
        "max_cost_usd": 5.0,
        "max_seconds": 60,
    }
    assert inv.status == "queued"


# ─────────────────────────── maybe_auto_trigger ───────────────────────────


@pytest.mark.asyncio
async def test_maybe_auto_trigger_never_raises(monkeypatch):
    """Even a totally broken DB layer must not leak into the host path."""

    class _ExplodingSessionFactory:
        def __call__(self):
            raise RuntimeError("db down")

    import app.db.postgres as pg

    monkeypatch.setattr(pg, "AsyncSessionLocal", _ExplodingSessionFactory())
    result = await svc.maybe_auto_trigger(RUN_ID, "auto:newly_failing")
    assert result is None


@pytest.mark.asyncio
async def test_maybe_auto_trigger_gated_returns_none(monkeypatch):
    """A policy gate (already active) resolves to None, no task enqueued."""

    class _Session:
        async def __aenter__(self):
            return _GateDB(active=_investigation(status="running"))

        async def __aexit__(self, *args):
            return False

    class _Factory:
        def __call__(self):
            return _Session()

    import app.db.postgres as pg

    monkeypatch.setattr(pg, "AsyncSessionLocal", _Factory())

    # The run lookup happens on the same session; patch the gate DB to
    # return the run for test_runs selects.
    original_execute = _GateDB.execute

    async def _execute(self, stmt):
        text = str(stmt).lower()
        if "test_runs" in text:
            class _R:
                def scalar_one_or_none(self):
                    return _test_run()
            return _R()
        return await original_execute(self, stmt)

    monkeypatch.setattr(_GateDB, "execute", _execute)

    enqueued = []
    monkeypatch.setattr(svc, "enqueue_investigation_task", lambda i: enqueued.append(i))
    result = await svc.maybe_auto_trigger(RUN_ID, "auto:newly_failing")
    assert result is None
    assert enqueued == []


# ─────────────────────────── Hook wiring (static) ───────────────────────────


def test_transition_engine_hook_is_wired_and_isolated():
    source = (BACKEND_ROOT / "app" / "services" / "notification_transitions.py").read_text(
        encoding="utf-8"
    )
    assert '"auto:newly_failing"' in source
    # The hook must live in its own try/except so it can never affect the
    # notification path.
    assert (
        "try:\n            from app.services.agent_investigation_service "
        "import maybe_auto_trigger" in source
    ), "the auto-trigger hook must be wrapped in its own try/except"


def test_release_gate_no_go_hook_is_wired_and_isolated():
    source = (BACKEND_ROOT / "app" / "agents" / "release_risk_agent.py").read_text(
        encoding="utf-8"
    )
    assert "maybe_auto_trigger" in source
    assert '"auto:gate_no_go"' in source
    assert 'recommendation") == "NO_GO"' in source.replace("'", '"')


# ─────────────────────────── Compliance section ───────────────────────────


@pytest.mark.asyncio
async def test_gather_agent_activity_serializes_rows():
    from app.services.compliance_pack_service import _gather_agent_activity

    entry = AgentRun(
        id=uuid.uuid4(), agent_id="investigator", project_id=PROJECT_ID,
        run_id=RUN_ID, mode="shadow", trigger="auto:gate_no_go",
        status="completed", summary="s", actions_proposed=["x"],
        actions_taken=[], tokens=1, cost_usd=0.0, duration_ms=2,
        prompt_registry_digest="d", details_path=None,
        created_at=datetime.now(timezone.utc),
    )

    class _DB:
        async def execute(self, stmt):
            class _R:
                def scalars(self):
                    return SimpleNamespace(all=lambda: [entry])
            return _R()

    result = await _gather_agent_activity(_DB(), PROJECT_ID, RUN_ID)
    assert len(result["agent_runs"]) == 1
    assert result["agent_runs"][0]["trigger"] == "auto:gate_no_go"
    assert result["agent_runs"][0]["actions_taken"] == []


@pytest.mark.asyncio
async def test_gather_agent_activity_self_guards():
    from app.services.compliance_pack_service import _gather_agent_activity

    class _BrokenDB:
        async def execute(self, stmt):
            raise RuntimeError("ledger table missing")

    result = await _gather_agent_activity(_BrokenDB(), PROJECT_ID, RUN_ID)
    assert result["agent_runs"] == []
    assert "error" in result


def test_compliance_pack_ships_agent_activity_json():
    source = (BACKEND_ROOT / "app" / "services" / "compliance_pack_service.py").read_text(
        encoding="utf-8"
    )
    assert '"agent_activity.json": _serialize(agent_activity)' in source
    assert "_gather_agent_activity(db, release.project_id, run.id)" in source


# ─────────────────────────── Policy helpers ───────────────────────────


def test_effective_budgets_merges_partial_rows():
    assert svc._effective_budgets(None) == svc.DEFAULT_BUDGETS
    merged = svc._effective_budgets({"max_runs_per_day": 3, "junk": 1})
    assert merged["max_runs_per_day"] == 3
    assert merged["max_llm_calls_per_run"] == 30
    # Negative/garbage values fall back to defaults.
    assert svc._effective_budgets({"max_tokens_per_run": -5})["max_tokens_per_run"] == 60000


@pytest.mark.asyncio
async def test_upsert_policy_rejects_invalid_mode():
    with pytest.raises(ValueError):
        await svc.upsert_policy(
            _StageDB(), PROJECT_ID, "investigator", enabled=True, mode="yolo",
        )
