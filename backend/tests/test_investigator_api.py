"""Investigator API endpoint contract + authorization (AI-1 / AI-3).

The router handlers are awaited directly with content-dispatching fake async
sessions (mirrors ``test_duplicates_router``) so no live Postgres is needed.
Pins:

* the EXACT pinned wire shapes (InvestigationDetail, InvestigationSummary,
  AgentPolicy, AgentRunEntry, and the envelope keys) — the frontend is
  built against these verbatim;
* trigger gating: 403 actionable error when the policy disables the agent,
  409 for one-active-per-run, 429 for max_runs_per_day;
* cancel semantics: 202 ``{"status": "cancelling"}`` + cooperative flag,
  409 on terminal rows;
* policy defaults + PUT round-trip (QA_LEAD-guarded route);
* IDOR: ``require_investigation_access`` verifies the PROVIDED id.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException  # noqa: E402

from app.models.postgres import (  # noqa: E402
    AgentInvestigation,
    AgentConfig,
    AgentRun,
    TestRun,
    UserRole,
)
from app.routers import agent_investigations as router_mod  # noqa: E402
from app.services import agent_investigation_service as svc  # noqa: E402

PROJECT_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()

# The pinned wire shapes.
DETAIL_KEYS = {
    "id", "run_id", "project_id", "status", "mode", "triggered_by",
    "started_at", "completed_at", "cancelled_by", "budget", "spend",
    "hypotheses", "verdict", "prompt_versions", "model",
}
SUMMARY_KEYS = {
    "id", "run_id", "run_build_number", "status", "mode", "triggered_by",
    "primary_cause", "confidence", "started_at", "completed_at",
}
POLICY_KEYS = {"agent_id", "enabled", "mode", "budgets", "promotion"}
BUDGET_KEYS = {
    "max_runs_per_day", "max_llm_calls_per_run",
    "max_tokens_per_run", "max_seconds_per_run",
}
AGENT_RUN_KEYS = {
    "id", "agent_id", "project_id", "run_id", "mode", "trigger", "status",
    "summary", "actions_proposed", "actions_taken", "tokens", "cost_usd",
    "duration_ms", "prompt_registry_digest", "created_at", "details_path",
}
HYPOTHESIS_KEYS = {
    "id", "title", "status", "confidence", "confidence_basis", "summary",
    "evidence", "started_at", "completed_at",
}


def _user(role=UserRole.QA_ENGINEER):
    return SimpleNamespace(
        id=uuid.uuid4(), role=role, username="tester", email="t@example.com",
    )


def _run(project_id=PROJECT_ID, run_id=RUN_ID):
    return TestRun(id=run_id, project_id=project_id, build_number="b-100")


def _investigation(status="queued", **over):
    row = AgentInvestigation(
        id=uuid.uuid4(),
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        status=status,
        mode="shadow",
        triggered_by="manual",
        budget={"max_llm_calls": 30, "max_tokens": 60000, "max_seconds": 300},
        spend={"llm_calls": 0, "tokens": 0, "cost_usd": 0.0, "seconds": 0.0},
        hypotheses=[],
        cancel_requested=False,
        created_at=datetime.now(timezone.utc),
    )
    for k, v in over.items():
        setattr(row, k, v)
    return row


def _policy_config(*, enabled=True, mode="shadow", budgets=None, shadow_runs_completed=0, note=None):
    return AgentConfig(
        id=uuid.uuid4(),
        project_id=PROJECT_ID,
        agent_id="investigator",
        enabled=enabled,
        mode=mode,
        config={"extensions": {"investigator": {
            "budgets": budgets or {},
            "shadow_runs_completed": shadow_runs_completed,
            "promotion_note": note,
        }}},
        config_version=1,
    )


class _Result:
    def __init__(self, *, scalars=None, scalar=None, _all=None):
        self._scalars_val = scalars
        self._scalar = scalar
        self._all = _all

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._scalars_val or []))

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return list(self._all or [])


class _FakeSession:
    """Content-dispatching async session for this router's query surface."""

    def __init__(
        self,
        *,
        run=None,
        policy=None,
        active_investigation=None,
        investigation=None,
        today_count=0,
        list_rows=None,
        agent_runs=None,
        total=0,
    ):
        self._run = run
        self._policy = policy
        self._active = active_investigation
        self._investigation = investigation
        self._today_count = today_count
        self._list_rows = list_rows or []
        self._agent_runs = agent_runs or []
        self._total = total
        self.added = []
        self.committed = False
        self.flushed = False

    async def execute(self, stmt):
        text = str(stmt).lower()
        if "agent_configs" in text:
            return _Result(scalar=self._policy)
        if "count(" in text and "agent_investigations" in text:
            return _Result(scalar=self._today_count if not self._list_rows else self._total)
        if "count(" in text and "agent_runs" in text:
            return _Result(scalar=self._total)
        if "agent_investigations" in text and "test_runs" in text:
            return _Result(_all=self._list_rows)
        if "agent_investigations" in text and "status in" in text.replace("_", " "):
            return _Result(scalar=self._active)
        if "agent_investigations" in text:
            # Active-check has the status filter; detail select does not.
            if "in (__[postcompile" in text or ".status in" in text:
                return _Result(scalar=self._active)
            return _Result(scalar=self._investigation)
        if "agent_runs" in text:
            return _Result(scalars=self._agent_runs)
        if "test_runs" in text:
            return _Result(scalar=self._run)
        return _Result()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        self.committed = True


# ─────────────────────────── POST /runs/{run_id}/investigations ─────────────


@pytest.mark.asyncio
async def test_start_investigation_202_with_default_policy(monkeypatch):
    db = _FakeSession(run=_run())
    enqueued = []
    requester = _user()
    monkeypatch.setattr(svc, "enqueue_investigation_task", lambda i: enqueued.append(i) or True)

    result = await router_mod.start_investigation(
        run_id=RUN_ID, db=db, current_user=requester, _writer=requester,
    )
    assert set(result.keys()) == {"investigation_id"}
    uuid.UUID(result["investigation_id"])  # parseable
    assert db.committed
    assert enqueued and str(enqueued[0]) == result["investigation_id"]
    # The staged row seeds the five pending hypotheses in fixed order.
    (row,) = db.added
    assert [h["id"] for h in row.hypotheses] == list(svc.HYPOTHESIS_IDS)
    assert row.mode == "shadow" and row.triggered_by == "manual"
    assert row.requested_by == requester.id
    assert row.budget == {
        "max_llm_calls": 30,
        "max_tokens": 60000,
        "max_cost_usd": 5.0,
        "max_seconds": 300,
    }


@pytest.mark.asyncio
async def test_start_investigation_403_when_policy_disables(monkeypatch):
    policy = _policy_config(enabled=False)
    db = _FakeSession(run=_run(), policy=policy)
    with pytest.raises(HTTPException) as exc:
        await router_mod.start_investigation(
            run_id=RUN_ID, db=db, current_user=_user(), _writer=_user(),
        )
    assert exc.value.status_code == 403
    assert "agent-configs/investigator" in exc.value.detail


@pytest.mark.asyncio
async def test_start_investigation_409_when_one_already_active():
    active = _investigation(status="running")
    db = _FakeSession(run=_run(), active_investigation=active)
    with pytest.raises(HTTPException) as exc:
        await router_mod.start_investigation(
            run_id=RUN_ID, db=db, current_user=_user(), _writer=_user(),
        )
    assert exc.value.status_code == 409
    assert str(active.id) in exc.value.detail


@pytest.mark.asyncio
async def test_start_investigation_429_when_daily_budget_exhausted():
    db = _FakeSession(run=_run(), today_count=10)  # default max_runs_per_day=10
    with pytest.raises(HTTPException) as exc:
        await router_mod.start_investigation(
            run_id=RUN_ID, db=db, current_user=_user(), _writer=_user(),
        )
    assert exc.value.status_code == 429
    assert "max_runs_per_day" in exc.value.detail


# ─────────────────────────── GET /investigations/{id} ────────────────────────


@pytest.mark.asyncio
async def test_detail_shape_is_pinned():
    inv = _investigation(
        status="completed",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        hypotheses=[{
            "id": "infra", "title": "Infrastructure failure",
            "status": "validated", "confidence": 85,
            "confidence_basis": "heuristic_estimate", "summary": "s",
            "evidence": [{"kind": "failure_kind", "label": "l", "url_path": None, "detail": "d"}],
            "started_at": None, "completed_at": None,
        }],
        verdict={"primary_cause": "infra", "narrative": "n", "confidence": 85,
                 "recommended_actions": ["a"]},
        spend={"llm_calls": 2, "tokens": 1234, "cost_usd": 0.0, "seconds": 12.5},
        prompt_versions={"investigator_hypothesis_weigh": "v1:abc"},
        model_info=None,
    )
    db = _FakeSession(investigation=inv)
    detail = await router_mod.get_investigation(
        investigation_id=inv.id, db=db, current_user=_user(),
    )
    assert set(detail.keys()) == DETAIL_KEYS
    assert set(detail["budget"].keys()) == {"max_llm_calls", "max_tokens", "max_seconds"}
    assert set(detail["spend"].keys()) == {"llm_calls", "tokens", "cost_usd", "seconds"}
    assert set(detail["hypotheses"][0].keys()) == HYPOTHESIS_KEYS
    assert set(detail["verdict"].keys()) == {
        "primary_cause", "narrative", "confidence", "recommended_actions",
    }
    assert detail["status"] == "completed"
    assert detail["model"] is None
    assert isinstance(detail["spend"]["cost_usd"], float)
    assert isinstance(detail["spend"]["seconds"], float)


@pytest.mark.asyncio
async def test_detail_404_when_missing():
    db = _FakeSession(investigation=None)
    with pytest.raises(HTTPException) as exc:
        await router_mod.get_investigation(
            investigation_id=uuid.uuid4(), db=db, current_user=_user(),
        )
    assert exc.value.status_code == 404


# ─────────────────────────── POST /investigations/{id}/cancel ────────────────


@pytest.mark.asyncio
async def test_cancel_active_investigation_202():
    inv = _investigation(status="running")
    db = _FakeSession(investigation=inv)
    result = await router_mod.cancel_investigation(
        investigation_id=inv.id, db=db, current_user=_user(), _writer=_user(),
    )
    assert result == {"status": "cancelling"}
    assert inv.cancel_requested is True
    assert inv.cancelled_by == "tester"
    assert db.committed


@pytest.mark.asyncio
async def test_cancel_terminal_investigation_409():
    inv = _investigation(status="completed")
    db = _FakeSession(investigation=inv)
    with pytest.raises(HTTPException) as exc:
        await router_mod.cancel_investigation(
            investigation_id=inv.id, db=db, current_user=_user(), _writer=_user(),
        )
    assert exc.value.status_code == 409
    assert inv.cancel_requested is False


# ─────────────────────────── GET /projects/{id}/investigations ───────────────


@pytest.mark.asyncio
async def test_list_investigations_envelope_and_summary_shape():
    inv = _investigation(
        status="completed",
        verdict={"primary_cause": "known_flaky", "confidence": 88,
                 "narrative": "n", "recommended_actions": []},
    )
    db = _FakeSession(list_rows=[(inv, "b-100")], total=1)
    result = await router_mod.list_investigations(
        project_id=PROJECT_ID, limit=20, offset=0, db=db, current_user=_user(),
    )
    assert set(result.keys()) == {"items", "total"}
    assert result["total"] == 1
    (item,) = result["items"]
    assert set(item.keys()) == SUMMARY_KEYS
    assert item["primary_cause"] == "known_flaky"
    assert item["confidence"] == 88
    assert item["run_build_number"] == "b-100"


@pytest.mark.asyncio
async def test_list_investigations_null_verdict_yields_null_cause():
    inv = _investigation(status="running")
    db = _FakeSession(list_rows=[(inv, "b-101")], total=1)
    result = await router_mod.list_investigations(
        project_id=PROJECT_ID, limit=20, offset=0, db=db, current_user=_user(),
    )
    (item,) = result["items"]
    assert item["primary_cause"] is None
    assert item["confidence"] is None


# ─────────────────────────── Agent policies ──────────────────────────────────


@pytest.mark.asyncio
async def test_policies_default_shape_when_no_row():
    db = _FakeSession(policy=None)
    result = await router_mod.list_agent_policies(
        project_id=PROJECT_ID, db=db, current_user=_user(),
    )
    assert set(result.keys()) == {"policies"}
    (policy,) = result["policies"]
    assert set(policy.keys()) == POLICY_KEYS
    assert policy == {
        "agent_id": "investigator",
        "enabled": True,
        "mode": "shadow",
        "budgets": svc.DEFAULT_BUDGETS,
        "promotion": {"shadow_runs_completed": 0, "note": None},
    }


@pytest.mark.asyncio
async def test_policy_put_is_a_read_only_alias():
    db = _FakeSession(policy=None)
    body = router_mod.AgentPolicyUpdate(
        enabled=False,
        mode="suggest",
        budgets=router_mod.AgentPolicyBudgets(
            max_runs_per_day=5, max_llm_calls_per_run=10,
            max_tokens_per_run=20000, max_seconds_per_run=120,
        ),
        promotion=router_mod.AgentPolicyPromotion(shadow_runs_completed=999, note="ready"),
    )
    with pytest.raises(HTTPException) as exc:
        await router_mod.update_agent_policy(
            project_id=PROJECT_ID, agent_id="investigator", body=body,
            db=db, current_user=_user(UserRole.QA_LEAD), _lead=_user(UserRole.QA_LEAD),
        )
    assert exc.value.status_code == 405
    assert exc.value.headers["Location"].endswith("/agent-configs/investigator")
    assert not db.committed


@pytest.mark.asyncio
async def test_retired_policy_put_is_405_even_for_unknown_agent():
    db = _FakeSession()
    with pytest.raises(HTTPException) as exc:
        await router_mod.update_agent_policy(
            project_id=PROJECT_ID, agent_id="terminator",
            body=router_mod.AgentPolicyUpdate(),
            db=db, current_user=_user(UserRole.QA_LEAD), _lead=_user(UserRole.QA_LEAD),
        )
    assert exc.value.status_code == 405
    assert exc.value.headers["Location"].endswith("/agent-configs/investigator")


def test_policy_body_rejects_invalid_mode():
    with pytest.raises(ValueError):
        router_mod.AgentPolicyUpdate(mode="yolo")


# ─────────────────────────── Agent-runs ledger ───────────────────────────────


@pytest.mark.asyncio
async def test_agent_runs_envelope_and_entry_shape():
    entry = AgentRun(
        id=uuid.uuid4(), agent_id="investigator", project_id=PROJECT_ID,
        run_id=RUN_ID, mode="shadow", trigger="manual", status="completed",
        summary="primary_cause=infra (confidence 85)",
        actions_proposed=["Check runner health"], actions_taken=[],
        tokens=100, cost_usd=0.0, duration_ms=1200,
        prompt_registry_digest="abc123def456",
        details_path="/investigations/x",
        created_at=datetime.now(timezone.utc),
    )
    db = _FakeSession(agent_runs=[entry], total=1)
    result = await router_mod.list_agent_runs(
        project_id=PROJECT_ID, agent_id="investigator", limit=50, offset=0,
        db=db, current_user=_user(),
    )
    assert set(result.keys()) == {"items", "total"}
    (item,) = result["items"]
    assert set(item.keys()) == AGENT_RUN_KEYS
    assert item["actions_taken"] == []  # always [] this slice
    assert item["prompt_registry_digest"] == "abc123def456"


# ─────────────────────────── Authorization (IDOR) ────────────────────────────


@pytest.mark.asyncio
async def test_require_investigation_access_403_for_non_member():
    inv_id = uuid.uuid4()

    class _DB:
        def __init__(self):
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            if self.calls == 1:  # project resolution
                return _Result(scalar=PROJECT_ID)
            return _Result(scalar=None)  # no membership

    request = SimpleNamespace(path_params={"investigation_id": str(inv_id)})
    check = router_mod.require_investigation_access()
    with pytest.raises(HTTPException) as exc:
        await check(request=request, db=_DB(), current_user=_user())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_investigation_access_404_when_missing():
    class _DB:
        async def execute(self, stmt):
            return _Result(scalar=None)

    request = SimpleNamespace(path_params={"investigation_id": str(uuid.uuid4())})
    check = router_mod.require_investigation_access()
    with pytest.raises(HTTPException) as exc:
        await check(request=request, db=_DB(), current_user=_user())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_require_investigation_access_allows_member():
    class _DB:
        def __init__(self):
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            if self.calls == 1:
                return _Result(scalar=PROJECT_ID)
            return _Result(scalar=uuid.uuid4())  # membership row

    user = _user()
    request = SimpleNamespace(path_params={"investigation_id": str(uuid.uuid4())})
    check = router_mod.require_investigation_access()
    assert await check(request=request, db=_DB(), current_user=user) is user


def test_routes_are_guarded_and_write_ops_require_roles():
    """Every route carries the matching guard; write ops carry role deps."""
    from fastapi.routing import APIRoute

    def _dep_names(route):
        out = []
        stack = [route.dependant]
        while stack:
            dep = stack.pop()
            if dep.call is not None:
                out.append(getattr(dep.call, "__qualname__", ""))
            stack.extend(dep.dependencies)
        return " ".join(out)

    by_path = {
        (sorted(r.methods)[0], r.path): _dep_names(r)
        for r in router_mod.router.routes
        if isinstance(r, APIRoute)
    }
    assert "require_run_access" in by_path[("POST", "/api/v1/runs/{run_id}/investigations")]
    assert "require_role" in by_path[("POST", "/api/v1/runs/{run_id}/investigations")]
    assert "require_investigation_access" in by_path[("GET", "/api/v1/investigations/{investigation_id}")]
    assert "require_investigation_access" in by_path[("POST", "/api/v1/investigations/{investigation_id}/cancel")]
    assert "require_project_access" in by_path[("GET", "/api/v1/projects/{project_id}/investigations")]
    assert "require_project_access" in by_path[("GET", "/api/v1/projects/{project_id}/agent-policies")]
    assert "require_project_role" in by_path[("PUT", "/api/v1/projects/{project_id}/agent-policies/{agent_id}")]
    assert "require_project_access" in by_path[("GET", "/api/v1/projects/{project_id}/agent-runs")]
