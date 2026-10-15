"""EXEC-05: ``GET /api/v1/runs/{run_id}/duration-regressions`` is the run's.

The route is mounted behind ``require_run_access``: a run of a project the
caller is not a member of answers 404 (the same as a run that does not exist,
so the id leaks nothing) and the findings are never read. A member and an
admin get the findings; a live run still streaming answers "not measured"
instead of 404, as ``/regression-diff`` does.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.regression

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
RUN = uuid.UUID("33333333-3333-4333-8333-333333333333")

FINDINGS = {
    "schema_version": 1,
    "measured": True,
    "reason": None,
    "stored": True,
    "evaluated_at": "2026-10-14T10:00:00+00:00",
    "spikes": [{
        "test_fingerprint": "fp-pay", "test_name": "checkout::test_pay", "suite_name": "checkout",
        "observed_ms": 400, "baseline_mean_ms": 100.0, "baseline_stddev_ms": 10.3,
        "baseline_p95_ms": 116.9, "sample_count": 20, "sigma": 29.2, "delta_ms": 300,
        "ratio": 4.0, "previous_ms": 110, "was_slow_before": False, "new": True,
    }],
    "spike_count": 1,
    "suites": [],
    "suite_regression_count": 0,
    "test_check": {"measured": True, "reason": None},
    "suite_check": {"measured": False, "reason": "no suite timing was recorded for this run"},
    "notified": {"tests": ["fp-pay"], "suites": []},
}


def _user(role: str):
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True, api_key_project_id=None)


def _result(value):
    return MagicMock(scalar_one_or_none=MagicMock(return_value=value))


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import runs

    app = FastAPI()
    app.include_router(runs.router)
    state = {"user": _user("VIEWER"), "member": False, "run_project": PROJECT}
    db = AsyncMock()

    async def execute(stmt, *a, **k):
        sql = str(stmt)
        if "project_members" in sql:
            return _result(uuid.uuid4() if state["member"] else None)
        if "live_sessions" in sql:
            return _result(state.get("live"))
        if "test_runs" in sql:
            return _result(state["run_project"])
        raise AssertionError(sql)

    db.execute = AsyncMock(side_effect=execute)

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state["user"]
    read = AsyncMock(return_value=dict(FINDINGS))
    with patch("app.services.duration_regression_service.get_duration_regressions", read):
        yield TestClient(app, raise_server_exceptions=False), state, read


@pytest.mark.parametrize("role", ["VIEWER", "QA_ENGINEER", "QA_LEAD"])
def test_a_non_member_gets_404_and_the_findings_are_never_read(client, role):
    http, state, read = client
    state["user"], state["member"] = _user(role), False
    resp = http.get(f"/api/v1/runs/{RUN}/duration-regressions")
    assert resp.status_code == 404
    assert "checkout::test_pay" not in resp.text
    read.assert_not_awaited()


def test_a_member_reads_the_findings(client):
    http, state, read = client
    state["user"], state["member"] = _user("VIEWER"), True
    resp = http.get(f"/api/v1/runs/{RUN}/duration-regressions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == str(RUN)
    assert body["measured"] is True
    assert body["spikes"][0]["test_name"] == "checkout::test_pay"
    assert body["spikes"][0]["new"] is True
    assert body["suite_check"]["reason"] == "no suite timing was recorded for this run"
    read.assert_awaited_once()
    assert read.await_args.args[1] == RUN


def test_an_unknown_run_is_404_for_a_member_and_an_admin(client):
    http, state, read = client
    state["run_project"] = None
    for role, member in (("VIEWER", True), ("ADMIN", False)):
        state["user"], state["member"] = _user(role), member
        read.return_value = None
        resp = http.get(f"/api/v1/runs/{uuid.uuid4()}/duration-regressions")
        assert resp.status_code == 404, role


def test_a_live_run_still_streaming_is_not_measured_rather_than_404(client):
    http, state, read = client
    state["user"] = _user("ADMIN")
    read.return_value = None
    state["live"] = RUN
    resp = http.get(f"/api/v1/runs/{RUN}/duration-regressions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["measured"] is False
    assert "still streaming" in body["reason"]
    assert body["spikes"] == [] and body["stored"] is False


def test_a_malformed_run_id_is_refused(client):
    http, state, _read = client
    state["user"] = _user("VIEWER")
    assert http.get("/api/v1/runs/not-a-uuid/duration-regressions").status_code in (400, 422)


def test_the_route_is_guarded_by_require_run_access():
    from app.core.deps import get_current_active_user
    from app.routers import runs

    route = next(
        r for r in runs.router.routes
        if getattr(r, "path", "") == "/api/v1/runs/{run_id}/duration-regressions"
    )
    names = {getattr(d.call, "__qualname__", "") for d in route.dependant.dependencies}
    assert any("require_run_access" in n for n in names), names
    # require_run_access resolves the caller through get_current_active_user.
    guard = next(d for d in route.dependant.dependencies if "require_run_access" in d.call.__qualname__)
    assert any(sub.call is get_current_active_user for sub in guard.dependencies)
