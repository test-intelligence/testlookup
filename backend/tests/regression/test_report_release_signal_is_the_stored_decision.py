"""The report's release signal is the stored decision, never the model's pick (D-145, S-B-5).

One build showed three answers: the gate Go (the stored decision), the Evidence
report No-Go (the summary model's own ``release_impact``, chosen freely and
different on each regeneration), and a Conditional chip whenever the model
omitted or garbled the field (the schema defaulted to CONDITIONAL_GO).

Now:
* the summary stage stores no release verdict (it runs before the
  release-risk stage, so it has none to report), whatever the model wrote;
* the deterministic fallback guesses none from the pass rate either;
* the read sets ``executive_panel.status_signal`` and
  ``layer2_incident.release_impact`` from the stored ``ReleaseDecision``, and
  clears both when there is none, including in summaries stored before this fix.
"""
from __future__ import annotations

import inspect
import json
from unittest.mock import AsyncMock

import pytest

from app.agents.summary_agent import SummaryAgent
from app.services.run_intelligence_service import apply_stored_release_signal, get_run_intelligence

_LAYERS = {
    "structured incident view": json.dumps({
        "what_failed": "Checkout flow",
        "likely_cause": "Null pointer in the checkout handler",
        "criticality": "HIGH",
        "release_impact": "NO_GO",
    }),
    "evidence pack": json.dumps({
        "top_stack_traces": ["NullPointerException at Checkout.java:42"],
        "log_anomalies": [],
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


class _LLM:
    async def ainvoke(self, prompt, *_args, **_kwargs):
        text = prompt if isinstance(prompt, str) else str(prompt)
        for marker, payload in _LAYERS.items():
            if marker in text:
                return _Resp(payload)
        return _Resp("Executive summary text.")


@pytest.mark.asyncio
async def test_the_models_release_impact_is_not_stored(monkeypatch):
    agent = SummaryAgent()
    agent.log_decision = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr("app.agents.summary_agent.get_llm", AsyncMock(return_value=_LLM()))

    structured = await agent._generate_structured_report(
        run_data={"build_number": "42", "total_tests": 10, "failed_tests": 2, "pass_rate": 80.0},
        anomaly_summary="Pass rate dropped.",
        anomalies=[],
        analyses={"tc-a": {"test_name": "checkout", "failure_category": "PRODUCT_BUG",
                           "root_cause_summary": "Null pointer in the checkout handler."}},
        pipeline_run_id="pipe-d145",
    )

    layer2 = structured["layer2_incident_view"]
    assert layer2["what_failed"] == "Checkout flow", "the rest of the model's layer is kept"
    assert layer2["release_impact"] is None
    assert structured["executive_panel"]["status_signal"] is None


def _stored_summary(signal="CONDITIONAL_GO", impact="NO_GO"):
    return {
        "layer2_incident": {"what_failed": "Checkout", "release_impact": impact},
        "executive_panel": {"headline": "2 failed", "status_signal": signal, "risk_score": None},
    }


def test_the_read_serves_the_stored_decision():
    summary = _stored_summary()
    apply_stored_release_signal(summary, {"recommendation": "GO", "risk_score": 18})

    assert summary["executive_panel"]["status_signal"] == "GO"
    assert summary["executive_panel"]["risk_score"] == 18
    assert summary["layer2_incident"]["release_impact"] == "GO"
    assert summary["layer2_incident"]["what_failed"] == "Checkout"


def test_without_a_stored_decision_the_report_has_no_signal():
    """An old summary's model guess (or CONDITIONAL_GO default) is never served."""
    summary = _stored_summary()
    apply_stored_release_signal(summary, None)

    assert summary["executive_panel"]["status_signal"] is None
    assert summary["layer2_incident"]["release_impact"] is None


def test_no_summary_and_a_summary_without_the_fields_are_left_alone():
    apply_stored_release_signal(None, {"recommendation": "GO"})
    summary = {"layer2_incident": {"what_failed": "x"}, "executive_panel": None}
    apply_stored_release_signal(summary, {"recommendation": "GO"})
    assert summary == {"layer2_incident": {"what_failed": "x"}, "executive_panel": None}


def test_the_intelligence_read_uses_it():
    src = inspect.getsource(get_run_intelligence)
    assert "apply_stored_release_signal(structured_summary, release_decision)" in src
    assert 'release_decision.get("recommendation", panel.get("status_signal"))' not in src
