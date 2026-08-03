"""
AI_OFFLINE_MODE is a HARD CEILING on outbound LLM egress (security fix
2026-08-03).

The finding: ``llm_factory.get_llm()`` read the *effective* AI config, which
honoured a database override (``app_settings.ai_config.ai_offline_mode``) that
an ADMIN could flip from ``/settings/ai``. Cloud LLM calls could therefore be
turned back on with **no environment change** — while Jira, webhooks, GitHub,
GitLab, the Fixer and the Investigator all kept reading ``settings.
AI_OFFLINE_MODE`` directly and stayed blocked. An air-gapped operator who set
``AI_OFFLINE_MODE=true`` reasonably believed it was a kill switch. For LLM
egress alone, it was not.

The rule now: ``effective_offline = env_offline OR db_override_offline``.

This file pins the ceiling at the two consumer boundaries — the LLM factory
(does a cloud client actually get refused?) and the settings API (does the
page tell the truth, and is a disable request refused rather than eaten?).
The resolver's own four-combination truth table lives in
``tests/services/test_ai_config_resolver.py``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("asyncpg")

from app.routers import app_settings as router_mod  # noqa: E402
from app.services import ai_config_resolver as resolver  # noqa: E402


# ── plumbing ────────────────────────────────────────────────────────────────


class _AsyncSessionCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *_a):
        return False


def _db_with_row(row=None):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=row)
    db.execute = AsyncMock(return_value=result)
    return db


def _settings_row(value: dict):
    row = MagicMock()
    row.value = value
    return row


@pytest.fixture
def no_redis(monkeypatch):
    """Force the resolver down the DB path (and skip its 60 s cache)."""

    def _boom():
        raise RuntimeError("no redis in this test")

    monkeypatch.setattr("app.db.redis_client.get_redis", _boom)


@pytest.fixture
def no_secrets(monkeypatch):
    async def _read_secret(_db, _scope, _key):
        return None

    monkeypatch.setattr("app.services.secret_service.read_secret", _read_secret)


def _pin_env(monkeypatch, *, offline: bool):
    monkeypatch.setattr(resolver.settings, "AI_OFFLINE_MODE", offline)
    monkeypatch.setattr(router_mod.settings, "AI_OFFLINE_MODE", offline)


# ── consumer 1: the LLM factory refuses to build a cloud client ─────────────


@pytest.mark.asyncio
async def test_get_llm_refuses_cloud_provider_when_env_pinned(monkeypatch, no_redis, no_secrets):
    """The whole point: a DB override cannot conjure an outbound OpenAI client."""
    _pin_env(monkeypatch, offline=True)
    monkeypatch.setattr(
        "app.db.postgres.AsyncSessionLocal",
        MagicMock(return_value=_AsyncSessionCtx(_db_with_row(
            _settings_row({"llm_provider": "openai", "llm_model": "gpt-4o", "ai_offline_mode": False}),
        ))),
    )

    from app.services.llm_factory import get_llm

    with pytest.raises(ValueError, match="refusing to call external API"):
        await get_llm()


@pytest.mark.asyncio
async def test_get_llm_refuses_anthropic_when_env_pinned(monkeypatch, no_redis, no_secrets):
    _pin_env(monkeypatch, offline=True)
    monkeypatch.setattr(
        "app.db.postgres.AsyncSessionLocal",
        MagicMock(return_value=_AsyncSessionCtx(_db_with_row(
            _settings_row({"llm_provider": "anthropic", "ai_offline_mode": False}),
        ))),
    )

    from app.services.llm_factory import get_llm

    with pytest.raises(ValueError, match="refusing to call external API"):
        await get_llm()


@pytest.mark.asyncio
async def test_get_llm_still_allows_cloud_when_the_env_permits_it(monkeypatch, no_redis, no_secrets):
    """The ceiling only tightens — it must not break a legitimate cloud setup."""
    _pin_env(monkeypatch, offline=False)
    monkeypatch.setattr(
        "app.db.postgres.AsyncSessionLocal",
        MagicMock(return_value=_AsyncSessionCtx(_db_with_row(
            _settings_row({"llm_provider": "openai", "llm_model": "gpt-4o", "ai_offline_mode": False}),
        ))),
    )

    cfg = await resolver.get_effective_ai_config()
    assert cfg["offline_mode"] is False
    assert cfg["provider"] == "openai"


# ── consumer 2: the settings API tells the truth about why ──────────────────


@pytest.mark.asyncio
async def test_load_ai_config_reports_effective_value_and_provenance(monkeypatch):
    """/settings/ai (and, through it, /ai/model-status) must show what is IN FORCE."""
    _pin_env(monkeypatch, offline=True)
    db = _db_with_row(_settings_row({"ai_offline_mode": False}))

    cfg = await router_mod._load_ai_config(db)

    assert cfg["ai_offline_mode"] is True
    assert cfg["ai_offline_mode_source"] == "env"
    assert cfg["ai_offline_mode_env_pinned"] is True


@pytest.mark.asyncio
async def test_load_ai_config_reports_override_provenance_when_env_permits(monkeypatch):
    _pin_env(monkeypatch, offline=False)
    db = _db_with_row(_settings_row({"ai_offline_mode": True}))

    cfg = await router_mod._load_ai_config(db)

    assert cfg["ai_offline_mode"] is True
    assert cfg["ai_offline_mode_source"] == "override"
    assert cfg["ai_offline_mode_env_pinned"] is False


@pytest.mark.asyncio
async def test_put_rejecting_offline_disable_when_env_pinned(monkeypatch):
    """Rejected, not silently ignored — the operator must learn the click failed.

    409 (not 422): the payload is well-formed, it conflicts with deployment
    state. The detail names the exact remedy.
    """
    from app.models.schemas import AIConfigUpdate

    _pin_env(monkeypatch, offline=True)
    db = _db_with_row(_settings_row({"ai_offline_mode": True}))

    with pytest.raises(router_mod.HTTPException) as exc:
        await router_mod.update_ai_config(
            payload=AIConfigUpdate(ai_offline_mode=False),
            current_user=MagicMock(id="u1"),
            db=db,
        )

    assert exc.value.status_code == 409
    assert "AI_OFFLINE_MODE=false" in exc.value.detail
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_put_of_an_unrelated_field_still_works_when_env_pinned(monkeypatch):
    """The ceiling must not brick the rest of the settings page."""
    from app.models.schemas import AIConfigUpdate

    _pin_env(monkeypatch, offline=True)
    row = _settings_row({"ai_offline_mode": True})
    db = _db_with_row(row)
    _stub_put_collaborators(monkeypatch)

    out = await router_mod.update_ai_config(
        payload=AIConfigUpdate(llm_model="qwen2.5:14b"),
        current_user=MagicMock(id="u1"),
        db=db,
    )

    assert out.llm_model == "qwen2.5:14b"
    assert out.ai_offline_mode is True
    assert out.ai_offline_mode_source == "env"
    assert out.ai_offline_mode_env_pinned is True
    # Derived provenance is never persisted — it is an environment fact, and a
    # stored copy would be one process's snapshot masquerading as config.
    for derived in router_mod._DERIVED_AI_KEYS:
        assert derived not in row.value


@pytest.mark.asyncio
async def test_put_can_disable_offline_when_the_env_permits(monkeypatch):
    from app.models.schemas import AIConfigUpdate

    _pin_env(monkeypatch, offline=False)
    row = _settings_row({"ai_offline_mode": True})
    db = _db_with_row(row)
    _stub_put_collaborators(monkeypatch)

    out = await router_mod.update_ai_config(
        payload=AIConfigUpdate(ai_offline_mode=False),
        current_user=MagicMock(id="u1"),
        db=db,
    )

    assert out.ai_offline_mode is False
    assert out.ai_offline_mode_source == "not_offline"
    assert out.ai_offline_mode_env_pinned is False


def _stub_put_collaborators(monkeypatch):
    """Neutralise secret storage / audit / ML probing for the PUT path."""
    monkeypatch.setattr(router_mod, "extract_secrets_from_config", lambda _k, _u: {})
    monkeypatch.setattr(router_mod, "strip_secrets_from_config", lambda _k, merged: dict(merged))
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
