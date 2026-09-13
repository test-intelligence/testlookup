"""E1.2 (slice 1): invoke one agent through the public API.

An invocation is an ordinary pipeline run whose frozen plan keeps only the
invoked agent and its declared dependencies. These tests pin:

* which agents can run on their own, and the dependency closure they run with;
* that the restriction lives in the plan (and survives the verifier's rebuild);
* the invoke route: every refusal before the database, subject-derived project
  with the body as an assertion, one in-progress invocation per (run, agent),
  commit before dispatch, and a dispatch payload the real task accepts;
* the public projection (status and review read from the run) and the
  404-not-403 access guard;
* the worker runs the invocation as a restricted pipeline under the minted id.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Response

from app.services import agent_planner

# -- planner ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "stage, expected",
    [
        ("summary", "offline"),
        ("root_cause_analysis", "offline"),
        ("decision_report", "deep"),
        ("flaky_sentinel", "deep"),
        ("cluster_investigation_dispatch", None),
        ("cluster_investigation", None),
        ("investigator_plan", None),
        ("workflow", None),
        ("no_such_stage", None),
    ],
)
def test_which_workflow_can_run_an_agent_on_its_own(stage, expected):
    assert agent_planner.invocation_workflow_type(stage) == expected


def test_the_closure_is_the_agent_and_its_declared_dependencies_in_graph_order():
    assert agent_planner.invocation_stage_closure("offline", "summary") == ["ingestion", "summary"]
    closure = agent_planner.invocation_stage_closure("deep", "decision_report")
    assert closure[0] == "ingestion" and closure[-1] == "decision_report"
    assert {"flaky_sentinel", "test_health", "release_risk"} <= set(closure)
    assert "summary" not in closure


def _planned(plan):
    return {s["stage"] for s in plan["stages"] if s["planned"]}


def test_an_invocation_plan_selects_only_the_closure():
    failures = ["t-1"]
    full = agent_planner.build_workflow_plan(workflow_type="offline", failed_test_ids=failures)
    invoked = agent_planner.build_workflow_plan(
        workflow_type="offline", failed_test_ids=failures, invocation_stage="summary",
    )
    assert {"anomaly_detection", "root_cause_analysis"} <= _planned(full)
    assert _planned(invoked) == {"ingestion", "summary"}
    assert invoked["invocation_stage"] == "summary"
    assert "invocation_stage" not in full, "plans that are not invocations keep their hash inputs"
    assert invoked["plan_sha256"] != full["plan_sha256"]
    skipped = next(s for s in invoked["stages"] if s["stage"] == "anomaly_detection")
    assert "agent.summary.v1" in skipped["rationale"]


def test_a_stage_the_graph_does_not_have_is_refused():
    with pytest.raises(ValueError):
        agent_planner.build_workflow_plan(workflow_type="offline", invocation_stage="decision_report")


def test_the_verifier_rebuild_keeps_the_invocation_restriction():
    plan = agent_planner.build_workflow_plan(
        workflow_type="offline", failed_test_ids=["t-1"], invocation_stage="summary",
    )
    state = {
        "initial_workflow_plan": plan,
        "failed_test_ids": ["t-1"],
        "analyses": {},
        "completed_stages": ["ingestion", "summary"],
        "skipped_stages": ["anomaly_detection", "root_cause_analysis", "triage"],
    }
    out = agent_planner.attach_workflow_plan_and_verification(state, workflow_type="offline")
    assert _planned(out["workflow_plan"]) == {"ingestion", "summary"}


# -- invoke route ------------------------------------------------------------------


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(first=lambda: self._value, all=lambda: [] if self._value is None else [self._value])


def _body(**overrides):
    from app.routers.agent_invoke import AgentInvokeRequest

    base = {
        "project_id": str(uuid.uuid4()),
        "input": {"agent_id": "agent.summary.v1", "payload": {"test_run_id": str(uuid.uuid4())}},
    }
    base.update(overrides)
    return AgentInvokeRequest(**base)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "agent_id, overrides, code, fragment",
    [
        ("summary", {}, 422, "agent.<name>"),
        ("agent.no_such_agent.v1", {}, 404, "Unknown agent"),
        (
            "agent.cluster_investigation.v1",
            {"input": {"agent_id": "agent.cluster_investigation.v1", "payload": {"test_run_id": str(uuid.uuid4())}}},
            422,
            "cannot be invoked on its own",
        ),
        (
            "agent.summary.v1",
            {"input": {"agent_id": "agent.triage.v1", "payload": {"test_run_id": str(uuid.uuid4())}}},
            422,
            None,
        ),
        ("agent.summary.v1", {"input": {"agent_id": "agent.summary.v1", "payload": {"x": 1}}}, 422, None),
    ],
)
async def test_invalid_invocations_are_refused_before_the_database(agent_id, overrides, code, fragment):
    from app.routers.agent_invoke import invoke_agent

    with pytest.raises(HTTPException) as exc:
        await invoke_agent(
            agent_id=agent_id, body=_body(**overrides), response=Response(), db=None,
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )
    assert exc.value.status_code == code
    if fragment:
        assert fragment in str(exc.value.detail)


def _router_harness(monkeypatch, *results):
    pytest.importorskip("celery")
    from app.routers import agent_invoke as router
    from app.worker import tasks

    order: list[str] = []
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_Result(r) for r in results])
    db.add = MagicMock()
    db.commit = AsyncMock(side_effect=lambda: order.append("commit"))
    dispatch = MagicMock(side_effect=lambda **_kw: order.append("dispatch"))
    monkeypatch.setattr(router, "resolve_project_scope", AsyncMock())
    from app.services import agent_config_resolver
    monkeypatch.setattr(router, "resolve_for_project", AsyncMock(
        side_effect=lambda _db, _project, agent, **_kw: agent_config_resolver.resolve(agent, global_ai_config={})
    ))
    monkeypatch.setattr(router, "record_activity", AsyncMock())
    monkeypatch.setattr(router, "ActorRef", SimpleNamespace(from_user=lambda _u: "actor"))
    monkeypatch.setattr(tasks.run_agent_invocation, "apply_async", dispatch)
    return router, tasks, db, dispatch, order


@pytest.mark.asyncio
async def test_a_valid_invocation_is_recorded_committed_then_dispatched(monkeypatch):
    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), build_number="42")
    router, tasks, db, dispatch, order = _router_harness(monkeypatch, run, None)
    body = _body(
        project_id=str(run.project_id),
        input={"agent_id": "agent.summary.v1", "payload": {"test_run_id": str(run.id)}},
        correlation_id="ci-123",
    )
    response = Response()

    out = await router.invoke_agent(
        agent_id="agent.summary.v1", body=body, response=response, db=db,
        current_user=SimpleNamespace(id=uuid.uuid4()),
    )

    assert order == ["commit", "dispatch"], "the worker loads the row, so it must be committed first"
    added = db.add.call_args.args[0]
    assert (added.agent_id, added.stage_name, added.workflow_type) == ("agent.summary.v1", "summary", "offline")
    assert added.test_run_id == run.id and added.project_id == run.project_id
    assert added.pipeline_run_id is not None and added.correlation_id == "ci-123"
    kwargs = dispatch.call_args.kwargs["kwargs"]
    assert kwargs == {"invocation_id": str(added.id)}
    assert dispatch.call_args.kwargs["queue"] == "ai_analysis"
    inspect.signature(tasks.run_agent_invocation.run).bind(**kwargs)
    router.record_activity.assert_awaited_once()
    assert router.record_activity.await_args.kwargs["event_type"] == "agent.invoked"
    assert out["status"] == "in_progress"
    assert out["requires_human_review"] is True and out["review"]["state"] == "pending_review"
    assert out["links"]["self"] == f"/api/v1/agents/invocations/{added.id}"


@pytest.mark.asyncio
async def test_a_body_project_that_is_not_the_runs_project_is_refused(monkeypatch):
    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), build_number="42")
    router, _tasks, db, dispatch, _order = _router_harness(monkeypatch, run)
    body = _body(input={"agent_id": "agent.summary.v1", "payload": {"test_run_id": str(run.id)}})

    with pytest.raises(HTTPException) as exc:
        await router.invoke_agent(
            agent_id="agent.summary.v1", body=body, response=Response(), db=db,
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )
    assert exc.value.status_code == 400
    db.add.assert_not_called()
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_an_invocation_still_in_progress_is_returned_instead_of_a_second_one(monkeypatch):
    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), build_number="42")
    existing = _invocation(test_run_id=run.id, project_id=run.project_id)
    pipeline = _pipeline(existing, status="running")
    # run, latest invocation, its pipeline, its review, its stage row
    router, _tasks, db, dispatch, _order = _router_harness(monkeypatch, run, existing, pipeline, None, None)
    body = _body(
        project_id=str(run.project_id),
        input={"agent_id": "agent.summary.v1", "payload": {"test_run_id": str(run.id)}},
    )
    response = Response()

    out = await router.invoke_agent(
        agent_id="agent.summary.v1", body=body, response=response, db=db,
        current_user=SimpleNamespace(id=uuid.uuid4()),
    )

    assert response.status_code == 200
    assert out["id"] == existing.id and out["status"] == "in_progress"
    db.add.assert_not_called()
    dispatch.assert_not_called()


# -- projection ----------------------------------------------------------------------


def _invocation(**overrides):
    base = {
        "id": uuid.uuid4(), "project_id": uuid.uuid4(), "agent_id": "agent.summary.v1",
        "stage_name": "summary", "test_run_id": uuid.uuid4(), "pipeline_run_id": uuid.uuid4(),
        "workflow_type": "offline", "mode": "async", "created_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _pipeline(invocation, **overrides):
    base = {
        "id": invocation.pipeline_run_id, "status": "completed", "attempt": 1, "max_attempts": 5,
        "next_retry_at": None, "error": None, "review_policy": "required",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_an_invocation_whose_run_never_appeared_reads_failed_after_the_grace():
    from app.routers.agent_invoke import DISPATCH_GRACE, project_invocation

    invocation = _invocation(created_at=datetime.now(timezone.utc) - DISPATCH_GRACE - timedelta(seconds=1))
    view = project_invocation(invocation, None, None)
    assert view["status"] == "failed" and "invocation_not_started" in view["error"]
    fresh = project_invocation(_invocation(), None, None)
    assert fresh["status"] == "in_progress" and fresh["error"] is None


def test_status_and_attempts_are_read_from_the_pipeline_run():
    from app.routers.agent_invoke import project_invocation

    invocation = _invocation()
    view = project_invocation(invocation, _pipeline(invocation, status="retry_wait", attempt=2), None)
    assert (view["status"], view["attempt"], view["max_attempts"]) == ("in_progress", 2, 5)
    assert view["links"]["pipeline"] == f"/api/v1/agents/pipelines/{invocation.pipeline_run_id}"


def test_a_settled_review_is_carried_with_its_link():
    from app.routers.agent_invoke import project_invocation

    invocation = _invocation()
    review = SimpleNamespace(id=uuid.uuid4(), state="accepted", reviewed_at=datetime.now(timezone.utc))
    view = project_invocation(invocation, _pipeline(invocation, status="passed"), review)
    assert view["status"] == "passed"
    assert view["review"]["state"] == "accepted" and view["requires_human_review"] is True
    assert view["links"]["review"] == f"/api/v1/reviews/{review.id}"


def test_an_agent_that_produces_no_report_has_nothing_to_review():
    from app.routers.agent_invoke import project_invocation

    invocation = _invocation(agent_id="agent.anomaly_detection.v1", stage_name="anomaly_detection")
    view = project_invocation(invocation, None, None)
    assert view["review"]["state"] == "not_applicable" and view["requires_human_review"] is False


# -- access guard ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "results, role, expected",
    [
        ([None], "QA_ENGINEER", 404),
        ([uuid.uuid4(), None], "QA_ENGINEER", 404),
        ([uuid.uuid4(), uuid.uuid4()], "QA_ENGINEER", None),
        ([uuid.uuid4()], "ADMIN", None),
    ],
)
async def test_the_access_guard_hides_other_tenants_invocations(results, role, expected):
    from app.routers.agent_invoke import require_invocation_access

    check = require_invocation_access()
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_Result(r) for r in results])
    request = SimpleNamespace(path_params={"invocation_id": str(uuid.uuid4())})
    user = SimpleNamespace(id=uuid.uuid4(), role=role)
    if expected is None:
        assert await check(request=request, db=db, current_user=user) is user
    else:
        with pytest.raises(HTTPException) as exc:
            await check(request=request, db=db, current_user=user)
        assert exc.value.status_code == expected


def test_the_poll_route_uses_the_access_guard():
    from app.routers.agent_invoke import get_invocation

    default = inspect.signature(get_invocation).parameters["_"].default
    assert "require_invocation_access" in getattr(default.dependency, "__qualname__", "")


# -- worker ------------------------------------------------------------------------------


def test_the_worker_runs_the_invocation_as_a_restricted_pipeline_under_the_minted_id(monkeypatch):
    pytest.importorskip("celery")
    import app.agents.workflow as workflow
    from app.worker import tasks

    invocation = _invocation()

    class _Session:
        async def get(self, _model, _id):
            return invocation

        async def execute(self, _stmt):
            return _Result("build-7")

    @asynccontextmanager
    async def _factory():
        yield _Session()

    real_offline = workflow.run_offline_pipeline
    offline = AsyncMock(return_value={"completed_stages": ["ingestion", "summary"]})
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _factory)
    monkeypatch.setattr(workflow, "run_offline_pipeline", offline)
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)

    out = tasks.run_agent_invocation.run(str(invocation.id))

    kwargs = offline.await_args.kwargs
    assert kwargs["invocation_stage"] == "summary"
    assert kwargs["create_if_missing"] is True
    assert kwargs["pipeline_run_id"] == str(invocation.pipeline_run_id)
    assert kwargs["workflow_type"] == "offline" and kwargs["build_number"] == "build-7"
    inspect.signature(real_offline).bind(**kwargs)
    assert out["completed_stages"] == ["ingestion", "summary"]


def test_deep_pipeline_accepts_the_invocation_arguments():
    import app.agents.workflow as workflow

    inspect.signature(workflow.run_deep_pipeline).bind(
        test_run_id="r", project_id="p", build_number="b", pipeline_run_id="x",
        create_if_missing=True, invocation_stage="decision_report",
    )


# -- migration and model -----------------------------------------------------------------


MIGRATION = Path(__file__).resolve().parents[1] / "migrations/versions/0176_agent_invocations.py"


def test_the_migration_and_model_agree():
    from app.models.postgres import INVOCATION_MODES, AgentInvocation

    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    constants = {
        target.id: ast.literal_eval(node.value)
        for node in tree.body if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Name) and target.id in {"MODES", "down_revision"}
    }
    assert tuple(constants["MODES"]) == INVOCATION_MODES
    assert constants["down_revision"] == "0175"
    migrated: set[str] = set()
    for path in (
        MIGRATION,
        MIGRATION.with_name("0177_agent_invocation_dispatched_at.py"),
        MIGRATION.with_name("0178_agent_invocation_idempotency.py"),
    ):
        migrated |= {
            call.args[0].value
            for call in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "Column"
            and call.args and isinstance(call.args[0], ast.Constant)
        }
    assert migrated == {c.name for c in AgentInvocation.__table__.columns}


def test_the_invoked_event_is_registered():
    from app.services.activity.events import lookup

    assert lookup("agent.invoked") is not None
