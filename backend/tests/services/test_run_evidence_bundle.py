from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.models.evidence_contracts import RunEvidenceBundleV1
from app.services.run_evidence_bundle import (
    MAX_EVIDENCE_REFERENCES,
    build_run_evidence_bundle,
    build_run_metric_snapshot,
    validate_run_evidence_bundle,
)
from app.services.canonical_json import stable_json_sha256


def _state(**overrides):
    state = {
        "project_id": "project-1",
        "test_run_id": "run-1",
        "pipeline_run_id": "pipeline-1",
        "build_number": "42",
        "branch": "main",
        "workflow_type": "deep",
        "test_run_data": {
            "total_tests": 4,
            "passed_tests": 2,
            "failed_tests": 1,
            "broken_tests": 1,
            "skipped_tests": 0,
            "unknown_tests": 0,
            "pass_rate": 50.0,
        },
        "failed_test_ids": ["a", "b"],
        "analyses": {
            "a": {
                "evidence_references": [{
                    "source": "stacktrace",
                    "reference_id": "trace-1",
                    "excerpt": "AssertionError",
                }]
            }
        },
        "failure_clusters": [{"cluster_id": "c1", "member_test_ids": ["a", "b"]}],
        "deep_findings": {},
        "flaky_findings": [{"test_case_id": "a"}],
        "test_health_findings": [{"test_case_id": "b"}],
        "release_decision": {"recommendation": "NO_GO", "risk_score": 80},
    }
    state.update(overrides)
    return state


def test_metric_snapshot_preserves_zero_and_has_explicit_definition_and_denominator():
    snapshot = build_run_metric_snapshot(_state(test_run_data={
        "total_tests": 2,
        "passed_tests": 0,
        "failed_tests": 0,
        "broken_tests": 2,
        "skipped_tests": 0,
        "unknown_tests": 0,
        "pass_rate": 0.0,
    }))

    assert snapshot["definition_version"] == "run_metrics_v1"
    assert snapshot["window"] == {"kind": "single_run", "test_run_id": "run-1"}
    assert snapshot["denominators"]["pass_rate"] == 2
    assert snapshot["values"]["failed_tests"] == 0
    assert snapshot["values"]["broken_tests"] == 2
    assert snapshot["values"]["pass_rate"] == 0.0


def test_malformed_and_inconsistent_metrics_emit_quality_flags():
    snapshot = build_run_metric_snapshot(_state(test_run_data={
        "total_tests": 4,
        "passed_tests": "bad",
        "failed_tests": 1,
        "broken_tests": 0,
        "skipped_tests": 0,
        "unknown_tests": 0,
        "pass_rate": 99,
    }))

    codes = {item["code"] for item in snapshot["quality_flags"]}
    assert {"passed_tests_invalid", "outcome_total_mismatch", "pass_rate_mismatch"} <= codes


def test_fractional_counts_and_missing_failure_identities_are_errors():
    snapshot = build_run_metric_snapshot(_state(
        failed_test_ids=[],
        test_run_data={
            "total_tests": 2,
            "passed_tests": 0.5,
            "failed_tests": 1,
            "broken_tests": 1,
            "skipped_tests": 0,
            "unknown_tests": 0,
            "pass_rate": 0,
        },
    ))
    codes = {item["code"] for item in snapshot["quality_flags"]}
    assert "passed_tests_invalid" in codes
    assert "failed_test_identity_mismatch" in codes


def test_bundle_is_deterministic_scoped_bounded_and_does_not_alias_state():
    state = _state()
    first = build_run_evidence_bundle(state)
    second = build_run_evidence_bundle(state)

    assert first == second
    assert validate_run_evidence_bundle(first) == []
    assert first["evidence_refs"][0]["scope"] == {
        "project_id": "project-1",
        "test_run_id": "run-1",
        "test_case_id": "a",
    }
    assert first["evidence_refs"][0]["sensitivity"] == "restricted"
    mutated = deepcopy(first)
    mutated["evidence_refs"][0]["excerpt"] = "tampered"
    assert state["analyses"]["a"]["evidence_references"][0]["excerpt"] == "AssertionError"
    assert "run_evidence_bundle_hash_mismatch" in validate_run_evidence_bundle(mutated)


def test_evidence_reference_extraction_is_bounded_and_disclosed():
    refs = [
        {"source": "log", "reference_id": f"line-{index}", "excerpt": f"x-{index}"}
        for index in range(MAX_EVIDENCE_REFERENCES + 7)
    ]
    bundle = build_run_evidence_bundle(_state(analyses={"a": {"evidence_references": refs}}))

    assert len(bundle["evidence_refs"]) == MAX_EVIDENCE_REFERENCES
    assert bundle["omitted_evidence_count"] == 7
    assert "evidence_reference_limit_reached" in {
        item["code"] for item in bundle["quality_flags"]
    }


def test_evidence_is_redacted_deduplicated_and_preserves_classification():
    raw = {
        "source": "log",
        "reference_id": "https://logs.invalid?q=token=secret-value",
        "excerpt": "user@example.com password=hunter2",
        "sensitivity": "restricted",
        "freshness": "historical",
    }
    bundle = build_run_evidence_bundle(_state(analyses={
        "a": {"evidence_references": [raw, deepcopy(raw)]}
    }))

    assert len(bundle["evidence_refs"]) == 1
    evidence = bundle["evidence_refs"][0]
    assert "hunter2" not in evidence["excerpt"]
    assert "user@example.com" not in evidence["excerpt"]
    assert evidence["uri_or_ref"] is None
    assert evidence["authorization_status"] == "pipeline_scoped_unverified"
    assert evidence["sensitivity"] == "restricted"
    assert evidence["freshness"] == "historical"
    codes = {item["code"] for item in bundle["quality_flags"]}
    assert "evidence_content_redacted" in codes
    assert "duplicate_evidence_references_removed" in codes


@pytest.mark.parametrize("classification", ["RESTRICTED", "confidential", ""])
def test_sensitivity_aliases_and_unknown_values_fail_conservative(classification):
    bundle = build_run_evidence_bundle(_state(analyses={
        "a": {"evidence_references": [{
            "source": "log", "excerpt": "x", "sensitivity": classification,
        }]}
    }))
    assert bundle["evidence_refs"][0]["sensitivity"] == "restricted"


def test_redaction_expansion_is_bounded_after_sanitization():
    bundle = build_run_evidence_bundle(_state(analyses={
        "a": {"evidence_references": [{
            "source": "log", "excerpt": "1.1.1.1 " * 62,
        }]}
    }))
    assert len(bundle["evidence_refs"][0]["excerpt"]) <= 500


def test_malformed_uri_port_does_not_crash_bundle_creation():
    bundle = build_run_evidence_bundle(_state(analyses={
        "a": {"evidence_references": [{
            "source": "log", "reference_id": "https://host.invalid:bad/path",
        }]}
    }))
    assert bundle["evidence_refs"][0]["uri_or_ref"] is None


def test_malformed_references_are_not_mislabeled_as_capacity_omissions():
    bundle = build_run_evidence_bundle(_state(analyses={
        "a": {"evidence_references": [None, {"source": "log", "excerpt": "x"}]}
    }))
    assert bundle["omitted_evidence_count"] == 0
    assert "evidence_reference_invalid" in {
        item["code"] for item in bundle["quality_flags"]
    }


def test_reference_checksum_is_independently_validated():
    bundle = build_run_evidence_bundle(_state())
    bundle["evidence_refs"][0]["excerpt"] = "tampered"
    unsigned = deepcopy(bundle)
    unsigned.pop("content_sha256")
    bundle["content_sha256"] = stable_json_sha256(unsigned)
    assert "evidence_reference_checksum_mismatch" in validate_run_evidence_bundle(bundle)


@pytest.mark.parametrize("malformed", [123, "abc"])
def test_malformed_failed_id_containers_fail_closed_without_iterating(malformed):
    bundle = build_run_evidence_bundle(_state(failed_test_ids=malformed))
    assert bundle["failed_test_ids"] == []
    assert "failed_test_ids_invalid" in {
        item["code"] for item in bundle["quality_flags"]
    }


def test_rehashed_semantically_impossible_metric_snapshot_is_rejected():
    bundle = build_run_evidence_bundle(_state())
    bundle["metric_snapshot"]["values"]["passed_tests"] = -1
    metric_unsigned = deepcopy(bundle["metric_snapshot"])
    metric_unsigned.pop("content_sha256")
    bundle["metric_snapshot"]["content_sha256"] = stable_json_sha256(metric_unsigned)
    bundle_unsigned = deepcopy(bundle)
    bundle_unsigned.pop("content_sha256")
    bundle["content_sha256"] = stable_json_sha256(bundle_unsigned)
    assert validate_run_evidence_bundle(bundle) == ["run_evidence_bundle_schema_invalid"]


def test_reference_outside_failed_identity_set_is_rejected_and_flagged():
    bundle = build_run_evidence_bundle(_state(
        failed_test_ids=["a", "b"],
        analyses={"foreign": {"evidence_references": [{"source": "log"}]}},
    ))

    assert bundle["evidence_refs"] == []
    assert "evidence_scope_invalid" in {item["code"] for item in bundle["quality_flags"]}


def test_contract_is_frozen_and_rejects_unknown_fields():
    bundle = RunEvidenceBundleV1.model_validate(build_run_evidence_bundle(_state()))
    with pytest.raises(ValidationError):
        bundle.project_id = "other"
    with pytest.raises(TypeError):
        bundle.metric_snapshot.values["total_tests"] = 999

    raw = bundle.model_dump(mode="json")
    raw["unknown"] = True
    with pytest.raises(ValidationError):
        RunEvidenceBundleV1.model_validate(raw)

    malformed_hash = bundle.model_dump(mode="json")
    malformed_hash["content_sha256"] = "z" * 64
    with pytest.raises(ValidationError):
        RunEvidenceBundleV1.model_validate(malformed_hash)


def test_authorized_artifacts_build_v2_and_bind_pipeline():
    project_id = "24b97b76-80d0-4c84-87ca-2c6747a96543"
    run_id = "eaf324a1-6036-426d-bf31-55f24b70f3e1"
    pipeline_id = "cfa3031c-f9f3-4c15-b3a6-ad44773dfa30"
    test_id = "922c0d3b-7d0a-4ba7-9120-803ce787daf8"
    checksum = stable_json_sha256({
        "source": "fetch_allure_stacktrace",
        "kind": "tool_observation",
        "uri_or_ref": None,
        "excerpt": "assertion failed",
    })
    state = _state(
        project_id=project_id,
        test_run_id=run_id,
        pipeline_run_id=pipeline_id,
        failed_test_ids=[test_id],
        test_run_data={
            "total_tests": 1, "passed_tests": 0, "failed_tests": 1,
            "skipped_tests": 0, "broken_tests": 0, "unknown_tests": 0,
            "pass_rate": 0.0,
        },
    )
    state["authorized_evidence_artifacts"] = [{
        "schema_version": 2,
        "artifact_id": "6d4b9b9f-4d66-4bde-8243-cf1e61404cb9",
        "evidence_id": stable_json_sha256({
            "project_id": project_id,
            "test_run_id": run_id,
            "test_case_id": test_id,
            "checksum": checksum,
        }),
        "kind": "tool_observation",
        "source": "fetch_allure_stacktrace",
        "scope": {
            "project_id": project_id,
            "test_run_id": run_id,
            "test_case_id": test_id,
        },
        "producer_pipeline_run_id": pipeline_id,
        "checksum_sha256": checksum,
        "freshness": "current_run",
        "sensitivity": "restricted",
        "authorization_status": "tenant_run_pipeline_verified",
        "uri_or_ref": None,
        "excerpt": "assertion failed",
    }]
    state["evidence_authorization_errors"] = []

    bundle = build_run_evidence_bundle(state)

    assert bundle["schema_version"] == 2
    assert bundle["evidence_refs"][0]["producer_pipeline_run_id"] == pipeline_id
    assert validate_run_evidence_bundle(bundle) == []
