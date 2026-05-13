"""Unit tests for services.analysis_router — the central LLM/ML/Rules dispatcher.

CLAUDE.md mandates that every analysis call goes through this module
(``never call run_triage_agent() or RulesEngine directly``). The tests below
codify the contract callers depend on:

  - ``get_analysis_mode()`` resolves configured → auto → fallback chain
    (ML if trained → LLM if provider configured → Rules) with a 30s
    in-process cache.
  - ``classify_test()`` always attaches a ``_routing`` dict describing
    mode_requested / mode_resolved / mode_used / fallback_from /
    fallback_reason / auto_probe — this is the authoritative decision
    record consumed by the analysis_agent audit and the decision trail UI.
  - On every fallback path (ML→Rules, LLM→Rules) the routing dict is
    populated with a category-prefixed reason (``ml_model_unavailable:``,
    ``ml_error:``, ``llm_error:``) truncated to 200 chars.
  - ``generate_summary()`` dispatches by mode: ML→MLSummaryGenerator,
    Rules→RulesEngine, LLM→empty dict (signal to caller's SummaryAgent).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("asyncpg")

from app.services import analysis_router  # noqa: E402
from app.services.analysis_router import (  # noqa: E402
    AnalysisMode,
    classify_test,
    generate_summary,
    get_analysis_mode,
)


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Module-level caches leak across tests; reset before AND after each."""
    analysis_router._cached_mode = None
    analysis_router._cached_mode_ts = 0
    analysis_router._ollama_model_available = None
    analysis_router._ollama_probe_ts = 0
    yield
    analysis_router._cached_mode = None
    analysis_router._cached_mode_ts = 0
    analysis_router._ollama_model_available = None
    analysis_router._ollama_probe_ts = 0


@pytest.fixture
def settings_mode(monkeypatch):
    """Set settings.ANALYSIS_MODE for one test."""
    def _set(value: str, *, provider: str = "ollama", model: str = "qwen2.5:7b"):
        monkeypatch.setattr(analysis_router.settings, "ANALYSIS_MODE", value)
        monkeypatch.setattr(analysis_router.settings, "LLM_PROVIDER", provider)
        monkeypatch.setattr(analysis_router.settings, "LLM_MODEL", model)
    return _set


# ── get_analysis_mode() — configured paths ───────────────────────────────────


@pytest.mark.parametrize("mode", [AnalysisMode.LLM, AnalysisMode.ML, AnalysisMode.RULES])
def test_get_analysis_mode_returns_configured_value_verbatim(settings_mode, mode):
    settings_mode(mode)
    assert get_analysis_mode() == mode


def test_get_analysis_mode_lowercases_configured_value(settings_mode):
    settings_mode("LLM")
    assert get_analysis_mode() == AnalysisMode.LLM


def test_get_analysis_mode_30s_cache_serves_stale_value(settings_mode):
    """First call caches; changing settings inside 30s window must NOT take effect."""
    settings_mode(AnalysisMode.LLM)
    assert get_analysis_mode() == AnalysisMode.LLM

    # Change the underlying setting mid-window. The cached value must win.
    analysis_router.settings.ANALYSIS_MODE = AnalysisMode.RULES
    assert get_analysis_mode() == AnalysisMode.LLM


# ── get_analysis_mode() — auto resolution ────────────────────────────────────


def test_auto_picks_ml_when_classifier_available(settings_mode):
    settings_mode(AnalysisMode.AUTO)
    with patch("app.services.ml.classifier.MLClassifier.is_available", return_value=True):
        assert get_analysis_mode() == AnalysisMode.ML


def test_auto_falls_through_to_llm_when_ml_unavailable(settings_mode):
    settings_mode(AnalysisMode.AUTO, provider="ollama")
    with patch("app.services.ml.classifier.MLClassifier.is_available", return_value=False):
        assert get_analysis_mode() == AnalysisMode.LLM


def test_auto_returns_rules_when_ollama_probe_definitively_false(settings_mode):
    """A confirmed-missing Ollama model must short-circuit auto to rules."""
    settings_mode(AnalysisMode.AUTO, provider="ollama")
    analysis_router._ollama_model_available = False  # definitively absent
    with patch("app.services.ml.classifier.MLClassifier.is_available", return_value=False):
        assert get_analysis_mode() == AnalysisMode.RULES


def test_auto_attempts_llm_when_ollama_probe_unknown(settings_mode):
    """None probe means we haven't checked yet — try LLM and let dispatcher fall back on error."""
    settings_mode(AnalysisMode.AUTO, provider="ollama")
    analysis_router._ollama_model_available = None
    with patch("app.services.ml.classifier.MLClassifier.is_available", return_value=False):
        assert get_analysis_mode() == AnalysisMode.LLM


@pytest.mark.parametrize("provider", ["", "none"])
def test_auto_returns_rules_when_no_provider_configured(settings_mode, provider):
    settings_mode(AnalysisMode.AUTO, provider=provider)
    with patch("app.services.ml.classifier.MLClassifier.is_available", return_value=False):
        assert get_analysis_mode() == AnalysisMode.RULES


def test_auto_swallows_ml_classifier_import_error(settings_mode):
    """If MLClassifier itself blows up, auto must continue down the chain, not crash."""
    settings_mode(AnalysisMode.AUTO, provider="ollama")
    with patch(
        "app.services.ml.classifier.MLClassifier.is_available",
        side_effect=RuntimeError("model corrupt"),
    ):
        # Should not raise; falls through to LLM (provider configured).
        assert get_analysis_mode() == AnalysisMode.LLM


# ── classify_test() — _routing contract ──────────────────────────────────────


_TC = {"test_case_id": "tc-1", "test_name": "test_x", "error_message": "boom"}


def _routing(result: dict) -> dict:
    assert "_routing" in result, "classify_test must always attach _routing"
    return result["_routing"]


@pytest.mark.asyncio
async def test_routing_dict_has_all_required_keys_on_success():
    """Every consumer of _routing depends on this exact key set."""
    result = await classify_test(_TC, mode=AnalysisMode.RULES)
    r = _routing(result)
    expected_keys = {
        "mode_requested",
        "mode_resolved",
        "mode_used",
        "fallback_from",
        "fallback_reason",
        "auto_probe",
    }
    assert expected_keys <= set(r.keys())
    assert {"ollama_model_available", "probe_age_seconds"} <= set(r["auto_probe"].keys())


@pytest.mark.asyncio
async def test_routing_explicit_mode_arg_recorded_as_requested():
    result = await classify_test(_TC, mode=AnalysisMode.RULES)
    r = _routing(result)
    assert r["mode_requested"] == AnalysisMode.RULES
    assert r["mode_resolved"] == AnalysisMode.RULES
    assert r["mode_used"] == AnalysisMode.RULES
    assert r["fallback_from"] is None
    assert r["fallback_reason"] is None


@pytest.mark.asyncio
async def test_routing_implicit_mode_records_None_as_requested(settings_mode):
    """When the caller does not pass `mode=`, mode_requested is None."""
    settings_mode(AnalysisMode.RULES)
    result = await classify_test(_TC)
    r = _routing(result)
    assert r["mode_requested"] is None
    assert r["mode_resolved"] == AnalysisMode.RULES
    assert r["mode_used"] == AnalysisMode.RULES


@pytest.mark.asyncio
async def test_ml_runtime_error_falls_back_to_rules_with_unavailable_reason():
    """RuntimeError from MLClassifier means model not trained — rule-engine fallback."""
    with patch(
        "app.services.ml.classifier.MLClassifier.classify",
        side_effect=RuntimeError("no model loaded"),
    ):
        result = await classify_test(_TC, mode=AnalysisMode.ML)

    r = _routing(result)
    assert r["mode_used"] == AnalysisMode.RULES
    assert r["fallback_from"] == AnalysisMode.ML
    assert r["fallback_reason"].startswith("ml_model_unavailable:")
    assert "no model loaded" in r["fallback_reason"]
    # Result body must come from the rules engine.
    assert result.get("classified_by") == "rules_engine"


@pytest.mark.asyncio
async def test_ml_generic_error_uses_ml_error_prefix():
    """Non-RuntimeError must surface under the generic ml_error: prefix."""
    with patch(
        "app.services.ml.classifier.MLClassifier.classify",
        side_effect=ValueError("feature shape mismatch"),
    ):
        result = await classify_test(_TC, mode=AnalysisMode.ML)

    r = _routing(result)
    assert r["mode_used"] == AnalysisMode.RULES
    assert r["fallback_from"] == AnalysisMode.ML
    assert r["fallback_reason"].startswith("ml_error: ValueError:")


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_rules_with_llm_error_reason():
    with patch(
        "app.services.agent.run_triage_agent",
        new=AsyncMock(side_effect=TimeoutError("ollama timeout")),
    ):
        result = await classify_test(_TC, mode=AnalysisMode.LLM)

    r = _routing(result)
    assert r["mode_used"] == AnalysisMode.RULES
    assert r["fallback_from"] == AnalysisMode.LLM
    assert r["fallback_reason"].startswith("llm_error: TimeoutError:")
    assert result.get("classified_by") == "rules_engine"


@pytest.mark.asyncio
async def test_fallback_reason_truncated_to_200_chars():
    """Long error chains must not blow up the routing payload size budget."""
    huge = "x" * 1000
    with patch(
        "app.services.ml.classifier.MLClassifier.classify",
        side_effect=ValueError(huge),
    ):
        result = await classify_test(_TC, mode=AnalysisMode.ML)

    r = _routing(result)
    assert len(r["fallback_reason"]) <= 200


@pytest.mark.asyncio
async def test_routing_includes_auto_probe_age_when_probe_has_run():
    """Once the probe has executed, auto_probe.probe_age_seconds is populated."""
    import time
    analysis_router._ollama_model_available = True
    analysis_router._ollama_probe_ts = time.monotonic() - 5.0  # 5s ago

    result = await classify_test(_TC, mode=AnalysisMode.RULES)
    probe = _routing(result)["auto_probe"]
    assert probe["ollama_model_available"] is True
    assert probe["probe_age_seconds"] is not None
    assert probe["probe_age_seconds"] >= 5.0


@pytest.mark.asyncio
async def test_routing_probe_age_is_None_when_probe_never_ran():
    result = await classify_test(_TC, mode=AnalysisMode.RULES)
    probe = _routing(result)["auto_probe"]
    assert probe["ollama_model_available"] is None
    assert probe["probe_age_seconds"] is None


# ── classify_test() — happy paths actually hit the right engine ──────────────


@pytest.mark.asyncio
async def test_ml_success_uses_ml_classifier():
    fake_result = {"failure_category": "INFRASTRUCTURE", "classified_by": "ml_classifier"}
    with patch(
        "app.services.ml.classifier.MLClassifier.classify",
        return_value=fake_result,
    ), patch(
        "app.services.ml.feature_extractor.extract_features",
        return_value={"f1": 1.0},
    ):
        result = await classify_test(_TC, mode=AnalysisMode.ML)

    r = _routing(result)
    assert result["classified_by"] == "ml_classifier"
    assert r["mode_used"] == AnalysisMode.ML
    assert r["fallback_from"] is None


@pytest.mark.asyncio
async def test_llm_success_uses_triage_agent():
    fake_result = {"failure_category": "BUG", "classified_by": "llm_agent"}
    with patch(
        "app.services.agent.run_triage_agent",
        new=AsyncMock(return_value=fake_result),
    ):
        result = await classify_test(_TC, mode=AnalysisMode.LLM)

    r = _routing(result)
    assert result["classified_by"] == "llm_agent"
    assert r["mode_used"] == AnalysisMode.LLM
    assert r["fallback_from"] is None


# ── generate_summary() ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_ml_mode_calls_ml_summary_generator():
    sentinel = {"layer1_executive_summary": "from_ml"}
    with patch(
        "app.services.ml.summary_generator.MLSummaryGenerator.generate",
        return_value=sentinel,
    ) as gen, patch(
        "app.services.ml.classifier.MLClassifier.get_model_info",
        return_value={"version": "v3"},
    ):
        out = await generate_summary({}, {}, mode=AnalysisMode.ML)

    assert out is sentinel
    gen.assert_called_once()
    # model_version kwarg threaded through from MLClassifier.get_model_info().
    assert gen.call_args.kwargs.get("model_version") == "v3"


@pytest.mark.asyncio
async def test_summary_rules_mode_calls_rules_engine():
    sentinel = {"layer1_executive_summary": "from_rules"}
    with patch(
        "app.services.rules_engine.RulesEngine.generate_summary",
        return_value=sentinel,
    ) as gen:
        out = await generate_summary({"runs": 1}, {"tc-1": {}}, mode=AnalysisMode.RULES)

    assert out is sentinel
    gen.assert_called_once()


@pytest.mark.asyncio
async def test_summary_llm_mode_returns_empty_dict_signal():
    """LLM mode means caller should defer to SummaryAgent — sentinel is {}."""
    out = await generate_summary({}, {}, mode=AnalysisMode.LLM)
    assert out == {}
