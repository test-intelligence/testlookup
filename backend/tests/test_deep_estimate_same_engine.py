"""D-152: the deep-run estimate is measured only from runs of the SAME engine.

Seven model-free deep runs (about one second each) once estimated a local
model's run at "~0 min · estimate from 7 past runs": the sample was every
completed deep pipeline of the project, whatever engine ran it. The sample is
now restricted to pipelines whose frozen
``execution_metadata.analysis_mode_resolution`` (``resolved`` / ``provider`` /
``model``) equals the engine a run started now would use: the gate's RESOLVED
mode and the EFFECTIVE provider / model. Completed runs of another engine ->
``measured: false`` with "no past run with this model"; no completed run at
all keeps the original reason.

Driven through the real route with the stored AI config (``ai_config_fakes``);
only the estimate's one text statement is answered here, and its bound
parameters are checked.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.sql.elements import TextClause

from app.core.config import settings
from tests.ai_config_fakes import (
    RUN_ID,
    RecordingRedis,
    StoredConfigSession,
    install,
    reset_analysis_router_state,
)


def _row(sample: int, completed: int) -> dict[str, Any]:
    return {
        "sample_pipelines": sample, "completed_pipelines": completed, "pipeline_seconds_p50": None,
        "stage_name": None, "samples": None, "secs_p50": None, "in_p50": None, "out_p50": None,
        "secs_pf_p50": None, "in_pf_p50": None, "out_pf_p50": None,
    }


class _EstimateSession(StoredConfigSession):
    """The stored AI config, a run, and ONE answer row for the estimate statement."""

    def __init__(self, stored: dict, row: dict[str, Any]) -> None:
        super().__init__(stored)
        self.row = row
        self.bound: list[tuple[str, dict]] = []

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(stmt, TextClause):
            self.statements.append(stmt)
            self.bound.append((stmt.text, dict(args[0]) if args else {}))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [self.row]))
        return await super().execute(stmt, *args, **kwargs)

    async def scalar(self, stmt: Any, *args: Any, **kwargs: Any) -> Any:
        self.statements.append(stmt)
        return None


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    reset_analysis_router_state()
    monkeypatch.setattr(settings, "ANALYSIS_MODE", "llm")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "Ollama")
    monkeypatch.setattr(settings, "LLM_MODEL", "qwen2.5:7b")
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
    from app.routers import deep_investigation

    app = FastAPI()
    app.include_router(deep_investigation.router)

    async def _get_db():
        yield db

    admin = User(id=uuid.uuid4(), username="admin", email="a@example.test", role=UserRole.ADMIN, is_active=True)
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_active_user] = lambda: admin
    return app


async def _estimate(monkeypatch, row: dict[str, Any], *, mode: str = "llm") -> tuple[dict, _EstimateSession]:
    install(monkeypatch, redis=RecordingRedis())
    db = _EstimateSession({"analysis_mode": mode, "deep_investigation_enabled": True}, row)
    async with AsyncClient(transport=ASGITransport(app=_app(db)), base_url="http://t") as client:
        response = await client.get(f"/api/v1/deep-investigate/{RUN_ID}/estimate")
    assert response.status_code == 200, response.text
    return response.json(), db


def _estimate_statement(db: _EstimateSession) -> tuple[str, dict]:
    matches = [(sql, params) for sql, params in db.bound if "percentile_cont" in sql]
    assert len(matches) == 1, db.bound
    return matches[0]


@pytest.mark.asyncio
async def test_the_sample_is_filtered_by_the_engine_a_run_would_use(monkeypatch):
    body, db = await _estimate(monkeypatch, _row(sample=3, completed=10))

    sql, params = _estimate_statement(db)
    assert "analysis_mode_resolution" in sql and "resolved_agent_configs" in sql
    # The CONFIGURED mode (no probe: the API and the worker read the same stored value), the
    # effective provider (lower-cased) / model, and the Root Cause tier's endpoint.
    assert (params["requested"], params["uses_model"]) == ("llm", True)
    assert (params["provider"], params["model"]) == ("ollama", "qwen2.5:7b")
    assert params["rca_agent"] == "agent.root_cause_analysis.v1"
    assert params["rca_tier"] in ("slm", "llm") and params["rca_provider"] == "ollama"
    assert (body["measured"], body["reason"], body["sample_pipelines"]) == (True, None, 3)


@pytest.mark.asyncio
async def test_a_rules_install_is_keyed_on_the_mode_alone(monkeypatch):
    """Review finding 7b: a rules run calls no global model, so changing the model keeps its sample."""
    monkeypatch.setattr(settings, "ANALYSIS_MODE", "rules")
    _body, db = await _estimate(monkeypatch, _row(sample=2, completed=2), mode="rules")

    _sql, params = _estimate_statement(db)
    assert (params["requested"], params["uses_model"]) == ("rules", False)


def test_the_root_cause_tier_decides_the_endpoint_key():
    """Review finding 7c: the key is the Root Cause stage's tier endpoint, not the global model."""
    from app.services.agent_capability_registry import DEFAULT_TIERS
    from app.services.deep_investigation_insights_service import _engine_params

    def resolved(tier):
        return SimpleNamespace(
            config=SimpleNamespace(model=SimpleNamespace(tier=tier)),
            endpoints={"slm": SimpleNamespace(provider="Ollama", model="qwen2.5:3b"),
                       "llm": SimpleNamespace(provider="openai", model="big-model")},
        )

    global_cfg = {"provider": "ollama", "model": "qwen2.5:7b"}
    llm = _engine_params("auto", global_cfg, resolved("llm"))
    assert (llm["rca_tier"], llm["rca_provider"], llm["rca_model"]) == ("llm", "openai", "big-model")
    slm = _engine_params("auto", global_cfg, resolved("slm"))
    assert (slm["rca_tier"], slm["rca_provider"], slm["rca_model"]) == ("slm", "ollama", "qwen2.5:3b")
    # A deterministic Root Cause stage calls no model: no endpoint in the key.
    det = _engine_params("llm", global_cfg, resolved("deterministic"))
    assert (det["rca_tier"], det["rca_provider"], det["rca_model"]) == ("deterministic", None, None)
    # auto -> the registry default for the stage, as the worker resolves it.
    ml = _engine_params("ml", global_cfg, resolved("auto"))
    assert ml["rca_tier"] == DEFAULT_TIERS["root_cause_analysis"] and ml["uses_model"] is False


@pytest.mark.asyncio
async def test_completed_runs_of_another_engine_are_not_measured(monkeypatch):
    from app.services.deep_investigation_insights_service import ESTIMATE_NO_MATCHING_RUN

    body, _db = await _estimate(monkeypatch, _row(sample=0, completed=7))

    assert (body["measured"], body["reason"], body["sample_pipelines"]) == (False, ESTIMATE_NO_MATCHING_RUN, 0)
    assert body["minutes_p50"] is None and body["tokens_p50"] is None
    assert ESTIMATE_NO_MATCHING_RUN.startswith("No past run with this model")


@pytest.mark.asyncio
async def test_no_completed_run_at_all_keeps_the_original_reason(monkeypatch):
    from app.core.agent_ops_vocab import ESTIMATE_UNMEASURED

    body, _db = await _estimate(monkeypatch, _row(sample=0, completed=0))

    assert (body["measured"], body["reason"]) == (False, ESTIMATE_UNMEASURED)


def test_the_page_mirrors_the_reason_word_for_word():
    """The page swaps its caption on this exact sentence (``deepRunModel.ts``)."""
    from app.services.deep_investigation_insights_service import ESTIMATE_NO_MATCHING_RUN

    model = Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "deep" / "deepRunModel.ts"
    assert f"'{ESTIMATE_NO_MATCHING_RUN}'" in model.read_text(encoding="utf-8")
