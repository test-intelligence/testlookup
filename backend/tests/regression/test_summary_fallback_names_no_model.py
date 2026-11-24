"""D-13 (row 9): the deterministic summary says why it is deterministic.

The fallback sentence read "This summary was generated from stored pipeline evidence because the
configured LLM was unavailable." on every model-free install, where no LLM was ever configured, and
the progress event said the same. Now the sentence (in the executive summary), the progress message
and the advice pick a reason: no model in use (the rules tier, or Rules mode), refused by the AI
settings (no endpoint resolves), the model budget, or a configured model that failed. Only the last
tells the reader to fix the model (batch 6 review: a configured-but-refused model is not "none").
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.agents import summary_agent
from app.agents.summary_agent import (
    FALLBACK_PROGRESS,
    FALLBACK_SENTENCES,
    SummaryAgent,
    SummaryLLMUnavailable,
    _completion_message,
    _fallback_kind_for,
)

pytestmark = pytest.mark.regression

RUN_DATA = {
    "build_number": "1042",
    "branch": "main",
    "total_tests": 20,
    "failed_tests": 4,
    "passed_tests": 16,
    "pass_rate": 80.0,
}


def _resolved(endpoints: dict, tier: str | None = None):
    from app.services.agent_config_resolver import ResolvedAgentConfig
    from app.services.agent_config_service import default_config

    config = default_config("agent.summary.v1")
    if tier is not None:
        config = config.model_copy(update={"model": config.model.model_copy(update={"tier": tier})})
    return ResolvedAgentConfig(
        agent_id="agent.summary.v1", source="default", config_version=0, config=config,
        endpoints=endpoints, offline_mode=True, offline_mode_source="env", offline_mode_env_pinned=True,
    )


def _endpoint(model: str):
    from app.services.agent_config_resolver import ResolvedEndpoint

    return ResolvedEndpoint(
        provider="ollama", model=model, temperature=0.1, max_tokens=2048,
        base_url="http://127.0.0.1:11434", source="project",
    )


def _tiered(monkeypatch, resolved) -> SummaryLLMUnavailable:
    agent = SummaryAgent()
    monkeypatch.setattr(agent, "_routing_inputs", AsyncMock(return_value=(resolved, 1.0, 10)))
    monkeypatch.setattr(agent, "log_decision", AsyncMock())
    with pytest.raises(SummaryLLMUnavailable) as excinfo:
        asyncio.run(agent._generate_tiered_report(
            run_data=RUN_DATA, anomaly_summary="", anomalies=[], analyses={}, similar_failures=[],
            stage_quality="normal", stage_errors={}, pipeline_run_id="p",
            project_id="00000000-0000-0000-0000-000000000001", test_run_id="r", failed_test_ids=[],
        ))
    return excinfo.value


def _report(kind: str, error: str | None = "tier") -> dict:
    return SummaryAgent()._build_fallback_structured_report(
        RUN_DATA, "", [], {}, error_message=error, fallback_kind=kind,
    )


@pytest.mark.parametrize("kind", ["no_model", "refused", "budget"])
def test_a_fallback_no_failed_model_caused_never_says_the_configured_llm_was_unavailable(kind):
    report = _report(kind)
    text = report["layer1_executive_summary"]
    assert FALLBACK_SENTENCES[kind] in text
    assert "configured LLM was unavailable" not in text
    plan = str(report["layer4_action_plan"])
    assert "Ollama" not in plan
    assert "LLM model is available" not in plan


def test_a_configured_model_that_failed_keeps_the_unavailable_sentence_and_its_advice():
    report = _report("model_failed", error="model not found (404)")
    assert FALLBACK_SENTENCES["model_failed"] in report["layer1_executive_summary"]
    assert "LLM model is available" in str(report["layer4_action_plan"])


def test_refused_by_the_settings_points_at_the_ai_settings():
    assert "Check the AI settings" in str(_report("refused")["layer4_action_plan"])


def test_the_progress_message_makes_the_same_choice():
    assert _completion_message(None, "no_model") == "Structured test run summary generated"
    for kind in ("no_model", "refused", "budget", "model_failed"):
        assert _completion_message("why", kind) == FALLBACK_PROGRESS[kind]
    assert all("LLM" not in FALLBACK_PROGRESS[k] for k in ("no_model", "refused", "budget"))


@pytest.mark.parametrize(
    ("reason", "endpoints", "kind"),
    [
        ("tier", {"slm": 1, "llm": 1}, "no_model"),
        ("budget", {"slm": 1, "llm": 1}, "budget"),
        ("step_llm_budget", {"slm": None, "llm": 1}, "budget"),
        ("llm_unavailable", {"slm": None, "llm": None}, "refused"),
        ("llm_unavailable", {"slm": None, "llm": 1}, "model_failed"),
    ],
)
def test_the_router_reason_maps_to_a_fallback_kind(reason, endpoints, kind):
    assert _fallback_kind_for(reason, endpoints) == kind


def test_the_rules_tier_is_no_model(monkeypatch):
    resolved = _resolved({"slm": _endpoint("small"), "llm": _endpoint("large")}, tier="deterministic")
    assert _tiered(monkeypatch, resolved).fallback_kind == "no_model"


def test_no_endpoint_at_all_is_refused_by_the_settings(monkeypatch):
    # Provider and model always have defaults, so no endpoint means the AI settings refused it
    # (offline mode, the provider allow-list, a refused base URL), not "nothing configured".
    assert _tiered(monkeypatch, _resolved({"slm": None, "llm": None})).fallback_kind == "refused"


def test_rules_mode_turns_a_failed_model_into_no_model(monkeypatch):
    monkeypatch.setattr("app.services.analysis_router.get_analysis_mode", lambda: "rules")
    assert summary_agent._analysis_mode_is_rules() is True
    monkeypatch.setattr("app.services.analysis_router.get_analysis_mode", lambda: "llm")
    assert summary_agent._analysis_mode_is_rules() is False
