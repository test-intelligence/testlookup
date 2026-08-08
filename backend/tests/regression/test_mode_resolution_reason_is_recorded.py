"""The decision trail must record *why* a mode was chosen, not just which.

Observed on the live deployment: every `ai_analysis` row carried

    mode_requested = "auto"
    mode_resolved  = "rules"
    fallback_from  = null
    fallback_reason = null

`auto` prefers ML, then LLM, then rules. Resolving to rules therefore means
something ruled the LLM out — Ollama unreachable, no provider configured, or a
trained ML model out-ranking it. `analysis_router` logged
`auto_mode_ollama_model_unavailable` and then threw the reason away, so the trail
that documents itself as authoritative could not answer the first question an
operator asks when AI analysis looks thin: *why didn't the LLM run?*

Three different causes produced byte-identical trails. These tests pin that they
no longer do.
"""
from __future__ import annotations

import pytest

pytest.importorskip("app.services.analysis_router")

from app.services import analysis_router as router  # noqa: E402

pytestmark = pytest.mark.regression


@pytest.fixture(autouse=True)
def _clear_mode_cache(monkeypatch):
    """get_analysis_mode caches the configured mode for 30s in-process."""
    monkeypatch.setattr(router, "_cached_mode", None, raising=False)
    monkeypatch.setattr(router, "_cached_mode_ts", 0.0, raising=False)


def _resolve(monkeypatch, *, mode="auto", provider="ollama", ollama_ok=None, ml=False):
    monkeypatch.setattr(router.settings, "ANALYSIS_MODE", mode, raising=False)
    monkeypatch.setattr(router.settings, "LLM_PROVIDER", provider, raising=False)
    monkeypatch.setattr(router.settings, "LLM_MODEL", "qwen2.5:7b", raising=False)
    monkeypatch.setattr(router, "_ollama_model_available", ollama_ok, raising=False)

    class _ML:
        @staticmethod
        def is_available():
            return ml

    import sys
    import types
    mod = types.ModuleType("app.services.ml.classifier")
    mod.MLClassifier = _ML
    monkeypatch.setitem(sys.modules, "app.services.ml.classifier", mod)
    return router.resolve_analysis_mode_with_reason()


class TestTheReasonExists:
    def test_returns_a_mode_and_a_reason(self, monkeypatch):
        mode, reason = _resolve(monkeypatch)
        assert isinstance(mode, str) and isinstance(reason, str)
        assert reason.strip(), "an empty reason is no better than no reason"

    def test_get_analysis_mode_still_returns_a_bare_string(self, monkeypatch):
        """Many callers treat this as a str; the tuple must not leak into them."""
        _resolve(monkeypatch)
        assert isinstance(router.get_analysis_mode(), str)


class TestDistinctCausesGiveDistinctReasons:
    def test_ollama_probe_says_so(self, monkeypatch):
        mode, reason = _resolve(monkeypatch, ollama_ok=False)
        assert mode == "rules"
        assert "ollama" in reason.lower(), reason
        assert "qwen2.5:7b" in reason, "the reason should name the model that was missing"

    def test_no_provider_configured_says_so(self, monkeypatch):
        mode, reason = _resolve(monkeypatch, provider="none")
        assert mode == "rules"
        assert "no llm provider" in reason.lower(), reason

    def test_the_two_rules_paths_are_distinguishable(self, monkeypatch):
        """Both resolve to rules. If their reasons match, the field is useless —
        this is exactly the state the null fallback_reason left us in."""
        _, probe_reason = _resolve(monkeypatch, ollama_ok=False)
        _, unconfigured_reason = _resolve(monkeypatch, provider="none")
        assert probe_reason != unconfigured_reason

    def test_ml_wins_and_says_why(self, monkeypatch):
        mode, reason = _resolve(monkeypatch, ml=True)
        assert mode == "ml"
        assert "ml model" in reason.lower(), reason

    def test_explicit_mode_is_not_described_as_auto_resolution(self, monkeypatch):
        mode, reason = _resolve(monkeypatch, mode="rules")
        assert mode == "rules"
        assert "auto" in reason.lower() and "no auto resolution" in reason.lower(), reason


def test_the_snapshot_carries_the_reason():
    """The reason has to reach routing_metadata, not just the return value —
    the snapshot builder is what gets persisted."""
    import inspect

    from app.agents import workflow

    src = inspect.getsource(workflow)
    assert "resolution_reason" in src, (
        "the mode snapshot does not carry resolution_reason, so the reason never "
        "reaches AIAnalysis.routing_metadata and the decision trail is unchanged"
    )
    assert "resolve_analysis_mode_with_reason" in src, (
        "workflow still calls the reason-discarding get_analysis_mode()"
    )
