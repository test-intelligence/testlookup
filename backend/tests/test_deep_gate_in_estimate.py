"""W14 AGT-07 (§0.15): the estimate carries the server's OWN deep gate.

The Deep Investigation page read the CONFIGURED mode (``GET /settings/ai/mode``
-> ``analysis_mode``) while the trigger's gate refuses on the RESOLVED one
(``auto`` -> ML -> LLM -> rules through the effective provider): a configured
``auto`` that resolves to ``rules`` still offered **Run** and the click got a
422. The estimate now answers ``gate`` -- the ``DeepGateDecision`` of
``deep_investigation_gate(db)`` as data, read through the request session
only -- so the page offers Run only when ``gate.allowed``.

Pinned: configured ``auto`` with no LLM provider and no ML model ->
``/settings/ai/mode`` says ``auto`` while the estimate's ``gate.allowed`` is
false with W04's rules sentence (and a 200: the gate is data here, never a
422); a resolvable mode -> ``gate.allowed`` true. No second session is opened.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.sql.elements import TextClause

from app.core.config import settings
from app.services.deep_investigation_gate import MESSAGE_RULES_MODE
from tests.ai_config_fakes import (
    RUN_ID,
    RecordingRedis,
    StoredConfigSession,
    install,
    reset_analysis_router_state,
)


class _EstimateSession(StoredConfigSession):
    """The stored AI config, a run, and an EMPTY sample for the estimate's text statement."""

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(stmt, TextClause):
            self.statements.append(stmt)
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))
        return await super().execute(stmt, *args, **kwargs)

    async def scalar(self, stmt: Any, *args: Any, **kwargs: Any) -> Any:
        self.statements.append(stmt)   # the resolver's drift-pin probe: no pin
        return None


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    reset_analysis_router_state()
    monkeypatch.setattr(settings, "ANALYSIS_MODE", "auto")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "none")
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(settings, "DEEP_INVESTIGATION_ENABLED", True)
    from app.services.ml import classifier

    monkeypatch.setattr(classifier.MLClassifier, "is_available", staticmethod(lambda: False))
    yield
    reset_analysis_router_state()


def _app(db) -> FastAPI:
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.models.postgres import User, UserRole
    from app.routers import app_settings, deep_investigation

    app = FastAPI()
    app.include_router(app_settings.router)
    app.include_router(deep_investigation.router)

    async def _get_db():
        yield db

    admin = User(id=uuid.uuid4(), username="admin", email="a@example.test", role=UserRole.ADMIN, is_active=True)
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_active_user] = lambda: admin
    return app


async def _read(db) -> tuple[dict, dict]:
    async with AsyncClient(transport=ASGITransport(app=_app(db)), base_url="http://t") as client:
        mode = await client.get("/api/v1/settings/ai/mode")
        estimate = await client.get(f"/api/v1/deep-investigate/{RUN_ID}/estimate")
    assert mode.status_code == 200, mode.text
    assert estimate.status_code == 200, estimate.text
    return mode.json(), estimate.json()


@pytest.mark.asyncio
async def test_a_configured_auto_that_resolves_to_rules_is_refused_in_the_estimate(monkeypatch):
    opened = install(monkeypatch, redis=RecordingRedis())
    db = _EstimateSession({
        "analysis_mode": "auto", "llm_provider": "none", "deep_investigation_enabled": True,
    })

    mode, estimate = await _read(db)

    assert mode["analysis_mode"] == "auto"            # what the page used to trust
    assert mode["deep_investigation_enabled"] is True
    assert estimate["gate"] == {
        "allowed": False,
        "reason_code": "rules_mode",
        "message": MESSAGE_RULES_MODE,
        "analysis_mode": "rules",                     # the RESOLVED mode
        "deep_investigation_enabled": True,
    }
    assert estimate["measured"] is False and estimate["minutes_p50"] is None
    assert opened == []                               # the request session only


@pytest.mark.asyncio
async def test_a_resolvable_mode_is_allowed(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "LLM_MODEL", "qwen2.5:7b")
    opened = install(monkeypatch, redis=RecordingRedis())
    db = _EstimateSession({"analysis_mode": "llm", "deep_investigation_enabled": True})

    _mode, estimate = await _read(db)

    assert estimate["gate"]["allowed"] is True
    assert estimate["gate"]["reason_code"] is None and estimate["gate"]["message"] is None
    assert opened == []
