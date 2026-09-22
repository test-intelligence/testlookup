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


@pytest.mark.asyncio
async def test_a_member_without_a_project_gets_the_empty_payload():
    scope = await _scope(None, {uuid.uuid4()})
    assert scope.denied
    result = await metrics_router.dashboard_summary(scope=scope, db=AsyncMock())
    assert _without_meta(result) == {}
