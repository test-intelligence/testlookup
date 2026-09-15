"""E2.2: the agentic Prometheus metric names and bounded label sets."""
from __future__ import annotations

from app.core import metrics


def test_agentic_metric_names_and_labels_are_stable():
    expected = {
        "agent_invocations_total": (
            "testlookup_agent_invocations",
            ("agent", "tier", "status"),
        ),
        "agent_run_transitions_total": (
            "testlookup_agent_run_transitions",
            ("from", "to"),
        ),
        "agent_retry_attempts_total": (
            "testlookup_agent_retry_attempts",
            ("agent", "reason"),
        ),
        "review_requests_total": (
            "testlookup_review_requests",
            ("state",),
        ),
        "model_escalations_total": (
            "testlookup_model_escalations",
            ("agent", "from", "to"),
        ),
    }

    for attribute, (name, labels) in expected.items():
        metric = getattr(metrics, attribute)
        assert metric._name == name
        assert metric._labelnames == labels


def test_existing_breaker_metric_retains_endpoint_isolation():
    assert metrics.llm_circuit_breaker_state._name == (
        "testlookup_llm_circuit_breaker_state"
    )
    assert metrics.llm_circuit_breaker_state._labelnames == (
        "provider",
        "endpoint",
    )
