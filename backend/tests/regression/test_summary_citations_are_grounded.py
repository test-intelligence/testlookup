"""Regression guard: narrative claims cite a server-built evidence catalogue.

The defect (F-3)
----------------
``summary_assembler.extract_citations`` attached an evidence item only when the
first 40 characters of its excerpt appeared **verbatim** in the generated prose,
and it ran on layer 3 alone. A model that paraphrases -- which is what a summary
is for -- produced an empty citation list, so the layers a reader actually acts
on (incident view, action plan) could not carry a citation at all, and layer 3
only did when the model happened to copy text.

The report therefore rendered an "evidence pack" whose citation array was
almost always empty, while reading as though its claims were sourced.

What is guarded
---------------
* every claim-bearing layer resolves its ``evidence_ids`` against the catalogue
  THIS server built, so citations reach the incident view and action plan too;
* an id the model invents resolves to nothing, is dropped from the stored
  report, and is COUNTED -- a silent drop and a genuine absence of evidence are
  indistinguishable otherwise;
* the catalogue is built once per run, so an id means the same excerpt in all
  three layer calls;
* the resolved citation carries the SERVER's test_id, which is what makes
  ``agents/consistency.py``'s referential-integrity check structural rather
  than hopeful.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("asyncpg")

from app.agents.summary_agent import SummaryAgent  # noqa: E402


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    """Returns a canned payload per layer, keyed by a marker in the prompt."""

    def __init__(self, by_marker: dict[str, str]):
        self._by_marker = by_marker
        self.prompts: list[str] = []

    async def ainvoke(self, prompt, *_args, **_kwargs):
        text = prompt if isinstance(prompt, str) else str(prompt)
        self.prompts.append(text)
        for marker, payload in self._by_marker.items():
            if marker in text:
                return _FakeResponse(payload)
        return _FakeResponse("Executive summary text.")


def _analyses():
    return {
        "tc-alpha": {
            "test_name": "checkout_flow",
            "confidence_score": 90,
            "failure_category": "PRODUCT_BUG",
            "root_cause_summary": "Null pointer in the checkout handler.",
            "evidence_references": [
                {"source": "stacktrace", "kind": "tool_observation",
                 "excerpt": "java.lang.NullPointerException at Checkout.java:42"},
            ],
        },
        "tc-beta": {
            "test_name": "payment_api",
            "confidence_score": 85,
            "failure_category": "INFRASTRUCTURE",
            "root_cause_summary": "Upstream timeout.",
            "evidence_references": [
                {"source": "splunk", "kind": "tool_observation",
                 "excerpt": "2026-08-22 ERROR upstream timeout after 30s"},
            ],
        },
    }


def _agent() -> SummaryAgent:
    agent = SummaryAgent()
    agent.log_decision = AsyncMock()  # type: ignore[method-assign]
    return agent


async def _generate(monkeypatch, layers: dict[str, str]):
    agent = _agent()
    fake = _FakeLLM(layers)
    monkeypatch.setattr(
        "app.agents.summary_agent.get_llm", AsyncMock(return_value=fake)
    )
    structured = await agent._generate_structured_report(
        run_data={"build_number": "42", "total_tests": 10, "failed_tests": 2,
                  "pass_rate": 80.0},
        anomaly_summary="Pass rate dropped.",
        anomalies=[],
        analyses=_analyses(),
        pipeline_run_id="pipe-f3",
    )
    return structured, fake


@pytest.mark.asyncio
async def test_every_claim_bearing_layer_can_carry_citations(monkeypatch):
    structured, fake = await _generate(monkeypatch, {
        "structured incident view": json.dumps({
            "what_failed": "Checkout and payment flows.",
            "likely_cause": "A null pointer in the checkout handler.",
            "evidence_ids": ["E1"],
        }),
        "evidence pack": json.dumps({
            "top_stack_traces": ["NullPointerException"],
            "evidence_ids": ["E1", "E2"],
        }),
        "action plan": json.dumps({
            "immediate_mitigation": "Roll back the checkout change.",
            "evidence_ids": ["E2"],
        }),
    })

    # The catalogue reached the model at all.
    assert "[E1]" in fake.prompts[1]

    # ...and citations now land on the two layers that were uncitable before.
    assert structured["layer2_incident_view"]["citations"][0]["ev_id"] == "E1"
    assert structured["layer4_action_plan"]["citations"][0]["ev_id"] == "E2"
    assert len(structured["layer3_evidence_pack"]["citations"]) == 2

    coverage = structured["citation_coverage"]
    assert coverage["layers_cited"] == 3
    assert coverage["ids_resolved"] == 4
    assert coverage["ids_unresolved"] == 0


@pytest.mark.asyncio
async def test_an_invented_id_is_dropped_and_counted(monkeypatch):
    structured, _ = await _generate(monkeypatch, {
        "structured incident view": json.dumps({
            "what_failed": "Checkout broke.",
            # E9 was never in the catalogue this server served.
            "evidence_ids": ["E1", "E9"],
        }),
        "evidence pack": json.dumps({"top_stack_traces": [], "evidence_ids": []}),
        "action plan": json.dumps({"immediate_mitigation": "Roll back."}),
    })

    cited = structured["layer2_incident_view"]["citations"]
    assert [c["ev_id"] for c in cited] == ["E1"], "a fabricated id must not be stored"

    coverage = structured["citation_coverage"]
    assert coverage["ids_unresolved"] == 1
    assert "E9" in coverage["unresolved_sample"]


@pytest.mark.asyncio
async def test_the_raw_model_field_never_reaches_the_stored_report(monkeypatch):
    structured, _ = await _generate(monkeypatch, {
        "structured incident view": json.dumps({
            "what_failed": "Checkout broke.", "evidence_ids": ["E1"],
        }),
        "evidence pack": json.dumps({"evidence_ids": ["E1"]}),
        "action plan": json.dumps({"evidence_ids": ["E1"]}),
    })

    for layer in ("layer2_incident_view", "layer3_evidence_pack", "layer4_action_plan"):
        assert "evidence_ids" not in structured[layer], (
            f"{layer} kept the unresolved model field alongside resolved citations"
        )


@pytest.mark.asyncio
async def test_citations_carry_the_servers_test_id(monkeypatch):
    """What makes consistency.py's referential-integrity check structural."""
    structured, _ = await _generate(monkeypatch, {
        "structured incident view": json.dumps({"evidence_ids": ["E1"]}),
        "evidence pack": json.dumps({"evidence_ids": []}),
        "action plan": json.dumps({"evidence_ids": []}),
    })

    citation = structured["layer2_incident_view"]["citations"][0]
    assert citation["test_id"] in _analyses()


@pytest.mark.asyncio
async def test_a_layer_that_cites_nothing_is_recorded_not_failed(monkeypatch):
    """"Nothing here supports this" is a legitimate answer."""
    structured, _ = await _generate(monkeypatch, {
        "structured incident view": json.dumps({"what_failed": "Checkout broke."}),
        "evidence pack": json.dumps({"top_stack_traces": []}),
        "action plan": json.dumps({"immediate_mitigation": "Roll back."}),
    })

    assert structured["layer2_incident_view"]["citations"] == []
    assert structured["citation_coverage"]["layers_cited"] == 0
    assert structured["citation_coverage"]["ids_unresolved"] == 0
    # The report still exists — an uncited layer is not an error.
    assert structured["layer2_incident_view"]["what_failed"] == "Checkout broke."
