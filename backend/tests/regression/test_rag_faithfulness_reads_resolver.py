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
``ai_config`` row. The Ragas tests pin the result read that became reachable
with the fix: ragas 0.2+ returns an ``EvaluationResult`` with no ``.get``.
"""
from __future__ import annotations

import sys
import types
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
async def test_configured_evaluator_and_provider_are_honoured(stored_ai_config):
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
    # The env permits egress; the stored override does not. Ragas calls a
    # hosted LLM, so the effective flag wins over the evaluator setting.
    stored_ai_config({"ai_offline_mode": True, "rag_faithfulness_evaluator": "ragas"})

    assert await svc._resolve_backend() == "ollama"


# ── Ragas result shapes ───────────────────────────────────────────────────


class _EvaluationResult:
    """ragas 0.2+: no ``.get``; the metric maps to one score per sample."""

    def __init__(self, scores):
        self._scores = scores

    def __getitem__(self, key):
        return {"faithfulness": self._scores}[key]


@pytest.fixture
def fake_ragas(monkeypatch):
    """Install stand-ins for ``ragas`` and ``datasets`` for one test."""

    def _install(result):
        ragas = types.ModuleType("ragas")
        ragas.evaluate = MagicMock(return_value=result)
        metrics = types.ModuleType("ragas.metrics")
        metrics.faithfulness = object()
        datasets = types.ModuleType("datasets")
        datasets.Dataset = MagicMock()
        monkeypatch.setitem(sys.modules, "ragas", ragas)
        monkeypatch.setitem(sys.modules, "ragas.metrics", metrics)
        monkeypatch.setitem(sys.modules, "datasets", datasets)

    return _install


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result, expected",
    [
        (_EvaluationResult([0.8]), 0.8),   # ragas 0.2+
        ({"faithfulness": 0.6}, 0.6),       # ragas 0.1 dict-like Result
    ],
)
async def test_ragas_score_is_read_from_either_result_shape(fake_ragas, result, expected):
    fake_ragas(result)

    score, reason = await svc._evaluate_via_ragas("case", ["citation"])

    assert score == pytest.approx(expected)
    assert reason.startswith("ragas faithfulness metric")


@pytest.mark.asyncio
async def test_ragas_nan_score_fails_closed(fake_ragas):
    # A failed ragas judgement is NaN; clamped, it would read as 1.0 and pass.
    fake_ragas(_EvaluationResult([float("nan")]))

    score, reason = await svc._evaluate_via_ragas("case", ["citation"])

    assert score == 0.0
    assert "no faithfulness score" in reason
