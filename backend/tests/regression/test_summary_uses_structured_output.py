"""Regression guard: the summary asks the provider for its schema (F-4).

The measurement
---------------
Homelab, 2026-08-22, over 1,557 LLM calls:

    summary              796 calls   57 schema failures   7.16%
    root_cause_analysis  294 calls    0
    all other stages     467 calls    0

Every recorded schema failure in the pipeline came from this one stage, which
also makes half of all LLM calls. Recovering JSON from prose with a regex is
what makes those 57 possible -- so this is where provider-native structured
output pays, and the only stage where a before/after can be measured.

What is guarded
---------------
* the structured path is tried first, and its result is used without touching
  the regex parser;
* **every** failure mode falls back to the prose path rather than losing the
  report -- unsupported provider, transport error, timeout, or an unexpected
  response shape. This deployment has already run three different models, so
  "the provider cannot do it" is a live case, not a hypothetical;
* a structured success is recorded as a decision, so a later drop in schema
  failures can be attributed to this change rather than to the model having a
  good day.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("asyncpg")

from app.agents.summary_agent import SummaryAgent  # noqa: E402
from app.models.llm_schemas import IncidentView  # noqa: E402


class _Structured:
    """A provider that supports with_structured_output."""

    def __init__(self, result):
        self._result = result
        self.prose_calls = 0

    def with_structured_output(self, _schema):
        outer = self

        class _Bound:
            async def ainvoke(self, _prompt, *_a, **_kw):
                if isinstance(outer._result, Exception):
                    raise outer._result
                return outer._result

        return _Bound()

    async def ainvoke(self, _prompt, *_a, **_kw):
        self.prose_calls += 1
        class _R:
            content = '{"what_failed": "from prose", "likely_cause": "regex path"}'
        return _R()


class _NoStructuredSupport(_Structured):
    def with_structured_output(self, _schema):
        raise NotImplementedError("provider does not support structured output")


def _agent():
    agent = SummaryAgent()
    agent.log_decision = AsyncMock()  # type: ignore[method-assign]
    return agent


async def _call(llm):
    agent = _agent()
    payload = await agent._call_json_layer(
        llm,
        "{system}\n{context}",
        "context",
        expected_keys=["what_failed"],
        layer_name="incident_view",
        pipeline_run_id="p1",
    )
    return agent, payload


# ── The happy path ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_structured_output_is_used_and_skips_the_regex_parser():
    llm = _Structured(IncidentView(what_failed="checkout", likely_cause="npe"))
    agent, payload = await _call(llm)

    assert payload["what_failed"] == "checkout"
    assert llm.prose_calls == 0, "the prose path must not run when structured succeeds"

    points = [c.kwargs.get("decision_point") for c in agent.log_decision.call_args_list]
    assert "summary_structured_output" in points, "adoption must be measurable"


@pytest.mark.asyncio
async def test_a_dict_response_is_accepted():
    """Some providers hand back a plain dict rather than the model."""
    llm = _Structured({"what_failed": "checkout", "likely_cause": "npe"})
    _, payload = await _call(llm)
    assert payload["what_failed"] == "checkout"


# ── Every failure mode falls back rather than losing the report ──────────────


@pytest.mark.asyncio
async def test_an_unsupported_provider_falls_back_to_prose():
    """Live case, not hypothetical — this deployment runs three models."""
    llm = _NoStructuredSupport(None)
    _, payload = await _call(llm)

    assert payload["what_failed"] == "from prose"
    assert llm.prose_calls == 1


@pytest.mark.asyncio
async def test_a_transport_error_falls_back_to_prose():
    llm = _Structured(RuntimeError("connection reset"))
    _, payload = await _call(llm)

    assert payload["what_failed"] == "from prose"
    assert llm.prose_calls == 1


@pytest.mark.asyncio
async def test_a_timeout_falls_back_to_prose():
    llm = _Structured(TimeoutError("too slow"))
    _, payload = await _call(llm)

    assert payload["what_failed"] == "from prose"


@pytest.mark.asyncio
async def test_an_unexpected_shape_falls_back_to_prose():
    """A provider that returns a string despite being asked for a schema."""
    llm = _Structured("just some text")
    _, payload = await _call(llm)

    assert payload["what_failed"] == "from prose"
    assert llm.prose_calls == 1


@pytest.mark.asyncio
async def test_a_layer_with_no_schema_goes_straight_to_prose():
    agent = _agent()
    llm = _Structured(IncidentView(what_failed="unused"))
    payload = await agent._call_json_layer(
        llm, "{system}\n{context}", "context",
        layer_name="unmapped_layer", pipeline_run_id="p1",
    )

    assert llm.prose_calls == 1
    assert isinstance(payload, dict)
