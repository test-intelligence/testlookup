"""Regression: ``/api/v1/test-management/suites/{name}/trend`` endpoint.

Bug pinned (2026-05-19): /coverage/suite and /test-management Test
Suites had no per-day trend for any suite. ``suite_history_service``
landed as the canonical aggregate source; this endpoint exposes its
per-day data.

What this file pins:

  * Endpoint returns ``{suite_name, days, points}`` shape.
  * Days are zero-filled (chart x-axis stays continuous).
  * No-project + ALL_PROJECTS_ID-equivalent caller path returns
    empty without leaking cross-tenant data.
  * Delegates to ``compute_suite_trend`` — service-layer changes
    don't need to update the endpoint.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_trend_endpoint_delegates_to_service_and_returns_envelope():
    from app.routers.test_management_exports import get_suite_trend

    project_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), role="ADMIN")
    points = [
        {"date": "2026-05-13", "run_count": 1, "total_tests": 10,
         "passed_count": 9, "failed_count": 1, "skipped_count": 0, "broken_count": 0},
        {"date": "2026-05-14", "run_count": 0, "total_tests": 0,
         "passed_count": 0, "failed_count": 0, "skipped_count": 0, "broken_count": 0},
    ]
    db = AsyncMock()

    with patch("app.services.suite_history_service.compute_suite_trend",
               AsyncMock(return_value=points)):
        result = await get_suite_trend(
            suite_name="Auth",
            project_id=project_id,
            days=14,
            db=db,
            current_user=user,
        )
    assert result["suite_name"] == "Auth"
    assert result["days"] == 14
    assert result["points"] == points


@pytest.mark.asyncio
async def test_trend_endpoint_returns_empty_for_cross_project_caller():
    """When no project_id is supplied and the caller has accessible
    projects (i.e. not an unrestricted admin scope), return empty
    instead of a cross-tenant aggregate. Suite names can collide
    across projects — a cross-project trend would be misleading."""
    from app.routers.test_management_exports import get_suite_trend

    user = SimpleNamespace(id=uuid.uuid4(), role="QA_LEAD")
    db = AsyncMock()

    with patch("app.core.deps.get_accessible_project_ids",
               AsyncMock(return_value={uuid.uuid4()})), \
         patch("app.services.suite_history_service.compute_suite_trend",
               AsyncMock(return_value=[])) as svc:
        result = await get_suite_trend(
            suite_name="Auth", project_id=None, days=30,
            db=db, current_user=user,
        )
    assert result["points"] == []
    # Service is NOT called — the early-return short-circuit owns this case.
    svc.assert_not_called()
