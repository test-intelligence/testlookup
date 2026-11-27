"""D-58: "AI Create Plan" (``POST /plans/ai-create/async``) refuses up front.

It checked the role and the project, then queued the task and answered
"queued" on an install where no model could answer and in a project with no
approved test case; the page then claimed the plan was being built. Now:

* no approved case -> 400 with the synchronous route's sentence, nothing queued;
* no model can answer -> 409 with a plain sentence (the Settings > AI
  model-status reason appended), nothing queued -- decided by the real
  ``model_status_service.build_model_status`` over the effective config, with
  only the outbound Ollama probe faked;
* a model that answers and approved cases -> queued, owner recorded.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

P = uuid.UUID("11111111-1111-4111-8111-111111111111")
URL = "/api/v1/test-management/plans/ai-create/async"


@pytest.fixture
def world(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.core.deps as deps
    import app.services.test_management_ai_service as ai_service
    import app.worker.tasks as worker_tasks
    from app.db.postgres import get_db
    from app.routers import test_management
    from app.services import ai_config_resolver, model_status_service

    state = SimpleNamespace(
        cases=[SimpleNamespace(id=uuid.uuid4(), title="login")],
        ai={"provider": "ollama", "model": "qwen2.5:7b", "base_url": "http://127.0.0.1:11434",
            "offline_mode": False, "analysis_mode": "auto"},
        probe={"reachable": True, "models": ["qwen2.5:7b"], "error": None},
    )
    task = MagicMock()
    task.delay = MagicMock(return_value=SimpleNamespace(id="t-1"))
    record = AsyncMock(return_value=None)

    async def _config(**_kwargs):
        return dict(state.ai)

    async def _probe(*_args, **_kwargs):
        return dict(state.probe)

    monkeypatch.setattr(ai_config_resolver, "get_effective_ai_config", _config)
    monkeypatch.setattr(model_status_service, "probe_ollama", _probe)
    monkeypatch.setattr(deps, "resolve_project_scope", AsyncMock(return_value=(P, None)))
    monkeypatch.setattr(worker_tasks, "create_ai_test_plan_task", task)
    monkeypatch.setattr(ai_service, "record_ai_task_owner", record)

    async def _execute(*_args, **_kwargs):
        cases = state.cases
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: cases))

    db = SimpleNamespace(execute=AsyncMock(side_effect=_execute), commit=AsyncMock())

    async def _db():
        yield db

    user = SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER", is_active=True, api_key_project_id=None,
                           full_name="qa", username="qa")
    app = FastAPI()
    app.include_router(test_management.router)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[deps.get_current_active_user] = lambda: user
    state.http = TestClient(app, raise_server_exceptions=False)
    state.task, state.record = task, record
    return state


def _post(world):
    return world.http.post(URL, json={"project_id": str(P), "plan_name": "AI plan"})


def test_a_model_that_answers_and_approved_cases_queue_the_task(world):
    resp = _post(world)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"task_id": "t-1", "status": "queued"}
    world.task.delay.assert_called_once()
    world.record.assert_awaited_once()
    # The payload the route queues binds to the REAL task's signature (a mocked delay hides a mismatch).
    import inspect

    from app.worker.celery_app import celery_app

    # From Celery's registry: the module attribute is the fixture's mock here.
    real_task = celery_app.tasks["app.worker.tasks.create_ai_test_plan_task"]
    args, kwargs = world.task.delay.call_args
    bound = inspect.signature(real_task.run).bind(*args, **kwargs)
    assert bound.arguments["project_id"] == str(P) and bound.arguments["plan_name"] == "AI plan"


def test_no_approved_case_is_refused_before_anything_is_queued(world):
    world.cases = []

    resp = _post(world)

    assert resp.status_code == 400
    assert resp.json()["detail"] == "No approved test cases found for this project"
    world.task.delay.assert_not_called()


@pytest.mark.parametrize("probe, ai, why", [
    ({"reachable": False, "models": [], "error": "refused"}, {}, "Ollama is unreachable"),
    ({"reachable": True, "models": ["llama3:latest"], "error": None}, {}, "qwen2.5:7b"),
    ({"reachable": False, "models": [], "error": None}, {"provider": "openai", "model": "gpt-4o-mini",
                                                         "offline_mode": True}, "offline mode is on"),
], ids=["ollama-unreachable", "model-missing", "cloud-under-offline"])
def test_no_model_that_can_answer_is_refused_with_a_sentence(world, probe, ai, why):
    from app.routers.test_management_plans import AI_PLAN_NO_MODEL

    world.probe = probe
    world.ai.update(ai)

    resp = _post(world)

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail.startswith(AI_PLAN_NO_MODEL) and why in detail
    assert "_" not in detail.split(".", 1)[0]                 # the first sentence carries no code
    world.task.delay.assert_not_called()
    world.record.assert_not_awaited()
