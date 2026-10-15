"""AIC-01: the Settings > AI "Deep Investigation" toggle is obeyed on every path.

The single trigger route read ``settings.DEEP_INVESTIGATION_ENABLED`` (env only)
and the in-process mode; the bulk route, ``/agents/pipelines/trigger`` with
``workflow_id: "deep"`` and a manual retry that RE-RUNS a deep pipeline applied
no gate at all. A stored "off" therefore changed nothing anywhere.

Pinned here (contract W04 §2.2, assertions 1-4 and 8):

1. env True + stored False -> 422 "Deep investigation is disabled in Settings ›
   AI", nothing queued; env False + stored True -> accepted; disabled AND rules
   -> the ``disabled`` text.
2. bulk ``workflow_type="deep"`` off -> 422 before the scope query; ``offline`` -> 202.
3. ``/agents/pipelines/trigger {"workflow_id":"deep"}`` off -> 422; ``offline``
   unaffected; an in-progress deep pipeline still answers 200.
4. retry of a deep pipeline: rerun (config changed) off -> 422; resume -> 202.
8. invalid stored values are ignored (source ``env_default``).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.services.deep_investigation_gate import (
    MESSAGE_DISABLED,
    MESSAGE_RULES_MODE,
    deep_investigation_gate,
    require_deep_investigation,
)
from tests.ai_config_fakes import (
    PROJECT_ID,
    RUN_ID,
    RecordingRedis,
    StoredConfigSession,
    install,
    reset_analysis_router_state,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    reset_analysis_router_state()
    monkeypatch.setattr(settings, "ANALYSIS_MODE", "auto")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "LLM_MODEL", "qwen2.5:7b")
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(settings, "DEEP_INVESTIGATION_ENABLED", True)
    from app.services.ml import classifier

    monkeypatch.setattr(classifier.MLClassifier, "is_available", staticmethod(lambda: False))
    yield
    reset_analysis_router_state()


def _admin():
    from app.models.postgres import User, UserRole

    return User(id=uuid.uuid4(), username="admin", email="a@example.test", role=UserRole.ADMIN, is_active=True)


def _app(db, *routers) -> FastAPI:
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db

    app = FastAPI()
    for router in routers:
        app.include_router(router)

    async def _get_db():
        yield db

    user = _admin()
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_active_user] = lambda: user
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


# ── the gate itself ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_stored_false_overrides_env_true(monkeypatch):
    install(monkeypatch, redis=RecordingRedis())
    decision = await deep_investigation_gate(
        StoredConfigSession({"deep_investigation_enabled": False, "analysis_mode": "llm"})
    )
    assert decision.allowed is False
    assert decision.reason_code == "disabled"
    assert decision.message == MESSAGE_DISABLED
    assert "Deep investigation is disabled in Settings › AI" in decision.message
    assert decision.deep_investigation_enabled is False
    assert decision.deep_investigation_enabled_source == "ai_config"


@pytest.mark.asyncio
async def test_a_stored_true_overrides_env_false(monkeypatch):
    monkeypatch.setattr(settings, "DEEP_INVESTIGATION_ENABLED", False)
    install(monkeypatch, redis=RecordingRedis())
    decision = await deep_investigation_gate(
        StoredConfigSession({"deep_investigation_enabled": True, "analysis_mode": "auto"})
    )
    assert decision.allowed is True
    assert decision.reason_code is None and decision.message is None
    assert decision.analysis_mode == "llm"  # auto resolved through the provider
    assert decision.deep_investigation_enabled_source == "ai_config"


@pytest.mark.asyncio
async def test_disabled_wins_over_rules_mode(monkeypatch):
    install(monkeypatch, redis=RecordingRedis())
    decision = await deep_investigation_gate(
        StoredConfigSession({"deep_investigation_enabled": False, "analysis_mode": "rules"})
    )
    assert decision.reason_code == "disabled"
    assert decision.analysis_mode == "rules"


@pytest.mark.asyncio
async def test_rules_mode_refuses_with_the_unchanged_text(monkeypatch):
    install(monkeypatch, redis=RecordingRedis())
    with pytest.raises(HTTPException) as exc:
        await require_deep_investigation(
            StoredConfigSession({"deep_investigation_enabled": True, "analysis_mode": "rules"})
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == MESSAGE_RULES_MODE == (
        "Deep investigation requires LLM or Auto mode. Current mode: rules."
    )


@pytest.mark.asyncio
async def test_auto_resolving_to_rules_is_refused_as_rules(monkeypatch):
    """``auto`` with no LLM provider and no model resolves to rules: the deep
    pipeline would have nothing to investigate with."""
    install(monkeypatch, redis=RecordingRedis())
    decision = await deep_investigation_gate(
        StoredConfigSession({"analysis_mode": "auto", "llm_provider": "none"})
    )
    assert decision.reason_code == "rules_mode"


@pytest.mark.asyncio
@pytest.mark.parametrize("junk", ["false", 0, None, "off"])
async def test_an_invalid_stored_toggle_is_ignored(monkeypatch, junk):
    install(monkeypatch, redis=RecordingRedis())
    decision = await deep_investigation_gate(
        StoredConfigSession({"deep_investigation_enabled": junk, "analysis_mode": "llm"})
    )
    # env default (True) applies; "false" is NOT a boolean false.
    assert decision.deep_investigation_enabled is True
    assert decision.deep_investigation_enabled_source == "env_default"
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_the_gate_reads_only_the_request_session(monkeypatch):
    opened = install(monkeypatch, redis=RecordingRedis())
    db = StoredConfigSession({"deep_investigation_enabled": True, "analysis_mode": "llm"})
    await deep_investigation_gate(db)
    assert "AppSetting" in db.entities()
    assert opened == []


# ── POST /deep-investigate/{run_id} ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_deep_route_refuses_a_stored_off_and_queues_nothing(monkeypatch):
    from app.routers import deep_investigation

    install(monkeypatch, redis=RecordingRedis())
    queue = MagicMock(return_value=SimpleNamespace(id="task-1"))
    monkeypatch.setattr(deep_investigation.run_agent_pipeline, "apply_async", queue)
    db = StoredConfigSession({"deep_investigation_enabled": False, "analysis_mode": "rules"})

    async with _client(_app(db, deep_investigation.router)) as client:
        response = await client.post(f"/api/v1/deep-investigate/{RUN_ID}", json={"mode": "deep"})

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == MESSAGE_DISABLED
    queue.assert_not_called()
    assert "TestRun" not in db.entities()  # refused before the run lookup


@pytest.mark.asyncio
async def test_the_deep_route_accepts_a_stored_on_although_env_is_off(monkeypatch):
    from app.routers import deep_investigation

    monkeypatch.setattr(settings, "DEEP_INVESTIGATION_ENABLED", False)
    install(monkeypatch, redis=RecordingRedis())
    queue = MagicMock(return_value=SimpleNamespace(id="task-1"))
    monkeypatch.setattr(deep_investigation.run_agent_pipeline, "apply_async", queue)
    db = StoredConfigSession({"deep_investigation_enabled": True, "analysis_mode": "llm"})

    async with _client(_app(db, deep_investigation.router)) as client:
        response = await client.post(f"/api/v1/deep-investigate/{RUN_ID}", json={"mode": "deep"})

    assert response.status_code == 200, response.text
    queue.assert_called_once()
    assert queue.call_args.kwargs["kwargs"]["workflow_type"] == "deep"


# ── POST /agents/pipelines/bulk-trigger ──────────────────────────────────────


class _BulkSession(StoredConfigSession):
    """Also answers the bulk route's ``select(TestRun.id, project_id, build_number)``."""

    async def execute(self, stmt, *args, **kwargs):
        try:
            names = [c.name for c in stmt.selected_columns]
        except Exception:
            names = []
        if names == ["id", "project_id", "build_number"]:
            self.statements.append(stmt)
            return SimpleNamespace(all=lambda: [(RUN_ID, PROJECT_ID, "b-1")])
        return await super().execute(stmt, *args, **kwargs)


@pytest.mark.asyncio
async def test_bulk_deep_is_refused_before_the_scope_query(monkeypatch):
    from app.routers import agents
    from app.worker import tasks

    install(monkeypatch, redis=RecordingRedis())
    queue = MagicMock()
    monkeypatch.setattr(tasks.run_agent_pipeline, "apply_async", queue)
    monkeypatch.setattr(agents, "record_activity", AsyncMock())
    db = _BulkSession({"deep_investigation_enabled": False, "analysis_mode": "llm"})

    async with _client(_app(db, agents.router)) as client:
        response = await client.post(
            "/api/v1/agents/pipelines/bulk-trigger",
            json={"run_ids": [str(RUN_ID)], "workflow_type": "deep"},
        )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == MESSAGE_DISABLED
    queue.assert_not_called()
    assert "id" not in db.entities()  # the run/scope query never ran


@pytest.mark.asyncio
async def test_bulk_offline_is_unaffected_by_the_deep_toggle(monkeypatch):
    from app.routers import agents
    from app.worker import tasks

    install(monkeypatch, redis=RecordingRedis())
    queue = MagicMock()
    monkeypatch.setattr(tasks.run_agent_pipeline, "apply_async", queue)
    monkeypatch.setattr(agents, "record_activity", AsyncMock())
    db = _BulkSession({"deep_investigation_enabled": False, "analysis_mode": "rules"})

    async with _client(_app(db, agents.router)) as client:
        response = await client.post(
            "/api/v1/agents/pipelines/bulk-trigger",
            json={"run_ids": [str(RUN_ID)], "workflow_type": "offline"},
        )

    assert response.status_code == 202, response.text
    assert response.json()["queued"] == 1
    queue.assert_called_once()
    assert "AppSetting" not in db.entities()  # the gate is not even consulted


# ── POST /agents/pipelines/trigger ───────────────────────────────────────────


class _TriggerSession(StoredConfigSession):
    def __init__(self, stored, *, in_progress=None):
        super().__init__(stored)
        self.in_progress = in_progress

    async def execute(self, stmt, *args, **kwargs):
        entity = ""
        try:
            entity = str(stmt.column_descriptions[0]["name"])
        except Exception:
            pass
        if entity == "AgentPipelineRun":
            self.statements.append(stmt)
            return SimpleNamespace(scalar_one_or_none=lambda: self.in_progress)
        return await super().execute(stmt, *args, **kwargs)


def _trigger_harness(monkeypatch, base: str):
    from app.routers import agents
    from app.services import workflow_definition_service as workflow_svc
    from app.worker import tasks

    install(monkeypatch, redis=RecordingRedis())
    monkeypatch.setattr(
        workflow_svc,
        "get_published_definition",
        AsyncMock(return_value={"base": base, "workflow_id": base, "version": 1}),
    )
    delay = MagicMock(return_value=SimpleNamespace(id="task-1"))
    monkeypatch.setattr(tasks.run_agent_pipeline, "delay", delay)
    monkeypatch.setattr(agents, "record_activity", AsyncMock())
    return agents, delay


@pytest.mark.asyncio
async def test_trigger_with_workflow_deep_obeys_the_toggle(monkeypatch):
    agents, delay = _trigger_harness(monkeypatch, "deep")
    db = _TriggerSession({"deep_investigation_enabled": False, "analysis_mode": "llm"})

    async with _client(_app(db, agents.router)) as client:
        response = await client.post(
            "/api/v1/agents/pipelines/trigger",
            json={"test_run_id": str(RUN_ID), "workflow_id": "deep"},
        )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == MESSAGE_DISABLED
    delay.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_with_workflow_offline_is_unaffected(monkeypatch):
    agents, delay = _trigger_harness(monkeypatch, "offline")
    db = _TriggerSession({"deep_investigation_enabled": False, "analysis_mode": "rules"})

    async with _client(_app(db, agents.router)) as client:
        response = await client.post(
            "/api/v1/agents/pipelines/trigger",
            json={"test_run_id": str(RUN_ID), "workflow_id": "offline"},
        )

    assert response.status_code == 202, response.text
    delay.assert_called_once()
    assert delay.call_args.kwargs["workflow_type"] == "offline"


@pytest.mark.asyncio
async def test_an_in_progress_deep_pipeline_still_answers_already_in_progress(monkeypatch):
    agents, delay = _trigger_harness(monkeypatch, "deep")
    in_progress = SimpleNamespace(
        id=uuid.uuid4(), status="running", attempt=1, next_retry_at=None,
    )
    db = _TriggerSession(
        {"deep_investigation_enabled": False, "analysis_mode": "llm"}, in_progress=in_progress
    )

    async with _client(_app(db, agents.router)) as client:
        response = await client.post(
            "/api/v1/agents/pipelines/trigger",
            json={"test_run_id": str(RUN_ID), "workflow_id": "deep"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["message"] == "Pipeline already in progress"
    delay.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_with_workflow_deep_is_accepted_when_on(monkeypatch):
    agents, delay = _trigger_harness(monkeypatch, "deep")
    db = _TriggerSession({"deep_investigation_enabled": True, "analysis_mode": "llm"})

    async with _client(_app(db, agents.router)) as client:
        response = await client.post(
            "/api/v1/agents/pipelines/trigger",
            json={"test_run_id": str(RUN_ID), "workflow_id": "deep"},
        )

    assert response.status_code == 202, response.text
    delay.assert_called_once()


# ── POST /agents/pipelines/{id}/retry ────────────────────────────────────────


LIVE = {
    "deep_investigation_enabled": False,
    "analysis_mode": "llm",
    "llm_provider": "ollama",
    "llm_model": "qwen2.5:7b",
    "ai_offline_mode": False,
    "ai_confidence_threshold": 80,
    "ai_timeout_seconds": 300,
}


def _deep_pipeline(*, provider: str = "ollama"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        test_run_id=RUN_ID,
        workflow_type="deep",
        status="failed",
        attempt=1,
        max_attempts=5,
        next_retry_at=None,
        error="boom",
        cancel_requested=False,
        execution_metadata={
            "initial_workflow_plan": {"stages": ["ingestion", "summary"]},
            "analysis_mode_resolution": {
                "requested": "llm",
                "resolved": "llm",
                "provider": provider,
                "model": "qwen2.5:7b",
                "offline": False,
            },
            "ai_config_snapshot": {
                "schema": 1,
                "confidence_threshold": 80,
                "confidence_threshold_source": "ai_config",
                "llm_timeout_seconds": 300,
                "llm_timeout_source": "ai_config",
                "agent_timeout_overrides": {},
                "origin": "pipeline_start",
                "frozen_at": "2026-10-14T00:00:00+00:00",
            },
        },
    )


class _RetrySession(StoredConfigSession):
    def __init__(self, stored, pipeline):
        super().__init__(stored)
        self.pipeline = pipeline

    async def execute(self, stmt, *args, **kwargs):
        entity = ""
        try:
            entity = str(stmt.column_descriptions[0]["name"])
        except Exception:
            pass
        if entity == "AgentPipelineRun":
            self.statements.append(stmt)
            # The in-flight probe orders + limits; the row load does not.
            row = None if "ORDER BY" in str(stmt).upper() else self.pipeline
            return SimpleNamespace(scalar_one_or_none=lambda: row)
        return await super().execute(stmt, *args, **kwargs)


def _retry_harness(monkeypatch):
    from app.routers import agents
    from app.services import analysis_router
    from app.worker import tasks

    opened = install(monkeypatch, redis=RecordingRedis())
    monkeypatch.setattr(analysis_router, "_probe_ollama_model_async", AsyncMock(return_value=None))
    rerun = MagicMock()
    resume = MagicMock()
    monkeypatch.setattr(tasks.run_agent_pipeline, "delay", rerun)
    monkeypatch.setattr(tasks.resume_agent_pipeline, "apply_async", resume)
    monkeypatch.setattr(agents, "record_activity", AsyncMock())
    return agents, rerun, resume, opened


@pytest.mark.asyncio
async def test_a_deep_rerun_is_refused_when_deep_investigation_is_off(monkeypatch):
    agents, rerun, resume, opened = _retry_harness(monkeypatch)
    pipeline = _deep_pipeline(provider="openai")  # frozen under another provider -> rerun
    db = _RetrySession(dict(LIVE), pipeline)

    async with _client(_app(db, agents.router)) as client:
        response = await client.post(f"/api/v1/agents/pipelines/{pipeline.id}/retry")

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == MESSAGE_DISABLED
    rerun.assert_not_called()
    resume.assert_not_called()
    assert opened == []


@pytest.mark.asyncio
async def test_a_deep_resume_is_never_gated(monkeypatch):
    agents, rerun, resume, opened = _retry_harness(monkeypatch)
    pipeline = _deep_pipeline()  # frozen == live -> resume
    db = _RetrySession(dict(LIVE), pipeline)

    async with _client(_app(db, agents.router)) as client:
        response = await client.post(f"/api/v1/agents/pipelines/{pipeline.id}/retry")

    assert response.status_code == 202, response.text
    assert response.json()["mode"] == "resume"
    resume.assert_called_once()
    rerun.assert_not_called()
    assert opened == []


@pytest.mark.asyncio
async def test_a_changed_threshold_makes_the_retry_a_rerun(monkeypatch):
    """W04 §1.3: the live threshold/timeout are compared with the frozen ones."""
    agents, rerun, resume, _ = _retry_harness(monkeypatch)
    pipeline = _deep_pipeline()
    live = dict(LIVE, ai_confidence_threshold=95, deep_investigation_enabled=True)
    db = _RetrySession(live, pipeline)

    async with _client(_app(db, agents.router)) as client:
        response = await client.post(f"/api/v1/agents/pipelines/{pipeline.id}/retry")

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["mode"] == "rerun"
    assert body["reason"] == "config_changed"
    rerun.assert_called_once()
    resume.assert_not_called()
