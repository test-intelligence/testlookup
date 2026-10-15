"""The manual pipeline retry runs on the request's own session (W04 AIC-04/01).

``POST /api/v1/agents/pipelines/{id}/retry`` resolved the live analysis-mode
snapshot with ``_resolve_analysis_mode_snapshot()``, which read the AI config,
re-read Redis and synced ML models through sessions of its own -- while the
handler already held the request's ``get_db`` connection. W04 adds two more
reads on that path (the live threshold/timeout for the retry decision, and the
deep-investigation gate on a rerun). A production worker has three pooled
connections (2 + 1); two or three such requests each holding one and waiting
for another stall for the 30 s pool timeout.

Twin of ``test_deep_investigation_uses_one_connection.py``: the real route on a
pool of ONE connection. The resolver swallows database errors into env
defaults, so a stray second session would not fail the request -- it would
silently read env. The test therefore counts checkout ATTEMPTS, not only the
outcome.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.routers import agents

RUN_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()

STORED_AI_CONFIG = {
    "deep_investigation_enabled": True,
    "analysis_mode": "llm",
    "llm_provider": "ollama",
    "llm_model": "qwen2.5:7b",
    "ai_offline_mode": False,
    "ai_confidence_threshold": 80,
    "ai_timeout_seconds": 300,
}


def _pipeline(provider: str):
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
                "requested": "llm", "resolved": "llm", "provider": provider,
                "model": "qwen2.5:7b", "offline": False,
            },
            "ai_config_snapshot": {
                "schema": 1, "confidence_threshold": 80, "confidence_threshold_source": "ai_config",
                "llm_timeout_seconds": 300, "llm_timeout_source": "ai_config",
                "agent_timeout_overrides": {}, "origin": "pipeline_start",
                "frozen_at": "2026-10-14T00:00:00+00:00",
            },
        },
    )


class _OneConnectionPool:
    def __init__(self, timeout: float = 1.0) -> None:
        self._slot = asyncio.Semaphore(1)
        self._timeout = timeout
        self.held = 0
        self.max_held = 0
        self.attempts = 0

    async def checkout(self) -> None:
        self.attempts += 1
        await asyncio.wait_for(self._slot.acquire(), timeout=self._timeout)
        self.held += 1
        self.max_held = max(self.max_held, self.held)

    def checkin(self) -> None:
        self.held -= 1
        self._slot.release()


class _Session:
    def __init__(self, pool: _OneConnectionPool, pipeline) -> None:
        self._pool = pool
        self._pipeline = pipeline
        self._connected = False

    async def _connect(self) -> None:
        if not self._connected:
            await self._pool.checkout()
            self._connected = True

    async def execute(self, stmt, *args, **kwargs):
        await self._connect()
        try:
            entity = str(stmt.column_descriptions[0]["name"])
        except Exception:
            entity = ""
        if entity == "AgentPipelineRun":
            row = None if "ORDER BY" in str(stmt).upper() else self._pipeline
        elif entity == "AppSetting":
            row = SimpleNamespace(key="ai_config", value=dict(STORED_AI_CONFIG))
        else:
            row = None
        return SimpleNamespace(scalar_one_or_none=lambda: row)

    async def get(self, _model, _pk):
        await self._connect()
        return SimpleNamespace(id=RUN_ID, project_id=PROJECT_ID, build_number="b-1")

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        if self._connected:
            self._connected = False
            self._pool.checkin()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()


@pytest.fixture
def harness(monkeypatch, fake_redis):
    from app.services import analysis_router
    from app.worker import tasks

    pool = _OneConnectionPool()
    state = SimpleNamespace(pool=pool, pipeline=None)
    # Any session the code under test opens on its own comes from the SAME pool.
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", lambda *a, **k: _Session(pool, state.pipeline))
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake_redis)  # empty: every cache misses
    monkeypatch.setattr(analysis_router, "_probe_ollama_model_async", AsyncMock(return_value=None))
    monkeypatch.setattr(settings, "ANALYSIS_MODE", "auto")
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)  # the frozen snapshot says online
    # Past the session code: the queues and the activity ledger (its own writer).
    state.rerun = MagicMock()
    state.resume = MagicMock()
    monkeypatch.setattr(tasks.run_agent_pipeline, "delay", state.rerun)
    monkeypatch.setattr(tasks.resume_agent_pipeline, "apply_async", state.resume)
    monkeypatch.setattr(agents, "record_activity", AsyncMock())

    app = FastAPI()
    app.include_router(agents.router)

    async def _get_db():
        session = _Session(pool, state.pipeline)
        try:
            yield session
        finally:
            await session.close()

    admin = User(id=uuid.uuid4(), username="admin", email="a@example.test", role=UserRole.ADMIN, is_active=True)
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_active_user] = lambda: admin
    state.app = app
    yield state
    analysis_router._cached_mode = None
    analysis_router._effective_provider = None
    analysis_router._effective_model = None
    analysis_router._effective_base_url = None


async def _retry(state):
    async with AsyncClient(
        transport=ASGITransport(app=state.app, raise_app_exceptions=False), base_url="http://t"
    ) as client:
        return await asyncio.wait_for(
            client.post(f"/api/v1/agents/pipelines/{state.pipeline.id}/retry"), timeout=10,
        )


@pytest.mark.asyncio
async def test_a_resume_needs_one_connection(harness):
    harness.pipeline = _pipeline(provider="ollama")  # frozen == live: resume
    response = await _retry(harness)
    assert response.status_code == 202, response.text
    assert response.json()["mode"] == "resume"
    assert harness.pool.max_held == 1
    assert harness.pool.attempts == 1, "a second session was opened inside the handler"
    harness.resume.assert_called_once()


@pytest.mark.asyncio
async def test_a_gated_deep_rerun_needs_one_connection(harness):
    harness.pipeline = _pipeline(provider="openai")  # frozen under another provider: rerun + gate
    response = await _retry(harness)
    assert response.status_code == 202, response.text
    assert response.json()["mode"] == "rerun"
    assert harness.pool.max_held == 1
    assert harness.pool.attempts == 1, "a second session was opened inside the handler"
    harness.rerun.assert_called_once()
