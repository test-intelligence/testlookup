"""The deterministic summary fallback must be REACHABLE when the LLM is dead.

Found on the homelab. `/runs/{id}/intelligence` rendered a run whose stored
summary read:

```
layer1_executive_summary: "Executive summary generation failed.
                           Please refer to the detailed breakdown below."
layer2_incident_view:     {}
layer3_evidence_pack:     {"citations": []}
layer4_action_plan:       {}
```

The report tells the reader to consult a detailed breakdown that is three empty
objects. Cause, live: ``ollama list`` returned zero models while the deployment
was configured for ``qwen2.5:7b``, so every ``ainvoke`` raised
``model "qwen2.5:7b" not found (404)``.

The product already had the right answer for this. ``_build_fallback_structured
_report`` assembles all four layers from stored pipeline evidence — pass rate,
dominant category, real stack traces, an action plan — and even names the
remedy. It has exactly ONE caller: the ``except Exception`` wrapped around
``_generate_structured_report``.

And ``_generate_structured_report`` could not raise. Layer 1 caught
``Exception`` and substituted the placeholder; layers 2-4 went through
``_call_json_layer``, documented as "Returns parsed dict or error stub", which
returns ``{}``. Every failure was absorbed one layer at a time, so the branch
written for exactly this scenario was dead in exactly this scenario.

This is the repo's dead-branch class (cf. ``PerformanceBaseline``/``PerfBaseline``,
``oc_namespace``/``ocp_namespace``) in its costliest form: not a missing feature
but a working one that could never run.

The guard is the CLASS: **a degraded-mode path must be reachable from the
failure it degrades for**, and the product must never emit a summary that
points at a breakdown it left empty.
"""
from __future__ import annotations

import asyncio

import pytest

from app.agents.summary_agent import (
    SummaryAgent,
    SummaryLLMUnavailable,
    _layer_has_content,
)

# The live failure, verbatim from the Ollama pod.
MODEL_MISSING = 'model "qwen2.5:7b" not found, try pulling it first (404)'


class _DeadLLM:
    """Every call raises — a model that was never pulled."""

    def __init__(self, exc: Exception | None = None):
        self.exc = exc or RuntimeError(MODEL_MISSING)
        self.calls = 0

    async def ainvoke(self, _prompt):
        self.calls += 1
        raise self.exc


class _HangingLLM:
    """Every call outlives the layer timeout."""

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, _prompt):
        self.calls += 1
        await asyncio.sleep(3600)


RUN_DATA = {
    "build_number": "1042",
    "branch": "main",
    "total_tests": 20,
    "failed_tests": 4,
    "passed_tests": 16,
    "pass_rate": 80.0,
}

ANALYSES = {
    "tc-1": {
        "failure_category": "INFRASTRUCTURE",
        "root_cause_summary": "payments.internal did not resolve",
        "confidence_score": 88,
        "recommended_actions": ["Check the DNS entry for payments.internal"],
        "evidence_references": [
            {"source": "stack_trace", "excerpt": "java.net.UnknownHostException"}
        ],
    }
}


def _generate(llm, monkeypatch):
    agent = SummaryAgent()
    monkeypatch.setattr(
        "app.agents.summary_agent.get_llm", lambda: _async_return(llm)
    )
    return agent, agent._generate_structured_report(
        run_data=RUN_DATA,
        anomaly_summary="4 tests failed",
        anomalies=[],
        analyses=ANALYSES,
    )


async def _async_return(value):
    return value


# ── The reachability link ───────────────────────────────────────────────────


def test_a_dead_model_makes_the_generator_raise_so_the_fallback_can_run(
    monkeypatch,
):
    """The regression. Absorbing this error is what killed the fallback."""
    llm = _DeadLLM()
    _agent, coro = _generate(llm, monkeypatch)
    with pytest.raises(SummaryLLMUnavailable) as excinfo:
        asyncio.run(coro)
    # The reason must survive — the operator needs to know WHICH model.
    assert "qwen2.5:7b" in str(excinfo.value)


def test_an_unreachable_model_does_not_burn_three_more_doomed_calls(monkeypatch):
    """Layers 2-4 fail identically once the provider is unreachable. With a
    layer timeout in play, retrying them costs the user real wall-clock for a
    result already known to be empty."""
    llm = _DeadLLM()
    _agent, coro = _generate(llm, monkeypatch)
    with pytest.raises(SummaryLLMUnavailable):
        asyncio.run(coro)
    assert llm.calls == 1, f"made {llm.calls} calls to a provider known to be down"


def test_a_total_timeout_also_reaches_the_fallback(monkeypatch):
    """A timeout is not proof the model is unusable, so the later layers still
    get their turn — but if none of them produced anything, four blank layers
    must not be what ships."""
    monkeypatch.setattr("app.agents.summary_agent._LAYER_TIMEOUT_SECONDS", 0.05)
    llm = _HangingLLM()
    _agent, coro = _generate(llm, monkeypatch)
    with pytest.raises(SummaryLLMUnavailable):
        asyncio.run(coro)
    # All four were attempted — a timeout earns the benefit of the doubt.
    assert llm.calls == 4


# ── The payload the fallback delivers ───────────────────────────────────────


def test_the_fallback_fills_every_layer_the_ui_reads():
    """The user-visible half of the bug: "the data will not be present for many
    of the fields". Each layer the intelligence page renders must carry
    content."""
    report = SummaryAgent()._build_fallback_structured_report(
        run_data=RUN_DATA,
        anomaly_summary="4 tests failed",
        anomalies=[],
        analyses=ANALYSES,
        error_message=MODEL_MISSING,
    )
    for key in (
        "layer1_executive_summary",
        "layer2_incident_view",
        "layer3_evidence_pack",
        "layer4_action_plan",
    ):
        assert _layer_has_content(report.get(key)), f"{key} came back empty"


def test_the_fallback_summary_states_real_measured_numbers():
    """A degraded summary is only worth showing if it says something true and
    specific. Pinning the numbers stops it decaying into a generic apology."""
    report = SummaryAgent()._build_fallback_structured_report(
        run_data=RUN_DATA,
        anomaly_summary="4 tests failed",
        anomalies=[],
        analyses=ANALYSES,
        error_message=MODEL_MISSING,
    )
    layer1 = report["layer1_executive_summary"]
    assert "1042" in layer1 and "80.0%" in layer1
    breakdown = report["layer2_incident_view"]["failure_breakdown"]
    assert breakdown["infrastructure"] == 1


def test_the_fallback_says_why_it_is_degraded():
    """Silent degradation is how a fallback gets mistaken for a real answer."""
    report = SummaryAgent()._build_fallback_structured_report(
        run_data=RUN_DATA,
        anomaly_summary="4 tests failed",
        anomalies=[],
        analyses=ANALYSES,
        error_message=MODEL_MISSING,
    )
    assert "LLM was unavailable" in report["layer1_executive_summary"]


# ── The contradiction must not be emittable ─────────────────────────────────


def test_no_report_points_at_a_breakdown_it_left_empty(monkeypatch):
    """The precise thing the user saw. Whatever path runs, a summary that
    defers to "the detailed breakdown below" must be accompanied by one."""
    llm = _DeadLLM()
    agent = SummaryAgent()
    monkeypatch.setattr(
        "app.agents.summary_agent.get_llm", lambda: _async_return(llm)
    )
    try:
        report = asyncio.run(
            agent._generate_structured_report(
                run_data=RUN_DATA,
                anomaly_summary="4 tests failed",
                anomalies=[],
                analyses=ANALYSES,
            )
        )
    except SummaryLLMUnavailable as exc:
        report = agent._build_fallback_structured_report(
            run_data=RUN_DATA,
            anomaly_summary="4 tests failed",
            anomalies=[],
            analyses=ANALYSES,
            error_message=str(exc),
        )

    layer1 = str(report["layer1_executive_summary"])
    if "breakdown below" in layer1:
        assert any(
            _layer_has_content(report.get(k))
            for k in ("layer2_incident_view", "layer3_evidence_pack", "layer4_action_plan")
        ), "summary deferred to a breakdown that is entirely empty"


# ── _layer_has_content itself ───────────────────────────────────────────────
#
# The reachability tests all route through this predicate, so a sloppy version
# of it (e.g. `bool(layer)`, true for `{"citations": []}`) would silently stop
# them detecting anything. That shape is exactly what the live run stored.


@pytest.mark.parametrize(
    "layer,expected",
    [
        ({}, False),
        ({"citations": []}, False),          # the live layer3 — empty in substance
        ({"what_failed": "", "scope": None}, False),
        ({"what_failed": "4 tests failed"}, True),
        ({"citations": [], "log_anomalies": ["boom"]}, True),
        ("", False),
        ("a sentence", True),
        (None, False),
    ],
)
def test_layer_content_predicate(layer, expected):
    assert _layer_has_content(layer) is expected
