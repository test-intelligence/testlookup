"""Regression guard: the three JSON summary layers are in flight together (F-6).

The finding, and what measuring changed
---------------------------------------
F-6 read: *"four sequential calls over one identical ~2.6k-token context.
Roughly 4x the necessary input tokens and 4x the latency."* Half of that
survives measurement.

Homelab telemetry, 30 days, 1,191 ``summary`` stages:

    stages that made all four LLM calls      308  (25.9%)   p50  41.2s
    stages that made NONE                    880  (73.9%)   p50   0.22s
    p50 input tokens across all four calls  2,365  ->  ~591 per call

So the ~2.6k figure is the **stage total**, not the per-call context: the four
calls together send about what the finding attributed to each one. That matters
because the token half of the fix -- reordering the prompts so the shared
system+context block becomes a cacheable prefix -- needs that prefix to clear
the provider's minimum cacheable length, and that floor is 1,024 tokens on the
providers offering caching at all. The ENTIRE per-call prompt averages 591 here,
so no prefix inside it reaches the floor at any ordering. A reorder would have
re-versioned five prompts and re-hashed every attestation to buy nothing
measurable, so it is deliberately not done.

The latency half is real, and is what this guards: three independent calls that
cost the SUM of three round-trips where the slowest alone would do, on a quarter
of runs that take a p50 of 41 seconds.

What is guarded
---------------
* the three JSON layers are genuinely concurrent -- proved by a rendezvous all
  three must reach, not by a stopwatch;
* each layer's payload lands in its OWN slot (one tuple unpack over a gather
  makes a silent transposition possible where three sequential assignments
  could not);
* one layer failing does not cancel its siblings, which is the default
  ``gather`` behaviour this code must not have;
* layer 1 still gates the other three -- an unreachable provider costs exactly
  one call, not four issued at once;
* every layer still records its own decisions; a concurrent writer dropping
  them is the same class of defect as #803.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("asyncpg")

from app.agents.summary_agent import SummaryAgent, SummaryLLMUnavailable  # noqa: E402

# Long enough that a loaded machine cannot fake a pass, short enough that the
# sequential regression fails in one wait rather than three: the first waiter
# breaks the barrier, so the other two raise immediately.
_RENDEZVOUS_TIMEOUT = 2.0

_LAYER_PAYLOADS = {
    "structured incident view": json.dumps({
        "what_failed": "Checkout flow",
        "likely_cause": "Null pointer in the checkout handler",
        "criticality": "HIGH",
        "release_impact": "NO_GO",
    }),
    "evidence pack": json.dumps({
        "top_stack_traces": ["java.lang.NullPointerException at Checkout.java:42"],
        "log_anomalies": ["upstream timeout after 30s"],
        "data_sources_used": ["stacktrace"],
    }),
    "action plan": json.dumps({
        "immediate_mitigation": "Roll back the checkout change",
        "fix_recommendations": ["Guard the null branch"],
        "validation_steps": ["Re-run the checkout suite"],
    }),
}


class _Resp:
    def __init__(self, content: str):
        self.content = content


def _match_layer(text: str) -> str | None:
    for marker, payload in _LAYER_PAYLOADS.items():
        if marker in text:
            return payload
    return None


class _RendezvousLLM:
    """Every JSON layer must arrive before any of them may leave.

    A rendezvous rather than a stopwatch: no sleep to tune, no threshold a
    loaded CI box can trip. Sequential code cannot satisfy it at all -- the
    first arrival waits for two peers that will not be issued until it returns.

    No structured-output support, so this exercises the prose path. That is not
    a gap: both paths run inside the same ``_call_json_layer`` coroutine, and it
    is the overlap of those coroutines that is under test.
    """

    def __init__(self, parties: int = 3):
        self._parties = parties
        self._barrier: asyncio.Barrier | None = None
        self._in_flight = 0
        self.max_in_flight = 0
        self.serialized = False
        self.prompts: list[str] = []

    async def ainvoke(self, prompt, *_args, **_kwargs):
        text = prompt if isinstance(prompt, str) else str(prompt)
        self.prompts.append(text)
        payload = _match_layer(text)
        if payload is None:
            # Layer 1 is issued before the fan-out and is not a participant.
            return _Resp("Executive summary text.")

        if self._barrier is None:
            self._barrier = asyncio.Barrier(self._parties)
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            await asyncio.wait_for(self._barrier.wait(), timeout=_RENDEZVOUS_TIMEOUT)
        except (TimeoutError, asyncio.BrokenBarrierError):
            self.serialized = True
        finally:
            self._in_flight -= 1
        return _Resp(payload)


class _ScriptedLLM:
    """Canned payload per layer, with an optional per-layer failure."""

    def __init__(self, fail_marker: str | None = None, fail_layer_one: bool = False):
        self._fail_marker = fail_marker
        self._fail_layer_one = fail_layer_one
        self.prompts: list[str] = []

    async def ainvoke(self, prompt, *_args, **_kwargs):
        text = prompt if isinstance(prompt, str) else str(prompt)
        self.prompts.append(text)
        payload = _match_layer(text)
        if payload is None:
            if self._fail_layer_one:
                raise RuntimeError("model not pulled")
            return _Resp("Executive summary text.")
        if self._fail_marker and self._fail_marker in text:
            raise RuntimeError("provider rejected this layer")
        return _Resp(payload)


def _agent() -> SummaryAgent:
    agent = SummaryAgent()
    agent.log_decision = AsyncMock()  # type: ignore[method-assign]
    return agent


async def _generate(monkeypatch, llm):
    agent = _agent()
    monkeypatch.setattr(
        "app.agents.summary_agent.get_llm", AsyncMock(return_value=llm)
    )
    structured = await agent._generate_structured_report(
        run_data={"build_number": "42", "total_tests": 10, "failed_tests": 2,
                  "pass_rate": 80.0},
        anomaly_summary="Pass rate dropped.",
        anomalies=[],
        analyses={
            "tc-alpha": {
                "test_name": "checkout_flow",
                "confidence_score": 90,
                "failure_category": "PRODUCT_BUG",
                "root_cause_summary": "Null pointer in the checkout handler.",
            },
        },
        pipeline_run_id="pipe-f6",
    )
    return structured, agent


# -- The layers overlap ------------------------------------------------------


@pytest.mark.asyncio
async def test_the_three_json_layers_are_in_flight_together(monkeypatch):
    """The whole finding, in one assertion the sequential code cannot pass."""
    llm = _RendezvousLLM()

    structured, _agent_used = await _generate(monkeypatch, llm)

    assert not llm.serialized, (
        "the JSON layers did not reach the rendezvous together -- they are "
        "running one after another again, and the stage costs the sum of "
        "three round-trips instead of the slowest one"
    )
    assert llm.max_in_flight == 3
    # ...and it is still a complete report, not three timed-out blanks.
    assert structured["layer2_incident_view"]["what_failed"] == "Checkout flow"


@pytest.mark.asyncio
async def test_each_layer_lands_in_its_own_slot(monkeypatch):
    """Three sequential assignments could not be transposed; one tuple unpack
    over a gather can, and the swap would be invisible -- every layer is a dict
    and every layer would still be populated."""
    structured, _ = await _generate(monkeypatch, _ScriptedLLM())

    assert structured["layer2_incident_view"]["likely_cause"].startswith("Null pointer")
    assert structured["layer3_evidence_pack"]["data_sources_used"] == ["stacktrace"]
    assert structured["layer4_action_plan"]["immediate_mitigation"].startswith("Roll back")


# -- Failure isolation -------------------------------------------------------


@pytest.mark.asyncio
async def test_one_failing_layer_does_not_cancel_the_others(monkeypatch):
    """gather cancels siblings when a child raises. It must never come to that
    here: _call_json_layer absorbs the failure into an empty layer, so a
    provider that rejects one prompt still leaves a usable report."""
    structured, _ = await _generate(
        monkeypatch, _ScriptedLLM(fail_marker="evidence pack")
    )

    failed_layer = structured["layer3_evidence_pack"]
    assert not any(
        failed_layer.get(key)
        for key in ("top_stack_traces", "log_anomalies", "data_sources_used")
    ), "the rejected layer must carry no content, only the grounding scaffold"
    assert structured["layer2_incident_view"]["what_failed"] == "Checkout flow"
    assert structured["layer4_action_plan"]["fix_recommendations"]


@pytest.mark.asyncio
async def test_layer_one_still_gates_the_other_three(monkeypatch):
    """An invoke-level failure fails identically for layers 2-4, so the bail-out
    must still happen BEFORE the fan-out. Issuing three doomed calls at once is
    quicker than issuing them in sequence and no less wasteful."""
    llm = _ScriptedLLM(fail_layer_one=True)
    agent = _agent()
    monkeypatch.setattr(
        "app.agents.summary_agent.get_llm", AsyncMock(return_value=llm)
    )

    with pytest.raises(SummaryLLMUnavailable):
        await agent._generate_structured_report(
            run_data={"total_tests": 1, "failed_tests": 1, "pass_rate": 0.0},
            anomaly_summary="",
            anomalies=[],
            analyses={},
            pipeline_run_id="pipe-f6-dead",
        )

    assert len(llm.prompts) == 1, "the JSON layers were issued after layer 1 failed"


# -- Nothing is dropped in the crossing --------------------------------------


@pytest.mark.asyncio
async def test_every_layer_still_reports_its_own_decisions(monkeypatch):
    """Concurrent writers appending to one decision list is exactly the shape
    that lost evidence in #803. Each layer's parse decision must still arrive."""

    class _Unparseable(_ScriptedLLM):
        async def ainvoke(self, prompt, *_args, **_kwargs):
            text = prompt if isinstance(prompt, str) else str(prompt)
            self.prompts.append(text)
            if _match_layer(text) is None:
                return _Resp("Executive summary text.")
            return _Resp("not json at all")

    _structured, agent = await _generate(monkeypatch, _Unparseable())

    layers_reported = {
        call.kwargs["context"]["layer"]
        for call in agent.log_decision.await_args_list  # type: ignore[attr-defined]
        if call.kwargs.get("decision_point") == "summary_schema_validation"
        and isinstance(call.kwargs.get("context"), dict)
    }
    assert layers_reported == {"incident_view", "evidence_pack", "action_plan"}
