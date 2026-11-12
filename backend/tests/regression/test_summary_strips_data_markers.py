"""F-OR07-2 (owner: "fix F-OR07-2 in W31's PR", 2026-11-04): no data-block marker reaches a reader, and the inline
data line says the supplied failures carry no release signal.

On the reference K3s install's live model (invocation f86f192d, 2026-11-04) the inline summary read "the <<data>>or07a walk
sample<</data>> test in the <<data>>walk<</data>> suite" -- the model copied the markers ``untrusted_input`` wraps
caller values in -- and closed "This indicates a potential release readiness issue" about failures the caller
supplied. The markers are now removed from the structured report at the one point both the model path and the
fallback pass through, before it is stored or returned; a caller's value can never contain them (``neutralise``
turns ``<<`` into ``‹‹``), so nothing the caller wrote is lost.
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")

from tests.test_agent_invoke_inline import _inline, _resolved_summary_config, llm_gates  # noqa: F401,E402 -- fixture

pytestmark = pytest.mark.regression


def _all_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)


@pytest.mark.parametrize(("raw", "clean"), [
    ("the <<data>>or07a walk sample<</data>> test", "the or07a walk sample test"),
    ("the <<data>> or07a walk sample <</data>> test", "the or07a walk sample test"),
    ("in the << DATA >>walk<< / Data >> suite", "in the walk suite"),
    ("line one <<data>>\nline two<</data>>", "line one \nline two"),
    ("no markers here", "no markers here"),
    ("a caller's ‹‹data›› stays", "a caller's ‹‹data›› stays"),
    ("x << y >> z", "x << y >> z"),
])
def test_strip_removes_every_marker_variant_and_nothing_else(raw, clean):
    from app.agents.summary_agent import _strip_data_markers

    assert _strip_data_markers(raw) == clean


def test_strip_walks_nested_reports_and_keeps_other_values():
    from app.agents.summary_agent import _strip_data_markers

    report = {"layer1": "the <<data>>x<</data>> test", "layer4": {"steps": ["<<data>>a<</data>>", 3, None]}, "n": 2}
    assert _strip_data_markers(report) == {"layer1": "the x test", "layer4": {"steps": ["a", 3, None]}, "n": 2}


def test_the_markers_it_strips_are_the_ones_the_boundary_writes():
    from app.agents.summary_agent import _DATA_MARKER
    from app.services.inline_input_vocab import UNTRUSTED_CLOSE, UNTRUSTED_OPEN
    from app.services.untrusted_input import wrap_untrusted

    assert _DATA_MARKER.fullmatch(UNTRUSTED_OPEN) and _DATA_MARKER.fullmatch(UNTRUSTED_CLOSE)
    wrapped = wrap_untrusted("a value that tries <<data>> and <</data>>", limit=200)
    # The boundary's own markers are the only ones in a wrapped value: the caller's were neutralised.
    assert len(_DATA_MARKER.findall(wrapped)) == 2


def test_the_inline_data_line_says_the_failures_carry_no_release_signal():
    from app.agents.summary_agent import _format_results_line
    from app.services.inline_invocation import prepare, seed_state

    payload, _sha = prepare(_inline())
    line = _format_results_line(seed_state(payload, build_number="b", branch="main")["test_run_data"])
    assert line.endswith("says nothing about whether the build is ready to release.")
    assert "%" not in line


@pytest.mark.asyncio
async def test_a_model_that_echoes_the_markers_reaches_no_reader(monkeypatch, llm_gates):  # noqa: F811
    """Through the REAL BudgetedLLM wrapper: every layer the scripted model answers echoes the markers."""
    from langchain_core.language_models import FakeListChatModel

    from app.agents import summary_agent as summary_module
    from app.agents.summary_agent import SummaryAgent
    from app.services.inline_invocation import STATE_INPUT_KEY, prepare, seed_state
    from app.services.llm_factory import BudgetedLLM
    from app.services.pipeline_budget_service import reset_pipeline_budget_context, set_pipeline_budget_context

    echoing = json.dumps({
        "what_failed": "The <<data>>checkout pays with a saved card<</data>> test in the <<data>>payments<</data>> suite.",
        "likely_cause": "<<data>>A null card lookup.<</data>>", "criticality": "HIGH", "release_impact": "NO_GO",
        "top_stack_traces": [], "log_anomalies": ["<<data>>GET /health 503<</data>>"],
        "data_sources_used": ["inline_input"], "immediate_mitigation": "Retry the <<data>>checkout<</data>> step.",
        "fix_recommendations": ["Guard the <<data>>card<</data>> lookup."],
        "validation_steps": ["Re-run <<data>>payments<</data>>."], "evidence_ids": [],
    })
    wrapper = BudgetedLLM(FakeListChatModel(responses=[echoing]), provider="openrouter", model="scripted")

    async def get_llm(*_a, **_k):
        return wrapper

    def no_mongo():
        raise AssertionError("an inline summary must not touch run_summaries")

    monkeypatch.setattr(summary_module, "get_llm", get_llm)
    monkeypatch.setattr(summary_module, "get_mongo_db", no_mongo)
    payload, sha = prepare(_inline())
    state = {
        "pipeline_run_id": str(uuid.uuid4()), "test_run_id": str(uuid.uuid4()), "project_id": str(uuid.uuid4()),
        "anomalies": [], "anomaly_summary": None, "stage_errors": {}, "stage_quality": "normal",
        **seed_state(payload, build_number="1042", branch="main"), STATE_INPUT_KEY: sha,
    }
    agent = SummaryAgent()
    for hook in ("mark_stage_running", "mark_stage_done", "broadcast_progress", "log_decision"):
        monkeypatch.setattr(agent, hook, AsyncMock())
    monkeypatch.setattr(agent, "_routing_inputs", AsyncMock(return_value=(_resolved_summary_config(), 1.0, 10)))
    monkeypatch.setattr("app.services.agent_memory_service.recall_similar", AsyncMock(return_value=[]))
    monkeypatch.setattr("app.services.semantic_search.semantic_search", AsyncMock(return_value=([], 0, 0)))

    token = set_pipeline_budget_context(stage_name="summary")
    try:
        result = await agent.run(state)
    finally:
        reset_pipeline_budget_context(token)

    assert result.get("errors") == [], result.get("errors")
    assert (result.get("summary_provenance") or {}).get("fallback_used") is False, "the model path ran"
    texts = [result.get("executive_summary") or "", result.get("summary_markdown") or ""]
    texts += list(_all_strings(result.get("structured_summary") or {}))
    assert any("checkout pays with a saved card" in text for text in texts), "the model's words were kept"
    leaks = [text for text in texts if "<<" in text and "data" in text.lower()]
    assert leaks == [], leaks
