"""HON-04: ``GET /api/v1/value-metrics/export`` is scoped like ``/by-team``.

A project the caller cannot read used to answer 200 with a one-key
``{"report_type": "value_metrics"}`` stub, saved to disk as if it were the
report. It is a 403 now (``resolve_project_scope``), and so is a non-admin's
unpinned export: that is the every-project aggregate, which spans tenants.

The handler is called directly with every argument spelled out (a
``Query(None)`` default is not ``None`` outside FastAPI).
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core import deps
from app.routers import value_metrics as router

pytestmark = pytest.mark.asyncio

OWN = uuid.uuid4()
FOREIGN = uuid.uuid4()


def _user() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role="viewer")


@pytest.fixture
def metrics(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    fake = AsyncMock(return_value={"period_days": 30, "defects_auto_grouped": 3, "headline": {}})
    monkeypatch.setattr(router, "get_value_metrics", fake)
    return fake


def _member_of(monkeypatch: pytest.MonkeyPatch, accessible) -> None:
    monkeypatch.setattr(deps, "get_accessible_project_ids", AsyncMock(return_value=accessible))


async def test_a_foreign_project_is_a_403_not_a_stub(monkeypatch, metrics):
    _member_of(monkeypatch, {OWN})

    with pytest.raises(HTTPException) as exc:
        await router.export_metrics(project_id=str(FOREIGN), days=30, db=MagicMock(), current_user=_user())

    assert exc.value.status_code == 403
    metrics.assert_not_awaited()


async def test_a_non_admin_without_a_project_is_refused_the_cross_tenant_aggregate(monkeypatch, metrics):
    _member_of(monkeypatch, {OWN})

    with pytest.raises(HTTPException) as exc:
        await router.export_metrics(project_id=None, days=30, db=MagicMock(), current_user=_user())

    assert exc.value.status_code == 403
    assert "Select a project" in str(exc.value.detail)
    metrics.assert_not_awaited()


async def test_a_malformed_project_id_is_a_400(monkeypatch, metrics):
    _member_of(monkeypatch, {OWN})

    with pytest.raises(HTTPException) as exc:
        await router.export_metrics(project_id="not-a-uuid", days=30, db=MagicMock(), current_user=_user())

    assert exc.value.status_code == 400
    metrics.assert_not_awaited()


async def test_a_member_gets_the_windowed_report_as_an_attachment(monkeypatch, metrics):
    _member_of(monkeypatch, {OWN})
    db = MagicMock()

    response = await router.export_metrics(project_id=str(OWN), days=90, db=db, current_user=_user())

    metrics.assert_awaited_once_with(db, project_id=OWN, days=90)
    body = json.loads(response.body)
    assert body["report_type"] == "value_metrics"
    assert body["defects_auto_grouped"] == 3
    assert response.headers["content-disposition"] == "attachment; filename=value-metrics-90d.json"


async def test_an_admin_may_export_every_project(monkeypatch, metrics):
    _member_of(monkeypatch, None)  # admin: unrestricted
    db = MagicMock()

    response = await router.export_metrics(project_id=None, days=30, db=db, current_user=_user())

    metrics.assert_awaited_once_with(db, project_id=None, days=30)
    assert json.loads(response.body)["report_type"] == "value_metrics"
