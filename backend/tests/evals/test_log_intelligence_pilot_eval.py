"""Deterministic Log Intelligence pilot-readiness corpus.

This is a sanitized structural evaluation, not a claim of production quality.
It verifies that enabling the specialist contributes one bounded result per
failure cluster while the disabled path contributes none, and that the
projection contains no raw canary values.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents import workflow as wf


_CORPUS = [
    {"cluster_id": "checkout-timeout", "test_id": "tc-1", "service": "checkout"},
    {"cluster_id": "payments-5xx", "test_id": "tc-2", "service": "payments"},
    {"cluster_id": "profile-cache", "test_id": "tc-3", "service": "profile"},
    {"cluster_id": "search-degraded", "test_id": "tc-4", "service": "search"},
    {"cluster_id": "notifications-lag", "test_id": "tc-5", "service": "notifications"},
    # The sixth entry proves the deterministic five-cluster cap.
    {"cluster_id": "warehouse-overflow", "test_id": "tc-6", "service": "warehouse"},
]


def _state(*, enabled: bool) -> dict:
    return {
        "log_intelligence_enabled": enabled,
        "analyses": {
            item["test_id"]: {
                "service_name": item["service"],
                "timestamp_utc": "2026-06-12T00:00:00Z",
            }
            for item in _CORPUS
        },
        "failure_clusters": [
            {"cluster_id": item["cluster_id"], "member_test_ids": [item["test_id"]]}
            for item in reversed(_CORPUS)
        ],
    }


@pytest.mark.asyncio
async def test_log_pilot_corpus_enabled_contribution_is_bounded_and_disabled_is_empty(
    monkeypatch,
):
    agent = MagicMock()
    agent.investigate = AsyncMock(
        side_effect=lambda service, *_args: {
            "status": "complete",
            "distributed_trace": {
                "causal_summary": f"{service} trace password=hunter2",
                "raw_headers": "Authorization: Bearer sk-secret-value",
            },
            "log_anomaly": {"anomaly_detected": False},
            "log_summary": f"{service} evidence email=user@example.com",
            "evidence_refs": [{"source": "trace", "ref_id": service}],
        }
    )
    monkeypatch.setattr(wf, "_log_intelligence", agent)

    enabled = await wf.log_intelligence_node(_state(enabled=True))
    disabled = await wf.log_intelligence_node(_state(enabled=False))

    findings = enabled["log_findings"]
    assert findings["status"] == "complete"
    assert findings["cluster_count"] == 5
    assert len(findings["cluster_findings"]) == 5
    assert disabled["log_findings"]["status"] == "not_enough_evidence"
    assert disabled["log_findings"]["cluster_findings"] == []
    assert agent.investigate.await_count == 5

    serialized = repr(findings)
    assert "password=hunter2" not in serialized
    assert "Authorization: Bearer" not in serialized
    assert "sk-secret-value" not in serialized
    assert "hunter2" not in serialized
    assert "user@example.com" not in serialized
