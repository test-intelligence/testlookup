"""AIC-04: the analysis mode chosen on Settings > AI persists.

Before W04 the stored mode reached the runtime only through a Redis key written
by ``PUT /settings/ai`` with ``ex=86400``; nothing re-seeded it after expiry or
a flush, and the synchronous ``get_analysis_mode()`` fell back to the ENV value
whenever its 30 s in-process cache expired. So the API's deep-investigation
guard followed env, a pipeline longer than 30 s reverted mid-run, and ``auto``
resolution checked the env provider rather than the one saved on the same page.

What is pinned here (contract W04 §2.1, assertions 1-7):

1. Redis miss -> the STORED mode is returned with source ``ai_config`` and Redis
   is re-seeded WITHOUT a TTL; an env default is never seeded.
2. A refreshed mode survives an hour of process time (no 30 s expiry).
3. ``POST /deep-investigate/{run}`` follows the stored mode (rules -> 422; auto -> accepted).
4. ``auto`` uses the EFFECTIVE provider (stored over env, both directions).
5. ``PUT /settings/ai`` seeds Redis without ``ex``; invalidation deletes three keys.
6. The pipeline's mode snapshot records the effective provider/model/offline.
7. A pipeline longer than 30 s (virtual clock) keeps resolving the stored mode.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.services import analysis_router
from app.services.ai_config_resolver import (
    ANALYSIS_MODE_CACHE_KEY,
    invalidate_ai_config_cache,
)
from app.services.analysis_router import (
    ConfiguredMode,
    get_analysis_mode,
    refresh_analysis_mode_from_cache,
    resolve_analysis_mode_with_reason,
    resolve_configured_analysis_mode,
    resolve_effective_analysis_mode,
)
from tests.ai_config_fakes import (
    RUN_ID,
    RecordingRedis,
    StoredConfigSession,
    install,
    reset_analysis_router_state,
)
from tests.virtual_clock import VirtualClockLoop


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    reset_analysis_router_state()
    monkeypatch.setattr(settings, "ANALYSIS_MODE", "auto")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "LLM_MODEL", "qwen2.5:7b")
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(settings, "DEEP_INVESTIGATION_ENABLED", True)
    # No trained ML model: auto resolves through the LLM provider.
    from app.services.ml import classifier

    monkeypatch.setattr(classifier.MLClassifier, "is_available", staticmethod(lambda: False))
    yield
    reset_analysis_router_state()


def _seeds(redis: RecordingRedis) -> list[tuple]:
    return redis.sets_of(ANALYSIS_MODE_CACHE_KEY)


# ── 1. Redis -> stored row -> env, re-seeding without a TTL ──────────────────


@pytest.mark.asyncio
async def test_a_redis_miss_returns_the_stored_mode_and_reseeds_it_without_ttl(monkeypatch):
    redis = RecordingRedis()
    opened = install(monkeypatch, redis=redis)
    db = StoredConfigSession({"analysis_mode": "rules"})

    configured = await resolve_configured_analysis_mode(db)

    assert configured == ConfiguredMode("rules", "ai_config")
    [seed] = _seeds(redis)
    assert seed[0] == "set"  # not setex
    assert seed[2] == "rules"
    assert seed[3] == () and seed[4] == {}, f"seeded with a TTL: {seed}"
    assert "AppSetting" in db.entities()  # read through the request session
    assert opened == []  # and never through a second one


@pytest.mark.asyncio
async def test_an_env_default_is_returned_but_never_seeded(monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_MODE", "llm")
    redis = RecordingRedis()
    install(monkeypatch, redis=redis)

    configured = await resolve_configured_analysis_mode(StoredConfigSession(None))

    assert configured == ConfiguredMode("llm", "env_default")
    assert _seeds(redis) == []


@pytest.mark.asyncio
async def test_a_redis_hit_is_the_stored_value_and_reads_no_database(monkeypatch):
    redis = RecordingRedis({ANALYSIS_MODE_CACHE_KEY: "ml"})
    install(monkeypatch, redis=redis)
    db = StoredConfigSession({"analysis_mode": "rules"})

    assert await resolve_configured_analysis_mode(db) == ConfiguredMode("ml", "ai_config")
    assert db.statements == []


@pytest.mark.asyncio
async def test_a_bytes_or_invalid_redis_value_is_handled(monkeypatch):
    redis = RecordingRedis({ANALYSIS_MODE_CACHE_KEY: b"rules"})
    install(monkeypatch, redis=redis)
    assert await resolve_configured_analysis_mode(StoredConfigSession(None)) == ConfiguredMode(
        "rules", "ai_config"
    )

    # A junk accelerator value is ignored: the stored row decides and re-seeds.
    redis = RecordingRedis({ANALYSIS_MODE_CACHE_KEY: "turbo"})
    install(monkeypatch, redis=redis)
    db = StoredConfigSession({"analysis_mode": "llm"})
    assert await resolve_configured_analysis_mode(db) == ConfiguredMode("llm", "ai_config")
    assert redis.data[ANALYSIS_MODE_CACHE_KEY] == "llm"


@pytest.mark.asyncio
async def test_with_redis_down_the_stored_row_is_still_the_truth(monkeypatch):
    install(monkeypatch, redis=None)
    db = StoredConfigSession({"analysis_mode": "rules"})
    assert await resolve_configured_analysis_mode(db) == ConfiguredMode("rules", "ai_config")


@pytest.mark.asyncio
async def test_a_flushed_redis_is_reseeded_from_the_row(monkeypatch):
    """The homelab acceptance: restart/flush Redis and the stored mode still holds."""
    redis = RecordingRedis()
    install(monkeypatch, redis=redis)
    db = StoredConfigSession({"analysis_mode": "rules"})
    await resolve_configured_analysis_mode(db)
    redis.flushdb()

    assert await resolve_configured_analysis_mode(db) == ConfiguredMode("rules", "ai_config")
    assert redis.data[ANALYSIS_MODE_CACHE_KEY] == "rules"


# ── 2. no 30 s expiry back to env ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_refreshed_mode_survives_an_hour_of_process_time(monkeypatch):
    install(
        monkeypatch,
        redis=RecordingRedis(),
        fallback_session=StoredConfigSession({"analysis_mode": "rules"}, label="worker"),
    )
    from app.services.ml import model_store

    monkeypatch.setattr(model_store, "sync_down", AsyncMock(return_value=[]))
    monkeypatch.setattr(analysis_router, "_probe_ollama_model_async", AsyncMock(return_value=None))

    assert await refresh_analysis_mode_from_cache() == "rules"
    assert analysis_router._cached_mode == "rules"

    later = time.monotonic() + 3_600
    monkeypatch.setattr(time, "monotonic", lambda: later)
    assert get_analysis_mode() == "rules"
    mode, reason = resolve_analysis_mode_with_reason()
    assert mode == "rules"
    assert "mode explicitly set to 'rules'" in reason


# ── 3. the API deep guard follows the stored mode ────────────────────────────


def _deep_app(db: StoredConfigSession) -> FastAPI:
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


@pytest.mark.asyncio
async def test_the_deep_guard_follows_the_stored_mode(monkeypatch):
    from app.routers import deep_investigation

    redis = RecordingRedis()
    opened = install(monkeypatch, redis=redis)
    queue = MagicMock(return_value=MagicMock(id="task-1"))
    monkeypatch.setattr(deep_investigation.run_agent_pipeline, "apply_async", queue)
    db = StoredConfigSession({"analysis_mode": "rules"})  # env says auto
    app = _deep_app(db)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        refused = await client.post(f"/api/v1/deep-investigate/{RUN_ID}", json={"mode": "deep"})
        assert refused.status_code == 422, refused.text
        assert refused.json()["detail"] == (
            "Deep investigation requires LLM or Auto mode. Current mode: rules."
        )
        queue.assert_not_called()

        # Switch to Auto (what PUT /settings/ai does: store, then invalidate).
        db.row.value["analysis_mode"] = "auto"
        await invalidate_ai_config_cache()
        accepted = await client.post(f"/api/v1/deep-investigate/{RUN_ID}", json={"mode": "deep"})

    # The route answers 200 (it declares no status code); the contract's
    # "202" is the generic "accepted" -- the point is that it is not refused.
    assert accepted.status_code == 200, accepted.text
    queue.assert_called_once()
    assert opened == []


# ── 4. auto resolves through the EFFECTIVE provider ──────────────────────────


@pytest.mark.asyncio
async def test_auto_with_stored_provider_none_resolves_to_rules_although_env_has_ollama(monkeypatch):
    install(monkeypatch, redis=RecordingRedis())
    # ``none`` cannot be saved through the API (pattern); the row is edited directly.
    db = StoredConfigSession({"analysis_mode": "auto", "llm_provider": "none"})

    resolved, reason, configured = await resolve_effective_analysis_mode(db)

    assert configured == ConfiguredMode("auto", "ai_config")
    assert resolved == "rules"
    assert "no LLM provider configured" in reason


@pytest.mark.asyncio
async def test_auto_with_stored_ollama_resolves_to_llm_although_env_has_none(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "none")
    install(monkeypatch, redis=RecordingRedis())
    db = StoredConfigSession({"analysis_mode": "auto", "llm_provider": "ollama", "llm_model": "llama3"})

    resolved, reason, _ = await resolve_effective_analysis_mode(db)

    assert resolved == "llm"
    assert "'ollama'" in reason


@pytest.mark.asyncio
async def test_refresh_caches_the_effective_provider_for_synchronous_callers(monkeypatch):
    install(
        monkeypatch,
        redis=RecordingRedis(),
        fallback_session=StoredConfigSession({"analysis_mode": "auto", "llm_provider": "none"}),
    )
    from app.services.ml import model_store

    monkeypatch.setattr(model_store, "sync_down", AsyncMock(return_value=[]))
    probe = AsyncMock(return_value=None)
    monkeypatch.setattr(analysis_router, "_probe_ollama_model_async", probe)

    await refresh_analysis_mode_from_cache()

    assert analysis_router._effective_provider == "none"
    assert get_analysis_mode() == "rules"  # env says ollama; the stored provider wins
    probe.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_with_a_request_session_skips_the_model_sync(monkeypatch):
    """``model_store.sync_down`` resolves the storage provider through its OWN
    session on a cache miss; inside a guarded handler (the retry route) that is
    the second pooled connection. With ``db`` given it is skipped; the worker
    (no ``db``) still syncs."""
    opened = install(monkeypatch, redis=RecordingRedis())
    from app.services.ml import model_store

    sync = AsyncMock(return_value=[])
    monkeypatch.setattr(model_store, "sync_down", sync)
    monkeypatch.setattr(analysis_router, "_probe_ollama_model_async", AsyncMock(return_value=None))

    assert await refresh_analysis_mode_from_cache(StoredConfigSession({"analysis_mode": "ml"})) == "ml"
    sync.assert_not_awaited()
    assert opened == []

    install(monkeypatch, redis=RecordingRedis(), fallback_session=StoredConfigSession({"analysis_mode": "ml"}))
    await refresh_analysis_mode_from_cache()
    sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_ollama_probe_targets_the_effective_model(monkeypatch):
    seen: dict = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"models": [{"name": "llama3:8b"}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def get(self, url):
            seen["url"] = url
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    assert await analysis_router._probe_ollama_model_async(
        "ollama", "llama3:8b", "http://stored-ollama:11434/"
    ) is True
    assert seen["url"] == "http://stored-ollama:11434/api/tags"
    assert await analysis_router._probe_ollama_model_async("ollama", "mistral-nemo", None) is False
    assert await analysis_router._probe_ollama_model_async("openai", "gpt-4o", None) is None


# ── 5. PUT /settings/ai seeds without a TTL; invalidation deletes three keys ─


def _stub_put(monkeypatch):
    from app.routers import app_settings as router_mod

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
    monkeypatch.setattr(
        "app.services.agent_authority_lock.lock_global_agent_authority", AsyncMock()
    )
    return router_mod


@pytest.mark.asyncio
async def test_put_settings_ai_seeds_the_mode_without_ttl_and_reports_sources(monkeypatch):
    from app.models.schemas import AIConfigUpdate

    router_mod = _stub_put(monkeypatch)
    redis = RecordingRedis({
        "config:ai_effective:v2": "{}",
        "config:ai_effective": "{}",
        ANALYSIS_MODE_CACHE_KEY: "llm",
    })
    install(monkeypatch, redis=redis)
    db = StoredConfigSession(None)

    out = await router_mod.update_ai_config(
        payload=AIConfigUpdate(analysis_mode="rules"),
        current_user=MagicMock(id=uuid.uuid4()),
        db=db,
    )

    deletes = [c for c in redis.calls if c[0] == "delete"]
    assert deletes == [("delete", ("config:ai_effective:v2", "config:ai_effective", ANALYSIS_MODE_CACHE_KEY))]
    [seed] = _seeds(redis)
    assert seed[0] == "set" and seed[2] == "rules"
    assert seed[3] == () and seed[4] == {}, f"seeded with a TTL: {seed}"
    # After a save every key is stored (contract §0.9): all five sources say so.
    assert out.analysis_mode == "rules"
    assert out.analysis_mode_source == "ai_config"
    assert out.ai_timeout_seconds_source == "ai_config"
    assert out.deep_investigation_enabled_source == "ai_config"
    assert out.finetune_enabled_source == "ai_config"
    assert out.ai_confidence_threshold_source == "ai_config"
    assert out.ml_min_training_samples == settings.ML_MIN_TRAINING_SAMPLES
    # Derived provenance is never persisted (named explicitly: a key dropped
    # from _DERIVED_AI_KEYS would otherwise drop out of this check too).
    for derived in (
        *router_mod._DERIVED_AI_KEYS,
        "analysis_mode_source",
        "ai_timeout_seconds_source",
        "deep_investigation_enabled_source",
        "finetune_enabled_source",
        "ai_confidence_threshold_source",
    ):
        assert derived not in db.stored, derived


@pytest.mark.asyncio
async def test_put_of_another_field_still_reseeds_the_stored_mode(monkeypatch):
    """invalidate_ai_config_cache() deletes the accelerator on EVERY save, so
    the PUT re-seeds it whenever a valid mode is stored, not only when the
    request changed the mode."""
    from app.models.schemas import AIConfigUpdate

    router_mod = _stub_put(monkeypatch)
    redis = RecordingRedis()
    install(monkeypatch, redis=redis)
    db = StoredConfigSession({"analysis_mode": "ml"})

    await router_mod.update_ai_config(
        payload=AIConfigUpdate(llm_model="qwen2.5:14b"),
        current_user=MagicMock(id=uuid.uuid4()),
        db=db,
    )

    assert redis.data[ANALYSIS_MODE_CACHE_KEY] == "ml"
    assert [c[3:] for c in _seeds(redis)] == [((), {})]


# ── 6. the pipeline's frozen mode snapshot is the EFFECTIVE one ──────────────


@pytest.mark.asyncio
async def test_the_mode_snapshot_records_the_effective_provider_model_and_offline(monkeypatch):
    from app.agents.workflow import _resolve_analysis_mode_snapshot

    opened = install(monkeypatch, redis=RecordingRedis())
    db = StoredConfigSession({
        "analysis_mode": "rules",
        "llm_provider": "openai",
        "llm_model": "gpt-4o-mini",
        "ai_offline_mode": True,
    })

    snapshot = await _resolve_analysis_mode_snapshot(db)

    assert snapshot["requested"] == "rules"
    assert snapshot["requested_source"] == "ai_config"
    assert snapshot["resolved"] == "rules"
    assert "mode explicitly set to 'rules'" in snapshot["resolution_reason"]
    assert snapshot["provider"] == "openai"  # env: ollama
    assert snapshot["model"] == "gpt-4o-mini"  # env: qwen2.5:7b
    assert snapshot["offline"] is True  # env: False; the stored override tightens it
    assert snapshot["analysis_mode_env"] == "auto"
    assert opened == []  # the request session only (the retry route passes it)


# ── 7. a pipeline longer than 30 s keeps the stored mode (virtual clock) ─────


_TC = {"test_case_id": "tc-1", "test_name": "test_x", "error_message": "boom"}


def test_a_pipeline_longer_than_30s_keeps_resolving_the_stored_mode(monkeypatch):
    # env says "ml" (no model -> the offline ML path), the stored mode is
    # "rules": a revert to env shows up as mode_resolved == "ml" and never
    # reaches a network LLM call.
    monkeypatch.setattr(settings, "ANALYSIS_MODE", "ml")
    loop = VirtualClockLoop()
    # The clock the old in-process expiry read follows the virtual clock, so
    # the 31 s and the hour below really pass for it.
    monkeypatch.setattr(time, "monotonic", loop.time)
    install(
        monkeypatch,
        redis=RecordingRedis(),
        fallback_session=StoredConfigSession({"analysis_mode": "rules"}, label="worker"),
    )
    from app.services.ml import model_store

    monkeypatch.setattr(model_store, "sync_down", AsyncMock(return_value=[]))
    monkeypatch.setattr(analysis_router, "_probe_ollama_model_async", AsyncMock(return_value=None))

    async def pipeline() -> tuple[list[str], float]:
        await refresh_analysis_mode_from_cache()  # pipeline start (worker)
        seen = []
        for pause in (0, 31, 3_600):
            await asyncio.sleep(pause)
            result = await analysis_router.classify_test(dict(_TC))  # no explicit mode
            seen.append(result["_routing"]["mode_resolved"])
        return seen, loop.time()

    try:
        with patch("app.services.ml.classifier.MLClassifier.is_available", return_value=False):
            seen, elapsed = loop.run_until_complete(pipeline())
    finally:
        loop.close()

    assert elapsed >= 3_631
    assert seen == ["rules", "rules", "rules"]
