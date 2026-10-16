"""W08 TRND-07 -- ``GET /api/v1/analytics/coverage/history``: the scope
policy, the read layer, the exact body and the declared filters. The rows on a
real database are ``tests/integration/test_coverage_snapshot_postgres.py``.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.routers import analytics_coverage_map as router
from app.services import coverage_snapshot_service as svc
from app.services.analytics_scope import AnalyticsScope

PROJECT = uuid.UUID("11111111-1111-1111-1111-111111111111")
BODY_KEYS = {"window_days", "measured", "reason", "first_snapshot_day", "points"}


def _scope(**kwargs) -> AnalyticsScope:
    base = dict(project_id=PROJECT, allowed_project_ids=None, release_ids=(), suite_names=(), days=90)
    base.update(kwargs)
    return AnalyticsScope(**base)  # type: ignore[arg-type]


def test_the_policy_is_one_project_up_to_a_year_ninety_days_by_default():
    policy = router.COVERAGE_HISTORY_POLICY
    assert (policy.default_days, policy.max_days, policy.project_required) == (90, 365, True)
    assert policy.on_denied == "raise"


def test_the_route_is_cached_by_the_analytics_layer_under_its_own_namespace():
    from app.main import app

    route = next(r for r in app.routes if getattr(r, "path", None) == "/api/v1/analytics/coverage/history")
    assert route.methods == {"GET"}
    assert getattr(route.endpoint, "__analytics_read__", None) == "coverage_history"


@pytest.fixture
def served(monkeypatch):
    seen: dict = {}

    async def history(db, scope, *, today=None):
        seen["today"] = today
        return {"window_days": scope.window_days, "measured": False, "reason": svc.NO_SNAPSHOT_REASON,
                "first_snapshot_day": None, "points": []}

    async def fake_meta(db, scope, **kwargs):
        seen["meta"] = kwargs
        return {"measured": kwargs.get("measured"), "reason": kwargs.get("reason")}

    monkeypatch.setattr(svc, "coverage_history", history)
    monkeypatch.setattr(router, "build_meta", fake_meta)
    return seen


@pytest.mark.asyncio
async def test_the_body_is_exactly_the_contract_s(served):
    body = await router.coverage_history.__wrapped__(scope=_scope(), db=object())
    assert set(body) == BODY_KEYS | {"meta"}
    assert body["measured"] is False
    assert body["reason"] == "No coverage snapshot yet: the first is taken at 00:05 UTC."
    assert served["meta"]["measured"] is False and served["meta"]["reason"] == body["reason"]
    assert served["meta"]["ignored_filters"] == []
    assert set(body["meta"]["definitions"]) >= {"snapshot", "catalogue_tests", "exercised_pct", "scope"}
    assert isinstance(served["today"], date)


@pytest.mark.asyncio
async def test_release_and_suite_are_declared_ignored_when_sent(served):
    scope = _scope(release_ids=(str(uuid.uuid4()),), suite_names=("checkout",))
    await router.coverage_history.__wrapped__(scope=scope, db=object())
    assert [item["dimension"] for item in served["meta"]["ignored_filters"]] == ["release", "suite"]
    assert all("project-wide" in item["reason"] for item in served["meta"]["ignored_filters"])
