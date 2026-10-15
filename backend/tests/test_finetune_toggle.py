"""AIC-01: the "Fine-Tuning Pipeline" toggle is obeyed, and the settings API
reports where every obeyed value comes from.

``run_finetune_pipeline``, ``_maybe_trigger_finetune`` and
``_check_incremental_trigger`` read ``settings.FINETUNE_ENABLED`` (env only),
and ``GET /training/status`` reported the env flag: ticking the box changed
nothing. They now read the EFFECTIVE value (Settings > AI over env, both
directions) through the real resolver.

Pinned here (contract W04 §2.2, assertions 5, 6 and 8):

5. env True + stored False -> skipped / nothing queued; env False + stored True
   -> runs / queued, for each of the three task paths.
6. ``GET /training/status`` after ``PUT {"finetune_enabled": true}`` reports
   ``finetune_enabled: true`` from ``ai_config``; ``GET /settings/ai`` carries
   the four ``*_source`` fields; ``GET /settings/ai/mode`` carries
   ``deep_investigation_enabled`` and answers a VIEWER.
8. invalid stored values are ignored by the API exactly as by the runtime.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.worker import training_tasks
from tests.ai_config_fakes import RecordingRedis, StoredConfigSession, install


class _TrainingSession(StoredConfigSession):
    """Also answers the AIFeedback counts the training paths read."""

    def __init__(self, stored, *, unexported: int = 999):
        super().__init__(stored)
        self.unexported = unexported

    async def execute(self, stmt, *args, **kwargs):
        try:
            names = [c.name for c in stmt.selected_columns]
        except Exception:
            names = []
        if names and set(names) <= {"total", "unexported", "labelled", "count_1", "count"}:
            self.statements.append(stmt)
            if names == ["total", "unexported", "labelled"]:
                row = SimpleNamespace(total=10, unexported=self.unexported, labelled=0)
                return SimpleNamespace(one=lambda: row)
            return SimpleNamespace(scalar_one=lambda: self.unexported)
        return await super().execute(stmt, *args, **kwargs)


def _env_finetune(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(settings, "FINETUNE_ENABLED", value)


# ── 5. the three task paths ──────────────────────────────────────────────────


def test_run_finetune_pipeline_skips_on_a_stored_false_although_env_is_true(monkeypatch):
    _env_finetune(monkeypatch, True)
    install(monkeypatch, redis=RecordingRedis(), fallback_session=_TrainingSession({"finetune_enabled": False}))
    pipeline = AsyncMock(return_value={"promoted": True})
    monkeypatch.setattr(training_tasks, "_run_pipeline", pipeline)

    out = training_tasks.run_finetune_pipeline.run("classifier")

    assert out == {"skipped": True, "reason": "finetune_disabled"}
    pipeline.assert_not_called()


def test_run_finetune_pipeline_runs_on_a_stored_true_although_env_is_false(monkeypatch):
    _env_finetune(monkeypatch, False)
    install(monkeypatch, redis=RecordingRedis(), fallback_session=_TrainingSession({"finetune_enabled": True}))
    pipeline = AsyncMock(return_value={"promoted": True})
    monkeypatch.setattr(training_tasks, "_run_pipeline", pipeline)

    out = training_tasks.run_finetune_pipeline.run("classifier")

    assert out == {"promoted": True}
    pipeline.assert_awaited_once_with("classifier")


@pytest.mark.asyncio
@pytest.mark.parametrize(("env", "stored", "queued"), [(True, False, False), (False, True, True)])
async def test_maybe_trigger_finetune_follows_the_stored_toggle(monkeypatch, env, stored, queued):
    _env_finetune(monkeypatch, env)
    install(monkeypatch, redis=RecordingRedis(), fallback_session=_TrainingSession({"finetune_enabled": stored}))
    apply_async = MagicMock()
    monkeypatch.setattr(training_tasks.run_finetune_pipeline, "apply_async", apply_async)

    await training_tasks._maybe_trigger_finetune({"classifier": 10_000_000})

    assert apply_async.called is queued


@pytest.mark.asyncio
@pytest.mark.parametrize(("env", "stored", "queued"), [(True, False, False), (False, True, True)])
async def test_check_incremental_trigger_follows_the_stored_toggle(monkeypatch, env, stored, queued):
    _env_finetune(monkeypatch, env)
    session = _TrainingSession({"finetune_enabled": stored}, unexported=10_000_000)
    install(monkeypatch, redis=RecordingRedis(), fallback_session=session)
    apply_async = MagicMock()
    monkeypatch.setattr(training_tasks.export_training_data, "apply_async", apply_async)

    await training_tasks._check_incremental_trigger()

    assert apply_async.called is queued
    # Off means the feedback count is never even read.
    assert any("count" in str(s).lower() and "ai_feedback" in str(s).lower() for s in session.statements) is queued


@pytest.mark.asyncio
async def test_the_task_reads_the_toggle_fresh_not_from_the_60s_cache(monkeypatch):
    """A cached "on" must not outlive a stored "off" in the training tasks."""
    import json

    _env_finetune(monkeypatch, False)
    redis = RecordingRedis({"config:ai_effective:v2": json.dumps({"finetune_enabled": True})})
    install(monkeypatch, redis=redis, fallback_session=_TrainingSession({"finetune_enabled": False}))

    assert await training_tasks._finetune_enabled() is False


# ── 6. the API reports the effective values and their sources ────────────────


def _user(role):
    from app.models.postgres import User

    return User(id=uuid.uuid4(), username=f"u-{role.value}", email="u@example.test", role=role, is_active=True)


def _app(db, user, *routers) -> FastAPI:
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db

    app = FastAPI()
    for router in routers:
        app.include_router(router)

    async def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_active_user] = lambda: user
    return app


def _stub_settings_collaborators(monkeypatch):
    from app.routers import app_settings as router_mod
    from app.services import model_registry

    monkeypatch.setattr(router_mod, "log_settings_change", AsyncMock())
    monkeypatch.setattr(
        router_mod,
        "_ml_status",
        AsyncMock(return_value={
            "ml_model_available": False,
            "ml_model_accuracy": None,
            "ml_training_sample_count": 0,
            "ml_human_label_count": 0,
            "ml_human_label_floor": 50,
            "ml_maturity": "not_trained",
        }),
    )
    monkeypatch.setattr("app.services.feature_flags.is_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr("app.services.agent_authority_lock.lock_global_agent_authority", AsyncMock())
    monkeypatch.setattr(model_registry.ModelRegistry, "get_all_status", AsyncMock(return_value={}))
    from app.services.ml import classifier

    monkeypatch.setattr(classifier.MLClassifier, "is_available", staticmethod(lambda: False))
    return router_mod


@pytest.mark.asyncio
async def test_training_status_reports_the_toggle_saved_on_settings_ai(monkeypatch):
    from app.models.postgres import UserRole
    from app.routers import app_settings, feedback

    _env_finetune(monkeypatch, False)
    _stub_settings_collaborators(monkeypatch)
    opened = install(monkeypatch, redis=RecordingRedis())
    db = _TrainingSession(None)

    async with AsyncClient(
        transport=ASGITransport(app=_app(db, _user(UserRole.ADMIN), app_settings.router, feedback.router)),
        base_url="http://t",
    ) as client:
        before = (await client.get("/api/v1/training/status")).json()
        saved = await client.put("/api/v1/settings/ai", json={"finetune_enabled": True})
        after = (await client.get("/api/v1/training/status")).json()

    assert before["finetune_enabled"] is False
    assert before["finetune_enabled_source"] == "env_default"
    assert saved.status_code == 200, saved.text
    assert saved.json()["finetune_enabled_source"] == "ai_config"
    assert after["finetune_enabled"] is True
    assert after["finetune_enabled_source"] == "ai_config"
    assert opened == []  # every read went through the request session


@pytest.mark.asyncio
async def test_get_settings_ai_carries_the_four_sources(monkeypatch):
    from app.models.postgres import UserRole
    from app.routers import app_settings

    _stub_settings_collaborators(monkeypatch)
    install(monkeypatch, redis=RecordingRedis())
    db = _TrainingSession({
        "analysis_mode": "rules",
        "deep_investigation_enabled": False,
        "finetune_enabled": "true",       # invalid: a string, not a bool
        "ai_timeout_seconds": 3,          # invalid: below the 5 s floor
    })

    async with AsyncClient(
        transport=ASGITransport(app=_app(db, _user(UserRole.QA_LEAD), app_settings.router)),
        base_url="http://t",
    ) as client:
        response = await client.get("/api/v1/settings/ai")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["analysis_mode"], body["analysis_mode_source"]) == ("rules", "ai_config")
    assert (body["deep_investigation_enabled"], body["deep_investigation_enabled_source"]) == (False, "ai_config")
    assert (body["finetune_enabled"], body["finetune_enabled_source"]) == (
        settings.FINETUNE_ENABLED, "env_default",
    )
    assert (body["ai_timeout_seconds"], body["ai_timeout_seconds_source"]) == (
        settings.AI_TIMEOUT_SECONDS, "env_default",
    )
    assert body["ml_min_training_samples"] == settings.ML_MIN_TRAINING_SAMPLES


@pytest.mark.asyncio
@pytest.mark.parametrize(("stored", "expected"), [({"deep_investigation_enabled": False}, False), (None, True)])
async def test_get_settings_ai_mode_tells_every_role_whether_deep_is_offered(monkeypatch, stored, expected):
    from app.models.postgres import UserRole
    from app.routers import app_settings

    monkeypatch.setattr(settings, "DEEP_INVESTIGATION_ENABLED", True)
    install(monkeypatch, redis=RecordingRedis())
    db = _TrainingSession(stored)

    async with AsyncClient(
        transport=ASGITransport(app=_app(db, _user(UserRole.VIEWER), app_settings.router)),
        base_url="http://t",
    ) as client:
        response = await client.get("/api/v1/settings/ai/mode")

    assert response.status_code == 200, response.text
    assert response.json()["deep_investigation_enabled"] is expected
