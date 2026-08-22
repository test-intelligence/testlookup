from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agents.decision_report_agent import DecisionReportAgent, build_decision_intelligence, compute_decision_evidence_hash
from app.agents.decision_report_critic_agent import review_and_repair_decision_report
from app.services.decision_evidence_snapshot import EvidenceSnapshotConflict


def _state(**overrides):
    state = {
        "pipeline_run_id": "pipeline-1",
        "test_run_id": "run-1",
        "project_id": "project-1",
        "test_run_data": {
            "total_tests": 10,
            "passed_tests": 8,
            "failed_tests": 2,
            "pass_rate": 80.0,
        },
        "total_tests": 10,
        "pass_rate": 80.0,
        "failed_test_ids": ["a", "b"],
        "failure_clusters": [{"cluster_id": "c1", "member_test_ids": ["a", "b"]}],
        "flaky_findings": [{"test_case_id": "a", "recommendation": "MONITOR"}],
        "test_health_findings": [{"test_case_id": "b", "health_score": 55}],
        "release_decision": {"recommendation": "NO_GO", "risk_score": 80},
        "structured_summary": {
            "layer1_executive_summary": "Preliminary summary",
            "layer2_incident_view": {"release_impact": "GO"},
        },
        "summary_markdown": "# Preliminary report",
        "gap_report": None,
        "refined_report": None,
        "stage_errors": {},
    }
    state.update(overrides)
    return state


def test_build_decision_intelligence_includes_late_specialists_and_policy_conflict():
    result = build_decision_intelligence(_state())

    assert result["status"] == "complete"
    assert result["metrics"]["flaky_finding_count"] == 1
    assert result["metrics"]["test_health_finding_count"] == 1
    assert result["metric_snapshot"]["definition_version"] == "run_metrics_v1"
    assert result["metric_snapshot"]["values"] == result["metrics"]
    assert len(result["run_evidence_bundle_sha256"]) == 64
    assert result["deep_findings"]["c1"]["root_cause"] is None
    assert result["release_decision"]["recommendation"] == "NO_GO"
    assert result["quality_review"]["requires_human_review"] is True
    assert result["quality_review"]["contradictions"][0]["resolution"] == "prefer_final_release_policy"
    assert len(result["evidence_bundle_sha256"]) == 64


def test_cluster_child_outcomes_are_hashed_and_degradation_is_disclosed():
    state = _state(cluster_investigation_results={
        "status": "degraded",
        "selected_count": 1,
        "completed_count": 0,
        "stop_reasons": ["cluster_child_failed"],
        "children": [{
            "failure_cluster_id": "cluster-row-1",
            "status": "failed",
            "verdict": {},
        }],
    })
    result = build_decision_intelligence(state)
    original_hash = result["evidence_bundle_sha256"]

    assert result["status"] == "degraded"
    assert "cluster_investigation" in result["quality_review"][
        "missing_or_failed_specialists"
    ]
    assert "cluster_investigation_join" in result["source_stages"]

    tampered = dict(result)
    tampered["cluster_investigation_results"] = {
        **result["cluster_investigation_results"],
        "status": "complete",
    }
    repaired = review_and_repair_decision_report(state, tampered)
    assert repaired["cluster_investigation_results"]["status"] == "degraded"
    assert repaired["evidence_bundle_sha256"] == original_hash


def test_build_decision_intelligence_is_degraded_when_required_output_missing():
    result = build_decision_intelligence(_state(release_decision=None, failure_clusters=[]))

    assert result["status"] == "degraded"
    assert result["quality_review"]["missing_or_failed_specialists"] == [
        "release_risk",
        "failure_clustering",
    ]


def test_zero_metrics_are_not_replaced_by_truthy_fallbacks():
    result = build_decision_intelligence(_state(
        test_run_data={
            "total_tests": 2,
            "passed_tests": 0,
            "failed_tests": 0,
            "broken_tests": 2,
            "pass_rate": 0.0,
        },
        pass_rate=99.0,
    ))

    assert result["metrics"]["failed_tests"] == 0
    assert result["metrics"]["broken_tests"] == 2
    assert result["metrics"]["pass_rate"] == 0.0


@pytest.mark.asyncio
async def test_agent_returns_contracted_draft_for_terminal_critic(monkeypatch):
    agent = DecisionReportAgent()
    monkeypatch.setattr(agent, "mark_stage_running", AsyncMock())
    monkeypatch.setattr(agent, "mark_stage_done", AsyncMock())
    monkeypatch.setattr(agent, "log_decision", AsyncMock())
    monkeypatch.setattr(agent, "broadcast_progress", AsyncMock())
    snapshot = AsyncMock(return_value={
        "schema_version": 1,
        "content_sha256": "s" * 64,
        "status": "persisted",
    })
    monkeypatch.setattr(
        "app.agents.decision_report_agent.persist_decision_evidence_snapshot",
        snapshot,
    )
    result = await agent.run(_state())

    assert result["decision_intelligence"]["release_decision"]["recommendation"] == "NO_GO"
    assert result["structured_summary"]["decision_intelligence"]["metrics"]["flaky_finding_count"] == 1
    assert "## Final Decision Intelligence" in result["summary_markdown"]
    assert result["agent_contracts"]["decision_report"]["confidence_score"] == 95
    assert result["completed_stages"] == ["decision_report"]
    assert result["decision_evidence_snapshot"]["content_sha256"] == "s" * 64
    snapshot.assert_awaited_once()
    signed_state = snapshot.await_args.args[0]
    assert signed_state["agent_contracts"]["decision_report"] == result[
        "agent_contracts"
    ]["decision_report"]


def test_draft_does_not_alias_mutable_specialist_state():
    state = _state()
    result = build_decision_intelligence(state)

    result["flaky_findings"][0]["recommendation"] = "IGNORE_FAILURES"
    result["failure_clusters"][0]["member_test_ids"].append("fabricated")
    result["release_decision"]["recommendation"] = "GO"

    assert state["flaky_findings"][0]["recommendation"] == "MONITOR"
    assert state["failure_clusters"][0]["member_test_ids"] == ["a", "b"]
    assert state["release_decision"]["recommendation"] == "NO_GO"


def test_synthesized_deep_evidence_does_not_alias_analysis_state():
    evidence = [{"source": "log", "reference": "line-1"}]
    state = _state(
        analyses={
            "a": {
                "root_cause_summary": "assertion",
                "evidence_references": evidence,
            }
        },
    )
    result = build_decision_intelligence(state)

    result["deep_findings"]["c1"]["evidence"][0]["source"] = "tampered"

    assert state["analyses"]["a"]["evidence_references"][0]["source"] == "log"


def test_metric_errors_degrade_report_and_require_human_review():
    result = build_decision_intelligence(_state(test_run_data={
        "total_tests": 3,
        "passed_tests": "bad",
        "failed_tests": 2,
        "broken_tests": 0,
        "skipped_tests": 0,
        "unknown_tests": 0,
        "pass_rate": 99,
    }))

    assert result["status"] == "degraded"
    assert result["quality_review"]["requires_human_review"] is True
    assert any(
        item["severity"] == "error"
        for item in result["quality_review"]["data_quality_flags"]
    )
    reviewed = review_and_repair_decision_report(_state(test_run_data={
        "total_tests": 3,
        "passed_tests": "bad",
        "failed_tests": 2,
        "broken_tests": 0,
        "skipped_tests": 0,
        "unknown_tests": 0,
        "pass_rate": 99,
    }), result)
    assert reviewed["verification"]["status"] == "failed"
    assert "metric_data_quality" in reviewed["verification"]["unresolved_failures"]


def test_all_specialist_payloads_are_sanitized_before_persistence():
    result = build_decision_intelligence(_state(
        failure_clusters=[{"cluster_id": "c1", "label": "user@example.com"}],
        deep_findings={"c1": {"root_cause": "password=hunter2"}},
        flaky_findings=[{"test_case_id": "a", "recommendation": "token=abcdefghijklmnopqrst"}],
        test_health_findings=[{"test_case_id": "b", "recommendation": "user@example.com"}],
        release_decision={"recommendation": "NO_GO", "reasoning": "password=hunter2"},
    ))
    serialized = str(result)

    assert "hunter2" not in serialized
    assert "user@example.com" not in serialized
    assert "abcdefghijklmnopqrst" not in serialized
    assert "canonical_payload_redacted" in {
        item["code"] for item in result["quality_review"]["data_quality_flags"]
    }


def test_structured_secret_keys_are_redacted_before_decision_hashing():
    result = build_decision_intelligence(_state(
        deep_findings={"c1": {
            "password": "hunter2",
            "api_key": "short-secret",
            "authorization": "Basic abc",
        }},
    ))
    serialized = str(result)
    assert "hunter2" not in serialized
    assert "short-secret" not in serialized
    assert "Basic abc" not in serialized


@pytest.mark.asyncio
async def test_snapshot_conflict_fails_closed_without_returning_draft(monkeypatch):
    agent = DecisionReportAgent()
    monkeypatch.setattr(agent, "mark_stage_running", AsyncMock())
    monkeypatch.setattr(agent, "mark_stage_done", AsyncMock())
    monkeypatch.setattr(agent, "broadcast_progress", AsyncMock())
    monkeypatch.setattr(
        "app.agents.decision_report_agent.persist_decision_evidence_snapshot",
        AsyncMock(side_effect=EvidenceSnapshotConflict("different content")),
    )

    result = await agent.run(_state())

    assert result["decision_intelligence"] is None
    assert result["decision_evidence_snapshot"] is None
    assert "different content" in result["errors"][0]


def test_contract_findings_are_canonical_decision_evidence():
    state = _state(contract_agent_enabled=True, failed_test_ids=["tc-1"])
    state["contract_findings"] = {
        "status": "complete",
        "violations": [{"test_case_id": "tc-1", "violation_type": "missing_field"}],
        "summary": "contract drift",
    }
    result = build_decision_intelligence(state)
    assert result["contract_findings"]["status"] == "complete"
    assert "contract_validation" in result["source_stages"]
    original_hash = result["evidence_bundle_sha256"]
    result["contract_findings"]["summary"] = "tampered"
    assert compute_decision_evidence_hash(result) != original_hash

def test_log_findings_are_canonical_decision_evidence():
    state = _state(log_intelligence_enabled=True, failed_test_ids=["a", "b"])
    state["log_findings"] = {
        "status": "complete",
        "log_summary": "trace evidence",
        "distributed_trace": {"trace_steps": [{"level": "ERROR"}]},
    }
    result = build_decision_intelligence(state)
    assert result["log_findings"]["status"] == "complete"
    assert "log_intelligence" in result["source_stages"]
    assert result["source_stages"].count("contract_validation") == 0
    original = result["evidence_bundle_sha256"]
    result["log_findings"]["log_summary"] = "tampered"
    assert compute_decision_evidence_hash(result) != original

def test_regression_classification_is_canonical_decision_evidence():
    state = _state(regression_watchman_enabled=True, failed_test_ids=["a"])
    state["failure_clusters"] = [{"cluster_id": "c1", "member_test_ids": ["a"]}]
    state["regression_classification"] = {
        "c1": {"classification": "new_regression", "confidence": 80}
    }
    result = build_decision_intelligence(state)
    assert result["regression_classification"]["c1"]["classification"] == "new_regression"
    assert "regression_watchman" in result["source_stages"]
    original = result["evidence_bundle_sha256"]
    result["regression_classification"]["c1"]["classification"] = "known_flaky_recurrence"
    assert compute_decision_evidence_hash(result) != original

def test_decision_report_exposes_typed_claims_and_actions():
    state = _state()
    state["release_decision"] = {
        "recommendation": "NO_GO",
        "blocking_issues": ["critical regression"],
        "conditions_for_go": ["rerun failed tests"],
    }
    report = build_decision_intelligence(state)
    claims = report["claims"]
    actions = report["proposed_actions"]
    assert {item["kind"] for item in claims} >= {"fact", "inference"}
    assert all(item["evidence"] and item["confidence_basis"] for item in claims)
    assert actions[0]["required_permission"] == "release_review"
    assert actions[0]["idempotency_key"]

def test_typed_claims_bind_to_authorized_artifact_and_metric_references():
    state = _state(pipeline_run_id="00000000-0000-0000-0000-000000000002")
    state["authorized_evidence_artifacts"] = [{
        "artifact_id": "00000000-0000-0000-0000-000000000001",
        "evidence_id": "b" * 64,
        "source": "splunk",
        "kind": "tool_observation",
        "excerpt": "sanitized failure excerpt",
        "checksum_sha256": "a" * 64,
        "producer_pipeline_run_id": "00000000-0000-0000-0000-000000000002",
        "authorization_status": "tenant_run_pipeline_verified",
        "scope": {"project_id": "project-1", "test_run_id": "run-1", "test_case_id": "a"},
        "freshness": "current_run",
        "sensitivity": "internal",
    }]
    report = build_decision_intelligence(state)
    claims = {c["claim_id"]: c for c in report["claims"]}

    # The invariant this test has always protected: an authorized artifact and
    # the metric snapshot both remain citable from the report.
    #
    # What changed (F-16): they no longer land on the SAME claim. Every claim
    # used to carry one identical evidence array, so this assertion passed while
    # no claim cited evidence chosen for it — 10 of 10 published homelab reports
    # had byte-identical evidence on every claim. Artifacts now support the
    # claim that is about them; the metric supports the metric claim.
    artifact_evidence = claims["fact.evidence.captured"]["evidence"]
    assert any(
        item.get("type") == "artifact"
        and item.get("id") == "00000000-0000-0000-0000-000000000001"
        for item in artifact_evidence
    )

    metric_evidence = claims["fact.metrics.test_outcome"]["evidence"]
    assert any(
        item.get("type") == "metric"
        and item.get("definition_version") == "run_metrics_v1"
        for item in metric_evidence
    )

    # And the two are no longer interchangeable.
    assert not any(item.get("type") == "artifact" for item in metric_evidence)