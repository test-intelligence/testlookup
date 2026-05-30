"""
Unit tests for ``services.rag_faithfulness_service`` — pure helpers.

The DB-bound entry points (``gate_accept``, ``persist_evaluation``) are
covered by integration tests; this suite focuses on ``_parse_score``,
``_evaluate_via_ollama`` wiring, and the feature-flag gate.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import rag_faithfulness_service as svc


# ── _parse_score ───────────────────────────────────────────────────────────


def test_parse_score_handles_clean_json():
    score, reason = svc._parse_score(
        '{"score": 0.85, "reason": "fully supported by citation 2"}'
    )
    assert score == pytest.approx(0.85)
    assert "supported" in reason


def test_parse_score_handles_json_wrapped_in_preamble():
    raw = (
        "Looking at the generated case, I see:\n"
        '{"score": 0.42, "reason": "only partially cited"}\n'
        "Hope that helps!"
    )
    score, reason = svc._parse_score(raw)
    assert score == pytest.approx(0.42)
    assert "partially" in reason


def test_parse_score_clamps_above_one():
    """Some models return 0.0-100 instead of 0.0-1.0. Normalize."""
    score, _ = svc._parse_score("The faithfulness score is 80")
    assert score == pytest.approx(0.80)


def test_parse_score_falls_back_to_numeric_extraction():
    score, _ = svc._parse_score("I'd say about 0.7 of the claims are grounded.")
    assert score == pytest.approx(0.7)


def test_parse_score_returns_zero_for_unparseable_output():
    score, reason = svc._parse_score("I don't know")
    assert score == 0.0
    assert "could not parse" in reason.lower()


def test_parse_score_clamps_below_zero():
    score, _ = svc._parse_score('{"score": -0.5, "reason": "invalid"}')
    assert score == 0.0


# ── Feature flag gate ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluate_returns_none_when_flag_off():
    with patch(
        "app.services.rag_faithfulness_service._feature_enabled",
        AsyncMock(return_value=False),
    ):
        result = await svc.evaluate("generated content", ["citation 1"])
    assert result is None


@pytest.mark.asyncio
async def test_evaluate_ollama_backend_when_flag_on():
    """When the flag is on and offline mode is on, the evaluator must
    route to the Ollama backend regardless of any other setting."""
    from app.core.config import settings
    with patch(
        "app.services.rag_faithfulness_service._feature_enabled",
        AsyncMock(return_value=True),
    ), patch.object(settings, "AI_OFFLINE_MODE", True), patch(
        "app.services.rag_faithfulness_service._evaluate_via_ollama",
        AsyncMock(return_value=(0.9, "grounded")),
    ) as mock_ollama, patch(
        "app.services.rag_faithfulness_service._evaluate_via_ragas",
        AsyncMock(return_value=(0.1, "should_not_fire")),
    ) as mock_ragas:
        result = await svc.evaluate("content", ["cite"])
    assert result is not None
    assert result["evaluator"] == "ollama"
    assert result["score"] == 0.9
    mock_ollama.assert_awaited_once()
    mock_ragas.assert_not_called()
