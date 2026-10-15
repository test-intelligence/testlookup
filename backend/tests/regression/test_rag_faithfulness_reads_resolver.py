"""Regression: the faithfulness evaluator reads the AI config it is given.

Bug pinned (2026-10-15): ``rag_faithfulness_service`` imported
``resolve_ai_config`` from ``app.services.ai_config_resolver``. No such
function exists (the resolver exposes ``get_effective_ai_config``), and both
call sites wrapped the import in ``except Exception``, so the ImportError was
swallowed:

* the evaluator strategy was always the ``ollama`` default, whatever
  ``rag_faithfulness_evaluator`` was set to;
* the provider label on every judged case came from the env var, never from
  the stored ``ai_config`` (and the code read ``llm_provider``, a key the
  resolver does not publish; it publishes ``provider``).

Its tests patched ``_resolve_backend`` and ``_active_llm_provider`` out, so
nothing ran the lookup. These drive the real resolver from a stored
``ai_config`` row. How the Ragas strategy scores, and with which LLM, is
pinned in test_ragas_judges_with_the_configured_llm.py.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from app.core.config import settings  # noqa: E402
from app.services import rag_faithfulness_service as svc  # noqa: E402


class _SessionCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def stored_ai_config(monkeypatch, fake_redis):
    """Serve ``overrides`` as the ``ai_config`` row to the real resolver."""
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake_redis)

    async def _no_secret(_db, _scope, _key):
        return None

    monkeypatch.setattr("app.services.secret_service.read_secret", _no_secret)

    def _store(overrides: dict) -> None:
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=MagicMock(value=overrides))
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result)
        monkeypatch.setattr(
            "app.db.postgres.AsyncSessionLocal", MagicMock(return_value=_SessionCtx(db))
        )

    return _store


@pytest.mark.asyncio
async def test_configured_evaluator_and_provider_are_honoured(stored_ai_config, monkeypatch):
    # ragas is not in requirements.txt; the strategy runs only where it is installed.
    monkeypatch.setattr(svc, "ragas_installed", lambda: True)
    stored_ai_config({"llm_provider": "openrouter", "rag_faithfulness_evaluator": "ragas"})

    assert await svc._resolve_backend() == "ragas"
    # The env var says ollama; the stored provider is what judges.
    assert await svc._active_llm_provider() == "openrouter"


@pytest.mark.asyncio
async def test_evaluate_labels_the_stored_provider(stored_ai_config, monkeypatch):
    stored_ai_config({"llm_provider": "openrouter"})
    monkeypatch.setattr(svc, "_feature_enabled", AsyncMock(return_value=True))
    judge = AsyncMock(return_value=(0.9, "supported"))
    monkeypatch.setattr(svc, "_evaluate_via_ollama", judge)

    result = await svc.evaluate("Login rejects a wrong password", ["Spec 4.2: wrong password -> 401"])

    judge.assert_awaited_once()
    assert result == {"score": 0.9, "evaluator": "openrouter", "reason": "supported"}


@pytest.mark.asyncio
async def test_stored_offline_override_keeps_ragas_off(stored_ai_config):
    # The env permits egress; the stored override does not. Offline mode keeps
    # Ragas off, so the effective flag wins over the evaluator setting.
    stored_ai_config({"ai_offline_mode": True, "rag_faithfulness_evaluator": "ragas"})

    assert await svc._resolve_backend() == "ollama"
