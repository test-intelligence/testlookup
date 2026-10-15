"""EXEC-03: the execution-metrics routes are guarded, bounded and null-honest.

* ``GET /runs/{run_id}/timing`` sits behind ``require_run_access``: a run of a
  project the caller is not a member of answers 404 -- the same as a run that
  does not exist -- and the timing is never read; the bounds on ``slowest`` /
  ``timeline_limit`` are 422s.
* The two analytics reads take the shared scope with ``project_required`` and
  ``on_denied="empty"`` (a project the caller cannot read answers the empty
  shape, never its data), wear the VIZ-209 read layer, and answer the denied
  shape without a single data query.

The same guards over real HTTP and real rows, the query-id (``release_id``)
IDOR included, are ``tests/integration/test_execution_metrics_postgres.py``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.regression

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
RUN = uuid.UUID("33333333-3333-4333-8333-333333333333")


def _user(role: str):
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True, api_key_project_id=None)


def _result(value):
    return MagicMock(scalar_one_or_none=MagicMock(return_value=value))


def _timing_payload():
    from app.services import execution_metrics_service as svc

    agg = SimpleNamespace(test_count=1, timed_count=0, duration_count=1, summed_ms=500, phased_count=0,
                          setup_ms=None, call_ms=None, teardown_ms=None, first_started_at=None,
                          last_finished_at=None, worker_rows=0)
    run = SimpleNamespace(wall_clock_ms=None, duration_ms=500, timing_source="derived_sum",
                          exec_started_at=None, exec_finished_at=None)
    return {
        "run_id": RUN, "project_id": PROJECT, "status": "PASSED", "build_number": "secret-build-7",
        "branch": "main", "created_at": None,
        "run": svc.run_figures(run, agg), "phases": svc.phase_block(agg), "suites": svc.suites_block([]),
        "slowest": svc.slowest_block([], {}, limit=20, agg=agg), "workers": svc.workers_block([], agg),
        "timeline": svc.timeline_block([], agg, limit=2000), "duration_findings": None,
    }


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import execution_metrics

    app = FastAPI()
    app.include_router(execution_metrics.router)
    state = {"user": _user("VIEWER"), "member": False, "run_project": PROJECT}
    db = AsyncMock()

    async def execute(stmt, *a, **k):
        sql = str(stmt)
        if "project_members" in sql:
            return _result(uuid.uuid4() if state["member"] else None)
        if "test_runs" in sql:
            return _result(state["run_project"])
        raise AssertionError(sql)

    db.execute = AsyncMock(side_effect=execute)

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state["user"]
    read = AsyncMock(side_effect=lambda *a, **k: _timing_payload())
    with patch("app.services.execution_metrics_service.run_timing", read):
        yield TestClient(app, raise_server_exceptions=False), state, read


@pytest.mark.parametrize("role", ["VIEWER", "QA_ENGINEER", "QA_LEAD"])
def test_a_non_member_gets_404_and_the_timing_is_never_read(client, role):
    http, state, read = client
    state["user"], state["member"] = _user(role), False
    resp = http.get(f"/api/v1/runs/{RUN}/timing")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Test run not found"}
    assert "secret-build-7" not in resp.text
    read.assert_not_awaited()


def test_a_member_reads_the_timing_with_null_not_zero(client):
    http, state, read = client
    state["user"], state["member"] = _user("VIEWER"), True
    resp = http.get(f"/api/v1/runs/{RUN}/timing?slowest=5&timeline_limit=100")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == str(RUN)
    assert body["run"]["wall_clock_ms"] is None and body["run"]["parallelism"] is None
    assert body["run"]["timing_source"] == "derived_sum"
    assert body["timeline"]["measured"] is False
    assert body["timeline"]["reason"] == "report carried no timestamps"
    assert body["phases"]["setup_ms"] is None
    read.assert_awaited_once()
    assert read.await_args.args[1] == RUN
    assert read.await_args.kwargs == {"slowest": 5, "timeline_limit": 100}


def test_an_unknown_run_is_404_for_a_member_and_an_admin(client):
    http, state, read = client
    state["run_project"] = None
    read.side_effect = None
    read.return_value = None
    for role, member in (("VIEWER", True), ("ADMIN", False)):
        state["user"], state["member"] = _user(role), member
        assert http.get(f"/api/v1/runs/{uuid.uuid4()}/timing").status_code == 404, role


@pytest.mark.parametrize("query", ["slowest=0", "slowest=101", "timeline_limit=0", "timeline_limit=2001",
                                   "slowest=abc"])
def test_the_bounds_are_422(client, query):
    http, state, read = client
    state["user"], state["member"] = _user("VIEWER"), True
    assert http.get(f"/api/v1/runs/{RUN}/timing?{query}").status_code == 422
    read.assert_not_awaited()


def test_a_malformed_run_id_is_refused(client):
    http, state, _read = client
    assert http.get("/api/v1/runs/not-a-uuid/timing").status_code in (400, 422)


def _route(path):
    from app.routers import execution_metrics

    return next(r for r in execution_metrics.router.routes if getattr(r, "path", "") == path)


def test_the_run_route_is_guarded_by_require_run_access():
    from app.core.deps import get_current_active_user

    route = _route("/api/v1/runs/{run_id}/timing")
    guard = next(d for d in route.dependant.dependencies if "require_run_access" in d.call.__qualname__)
    assert any(sub.call is get_current_active_user for sub in guard.dependencies)


@pytest.mark.parametrize("path,namespace,days", [
    ("/api/v1/analytics/execution/suite-history", "execution_suite_history", 90),
    ("/api/v1/analytics/execution-metrics", "execution_metrics", 30),
])
def test_the_analytics_routes_take_the_shared_scope_and_the_read_layer(path, namespace, days):
    from app.core.analytics_errors import POLICY_ATTR
    from app.core.deps import get_current_active_user

    route = _route(path)
    assert getattr(route.endpoint, "__analytics_read__", None) == namespace
    policies = []
    for dep in route.dependant.dependencies:
        policy = getattr(dep.call, POLICY_ATTR, None)
        if policy is not None:
            policies.append(policy)
            assert any(sub.call is get_current_active_user for sub in dep.dependencies)
    assert len(policies) == 1
    policy = policies[0]
    assert policy.project_required is True and policy.on_denied == "empty"
    assert policy.default_days == days and policy.max_days == 365


def _denied(**kwargs):
    from app.services.analytics_scope import AnalyticsScope

    base = dict(project_id=PROJECT, allowed_project_ids=frozenset(), release_ids=(), suite_names=("checkout",),
                days=30, denied=True)
    base.update(kwargs)
    return AnalyticsScope(**base)


@pytest.mark.asyncio
async def test_a_denied_suite_history_is_the_empty_shape_with_no_data_query():
    from app.routers import execution_metrics

    db = AsyncMock()
    body = await execution_metrics.get_suite_execution_history(
        branch=None, limit=100, scope=_denied(days=90), db=db,
    )
    db.execute.assert_not_awaited()
    assert body["points"] == [] and body["measured"] is False and body["project_id"] is None
    assert body["meta"]["measured"] is False and body["meta"]["scope"]["projects"] == []


@pytest.mark.asyncio
async def test_a_denied_execution_metrics_is_the_empty_shape_with_no_data_query():
    from app.routers import execution_metrics

    db = AsyncMock()
    body = await execution_metrics.get_execution_metrics(
        branch=None, top=20, suite_limit=50, scope=_denied(suite_names=()), db=db,
    )
    db.execute.assert_not_awaited()
    assert body["project_id"] is None and body["measured"] is False
    assert body["suites"]["items"] == [] and body["slowest_tests"]["items"] == []
    assert all(p["wall_clock_ms"] is None for p in body["per_day"]["points"])
    assert len(body["duration_histogram"]["buckets"]) == 14
    assert body["meta"]["scope"]["projects"] == []
