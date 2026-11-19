"""Test plans, strategies, suite-review verdicts and AI generation have role floors.

A VIEWER is read-only everywhere else in test management, but these routes
checked project membership only: a VIEWER created and renamed plans, recorded
executions, set a strategy to "approved", set a suite's review verdict and
started AI plan / strategy / coverage generation charged to the project.

The rule this file pins:

* plan create, update, add item, remove item, record execution: TESTER;
* strategy ``PUT``: TESTER, and changing its ``status`` needs QA_LEAD (a
  status is a sign-off), with ``status`` limited to the known values;
* suite-review ``PUT``: QA_ENGINEER (the same floor as a per-test verdict);
* the five AI routes (plan create sync/async, strategy generate sync/async,
  coverage analysis): QA_ENGINEER (they spend model budget).

Every role below a floor gets 403 ``Requires at least <ROLE> role`` and the
work behind the route is NOT started; the floor role reaches it once. The
floors are route parameters (``Depends(require_role(...))``), so the decorator
lines and the activity-coverage baseline keys are unchanged.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.regression

P = uuid.UUID("11111111-1111-4111-8111-111111111111")
PLAN = uuid.UUID("22222222-2222-4222-8222-222222222222")
ITEM = uuid.UUID("33333333-3333-4333-8333-333333333333")
CASE = uuid.UUID("44444444-4444-4444-8444-444444444444")
STRATEGY = uuid.UUID("55555555-5555-4555-8555-555555555555")
RUN = uuid.UUID("66666666-6666-4666-8666-666666666666")
NOW = datetime(2026, 11, 19, tzinfo=timezone.utc)
ROLES = ["VIEWER", "TESTER", "QA_ENGINEER", "QA_LEAD", "ADMIN"]
BASE = "/api/v1/test-management"
STATUS_REFUSAL = (
    "Changing a strategy's status requires at least QA_LEAD role. Ask a QA lead to "
    "change the status, or save your other edits without it."
)


def _user(role: str):
    return SimpleNamespace(
        id=uuid.uuid4(), role=role, is_active=True, api_key_project_id=None,
        full_name=f"{role} person", username=role.lower(),
    )


def _plan_row():
    return SimpleNamespace(
        id=PLAN, project_id=P, name="Release 4 regression", description=None, objective=None,
        status="draft", planned_start_date=None, planned_end_date=None, actual_start_date=None,
        actual_end_date=None, created_by_id=None, assigned_to_id=None, ai_generated=False,
        total_cases=0, executed_cases=0, passed_cases=0, failed_cases=0, blocked_cases=0,
        tags=None, created_at=NOW, updated_at=NOW,
    )


def _item_row():
    return SimpleNamespace(
        id=ITEM, plan_id=PLAN, test_case_id=CASE, order_index=0, priority_override=None,
        execution_status="not_run", executed_by_id=None, executed_at=None, execution_notes=None,
        actual_duration_minutes=None, created_at=NOW, executed_case_version=None,
    )


def _strategy_row(status: str = "draft"):
    return SimpleNamespace(
        id=STRATEGY, project_id=P, name="Checkout strategy", version_label="v1.0", status=status,
        objective=None, scope=None, out_of_scope=None, test_approach=None, risk_assessment=None,
        test_types=None, entry_criteria=None, exit_criteria=None, environments=None,
        automation_approach=None, defect_management=None, ai_generated=False, ai_model_used=None,
        created_by_id=None, approved_by_id=None, approved_at=None, created_at=NOW, updated_at=NOW,
    )


def _review_row():
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=P, suite_name="checkout", test_run_id=RUN, state="confirmed",
        note=None, reviewer_user_id=None, reviewed_at=NOW, created_at=NOW, updated_at=NOW,
    )


#: (method, path, json body, floor role, name of the mock the handler reaches,
#:  status the floor role gets)
ROUTES = [
    ("POST", f"{BASE}/plans", {"project_id": str(P), "name": "Release 4 regression"}, "TESTER",
     "create_test_plan", 201),
    ("PATCH", f"{BASE}/plans/{PLAN}", {"name": "Release 4 smoke"}, "TESTER",
     "update_test_plan", 200),
    ("POST", f"{BASE}/plans/{PLAN}/items", {"test_case_id": str(CASE)}, "TESTER",
     "add_test_plan_item", 201),
    ("DELETE", f"{BASE}/plans/{PLAN}/items/{ITEM}", None, "TESTER",
     "remove_test_plan_item", 204),
    ("PATCH", f"{BASE}/plans/{PLAN}/items/{ITEM}/execute", {"execution_status": "passed"}, "TESTER",
     "record_test_plan_execution", 200),
    ("PUT", f"{BASE}/strategies/{STRATEGY}", {"name": "Checkout strategy, revised"}, "TESTER",
     "update_strategy", 200),
    ("PUT", f"{BASE}/suite-reviews/by-run/{RUN}/checkout", {"state": "confirmed"}, "QA_ENGINEER",
     "update_review", 200),
    # The sync AI plan route reads the project's approved cases before it calls
    # the model; with none it answers 400, which is past the role floor and the
    # tenant guard and before any model call.
    ("POST", f"{BASE}/plans/ai-create", {"project_id": str(P), "plan_name": "AI plan"}, "QA_ENGINEER",
     "plans_scope", 400),
    ("POST", f"{BASE}/plans/ai-create/async", {"project_id": str(P), "plan_name": "AI plan"}, "QA_ENGINEER",
     "create_ai_test_plan_task", 200),
    ("POST", f"{BASE}/strategies/ai-generate", {"project_id": str(P), "project_context": "A web shop"},
     "QA_ENGINEER", "generate_ai_strategy", 200),
    ("POST", f"{BASE}/strategies/ai-generate/async", {"project_id": str(P), "project_context": "A web shop"},
     "QA_ENGINEER", "enqueue_ai_strategy_generation", 200),
    ("POST", f"{BASE}/cases/ai-coverage", {"project_id": str(P), "requirements": "Checkout"}, "QA_ENGINEER",
     "analyze_case_coverage", 200),
]
ROUTE_IDS = [f"{r[0]} {r[1].removeprefix(BASE)}" for r in ROUTES]


@pytest.fixture
def world(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.core.deps as deps
    import app.worker.tasks as worker_tasks
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.models.schemas import AICoverageAnalysisResponse, AITaskEnqueueResponse
    from app.routers import test_management
    from app.routers import test_management_ai as ai
    from app.routers import test_management_plans as plans
    from app.routers import test_management_strategies as strategies
    from app.routers import test_management_suite_reviews as suite_reviews
    from app.routers.test_management_shared import require_plan_access

    # project_role: the caller's role in the project; None means "the same as
    # their instance role", which is what a normally added member has.
    state = SimpleNamespace(user=_user("VIEWER"), strategy=_strategy_row(), project_role=None)
    plan = _plan_row()

    task = MagicMock()
    task.delay = MagicMock(return_value=SimpleNamespace(id="t-1"))
    reached = {
        "create_test_plan": AsyncMock(return_value=plan),
        "update_test_plan": AsyncMock(return_value=plan),
        "add_test_plan_item": AsyncMock(return_value=_item_row()),
        "remove_test_plan_item": AsyncMock(return_value=None),
        "record_test_plan_execution": AsyncMock(return_value=_item_row()),
        "update_strategy": AsyncMock(side_effect=lambda *a, **k: state.strategy),
        "update_review": AsyncMock(return_value=_review_row()),
        "plans_scope": AsyncMock(return_value=(P, None)),
        "create_ai_test_plan_task": task.delay,
        "generate_ai_strategy": AsyncMock(side_effect=lambda *a, **k: state.strategy),
        "enqueue_ai_strategy_generation": AsyncMock(return_value=AITaskEnqueueResponse(task_id="t-2")),
        "analyze_case_coverage": AsyncMock(return_value=AICoverageAnalysisResponse(coverage_score=50)),
    }
    for name in (
        "create_test_plan", "update_test_plan", "add_test_plan_item", "remove_test_plan_item",
        "record_test_plan_execution",
    ):
        monkeypatch.setattr(plans, name, reached[name])
    # plans.py imports resolve_project_scope inside its two AI handlers.
    monkeypatch.setattr(deps, "resolve_project_scope", reached["plans_scope"])
    monkeypatch.setattr(worker_tasks, "create_ai_test_plan_task", task)
    # The async plan route records who queued the task (Redis); not under test here.
    import app.services.test_management_ai_service as ai_service

    monkeypatch.setattr(ai_service, "record_ai_task_owner", AsyncMock(return_value=None))

    monkeypatch.setattr(strategies, "resolve_project_scope", AsyncMock(return_value=(P, None)))
    monkeypatch.setattr(strategies, "get_strategy_or_404", AsyncMock(side_effect=lambda *a, **k: state.strategy))
    monkeypatch.setattr(strategies, "update_strategy_model", reached["update_strategy"])
    monkeypatch.setattr(strategies, "generate_ai_strategy", reached["generate_ai_strategy"])
    monkeypatch.setattr(
        strategies, "enqueue_ai_strategy_generation", reached["enqueue_ai_strategy_generation"]
    )

    monkeypatch.setattr(suite_reviews, "get_accessible_project_ids", AsyncMock(return_value=None))
    monkeypatch.setattr(suite_reviews.svc, "get_or_create_review", AsyncMock(return_value=_review_row()))
    monkeypatch.setattr(suite_reviews.svc, "update_review", reached["update_review"])
    monkeypatch.setattr(suite_reviews.svc, "enrich_reviewers", AsyncMock(return_value={}))

    monkeypatch.setattr(ai, "resolve_project_scope", AsyncMock(return_value=(P, None)))
    monkeypatch.setattr(ai, "analyze_case_coverage", reached["analyze_case_coverage"])

    # One result object serves both readers: the suite-review route loads the
    # run, the sync AI plan route lists the project's approved cases (none).
    result = MagicMock()
    result.scalar_one_or_none.return_value = SimpleNamespace(id=RUN, project_id=P)
    result.scalars.return_value.all.return_value = []

    async def _execute(statement, *args, **kwargs):
        if "project_members" in str(statement):  # the status rule's membership-role lookup
            answer = MagicMock()
            answer.scalar_one_or_none.return_value = state.project_role or str(state.user.role)
            return answer
        return result

    db = SimpleNamespace(
        commit=AsyncMock(), refresh=AsyncMock(), rollback=AsyncMock(),
        execute=AsyncMock(side_effect=_execute),
    )

    async def _db():
        yield db

    app = FastAPI()
    app.include_router(test_management.router)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state.user
    app.dependency_overrides[require_plan_access] = lambda: plan
    state.http = TestClient(app, raise_server_exceptions=False)
    state.reached = reached
    state.db = db
    return state


def _send(world, method, path, body):
    if body is None:
        return world.http.request(method, path)
    return world.http.request(method, path, json=body)


@pytest.mark.parametrize("method, path, body, floor, reached, _ok", ROUTES, ids=ROUTE_IDS)
def test_every_role_below_the_floor_is_refused_before_any_work(world, method, path, body, floor, reached, _ok):
    for role in ROLES[: ROLES.index(floor)]:
        world.user = _user(role)
        resp = _send(world, method, path, body)
        assert resp.status_code == 403, (role, path, resp.status_code, resp.text)
        assert resp.json()["detail"] == f"Requires at least {floor} role", (role, path)
    assert world.reached[reached].call_count == 0
    world.db.commit.assert_not_awaited()


@pytest.mark.parametrize("method, path, body, floor, reached, ok", ROUTES, ids=ROUTE_IDS)
def test_the_floor_role_reaches_the_work_once(world, method, path, body, floor, reached, ok):
    world.user = _user(floor)
    resp = _send(world, method, path, body)
    assert resp.status_code == ok, (path, resp.status_code, resp.text)
    assert world.reached[reached].call_count == 1


@pytest.mark.parametrize("role", ["QA_LEAD", "ADMIN"])
def test_roles_above_every_floor_pass_every_route(world, role):
    world.user = _user(role)
    for method, path, body, _floor, _reached, ok in ROUTES:
        resp = _send(world, method, path, body)
        assert resp.status_code == ok, (role, path, resp.status_code, resp.text)


# ── strategy status: a sign-off, so QA_LEAD ──────────────────────────────────


@pytest.mark.parametrize("role", ["TESTER", "QA_ENGINEER"])
def test_changing_a_strategy_status_below_qa_lead_is_refused(world, role):
    world.user = _user(role)

    resp = world.http.put(f"{BASE}/strategies/{STRATEGY}", json={"status": "approved"})

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == STATUS_REFUSAL
    world.reached["update_strategy"].assert_not_awaited()
    world.db.commit.assert_not_awaited()


def test_a_status_change_hidden_among_other_edits_is_refused_whole(world):
    """No partial write: the name must not be saved while the status is refused."""
    world.user = _user("TESTER")

    resp = world.http.put(
        f"{BASE}/strategies/{STRATEGY}", json={"name": "Renamed", "status": "approved"}
    )

    assert resp.status_code == 403, resp.text
    world.reached["update_strategy"].assert_not_awaited()


@pytest.mark.parametrize("role", ["QA_LEAD", "ADMIN"])
def test_a_qa_lead_changes_a_strategy_status(world, role):
    world.user = _user(role)

    resp = world.http.put(f"{BASE}/strategies/{STRATEGY}", json={"status": "approved"})

    assert resp.status_code == 200, resp.text
    world.reached["update_strategy"].assert_awaited_once()


def test_an_instance_lead_with_a_lower_project_role_does_not_change_the_status(world):
    """The lower of the instance role and the project role decides a write."""
    world.user = _user("QA_LEAD")
    world.project_role = "TESTER"

    resp = world.http.put(f"{BASE}/strategies/{STRATEGY}", json={"status": "approved"})

    assert resp.status_code == 403, resp.text
    world.reached["update_strategy"].assert_not_awaited()


def test_resending_the_current_status_is_not_a_status_change(world):
    """A client that sends the whole strategy back, status included, keeps working."""
    world.user = _user("TESTER")
    world.strategy = _strategy_row(status="active")

    resp = world.http.put(
        f"{BASE}/strategies/{STRATEGY}", json={"name": "Renamed", "status": "active"}
    )

    assert resp.status_code == 200, resp.text
    world.reached["update_strategy"].assert_awaited_once()


@pytest.mark.parametrize("status", ["signed-off", "APPROVED", "", "x" * 31])
def test_an_unknown_strategy_status_is_a_422_for_anyone(world, status):
    world.user = _user("ADMIN")

    resp = world.http.put(f"{BASE}/strategies/{STRATEGY}", json={"status": status})

    assert resp.status_code == 422, resp.text
    world.reached["update_strategy"].assert_not_awaited()


def test_the_known_strategy_statuses_are_the_ones_the_page_offers():
    """The page's status menu (frontend StrategyTab) plus the model's ``archived``."""
    from app.models.schemas import TEST_STRATEGY_STATUSES, TestStrategyUpdate

    assert set(TEST_STRATEGY_STATUSES) == {
        "draft", "review", "approved", "active", "suspended", "closed", "archived",
    }
    for status in TEST_STRATEGY_STATUSES:
        assert TestStrategyUpdate(status=status).status == status


# ── reads stay open ──────────────────────────────────────────────────────────


def test_reads_stay_open_to_a_viewer():
    import inspect

    from app.routers import test_management_plans as plans
    from app.routers import test_management_strategies as strategies
    from app.routers import test_management_suite_reviews as suite_reviews

    reads = [
        plans.list_plans, plans.get_plan, plans.list_plan_items,
        strategies.list_strategies, strategies.get_strategy,
        suite_reviews.list_suite_owners, suite_reviews.list_suite_reviews,
        suite_reviews.list_reviews_for_run,
    ]
    for handler in reads:
        assert "require_role" not in inspect.getsource(handler), handler.__name__
