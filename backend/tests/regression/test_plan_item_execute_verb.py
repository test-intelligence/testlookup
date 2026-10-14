"""HON-01: recording a test-plan execution is a PATCH, end to end.

The backend registers ``PATCH /api/v1/test-management/plans/{plan_id}/items/
{item_id}/execute`` and nothing else on that path. The frontend's
``testManagementService.executeItem`` sent a POST, so every "passed / failed /
blocked" click on /test-management -> Plans answered 405 and the page showed a
generic error. The frontend unit test pinned ``postData`` for the call, which
is how the mismatch survived: each half was tested against itself.

These tests hold the two halves together:

* the route table has exactly one verb on the path, PATCH;
* a PATCH through the real router (guard overridden, service helpers faked)
  returns 200 with the new ``execution_status`` and commits once;
* the frontend service calls ``patchData`` for ``/execute`` -- the verb the
  route table registers -- so a client regression fails here too.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.regression

EXECUTE_PATH = "/api/v1/test-management/plans/{plan_id}/items/{item_id}/execute"


def _routes_on_execute_path() -> set[str]:
    from fastapi.routing import APIRoute

    from app.routers import test_management

    methods: set[str] = set()
    for route in test_management.router.routes:
        if isinstance(route, APIRoute) and route.path == EXECUTE_PATH:
            methods |= set(route.methods or ())
    return methods


def test_the_route_table_registers_patch_and_only_patch_on_execute():
    methods = _routes_on_execute_path()
    assert methods == {"PATCH"}, (
        f"/execute registers {sorted(methods)}; the client and the server must "
        "agree on one verb for one write"
    )


@pytest.fixture
def execute_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import test_management
    from app.routers.test_management_shared import require_plan_access
    from app.services import test_management_service as service

    plan = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    item = SimpleNamespace(
        id=uuid.uuid4(),
        plan_id=plan.id,
        test_case_id=uuid.uuid4(),
        order_index=0,
        priority_override=None,
        execution_status="not_run",
        executed_by_id=None,
        executed_at=None,
        execution_notes=None,
        actual_duration_minutes=None,
        created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    actor = SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER", is_active=True, api_key_project_id=None)

    # The real service runs; only its DB-touching helpers are faked.
    monkeypatch.setattr(service, "get_plan_or_404", AsyncMock(return_value=plan))
    monkeypatch.setattr(service, "get_plan_item_or_404", AsyncMock(return_value=item))
    monkeypatch.setattr(service, "recompute_plan_counts", AsyncMock())
    monkeypatch.setattr(service, "audit_event", AsyncMock())

    db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())

    async def _db():
        yield db

    app = FastAPI()
    app.include_router(test_management.router)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: actor
    app.dependency_overrides[require_plan_access] = lambda: plan

    url = f"/api/v1/test-management/plans/{plan.id}/items/{item.id}/execute"
    yield TestClient(app, raise_server_exceptions=False), url, item, db, actor


def test_a_patch_records_the_execution(execute_client):
    http, url, item, db, actor = execute_client

    resp = http.patch(url, json={"execution_status": "passed"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["execution_status"] == "passed"
    assert body["id"] == str(item.id)
    assert body["executed_by_id"] == str(actor.id)
    assert body["executed_at"] is not None
    db.commit.assert_awaited_once()


def test_a_post_is_not_a_second_way_to_write_it(execute_client):
    http, url, item, db, _ = execute_client

    resp = http.post(url, json={"execution_status": "passed"})

    assert resp.status_code == 405
    assert item.execution_status == "not_run"
    db.commit.assert_not_awaited()


def _frontend_service() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "frontend"
        / "src"
        / "services"
        / "testManagementService.ts"
    )


def test_the_frontend_client_uses_the_verb_the_route_table_registers():
    """The tripwire across the stack: the client's helper for ``/execute``
    must be the one for the verb the server registers."""
    source_path = _frontend_service()
    if not source_path.exists():  # pragma: no cover - backend-only checkouts
        pytest.skip("frontend not present in this checkout")
    text = source_path.read_text(encoding="utf-8")

    match = re.search(r"executeItem:[^\n]*\n(?:\s*//[^\n]*\n)*\s*(\w+)Data\(\s*`[^`]*/execute`", text)
    assert match, "executeItem in testManagementService.ts no longer calls <verb>Data(`.../execute`)"
    client_verb = match.group(1).upper()

    assert {client_verb} == _routes_on_execute_path(), (
        f"the client sends {client_verb} but the server registers "
        f"{sorted(_routes_on_execute_path())} on /execute"
    )
