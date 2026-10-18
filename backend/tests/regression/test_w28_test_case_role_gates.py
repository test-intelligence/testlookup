"""W28 (TCM-01, the API role gap): every write to a test case has a role floor.

Before W28 create, edit, request-review, transition, review-action, comments,
AI generation and AI review checked project membership only: a VIEWER created,
edited and commented on cases, generated AI cases into the project and wrote
the AI review through the API, while the page hid those controls (contract
§0.3). The floors are route PARAMETERS (``Depends(require_role(...))``), so the
decorator lines and their activity-coverage baseline keys are unchanged.

For each of the 12 routes (§3.1's ten changed rows + §3.2's two new ones):
every role below the floor gets 403 ``Requires at least <ROLE> role`` and the
service the handler would reach is NOT awaited; the floor role reaches it once.

DELETE's floor is QA_ENGINEER, while the lifecycle itself still requires
QA_LEAD to deprecate (the contract's amendment): a QA engineer passes the route
and is refused by the lifecycle, which this file does not exercise (the
lifecycle tests do).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.regression

P = uuid.UUID("11111111-1111-4111-8111-111111111111")
CASE = uuid.UUID("22222222-2222-4222-8222-222222222222")
NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)
ROLES = ["VIEWER", "TESTER", "QA_ENGINEER", "QA_LEAD", "ADMIN"]
BASE = "/api/v1/test-management"


def _user(role: str):
    return SimpleNamespace(
        id=uuid.uuid4(), role=role, is_active=True, api_key_project_id=None,
        full_name=f"{role} person", username=role.lower(),
    )


def _case_row():
    return SimpleNamespace(
        id=CASE, project_id=P, title="Pay with a card", description=None, objective=None,
        preconditions=None, steps=None, parameters=None, expected_result=None, test_data=None,
        test_type="functional", priority="medium", severity="major", feature_area=None,
        suite_name=None, test_suite_id=None, tags=None, status="draft", version=1,
        author_id=None, assignee_id=None, reviewer_id=None, is_automated=False,
        automation_status="not_automated", test_fingerprint=None, ai_generated=False,
        ai_quality_score=None, ai_review_notes=None, estimated_duration_minutes=None,
        last_executed_at=None, last_execution_status=None, created_at=NOW, updated_at=NOW,
        edited_since_approval_version=None,
    )


def _review_row():
    return SimpleNamespace(
        id=uuid.uuid4(), test_case_id=CASE, reviewer_id=None, requested_by_id=None,
        status="pending", ai_review_completed=False, ai_quality_score=None,
        ai_review_notes=None, ai_reviewed_at=None, human_notes=None, reviewed_at=None,
        created_at=NOW, updated_at=NOW,
    )


def _comment_row():
    return SimpleNamespace(
        id=uuid.uuid4(), test_case_id=CASE, author_id=None, content="Looks right",
        comment_type="general", parent_id=None, step_number=None, is_resolved=False,
        created_at=NOW, updated_at=NOW,
    )


#: (method, path, json body, floor role, (module, name) of the awaited service)
ROUTES = [
    ("POST", f"{BASE}/cases", {"project_id": str(P), "title": "Pay with a card"}, "TESTER",
     ("cases", "create_managed_test_case")),
    ("PATCH", f"{BASE}/cases/{CASE}", {"expected_version": 1, "title": "Pay with a saved card"}, "TESTER",
     ("cases", "update_managed_test_case")),
    ("DELETE", f"{BASE}/cases/{CASE}", {"reason": "obsolete"}, "QA_ENGINEER",
     ("cases", "deprecate_managed_test_case")),
    ("POST", f"{BASE}/cases/{CASE}/request-review", None, "TESTER",
     ("cases", "request_test_case_review")),
    ("POST", f"{BASE}/cases/{CASE}/transition", {"action": "activate", "expected_version": 1}, "TESTER",
     ("cases", "transition")),
    ("POST", f"{BASE}/cases/{CASE}/review-action", {"action": "approve", "notes": "ok"}, "TESTER",
     ("cases", "apply_review_action")),
    ("POST", f"{BASE}/cases/{CASE}/comments", {"content": "Looks right"}, "TESTER",
     ("cases", "add_test_case_comment")),
    ("POST", f"{BASE}/cases/ai-generate", {"project_id": str(P), "requirements": "Checkout"}, "TESTER",
     ("ai", "generate_ai_cases")),
    ("POST", f"{BASE}/cases/ai-generate/async", {"project_id": str(P), "requirements": "Checkout"}, "TESTER",
     ("ai", "enqueue_ai_case_generation")),
    ("POST", f"{BASE}/cases/{CASE}/ai-review", None, "TESTER",
     ("ai", "review_test_case_with_ai")),
    # §3.2, G-B's router (routers/test_management_editing.py).
    ("POST", f"{BASE}/cases/bulk",
     {"project_id": str(P), "case_ids": [str(CASE)], "action": "set_priority", "priority": "high"}, "TESTER",
     ("editing", "apply_bulk_action")),
    ("POST", f"{BASE}/cases/{CASE}/restore", {"version": 1, "expected_version": 2}, "TESTER",
     ("editing", "restore_case_version")),
]


@pytest.fixture
def app_as(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.models.schemas import (
        AIGenerateTestCasesResponse,
        AIReviewTestCaseResponse,
        AITaskEnqueueResponse,
        TestCaseBulkResponse,
    )
    from app.routers import test_management
    from app.routers import test_management_ai as ai
    from app.routers import test_management_cases as cases
    from app.routers import test_management_editing as editing
    from app.routers.test_management_shared import (
        require_case_access,
        require_case_access_for_review_target,
    )
    from app.services.test_case_lifecycle_service import LifecycleTransitionResult

    case = _case_row()
    modules = {"cases": cases, "ai": ai, "editing": editing}
    services = {
        ("cases", "create_managed_test_case"): AsyncMock(return_value=case),
        ("cases", "update_managed_test_case"): AsyncMock(return_value=case),
        ("cases", "deprecate_managed_test_case"): AsyncMock(return_value=None),
        ("cases", "request_test_case_review"): AsyncMock(return_value=_review_row()),
        ("cases", "transition"): AsyncMock(return_value=LifecycleTransitionResult(case=case)),
        ("cases", "apply_review_action"): AsyncMock(return_value=case),
        ("cases", "add_test_case_comment"): AsyncMock(return_value=_comment_row()),
        ("ai", "generate_ai_cases"): AsyncMock(return_value=AIGenerateTestCasesResponse(test_cases=[])),
        ("ai", "enqueue_ai_case_generation"): AsyncMock(return_value=AITaskEnqueueResponse(task_id="t-1")),
        ("ai", "review_test_case_with_ai"): AsyncMock(return_value=AIReviewTestCaseResponse(quality_score=80)),
        ("editing", "apply_bulk_action"): AsyncMock(
            return_value=TestCaseBulkResponse(
                action="set_priority", requested=1, changed=1, unchanged=0, skipped=0,
                not_found=0, results=[],
            )
        ),
        ("editing", "restore_case_version"): AsyncMock(return_value=case),
    }
    for (module, name), mock in services.items():
        monkeypatch.setattr(modules[module], name, mock)
    # Everything else the handlers reach, made inert.
    response = cases._validate_case(case)
    for module in (cases, editing):
        monkeypatch.setattr(module, "case_response", AsyncMock(return_value=response))
        monkeypatch.setattr(module, "emit_staged_test_management_metrics", AsyncMock())
        monkeypatch.setattr(module, "require_lifecycle_v2_enabled", AsyncMock())
        monkeypatch.setattr(module, "project_requires_case_review", AsyncMock(return_value=False))
    monkeypatch.setattr(ai, "resolve_project_scope", AsyncMock(return_value=(P, None)))
    monkeypatch.setattr(editing, "resolve_project_scope", AsyncMock(return_value=(P, None)))

    app = FastAPI()
    app.include_router(test_management.router)
    db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock(), rollback=AsyncMock())

    async def _db():
        yield db

    state = {"user": _user("VIEWER")}
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state["user"]
    app.dependency_overrides[require_case_access] = lambda: case
    app.dependency_overrides[require_case_access_for_review_target] = lambda: case
    return TestClient(app, raise_server_exceptions=False), state, services, db


def _send(http, method, path, body):
    if body is None:
        return http.request(method, path)
    return http.request(method, path, json=body)


@pytest.mark.parametrize("method, path, body, floor, service", ROUTES, ids=[f"{r[0]} {r[1]}" for r in ROUTES])
def test_every_role_below_the_floor_is_refused_before_the_service(app_as, method, path, body, floor, service):
    http, state, services, db = app_as
    for role in ROLES[: ROLES.index(floor)]:
        state["user"] = _user(role)
        resp = _send(http, method, path, body)
        assert resp.status_code == 403, (role, path, resp.status_code, resp.text)
        assert resp.json()["detail"] == f"Requires at least {floor} role", (role, path)
    services[service].assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.parametrize("method, path, body, floor, service", ROUTES, ids=[f"{r[0]} {r[1]}" for r in ROUTES])
def test_the_floor_role_reaches_the_service_once(app_as, method, path, body, floor, service):
    http, state, services, db = app_as
    state["user"] = _user(floor)
    resp = _send(http, method, path, body)
    assert resp.status_code < 300, (path, resp.status_code, resp.text)
    services[service].assert_awaited_once()


@pytest.mark.parametrize("role", ["QA_LEAD", "ADMIN"])
def test_roles_above_every_floor_pass_every_route(app_as, role):
    http, state, services, _ = app_as
    state["user"] = _user(role)
    for method, path, body, _floor, _service in ROUTES:
        resp = _send(http, method, path, body)
        assert resp.status_code < 300, (role, path, resp.status_code, resp.text)
    assert all(mock.await_count == 1 for mock in services.values())


def test_reads_stay_open_to_a_viewer():
    """The floors are on writes only: a VIEWER reads every case, its history,
    reviews, comments and allowed transitions (no require_role on a GET)."""
    import inspect

    from app.routers import test_management_cases as cases

    reads = [
        cases.list_test_cases, cases.get_test_case, cases.get_test_case_history,
        cases.get_reviews, cases.list_comments, cases.get_allowed_transitions,
        cases.get_test_case_evidence_gaps,
    ]
    for handler in reads:
        assert "require_role" not in inspect.getsource(handler), handler.__name__
