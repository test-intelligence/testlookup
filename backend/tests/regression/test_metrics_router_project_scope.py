"""Regression: dashboard-metrics + value-metrics endpoints leaked cross-tenant
data when an explicit project_id was supplied.

Bug pinned (review/metrics-service, 2026-06-01):

``routers/metrics.py`` (/summary, /trends) and ``routers/value_metrics.py``
(get_metrics, export_metrics) only gated the *no-project_id* path for
non-admins; when a ``project_id`` WAS provided they passed it straight to the
service with no membership check. The services scope to project_id trustingly,
so any authenticated user could read any tenant's KPIs/trends/value-metrics via
``?project_id=<other-tenant-uuid>``. Fix: verify the provided project_id is in
the caller's accessible set (admins, accessible is None, skip).
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.routers import metrics as metrics_router  # noqa: E402
from app.services.analytics_scope import analytics_scope  # noqa: E402

# Since VIZ-201 the membership check lives in the shared scope dependency
# (``on_denied="empty"`` for these routes); the handler answers from the scope
# it is given. Each test runs the REAL dependency, then the handler with its
# result, with only the membership lookup patched.


def _without_meta(body: dict) -> dict:
    """The payload minus the additive VIZ-204 ``meta`` envelope."""
    return {k: v for k, v in body.items() if k != "meta"}


def _user():
    return object()  # current_user is opaque to these handlers


class _Request:
    def __init__(self, project_id):
        values = [project_id] if project_id else []
        self.query_params = type("Q", (), {"getlist": staticmethod(lambda _key: values)})()


async def _scope(project_id, accessible):
    dependency = analytics_scope(metrics_router.METRICS_SCOPE)
    with patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value=accessible)):
        return await dependency(
            _Request(project_id), project_id=project_id, release_id=None,
            suite_name=None, days=7, db=AsyncMock(), current_user=_user(),
        )


@pytest.mark.asyncio
async def test_summary_blocks_foreign_project_for_non_admin():
    mine = uuid.uuid4()
    foreign = uuid.uuid4()
    svc = AsyncMock(return_value={"should": "not be returned"})

    scope = await _scope(str(foreign), {mine})
    with patch.object(metrics_router, "get_dashboard_summary", svc):
        result = await metrics_router.dashboard_summary(scope=scope, db=AsyncMock())

    # foreign project → the empty payload (plus the VIZ-204 envelope, which
    # names no project and measures nothing)
    assert _without_meta(result) == {}
    assert result["meta"]["scope"]["projects"] == [] and result["meta"]["measured"] is False
    svc.assert_not_called()                   # service never reached


@pytest.mark.asyncio
async def test_summary_allows_own_project_for_non_admin():
    mine = uuid.uuid4()
    svc = AsyncMock(return_value={"avg_pass_rate_7d": {"value": 90.0}})

    scope = await _scope(str(mine), {mine})
    with patch.object(metrics_router, "get_dashboard_summary", svc):
        result = await metrics_router.dashboard_summary(scope=scope, db=AsyncMock())

    assert result == {"avg_pass_rate_7d": {"value": 90.0}}
    svc.assert_awaited_once()
    assert svc.await_args.args[1] == str(mine)
    # Pinned to their own project: no set (the pin is the whole scope).
    assert svc.await_args.kwargs["allowed_project_ids"] is None


@pytest.mark.asyncio
async def test_summary_admin_unrestricted():
    """accessible is None ⇒ admin ⇒ any project_id (or None) passes through."""
    svc = AsyncMock(return_value={"ok": True})
    scope = await _scope(str(uuid.uuid4()), None)
    with patch.object(metrics_router, "get_dashboard_summary", svc):
        result = await metrics_router.dashboard_summary(scope=scope, db=AsyncMock())
    assert result == {"ok": True}
    svc.assert_awaited_once()


@pytest.mark.asyncio
async def test_trends_blocks_foreign_project_for_non_admin():
    mine = uuid.uuid4()
    svc = AsyncMock(return_value=[{"date": "2026-06-01"}])
    scope = await _scope(str(uuid.uuid4()), {mine})
    with patch.object(metrics_router, "get_trend_data", svc):
        result = await metrics_router.trend_data(scope=scope, db=AsyncMock())
    assert _without_meta(result) == {"data": [], "period_days": 7}
    svc.assert_not_called()


# STRAT-01 / D28 (``METRICS_SCOPE.aggregate_unpinned``). Until W35 this file
# pinned "a member without a project gets the empty payload". A member of A and
# B who names no project now reads All Projects over A and B: the scope carries
# exactly their set (a foreign project is not in it) and both handlers hand it
# to the service, whose queries apply it (``test_metrics_all_projects_member_
# scope.py``; over real rows, ``tests/integration/test_all_projects_member_
# scope_postgres.py``).


@pytest.mark.asyncio
async def test_a_member_without_a_project_reads_only_their_projects():
    a, b, foreign = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    scope = await _scope(None, {a, b})
    assert not scope.denied and scope.project is None
    assert scope.allowed_project_ids == frozenset({a, b})
    assert foreign not in scope.allowed_project_ids

    summary = AsyncMock(return_value={"ok": True})
    with patch.object(metrics_router, "get_dashboard_summary", summary):
        assert await metrics_router.dashboard_summary(scope=scope, db=AsyncMock()) == {"ok": True}
    assert summary.await_args.args[1] is None
    assert summary.await_args.kwargs["allowed_project_ids"] == frozenset({a, b})

    trends = AsyncMock(return_value=[{"date": "2026-11-01"}])
    with patch.object(metrics_router, "get_trend_data", trends), \
            patch.object(metrics_router, "build_meta", AsyncMock(return_value={})):
        result = await metrics_router.trend_data(scope=scope, db=AsyncMock())
    assert result["data"] == [{"date": "2026-11-01"}]
    assert trends.await_args.args[1] is None
    assert trends.await_args.kwargs["allowed_project_ids"] == frozenset({a, b})


@pytest.mark.asyncio
async def test_a_caller_with_no_project_at_all_still_gets_the_empty_payload():
    scope = await _scope(None, set())
    assert scope.denied
    refuse = AsyncMock(side_effect=AssertionError("a denied scope reads no data"))
    with patch.object(metrics_router, "get_dashboard_summary", refuse), \
            patch.object(metrics_router, "get_trend_data", refuse):
        summary = await metrics_router.dashboard_summary(scope=scope, db=AsyncMock())
        trends = await metrics_router.trend_data(scope=scope, db=AsyncMock())
    assert _without_meta(summary) == {}
    assert _without_meta(trends) == {"data": [], "period_days": 7}


@pytest.mark.asyncio
async def test_the_admin_without_a_project_stays_unrestricted():
    scope = await _scope(None, None)
    assert not scope.denied and scope.allowed_project_ids is None

    summary = AsyncMock(return_value={"ok": True})
    trends = AsyncMock(return_value=[])
    with patch.object(metrics_router, "get_dashboard_summary", summary), \
            patch.object(metrics_router, "get_trend_data", trends), \
            patch.object(metrics_router, "build_meta", AsyncMock(return_value={})):
        await metrics_router.dashboard_summary(scope=scope, db=AsyncMock())
        await metrics_router.trend_data(scope=scope, db=AsyncMock())
    for svc in (summary, trends):
        assert svc.await_args.args[1] is None
        assert svc.await_args.kwargs["allowed_project_ids"] is None
