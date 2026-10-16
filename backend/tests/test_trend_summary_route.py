"""W08 TRND-06: ``GET /api/v1/analytics/trend-summary``.

* the route wears the VIZ-209 read layer under its own namespace and takes the
  shared scope with ``/metrics/trends``' rule (``days`` 1-90, default 14,
  ``on_denied="empty"``);
* the body is exactly the contract's keys plus ``meta``, computed from the
  page's two reads called with the scope's release and suite;
* a scope the caller cannot read is the empty ``measured: false`` body, and
  nothing is read;
* ``days=91`` is the scope's 422.

The same over real rows is ``tests/integration/test_trend_summary_postgres.py``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.routers import analytics_trend_summary as route_module
from app.services.analytics_scope import AnalyticsScope

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
RELEASE = "22222222-2222-4222-8222-222222222222"
TODAY_KEYS = {
    "window_days", "today", "measured", "reason", "days_with_runs", "empty_days", "active_day_share",
    "executions", "pass_rate", "variance", "gap", "last_run_day", "previous_run_day", "untagged_share",
    "confidence", "meta",
}


def _route():
    [route] = [r for r in route_module.router.routes if r.path == "/api/v1/analytics/trend-summary"]
    return route


def _scope(**kw) -> AnalyticsScope:
    base = dict(project_id=PROJECT, allowed_project_ids=None, release_ids=(RELEASE,),
                suite_names=("checkout",), days=14)
    base.update(kw)
    return AnalyticsScope(**base)


def test_the_route_is_cached_and_takes_the_trend_charts_scope():
    from app.core.analytics_errors import POLICY_ATTR
    from app.core.deps import get_current_active_user

    route = _route()
    assert route.methods == {"GET"}
    assert getattr(route.endpoint, "__analytics_read__", None) == "trend_summary"
    policies = []
    for dep in route.dependant.dependencies:
        policy = getattr(dep.call, POLICY_ATTR, None)
        if policy is not None:
            policies.append(policy)
            assert any(sub.call is get_current_active_user for sub in dep.dependencies)
    assert len(policies) == 1
    policy = policies[0]
    assert (policy.default_days, policy.max_days, policy.on_denied, policy.project_required) == (
        14, 90, "empty", False,
    )


@pytest.mark.asyncio
async def test_the_body_is_the_contract_computed_from_the_pages_two_reads(monkeypatch):
    from app.services import analytics_service, metrics_service

    trend = AsyncMock(return_value=[
        {"date": "2026-09-12", "passed": 9, "failed": 1, "skipped": 0, "broken": 0, "total": 10, "pass_rate": 90.0},
    ])
    coverage = AsyncMock(return_value={"suites": [{"suite_name": "checkout", "passed": 9, "failed": 1, "skipped": 0}]})
    meta = AsyncMock(return_value={"schema_version": 3})
    monkeypatch.setattr(metrics_service, "get_trend_data", trend)
    monkeypatch.setattr(analytics_service, "coverage_stats", coverage)
    monkeypatch.setattr(route_module, "build_meta", meta)
    db = object()

    body = await route_module.get_trend_summary(scope=_scope(), db=db)

    assert set(body) == TODAY_KEYS
    assert body["meta"] == {"schema_version": 3}
    assert body["executions"]["total"] == 10 and body["pass_rate"] == 90.0
    # The page's own two reads, with the scope's release and suite (D-3).
    trend.assert_awaited_once_with(db, str(PROJECT), 14, suite_name="checkout", release_id=RELEASE)
    coverage.assert_awaited_once_with(db, str(PROJECT), 14, "checkout", None, RELEASE)
    # The envelope states the trend chart's UTC-midnight window.
    kwargs = meta.await_args.kwargs
    assert kwargs["pass_rate_basis"] == "executions" and kwargs["window_start"].hour == 0


@pytest.mark.asyncio
async def test_a_denied_scope_is_the_empty_unmeasured_body_and_reads_nothing(monkeypatch):
    from app.services import analytics_service, metrics_service

    refuse = AsyncMock(side_effect=AssertionError("a denied scope must read no data"))
    monkeypatch.setattr(metrics_service, "get_trend_data", refuse)
    monkeypatch.setattr(analytics_service, "coverage_stats", refuse)
    db = AsyncMock()

    body = await route_module.get_trend_summary(scope=_scope(denied=True, allowed_project_ids=frozenset()), db=db)

    db.execute.assert_not_awaited()
    assert body["measured"] is False and body["reason"] == "No executions in this window."
    assert body["executions"]["total"] == 0 and body["confidence"]["verdict"] == "PENDING"
    assert body["meta"]["measured"] is False and body["meta"]["scope"]["projects"] == []


@pytest.mark.asyncio
async def test_a_project_the_caller_cannot_read_resolves_to_the_denied_scope(monkeypatch):
    """``on_denied="empty"``: a non-member's project is the empty body, not a 403."""
    from app.core import deps
    from app.services.analytics_scope import resolve_analytics_scope

    monkeypatch.setattr(deps, "get_accessible_project_ids", AsyncMock(return_value={uuid.uuid4()}))
    scope = await resolve_analytics_scope(
        AsyncMock(), SimpleNamespace(id=uuid.uuid4(), role="VIEWER"),
        policy=route_module.TREND_SUMMARY_POLICY, project_id=str(PROJECT),
    )
    assert scope.denied is True


@pytest.mark.parametrize("days,status", [(91, 422), (0, 422)])
def test_the_window_is_one_to_ninety_days(days, status):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.analytics_errors import AnalyticsQueryError, analytics_query_error_handler
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db

    app = FastAPI()
    app.include_router(route_module.router)
    app.add_exception_handler(AnalyticsQueryError, analytics_query_error_handler)

    async def _db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(id=uuid.uuid4(), role="ADMIN")
    response = TestClient(app).get(f"/api/v1/analytics/trend-summary?project_id={PROJECT}&days={days}")
    assert response.status_code == status, response.text
    assert "window_days_range" in response.text
