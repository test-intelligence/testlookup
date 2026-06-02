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


def _user():
    return object()  # current_user is opaque to these handlers


@pytest.mark.asyncio
async def test_summary_blocks_foreign_project_for_non_admin():
    mine = uuid.uuid4()
    foreign = uuid.uuid4()
    svc = AsyncMock(return_value={"should": "not be returned"})

    with patch.object(metrics_router, "get_accessible_project_ids", AsyncMock(return_value={mine})), \
         patch.object(metrics_router, "get_dashboard_summary", svc):
        result = await metrics_router.dashboard_summary(
            project_id=str(foreign), days=7, suite_name=None, db=AsyncMock(), current_user=_user(),
        )

    assert result == {}                       # foreign project → empty
    svc.assert_not_called()                   # service never reached


@pytest.mark.asyncio
async def test_summary_allows_own_project_for_non_admin():
    mine = uuid.uuid4()
    svc = AsyncMock(return_value={"avg_pass_rate_7d": {"value": 90.0}})

    with patch.object(metrics_router, "get_accessible_project_ids", AsyncMock(return_value={mine})), \
         patch.object(metrics_router, "get_dashboard_summary", svc):
        result = await metrics_router.dashboard_summary(
            project_id=str(mine), days=7, suite_name=None, db=AsyncMock(), current_user=_user(),
        )

    assert result == {"avg_pass_rate_7d": {"value": 90.0}}
    svc.assert_awaited_once()


@pytest.mark.asyncio
async def test_summary_admin_unrestricted():
    """accessible is None ⇒ admin ⇒ any project_id (or None) passes through."""
    svc = AsyncMock(return_value={"ok": True})
    with patch.object(metrics_router, "get_accessible_project_ids", AsyncMock(return_value=None)), \
         patch.object(metrics_router, "get_dashboard_summary", svc):
        result = await metrics_router.dashboard_summary(
            project_id=str(uuid.uuid4()), days=7, suite_name=None, db=AsyncMock(), current_user=_user(),
        )
    assert result == {"ok": True}
    svc.assert_awaited_once()


@pytest.mark.asyncio
async def test_trends_blocks_foreign_project_for_non_admin():
    mine = uuid.uuid4()
    svc = AsyncMock(return_value=[{"date": "2026-06-01"}])
    with patch.object(metrics_router, "get_accessible_project_ids", AsyncMock(return_value={mine})), \
         patch.object(metrics_router, "get_trend_data", svc):
        result = await metrics_router.trend_data(
            project_id=str(uuid.uuid4()), days=7, suite_name=None, db=AsyncMock(), current_user=_user(),
        )
    assert result == {"data": [], "period_days": 7}
    svc.assert_not_called()
