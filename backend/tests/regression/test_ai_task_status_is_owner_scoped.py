"""The AI task status route answers only for tasks it issued, to people who may see them.

``GET /api/v1/test-management/cases/ai-task/{task_id}`` used to look up ANY
Celery task id for ANY signed-in user and hand back the stored result or the
failure text. The rule now:

* every async AI enqueue helper (cases, plans, strategies) records an owner,
  ``ai-task-owner:<task_id>`` -> user id + project id, in Redis for one hour
  (the lifetime of a stored result);
* the status route answers 404 unless that record exists AND the caller either
  queued the task or has access to the recorded project;
* an id with no owner record (every task that is not one of these) is a 404,
  and the result backend is not read at all before that decision;
* if Redis cannot be read, the answer is 404 (fail closed).

The result backend, Redis and the project-access check are stubbed.
"""
from __future__ import annotations

import ast
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.deps import get_current_active_user  # noqa: E402
from app.db.postgres import get_db  # noqa: E402
from app.routers import test_management_ai as router_module  # noqa: E402
from app.services import test_management_ai_service as svc  # noqa: E402
from tests.conftest import FakeRedis  # noqa: E402

pytestmark = pytest.mark.regression

APP = Path(__file__).resolve().parents[2] / "app"
_URL = "/api/v1/test-management/cases/ai-task/{}"
_PROJECT_ID = uuid.uuid4()
_RESULT = {"project_id": str(_PROJECT_ID), "test_cases": [{"title": "stored-result-marker"}]}
_FAILURE_TEXT = "stored-failure-marker"


class _StrictLogger:
    """Accepts what the configured structlog logger accepts: one event name
    and keyword fields. A %-style positional argument is a TypeError, as it is
    in a running app (where it turned these failure paths into a 500)."""

    def warning(self, event: str, **fields) -> None:
        return None

    error = info = warning


def _user(role: str = "TESTER") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True)


class _Harness:
    """The status route with Redis, the result backend and the project check stubbed."""

    def __init__(self, caller, *, state="SUCCESS", has_project_access=False, redis=None):
        self.redis = redis if redis is not None else FakeRedis()
        self.async_result = Mock(
            return_value=SimpleNamespace(
                state=state,
                result=_RESULT if state == "SUCCESS" else None,
                info=_FAILURE_TEXT if state == "FAILURE" else None,
            )
        )
        self.scope = AsyncMock(return_value=_PROJECT_ID)
        if not has_project_access:
            self.scope.side_effect = HTTPException(status_code=403, detail="Access denied to this project")
        app = FastAPI()
        app.include_router(router_module.router, prefix="/api/v1/test-management")
        app.dependency_overrides[get_current_active_user] = lambda: caller
        app.dependency_overrides[get_db] = lambda: AsyncMock()
        self.http = TestClient(app)
        self._patches = [
            patch.object(svc, "logger", _StrictLogger()),
            patch.object(svc, "get_redis", lambda: self.redis),
            patch("app.core.deps.resolve_project_scope", self.scope),
            patch.dict(sys.modules, {
                "celery.result": SimpleNamespace(AsyncResult=self.async_result),
                "app.worker.celery_app": SimpleNamespace(celery_app=object()),
            }),
        ]

    def __enter__(self) -> "_Harness":
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc) -> None:
        for p in reversed(self._patches):
            p.stop()

    async def queue(self, task_id: str, owner) -> None:
        await svc.record_ai_task_owner(task_id, user_id=owner.id, project_id=_PROJECT_ID)

    def get(self, task_id: str):
        return self.http.get(_URL.format(task_id))


def _assert_refused(harness: _Harness, response) -> None:
    assert response.status_code == 404
    assert "stored-result-marker" not in response.text
    assert _FAILURE_TEXT not in response.text
    # Refused before the result backend is read at all.
    harness.async_result.assert_not_called()


# ── refusals ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["SUCCESS", "FAILURE", "PENDING"])
async def test_a_user_outside_the_project_cannot_read_another_users_task(state):
    owner, outsider = _user(), _user("VIEWER")
    with _Harness(outsider, state=state, has_project_access=False) as h:
        await h.queue("task-of-a", owner)
        response = h.get("task-of-a")
        _assert_refused(h, response)
        # The project check was asked about the RECORDED project.
        assert h.scope.await_args.args[2] == str(_PROJECT_ID)


@pytest.mark.parametrize("state", ["SUCCESS", "FAILURE"])
def test_a_task_with_no_owner_record_is_not_found(state):
    # Any Celery task that was not queued by an AI enqueue helper: a reindex,
    # a webhook delivery. An ADMIN is refused too; the route never issued it.
    with _Harness(_user("ADMIN"), state=state, has_project_access=True) as h:
        _assert_refused(h, h.get(str(uuid.uuid4())))


@pytest.mark.asyncio
async def test_an_unreadable_owner_store_is_not_found():
    owner = _user()
    broken = FakeRedis()
    with _Harness(owner, redis=broken) as h:
        await h.queue("task-1", owner)
        broken.get = AsyncMock(side_effect=ConnectionError("redis is down"))
        _assert_refused(h, h.get("task-1"))


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", ["not json", "[]", "{}", '{"user_id": "x"}', '{"project_id": 5, "user_id": 7}'])
async def test_a_malformed_owner_record_is_not_found(stored):
    caller = _user("ADMIN")
    with _Harness(caller, has_project_access=True) as h:
        await h.redis.set(svc.ai_task_owner_key("task-1"), stored)
        _assert_refused(h, h.get("task-1"))


# ── the legitimate paths still work ────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("state, expected", [
    ("PENDING", {"task_id": "task-1", "status": "pending", "result": None, "error": None}),
    ("SUCCESS", {"task_id": "task-1", "status": "success", "result": _RESULT, "error": None}),
    # D-58 review: a failure's raw exception text is never passed on (it can carry a host or a
    # URL); only an ``AIPlanNotBuilt`` sentence is (test_ai_plan_task_fails_without_a_plan.py).
    ("FAILURE", {"task_id": "task-1", "status": "failure", "result": None, "error": None}),
])
async def test_the_user_who_queued_a_task_reads_it(state, expected):
    owner = _user()
    # Even with no access to the project any more: it is their own task.
    with _Harness(owner, state=state, has_project_access=False) as h:
        await h.queue("task-1", owner)
        response = h.get("task-1")
    assert response.status_code == 200
    assert response.json() == expected


@pytest.mark.asyncio
async def test_another_member_of_the_project_reads_it():
    owner, teammate = _user(), _user("VIEWER")
    with _Harness(teammate, has_project_access=True) as h:
        await h.queue("task-1", owner)
        response = h.get("task-1")
    assert response.status_code == 200
    assert response.json()["result"] == _RESULT


# ── every enqueue helper records the owner ─────────────────────────────────


@pytest.mark.asyncio
async def test_owner_record_holds_user_and_project_for_one_hour():
    redis = FakeRedis()
    redis.set = AsyncMock(wraps=redis.set)
    owner = _user()
    with patch.object(svc, "get_redis", lambda: redis):
        await svc.record_ai_task_owner("task-1", user_id=owner.id, project_id=_PROJECT_ID)
    (key, value), kwargs = redis.set.await_args
    assert key == "ai-task-owner:task-1"
    assert json.loads(value) == {"user_id": str(owner.id), "project_id": str(_PROJECT_ID)}
    assert kwargs == {"ex": 3600}


def test_owner_record_lives_as_long_as_a_stored_result():
    from app.worker.celery_app import celery_app

    assert svc.AI_TASK_OWNER_TTL_SECONDS == celery_app.conf.result_expires == 3600


@pytest.mark.asyncio
@pytest.mark.parametrize("helper, task_name, payload", [
    ("enqueue_ai_case_generation", "generate_ai_test_cases_task",
     SimpleNamespace(requirements="r", project_id=_PROJECT_ID)),
    ("enqueue_ai_strategy_generation", "generate_ai_strategy_task",
     SimpleNamespace(project_id=_PROJECT_ID, project_context="c", strategy_name="s")),
])
async def test_service_enqueue_helpers_record_the_owner(helper, task_name, payload):
    redis = FakeRedis()
    owner = _user()
    task = SimpleNamespace(delay=Mock(return_value=SimpleNamespace(id="queued-id")))
    with patch.object(svc, "get_redis", lambda: redis), \
         patch.dict(sys.modules, {"app.worker.tasks": SimpleNamespace(**{task_name: task})}):
        answer = await getattr(svc, helper)(payload, owner)
        stored = await redis.get("ai-task-owner:queued-id")
    assert answer == {"task_id": "queued-id", "status": "queued"}
    assert json.loads(stored) == {"user_id": str(owner.id), "project_id": str(_PROJECT_ID)}


@pytest.mark.asyncio
async def test_enqueue_is_refused_when_the_owner_cannot_be_recorded():
    # A task id handed out with no owner record could never be read back, so
    # the caller is told the queue is unavailable instead.
    redis = FakeRedis()
    redis.set = AsyncMock(side_effect=ConnectionError("redis is down"))
    task = SimpleNamespace(delay=Mock(return_value=SimpleNamespace(id="queued-id")))
    with patch.object(svc, "get_redis", lambda: redis), \
         patch.object(svc, "logger", _StrictLogger()), \
         patch.dict(sys.modules, {"app.worker.tasks": SimpleNamespace(generate_ai_test_cases_task=task)}):
        with pytest.raises(HTTPException) as refused:
            await svc.enqueue_ai_case_generation(
                SimpleNamespace(requirements="r", project_id=_PROJECT_ID), _user(),
            )
    assert refused.value.status_code == 503


def _functions_that_queue_a_task() -> dict[str, ast.AST]:
    """Every function in the test-management routers and services that calls
    ``<task>.delay(...)`` or ``<task>.apply_async(...)``."""
    found: dict[str, ast.AST] = {}
    files = sorted((APP / "routers").glob("test_management*.py")) + sorted(
        (APP / "services").glob("test_management*.py")
    )
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr in {"delay", "apply_async"}
                ):
                    found[f"{path.parent.name}/{path.name}::{node.name}"] = node
    return found


def _calls(node: ast.AST, name: str) -> bool:
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if (isinstance(func, ast.Name) and func.id == name) or (
            isinstance(func, ast.Attribute) and func.attr == name
        ):
            return True
    return False


def test_every_test_management_enqueue_records_the_owner():
    """The status route serves every task id these functions hand out. One
    that queues a task without ``record_ai_task_owner`` hands out an id the
    route will answer 404 for; add the call next to the ``.delay(...)``."""
    queuing = _functions_that_queue_a_task()
    # Cases and strategies (service helpers) and plans (in its router).
    assert len(queuing) >= 3, sorted(queuing)
    missing = sorted(name for name, node in queuing.items() if not _calls(node, "record_ai_task_owner"))
    assert not missing, (
        "these functions queue an AI task without recording its owner "
        f"(await record_ai_task_owner(task.id, user_id=..., project_id=...)): {missing}"
    )
