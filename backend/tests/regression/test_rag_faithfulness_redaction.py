"""Regression: faithfulness evaluator must redact content before the LLM.

Bug pinned (review/rag-faithfulness-service, 2026-06-02): ``evaluate`` sent the
generated case + its citations straight to the evaluator backend, but that
backend can be a hosted LLM (Ragas, or ``get_llm()`` with a cloud provider when
AI_OFFLINE_MODE=false). The privacy invariant requires scrubbing at every LLM
boundary (rag_generation already does this via redact_prompt). Fix: ``evaluate``
runs ``redact_prompt`` over the content + each citation before dispatch.

Also pins the offline-mode backend gate (Ragas can't egress in air-gapped mode).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.core.config import settings  # noqa: E402
from app.services import rag_faithfulness_service as svc  # noqa: E402


@pytest.mark.asyncio
async def test_evaluate_redacts_content_and_citations_before_backend():
    captured = {}

    async def _fake_backend(content, citations):
        captured["content"] = content
        captured["citations"] = citations
        return (0.9, "ok")

    def _spy_redact(text, *a, **k):
        return (f"REDACTED::{text}", True)

    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "_resolve_backend", AsyncMock(return_value="ollama")), \
         patch.object(svc, "_evaluate_via_ollama", _fake_backend), \
         patch("app.services.rag_redaction_service.redact_prompt", _spy_redact):
        result = await svc.evaluate("secret body", ["cite-secret-1", "cite-secret-2"])

    assert result == {"score": 0.9, "evaluator": "ollama", "reason": "ok"}
    # The backend received the redacted content, never the raw secret.
    assert captured["content"] == "REDACTED::secret body"
    assert captured["citations"] == ["REDACTED::cite-secret-1", "REDACTED::cite-secret-2"]


@pytest.mark.asyncio
async def test_evaluate_noop_when_flag_disabled():
    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=False)):
        assert await svc.evaluate("x", ["c"]) is None


@pytest.mark.asyncio
async def test_resolve_backend_forces_ollama_when_offline():
    # Offline mode must route to the local backend regardless of config.
    with patch.object(settings, "AI_OFFLINE_MODE", True):
        assert await svc._resolve_backend() == "ollama"
