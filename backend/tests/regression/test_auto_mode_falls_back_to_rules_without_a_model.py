"""Auto mode with no reachable model classifies with rules (D-01, D-84, S-A-1).

Seen on the one-command install (offline mode on, provider ``ollama``, no
model service): every failed test read

    "AI Triage Result · unknown · UNKNOWN", "estimated 0%",
    "SUGGESTED ROOT CAUSE: Analysis failed — manual review required"

although the compose file and the docs promise "ANALYSIS_MODE=auto falls back
to ML then rules". Three faults in a chain:

1. The Ollama probe answered ``None`` ("unknown") when the host did not resolve
   or the provider policy refused it, and Auto falls back only on ``False``.
2. ``FastClassifier`` built its client outside its own ``try``, so a policy
   refusal escaped the tiered path's rules fallback.
3. The pipeline caught the exception and stored the "Analysis failed" stub.

A deep run then wrote those stubs over good stored rules results (D-84), and
the stubs were credited to the LLM ("Produced by LLM · unknown · unknown").
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql

from app.agents import analysis_agent
from app.agents.analysis_agent import AnalysisAgent
from app.core.config import settings
from app.services import analysis_router
from app.services.analysis_router import AnalysisMode


class _Unreachable:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, _url):
        raise OSError("[Errno -3] Temporary failure in name resolution")


@pytest.mark.asyncio
async def test_an_unreachable_ollama_is_definitely_unavailable(monkeypatch):
    import httpx

    from app.services import llm_policy_service

    async def _allow(*_a, **_k):
        return None

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(llm_policy_service, "enforce_provider_policy_async", _allow)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _Unreachable())

    available = await analysis_router._probe_ollama_model_async(
        "ollama", "qwen2.5:7b-instruct", "http://ollama:11434"
    )

    assert available is False


@pytest.mark.asyncio
async def test_a_policy_refusal_is_definitely_unavailable(monkeypatch):
    from app.services import llm_policy_service

    async def _refuse(*_a, **_k):
        raise RuntimeError("refused_by_policy")

    monkeypatch.setattr(llm_policy_service, "enforce_provider_policy_async", _refuse)

    assert await analysis_router._probe_ollama_model_async(
        "ollama", "qwen2.5:7b-instruct", "http://ollama:11434"
    ) is False


def test_auto_resolves_to_rules_once_the_probe_says_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_MODE", AnalysisMode.AUTO)
    monkeypatch.setattr(analysis_router, "_cached_mode", None, raising=False)
    monkeypatch.setattr(analysis_router, "_effective_provider", "ollama", raising=False)
    monkeypatch.setattr(analysis_router, "_ollama_model_available", False)
    with patch("app.services.ml.classifier.MLClassifier.is_available", return_value=False):
        assert analysis_router.get_analysis_mode() == AnalysisMode.RULES


@pytest.mark.asyncio
async def test_the_fast_classifier_reports_a_refused_client_as_a_failed_call(monkeypatch):
    from app.services.training import classifier

    async def _refuse(*_a, **_k):
        raise RuntimeError("LLMPolicyViolation: base URL not on the configured path")

    async def _no_fine_tune(*_a, **_k):
        return None

    monkeypatch.setattr(classifier, "get_llm", _refuse)
    monkeypatch.setattr(classifier.ModelRegistry, "get_active_model", _no_fine_tune)

    result, outcome = await classifier.FastClassifier.classify_with_outcome(
        test_name="charges the discounted total",
        error_message="AssertionError: expected 90.00 but was 100.00",
    )

    assert (result, outcome) == (None, "call_failed")


def test_a_rules_failure_leaves_a_stub_that_names_no_engine(monkeypatch):
    def _broken(*_a, **_k):
        raise ValueError("rules exploded")

    monkeypatch.setattr(analysis_router, "_classify_rules", _broken)

    stub = AnalysisAgent()._rules_after_model_error(
        RuntimeError("no model"), {"error_message": "boom"}, {},
        mode_requested="auto", mode_resolved="llm",
    )

    assert stub["failure_category"] == "UNKNOWN"
    assert stub["_routing"]["mode_used"] is None


def test_a_failed_attempt_only_replaces_an_unknown_row():
    stub = {"error": "no model", "root_cause_summary": "Analysis failed"}
    condition = analysis_agent._only_replace_unknown(stub, {"analysis_mode": "llm"})

    sql = str(condition.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "ai_analysis.failure_category IS NULL" in sql
    assert "ai_analysis.failure_category = 'UNKNOWN'" in sql

    no_engine = {"root_cause_summary": "timed out"}
    assert analysis_agent._only_replace_unknown(no_engine, {"analysis_mode": None}) is not None


def test_a_real_answer_replaces_whatever_was_stored():
    answer = {"failure_category": "PRODUCT_BUG", "root_cause_summary": "Rounding."}
    assert analysis_agent._only_replace_unknown(answer, {"analysis_mode": "rules"}) is None
