"""Regression: /suites and /suites/{name}/trend leaked cross-tenant via project_id.

Bug pinned (review/suite-history-service, 2026-06-02):

``list_test_suites`` and ``get_suite_trend`` (test_management_exports.py, both
backed by suite_history_service) only gated the no-project_id path (returned []
for non-admins in all-projects mode). A provided ``?project_id=<uuid>`` fell
through to the project-filtered query / ``compute_suite_*`` with no access
check, so any authenticated user could read another tenant's suite catalog and
trend. Fix: the routers verify the provided project_id is in the caller's
accessible set (403 otherwise, admin bypass).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")

from app.routers import test_management_exports as tme  # noqa: E402


@pytest.mark.asyncio
async def test_list_test_suites_denies_foreign_project():
    foreign = uuid.uuid4()
    with patch("app.core.deps.get_accessible_project_ids",
               AsyncMock(return_value={uuid.uuid4()})):
        with pytest.raises(HTTPException) as exc:
            await tme.list_test_suites(
                project_id=foreign, db=AsyncMock(),
                current_user=SimpleNamespace(id=uuid.uuid4()),
            )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_suite_trend_denies_foreign_project():
    foreign = uuid.uuid4()
    with patch("app.core.deps.get_accessible_project_ids",
               AsyncMock(return_value={uuid.uuid4()})):
        with pytest.raises(HTTPException) as exc:
            await tme.get_suite_trend(
                suite_name="auth", project_id=foreign, days=30, db=AsyncMock(),
                current_user=SimpleNamespace(id=uuid.uuid4()),
            )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_suite_trend_admin_allowed_calls_service():
    pid = uuid.uuid4()
    called = {}

    async def _fake_trend(db, project_id, suite_name, days):
        called["project_id"] = project_id
        return []

    with patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value=None)), \
         patch("app.services.suite_history_service.compute_suite_trend", _fake_trend):
        result = await tme.get_suite_trend(
            suite_name="auth", project_id=pid, days=30, db=AsyncMock(),
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )
    assert result["points"] == []
    assert called["project_id"] == pid  # admin → proceeds with the pinned project


@pytest.mark.asyncio
async def test_get_suite_trend_non_admin_no_project_returns_empty():
    with patch("app.core.deps.get_accessible_project_ids",
               AsyncMock(return_value={uuid.uuid4()})):
        result = await tme.get_suite_trend(
            suite_name="auth", project_id=None, days=30, db=AsyncMock(),
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )
    assert result["points"] == []
