"""Sanitized, deterministic pilot corpus for RegressionWatchman.

The corpus exercises the policy boundary without a live database or provider:
history is supplied as the authoritative baseline projection and the
deterministic classifier is evaluated directly.  Live baseline quality and
provider latency remain separate enablement gates.
"""
from __future__ import annotations

import json

import pytest


def _corpus() -> tuple[list[dict], dict, dict]:
    clusters = [
        {
            "cluster_id": "new-product",
            "label": "product failure password=hunter2",
            "representative_error": "Authorization: Bearer sk-secret-value",
            "member_test_ids": ["test-new"],
        },
        {
            "cluster_id": "flaky-retry",
            "member_test_ids": ["test-flaky-1", "test-flaky-2"],
        },
        {
            "cluster_id": "infra-outage",
            "member_test_ids": ["test-infra-1", "test-infra-2"],
        },
        {
            "cluster_id": "insufficient-history",
            "member_test_ids": ["test-unknown"],
        },
    ]
    analyses = {
        "test-new": {"failure_category": "ASSERTION", "is_flaky": False},
        "test-flaky-1": {"failure_category": "ASSERTION", "is_flaky": True},
        "test-flaky-2": {"failure_category": "ASSERTION", "is_flaky": True},
        "test-infra-1": {"failure_category": "INFRASTRUCTURE", "is_flaky": False},
        "test-infra-2": {"failure_category": "INFRASTRUCTURE", "is_flaky": False},
        "test-unknown": {"failure_category": "ASSERTION", "is_flaky": False},
    }
    history = {
        "new-product": {
            "seen_in_baseline": False,
            "recent_occurrences": 0,
            "baseline_run_count": 5,
        },
        "flaky-retry": {
            "seen_in_baseline": True,
            "recent_occurrences": 3,
            "baseline_run_count": 5,
        },
        "infra-outage": {
            "seen_in_baseline": True,
            "recent_occurrences": 1,
            "baseline_run_count": 5,
        },
        "insufficient-history": {
            "seen_in_baseline": False,
            "recent_occurrences": 0,
            "baseline_run_count": 1,
        },
    }
    return clusters, analyses, history


def test_regression_watchman_deterministic_policy_corpus():
    from app.agents.regression_watchman import RegressionWatchman

    clusters, analyses, history = _corpus()
    result = RegressionWatchman()._deterministic_classify(  # noqa: SLF001
        clusters, analyses, history
    )

    assert result["new-product"]["classification"] == "new_regression"
    assert result["new-product"]["confidence"] >= 80
    assert result["flaky-retry"]["classification"] == "known_flaky_recurrence"
    assert result["infra-outage"]["classification"] == "environmental_anomaly"
    assert result["insufficient-history"]["reason_code"] == "LOW_EVIDENCE"
    assert result["insufficient-history"]["confidence"] < 70


@pytest.mark.asyncio
async def test_regression_watchman_enabled_disabled_and_privacy_projection(monkeypatch):
    from app.agents import workflow

    clusters, analyses, history = _corpus()
    classification = workflow._regression_watchman._deterministic_classify(  # noqa: SLF001
        clusters, analyses, history
    )

    async def run(_state):
        return {
            "regression_classification": classification,
            "agent_contracts": {"regression_watchman": {"schema_version": 1}},
        }

    monkeypatch.setattr(workflow._regression_watchman, "run", run)
    state = {
        "pipeline_run_id": "pipeline-eval",
        "project_id": "project-eval",
        "test_run_id": "run-eval",
        "failed_test_ids": ["test-new"],
        "failure_clusters": clusters,
        "regression_watchman_enabled": True,
    }
    enabled = await workflow.regression_watchman_node(state)
    assert set(enabled["regression_classification"]) == {
        "new-product", "flaky-retry", "infra-outage", "insufficient-history",
    }

    disabled = await workflow.regression_watchman_node(
        {**state, "regression_watchman_enabled": False}
    )
    assert disabled["regression_classification"] == {}

    serialized = json.dumps(enabled, sort_keys=True)
    assert "password=hunter2" not in serialized
    assert "Authorization:" not in serialized
    assert "sk-secret-value" not in serialized
