"""W11 REL-07: the model draft runs through the REAL ``BudgetedLLM`` and falls back honestly.

``get_llm`` is patched in ``release_phase_generator``'s namespace to return
``BudgetedLLM(FakeListChatModel(...), provider="ollama", model="qwen2.5:7b")``
-- the real wrapper, so its budget check, cost reservation (charged to the
release's project), cluster slot and circuit breaker all run; only their
stores are stubbed (the shape of
``tests/regression/test_triage_react_runs_through_the_budget_wrapper.py``).
Never a bare chat model: a mocked ``get_llm`` once hid a crash on every chat
message.

Pinned: a valid reply is the model's draft, normalised item by item; a fenced
reply parses; prose, an empty list, a timeout, an error, a policy refusal and
the rules / ml analysis modes each return the RULES draft with their
``fallback_reason``; the metric is counted once per call with its labels; the
PRD text never reaches a log record; the prompt is registered.
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest
import structlog
from langchain_core.language_models import FakeListChatModel
from prometheus_client import REGISTRY

from app.core.config import settings
from app.core.release_planning import DRAFT_WARNING_TRUNCATED, PRD_LLM_INPUT_CHARS
from app.services import llm_circuit_breaker, llm_cost_reservation as cost
from app.services import release_phase_generator as gen
from app.services.llm_cost_reservation import Reservation
from app.services.llm_factory import BudgetedLLM
from app.services.llm_policy_service import LLMPolicyViolation
from app.services.prompt_registry import get_prompt_text, registry_prompt_ids

PROJECT = "7d0b3c1e-0000-4000-8000-00000000c0de"
PRD = (
    "# Checkout v2\n\n## Overview\nOne page.\n\n## Smoke testing\nRun the smoke pack.\n\n"
    "## Regression (5 days)\nEverything.\n\n## UAT\nFinance signs off.\n"
)
RULES_NAMES = ["Smoke testing", "Regression", "UAT"]


@pytest.fixture
def gates(monkeypatch):
    calls: dict = {"reserve": 0, "scopes": [], "failure": []}

    async def available(*_a, **_k):
        return None

    async def reserve(provider, model, *, input_tokens, max_output_tokens):
        calls["reserve"] += 1
        calls["scopes"].append(cost.current_cost_scope())
        return Reservation(key="k", project_id=PROJECT, estimated_usd=0.0, ttl_seconds=60)

    async def settle(*_a, **_k):
        return None

    async def record_failure(provider, base_url, exc):
        calls["failure"].append(exc)

    async def record_success(provider, base_url):
        return None

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", available)
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(record_failure))
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(record_success))
    monkeypatch.setattr(cost, "reserve", reserve)
    monkeypatch.setattr(cost, "settle", settle)
    return calls


class _Recording(FakeListChatModel):
    """Records the messages it was sent."""

    seen: list = []

    async def ainvoke(self, input, config=None, **kwargs):
        type(self).seen.append(input)
        return await super().ainvoke(input, config, **kwargs)


class _Slow(FakeListChatModel):
    async def ainvoke(self, input, config=None, **kwargs):
        await asyncio.sleep(2)
        return await super().ainvoke(input, config, **kwargs)


class _Broken(FakeListChatModel):
    async def ainvoke(self, input, config=None, **kwargs):
        raise RuntimeError("connection reset by the model server")


def _wrapped(responses, model_cls=FakeListChatModel) -> BudgetedLLM:
    return BudgetedLLM(model_cls(responses=responses), provider="ollama", model="qwen2.5:7b")


def _use(monkeypatch, llm=None, *, raises=None) -> list[int]:
    awaited: list[int] = []

    async def get_llm(*_a, **_k):
        awaited.append(1)
        if raises is not None:
            raise raises
        return llm

    monkeypatch.setattr(gen, "get_llm", get_llm)
    return awaited


def _ctx(mode="auto", offline=False, model="qwen2.5:7b"):
    return gen.DraftContext.from_config(
        {"analysis_mode": mode, "offline_mode": offline, "model": model, "api_key": "sk-never-copied"},
        project_id=PROJECT,
    )


def _drafts(source: str, reason: str) -> float:
    return REGISTRY.get_sample_value(
        "testlookup_release_phase_drafts_total", {"source": source, "fallback_reason": reason}
    ) or 0.0


REPLY = json.dumps({"phases": [
    {"name": " Smoke testing ", "phase_type": "QA testing", "description": "Run the smoke pack.",
     "exit_criteria": "all green", "planned_days": 2},
    {"name": "Regression", "phase_type": "regression", "planned_days": 95},
    {"name": "UAT", "phase_type": "uat", "exit_criteria": "", "planned_days": 5.0},
    {"name": "", "phase_type": "uat"},
    "not an object",
    {"name": "Go-live", "phase_type": "production", "planned_days": True, "extra": "ignored"},
]})


def test_the_context_copies_no_secret_and_defaults_the_mode():
    ctx = _ctx()
    assert (ctx.offline, ctx.analysis_mode, ctx.model_hint, ctx.project_id) == (False, "auto", "qwen2.5:7b", PROJECT)
    assert not hasattr(ctx, "api_key")
    assert gen.DraftContext.from_config({"analysis_mode": "bogus"}, project_id=PROJECT).analysis_mode == "auto"
    assert gen.DraftContext.from_config({"analysis_mode": "LLM"}, project_id=PROJECT).analysis_mode == "llm"


async def test_a_valid_reply_is_the_model_draft_normalised(monkeypatch, gates):
    _Recording.seen = []
    _use(monkeypatch, _wrapped([REPLY], _Recording))
    before = _drafts("llm", "")

    out = await gen.draft_phases(_ctx(), PRD)

    assert out["source"] == "llm"
    assert out["model_name"] == "qwen2.5:7b"
    assert out["fallback_reason"] is None
    assert out["warnings"] == []
    assert [(p["name"], p["phase_type"], p["planned_days"]) for p in out["phases"]] == [
        ("Smoke testing", "qa_testing", 2),
        ("Regression", "qa_testing", None),     # an unknown type -> qa_testing; 95 days -> None
        ("UAT", "uat", 5),
        ("Go-live", "production", None),        # a bool is not a number of days
    ]
    assert out["phases"][0]["exit_criteria_text"] == "all green"
    assert out["phases"][2]["exit_criteria_text"] is None
    assert out["text_chars"] == len(PRD)
    # Through the wrapper's gates, charged to the release's project.
    assert gates["reserve"] == 1
    assert gates["scopes"] == [PROJECT]
    system, human = _Recording.seen[0]
    assert system.content == get_prompt_text("release_phase_draft")
    assert human.content == PRD
    assert _drafts("llm", "") == before + 1


async def test_the_model_is_handed_the_prd_clipped(monkeypatch, gates):
    _Recording.seen = []
    _use(monkeypatch, _wrapped([REPLY], _Recording))
    long_prd = "## Smoke\n" + "x" * (PRD_LLM_INPUT_CHARS + 500)
    await gen.draft_phases(_ctx(), long_prd)
    assert len(_Recording.seen[0][1].content) == PRD_LLM_INPUT_CHARS


async def test_a_fenced_reply_parses(monkeypatch, gates):
    _use(monkeypatch, _wrapped([f"Here you go:\n```json\n{REPLY}\n```"]))
    out = await gen.draft_phases(_ctx(), PRD)
    assert out["source"] == "llm" and len(out["phases"]) == 4


async def test_more_than_30_model_phases_are_truncated_with_the_warning(monkeypatch, gates):
    reply = json.dumps({"phases": [{"name": f"Wave {i}", "phase_type": "qa_testing"} for i in range(35)]})
    _use(monkeypatch, _wrapped([reply]))
    out = await gen.draft_phases(_ctx(), PRD)
    assert out["source"] == "llm" and len(out["phases"]) == 30
    assert out["warnings"] == [DRAFT_WARNING_TRUNCATED]


async def _rules_answer(monkeypatch, ctx, reason, **use):
    before = _drafts("rules", reason)
    out = await gen.draft_phases(ctx, PRD)
    assert out["source"] == "rules"
    assert out["fallback_reason"] == reason
    assert out["model_name"] is None
    assert [p["name"] for p in out["phases"]] == RULES_NAMES
    assert _drafts("rules", reason) == before + 1
    return out


async def test_prose_is_unparsable_and_the_rules_draft_answers(monkeypatch, gates):
    _use(monkeypatch, _wrapped(["The phases are smoke testing, regression and UAT."]))
    await _rules_answer(monkeypatch, _ctx(), "llm_unparsable")


async def test_a_reply_without_the_phases_key_is_unparsable(monkeypatch, gates):
    _use(monkeypatch, _wrapped(['{"steps": [{"name": "Smoke"}]}']))
    await _rules_answer(monkeypatch, _ctx(), "llm_unparsable")


async def test_an_empty_list_is_llm_empty(monkeypatch, gates):
    _use(monkeypatch, _wrapped(['{"phases": []}']))
    await _rules_answer(monkeypatch, _ctx(), "llm_empty")


async def test_only_invalid_items_is_llm_empty(monkeypatch, gates):
    _use(monkeypatch, _wrapped(['{"phases": [{"name": ""}, 3, null]}']))
    await _rules_answer(monkeypatch, _ctx(), "llm_empty")


async def test_a_model_past_the_timeout_is_llm_timeout(monkeypatch, gates):
    monkeypatch.setattr(gen, "PRD_LLM_TIMEOUT_SECONDS", 0.05)
    _use(monkeypatch, _wrapped([REPLY], _Slow))
    await _rules_answer(monkeypatch, _ctx(), "llm_timeout")


async def test_a_failed_call_is_llm_error(monkeypatch, gates):
    _use(monkeypatch, _wrapped([REPLY], _Broken))
    await _rules_answer(monkeypatch, _ctx(), "llm_error")
    assert len(gates["failure"]) == 1   # the wrapper's circuit breaker saw it


async def test_a_policy_refusal_offline_is_offline_mode(monkeypatch, gates):
    _use(monkeypatch, raises=LLMPolicyViolation("openai is not allowed while AI_OFFLINE_MODE is on"))
    await _rules_answer(monkeypatch, _ctx(offline=True), "offline_mode")


async def test_a_policy_refusal_online_is_llm_unavailable(monkeypatch, gates):
    _use(monkeypatch, raises=LLMPolicyViolation("provider not in the allowlist"))
    await _rules_answer(monkeypatch, _ctx(offline=False), "llm_unavailable")


async def test_any_construction_failure_is_llm_unavailable(monkeypatch, gates):
    _use(monkeypatch, raises=RuntimeError("Ollama is not running"))
    await _rules_answer(monkeypatch, _ctx(), "llm_unavailable")


@pytest.mark.parametrize("mode", ["rules", "ml"])
async def test_rules_and_ml_modes_never_ask_the_model(monkeypatch, gates, mode):
    awaited = _use(monkeypatch, _wrapped([REPLY]))
    await _rules_answer(monkeypatch, _ctx(mode=mode), "rules_mode")
    assert awaited == []
    assert gates["reserve"] == 0


async def test_llm_mode_asks_the_model(monkeypatch, gates):
    awaited = _use(monkeypatch, _wrapped([REPLY]))
    out = await gen.draft_phases(_ctx(mode="llm"), PRD)
    assert awaited == [1] and out["source"] == "llm"


async def test_the_model_name_falls_back_to_the_config_hint(monkeypatch, gates):
    _use(monkeypatch, BudgetedLLM(FakeListChatModel(responses=[REPLY])))   # model "unknown"
    out = await gen.draft_phases(_ctx(model="llama3.1:8b"), PRD)
    assert out["model_name"] == "llama3.1:8b"


async def test_the_prd_text_never_reaches_a_log_record(monkeypatch, gates, caplog):
    sentinel = "SENTINEL-PRD-7c41 confidential roadmap"
    prd = f"## Smoke testing\n{sentinel}\n\n## UAT\n{sentinel}\n"
    _use(monkeypatch, _wrapped(["not json at all " + sentinel]))
    with caplog.at_level(logging.DEBUG), structlog.testing.capture_logs() as captured:
        out = await gen.draft_phases(_ctx(), prd)
    assert out["fallback_reason"] == "llm_unparsable"
    assert sentinel not in caplog.text
    assert all(sentinel not in repr(entry) for entry in captured)
    draft_lines = [e for e in captured if e.get("event") == "release_phase_draft"]
    assert len(draft_lines) == 1
    line = draft_lines[0]
    assert line["text_chars"] == len(prd) and line["phases"] == 2
    assert len(line["text_sha256"]) == 12 and line["source"] == "rules"


def test_the_prompt_is_registered_and_frames_the_prd_as_data():
    assert "release_phase_draft" in registry_prompt_ids()
    text = get_prompt_text("release_phase_draft")
    assert "DATA" in text and "untrusted" in text
    assert '{"phases": [' in text
