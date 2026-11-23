"""S-D-1: changing a test plan's status works.

The plan row's status select sends ``PATCH /plans/{id}`` with ``{status}``.
``update_test_plan`` passed the body to ``apply_model_updates``, which refuses
``status`` for every model (that guard belongs to the test-case lifecycle), so
the ``ValueError`` ended every plan status change in a 500. A plan's status is
a plain field: the service sets it, audits it, and the schema refuses values
the UI does not offer (422 through the route).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.models.schemas import TestPlanUpdate as PlanUpdate

PLAN = uuid.UUID("22222222-2222-4222-8222-222222222222")
PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)


def _plan_row():
    return SimpleNamespace(
        id=PLAN, project_id=PROJECT, name="Release 4", description=None, objective=None,
        status="draft", planned_start_date=None, planned_end_date=None, actual_start_date=None,
        actual_end_date=None, created_by_id=None, assigned_to_id=None, ai_generated=False,
        total_cases=0, executed_cases=0, passed_cases=0, failed_cases=0, blocked_cases=0,
        tags=None, created_at=NOW, updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_update_test_plan_sets_status_and_audits(monkeypatch):
    import app.services.test_management_service as svc

    plan = _plan_row()
    audit = AsyncMock()
    monkeypatch.setattr(svc, "get_plan_or_404", AsyncMock(return_value=plan))
    monkeypatch.setattr(svc, "audit_event", audit)

    result = await svc.update_test_plan(
        AsyncMock(), PLAN, PlanUpdate(status="active", name="Release 4 smoke"),
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert result.status == "active"
    assert result.name == "Release 4 smoke"
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["new_values"]["status"] == "active"


@pytest.mark.parametrize("status", ["draft", "active", "in_progress", "completed", "archived"])
def test_every_status_the_ui_offers_is_accepted(status):
    assert PlanUpdate(status=status).status == status


def test_unknown_status_is_refused():
    with pytest.raises(ValidationError):
        PlanUpdate(status="approved")


def test_route_answers_422_for_unknown_status_and_200_for_a_known_one(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.services.test_management_service as svc
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import test_management_plans as plans
    from app.routers.test_management_shared import require_plan_access

    row = _plan_row()
    monkeypatch.setattr(svc, "get_plan_or_404", AsyncMock(return_value=row))
    monkeypatch.setattr(svc, "audit_event", AsyncMock())

    user = SimpleNamespace(
        id=uuid.uuid4(), role="QA_LEAD", is_active=True, api_key_project_id=None,
        full_name="Lead", username="lead",
    )
    db = AsyncMock()

    async def _db():
        yield db

    app = FastAPI()
    app.include_router(plans.router, prefix="/api/v1/test-management")
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[require_plan_access] = lambda: row
    client = TestClient(app)

    bad = client.patch(f"/api/v1/test-management/plans/{PLAN}", json={"status": "approved"})
    assert bad.status_code == 422

    ok = client.patch(f"/api/v1/test-management/plans/{PLAN}", json={"status": "completed"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "completed"
