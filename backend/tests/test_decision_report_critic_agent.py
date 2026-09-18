from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.agents.decision_report_agent import build_decision_intelligence
from app.agents.decision_report_agent import compute_decision_evidence_hash
from app.agents.decision_report_critic_agent import (
    DecisionReportCriticAgent,
    _attach_durable_verification,
    _missing_contracts,
    _snapshot_verification_state,
    _verify_snapshot_artifacts,
    evaluate_decision_report,
    review_and_repair_decision_report,
)
from app.services.run_evidence_bundle import build_run_evidence_bundle


@pytest.mark.asyncio
async def test_failure_attempt_and_summary_hold_the_subject_lock(monkeypatch):
    from app.agents import decision_report_critic_agent as critic_module

    locked = False

    @asynccontextmanager
    async def subject_lock(_test_run_id):
        nonlocal locked
        locked = True
        try:
            yield
        finally:
            locked = False

    async def record_attempt(*_args, **_kwargs):
        assert locked, "failure attempt escaped the TestRun share lock"
        return {
            "attempt_id": "attempt-1",
            "status": "rejected",
            "attempted_at": "2026-09-18T00:00:00+00:00",
            "supersedes_report_id": None,
        }

    class Collection:
        async def update_one(self, *_args, **_kwargs):
            assert locked, "failure summary escaped the TestRun share lock"

    class Mongo:
        def __getitem__(self, _name):
            return Collection()

    monkeypatch.setattr(critic_module, "lock_decision_report_subject", subject_lock)
    monkeypatch.setattr(critic_module, "get_mongo_db", lambda: Mongo())
    monkeypatch.setattr(
        critic_module,
        "record_decision_report_attempt",
        AsyncMock(side_effect=record_attempt),
    )

    agent = DecisionReportCriticAgent.__new__(DecisionReportCriticAgent)
    await agent._persist_failure(_state(), "critic failed closed")

    assert locked is False


def _state(**overrides):
    def contract(stage):
        return {
            "schema_version": 1,
            "agent_name": stage,
            "confidence_score": 90,
            "fallback_used": False,
            "decision_reason": "test_evidence",
            "output_keys": ["result"],
            "evidence_refs": [{"type": "test", "id": stage}],
        }

    state = {
        "pipeline_run_id": "pipeline-1",
        "test_run_id": "run-1",
        "project_id": "project-1",
        "test_run_data": {
            "total_tests": 3,
            "passed_tests": 1,
            "failed_tests": 2,
            "pass_rate": 33.3,
        },
        "failed_test_ids": ["a", "b"],
        "analyses": {
            "a": {"root_cause_summary": "assertion", "confidence_score": 90},
            "b": {"root_cause_summary": "timeout", "confidence_score": 80},
        },
        "failure_clusters": [
            {"cluster_id": "c1", "member_test_ids": ["a", "b"]},
        ],
        "flaky_findings": [{"test_case_id": "a"}],
        "test_health_findings": [{"test_case_id": "b", "health_score": 70}],
        "release_decision": {"recommendation": "NO_GO", "risk_score": 80},
        "structured_summary": {"layer2_incident_view": {"release_impact": "GO"}},
        "summary_markdown": "# Preliminary\n\n## Final Decision Intelligence\n\nDraft",
        "stage_errors": {},
        "completed_stages": [
            "flaky_sentinel", "test_health", "release_risk", "decision_report",
        ],
        "agent_contracts": {
            stage: contract(stage)
            for stage in (
                "flaky_sentinel", "test_health", "release_risk", "decision_report"
            )
        },
    }
    state.update(overrides)
    state.setdefault("decision_intelligence", build_decision_intelligence(state))
    return state


def test_clean_report_passes_all_critic_checks():
    state = _state()
    checks = evaluate_decision_report(state, state["decision_intelligence"])

    assert checks
    assert all(item["status"] == "pass" for item in checks)


@pytest.mark.asyncio
async def test_published_report_emits_release_webhook_with_exact_evidence(
    monkeypatch,
):
    from app.agents import decision_report_critic_agent as critic_module
    from app.services import release_decision_webhook

    evidence_hash = "e" * 64
    collection = type(
        "Collection",
        (),
        {"update_one": AsyncMock(return_value=None)},
    )()

    class _Mongo:
        def __getitem__(self, _name):
            return collection

    monkeypatch.setattr(critic_module, "get_mongo_db", lambda: _Mongo())
    monkeypatch.setattr(
        critic_module,
        "publish_decision_report",
        AsyncMock(
            return_value={
                "report_id": "report-1",
                "report_version": 1,
                "supersedes_report_id": None,
                "status": "published",
                "generated_at": "2026-09-17T00:00:00+00:00",
                "evidence_bundle_sha256": evidence_hash,
            }
        ),
    )
    emit = AsyncMock(return_value=1)
    monkeypatch.setattr(release_decision_webhook, "emit_release_decided", emit)

    agent = DecisionReportCriticAgent.__new__(DecisionReportCriticAgent)
    await agent._persist(_state(), {"verification": {"status": "passed"}}, "# report")

    emit.assert_awaited_once_with(
        "run-1",
        trigger=release_decision_webhook.TRIGGER_AGENT,
        pipeline_run_id="pipeline-1",
        evidence_bundle_sha256=evidence_hash,
    )


def test_bounded_repair_restores_metrics_release_disclosures_and_hash():
    state = _state()
    draft = build_decision_intelligence(state)
    draft["metrics"]["failed_tests"] = 99
    draft["release_decision"] = {"recommendation": "GO", "risk_score": 0}
    draft["quality_review"]["contradictions"] = []
    draft["evidence_bundle_sha256"] = "tampered"

    final = review_and_repair_decision_report(state, draft)

    assert final["metrics"]["failed_tests"] == 2
    assert final["release_decision"]["recommendation"] == "NO_GO"
    assert final["quality_review"]["contradictions"]
    assert final["quality_review"]["requires_human_review"] is True
    assert final["verification"]["status"] == "passed"
    assert final["verification"]["repair_attempted"] is True
    assert "recompute_evidence_fingerprint" in final["verification"]["repairs"]


def test_tampered_specialist_payload_with_self_consistent_hash_is_repaired():
    state = _state()
    draft = build_decision_intelligence(state)
    draft["flaky_findings"][0]["recommendation"] = "IGNORE_FAILURES"
    draft["evidence_bundle_sha256"] = compute_decision_evidence_hash(draft)

    final = review_and_repair_decision_report(state, draft)

    assert final["flaky_findings"] == state["flaky_findings"]
    assert "restore_specialist_payloads" in final["verification"]["repairs"]
    assert final["verification"]["status"] == "passed"
    assert final["evidence_bundle_sha256"] == compute_decision_evidence_hash(final)


def test_quality_and_source_stage_tampering_is_repaired():
    state = _state()
    draft = build_decision_intelligence(state)
    draft["quality_review"]["requires_human_review"] = False
    draft["quality_review"]["gap_report"] = {"coverage_ratio": 1.0}
    draft["source_stages"] = ["untrusted_stage"]

    final = review_and_repair_decision_report(state, draft)

    assert final["quality_review"]["requires_human_review"] is True
    assert final["quality_review"]["gap_report"] is None
    assert final["source_stages"] == build_decision_intelligence(state)["source_stages"]
    assert "restore_canonical_decision_payload" in final["verification"]["repairs"]
    assert final["verification"]["status"] == "passed"
    assert final["evidence_bundle_sha256"] == compute_decision_evidence_hash(final)


def test_referential_repair_removes_orphan_specialist_items():
    state = _state()
    draft = build_decision_intelligence(state)
    draft["failure_clusters"].append({"cluster_id": "orphan", "member_test_ids": ["x"]})
    draft["deep_findings"]["orphan"] = {"root_cause": "fabricated"}
    draft["flaky_findings"].append({"test_case_id": "x"})

    final = review_and_repair_decision_report(state, draft)

    assert [item["cluster_id"] for item in final["failure_clusters"]] == ["c1"]
    assert "orphan" not in final["deep_findings"]
    assert [item["test_case_id"] for item in final["flaky_findings"]] == ["a"]
    assert final["verification"]["status"] == "passed"


def test_unrepairable_missing_completed_agent_contract_degrades_report():
    state = _state(agent_contracts={})
    final = review_and_repair_decision_report(state, state["decision_intelligence"])

    assert final["status"] == "degraded"
    assert final["verification"]["status"] == "failed"
    assert final["verification"]["unresolved_failures"] == [
        "completed_agent_contracts_present"
    ]
    assert final["evidence_bundle_sha256"] == compute_decision_evidence_hash(final)


def test_malformed_completed_agent_contract_degrades_report():
    state = _state()
    state["agent_contracts"]["release_risk"] = {"schema_version": 1}

    final = review_and_repair_decision_report(state, state["decision_intelligence"])

    assert final["status"] == "degraded"
    assert "agent_contracts_well_formed" in final["verification"]["unresolved_failures"]


def test_boolean_confidence_score_is_not_accepted_as_an_integer():
    state = _state()
    state["agent_contracts"]["release_risk"]["confidence_score"] = True
    final = review_and_repair_decision_report(state, state["decision_intelligence"])
    assert "agent_contracts_well_formed" in final["verification"]["unresolved_failures"]


def test_contract_validation_error_marker_is_rejected():
    state = _state()
    state["agent_contracts"]["release_risk"]["contract_validation_error"] = "bad"
    final = review_and_repair_decision_report(state, state["decision_intelligence"])
    assert "agent_contracts_well_formed" in final["verification"]["unresolved_failures"]


def test_unsigned_live_decision_contract_cannot_satisfy_signed_authority():
    state = _state()
    snapshot = {
        "verification_context": {
            "known_test_ids": ["a", "b"],
            "completed_stages": ["flaky_sentinel", "test_health", "release_risk"],
            "skipped_stages": [],
            "agent_contracts": {
                key: value
                for key, value in state["agent_contracts"].items()
                if key != "decision_report"
            },
        }
    }
    authority = _snapshot_verification_state(snapshot)
    authority["completed_stages"].append("decision_report")

    assert _missing_contracts(authority) == ["decision_report"]
    assert "decision_report" in state["agent_contracts"]


def test_durable_critic_rejects_report_metrics_not_bound_to_frozen_snapshot():
    state = _state()
    authoritative = build_decision_intelligence(state)
    draft = build_decision_intelligence(state)
    draft["metric_snapshot"]["values"]["total_tests"] = 999
    snapshot = {
        "schema_version": 2,
        "content_sha256": "s" * 64,
        "signature_key_id": "key-1",
        "run_evidence_bundle": build_run_evidence_bundle(state),
    }

    final = _attach_durable_verification(
        draft,
        authority_state=state,
        authoritative_decision=authoritative,
        snapshot=snapshot,
        snapshot_metadata={"content_sha256": "s" * 64},
        policy_replay={"status": "passed"},
        release_record={"status": "passed"},
    )

    assert final["verification"]["status"] == "failed"
    assert "metric_snapshot_binding" in final["verification"]["unresolved_failures"]


@pytest.mark.asyncio
async def test_critic_agent_persists_verified_report(monkeypatch):
    state = _state(
        authorized_evidence_artifacts=[], evidence_authorization_errors=[]
    )
    state["decision_evidence_snapshot"] = {"content_sha256": "s" * 64}
    snapshot = {
        "schema_version": 3,
        "content_sha256": "s" * 64,
        "signature_key_id": "key-1",
        "canonical_decision": build_decision_intelligence(state),
        "run_evidence_bundle": build_run_evidence_bundle(state),
        "verification_context": {
            "known_test_ids": ["a", "b"],
            "completed_stages": state["completed_stages"],
            "skipped_stages": [],
            "agent_contracts": state["agent_contracts"],
        },
    }
    agent = DecisionReportCriticAgent()
    monkeypatch.setattr(agent, "mark_stage_running", AsyncMock())
    monkeypatch.setattr(agent, "mark_stage_done", AsyncMock())
    monkeypatch.setattr(agent, "broadcast_progress", AsyncMock())
    monkeypatch.setattr(agent, "log_decision", AsyncMock())
    monkeypatch.setattr(
        "app.agents.decision_report_critic_agent.load_decision_evidence_snapshot",
        AsyncMock(return_value=snapshot),
    )
    monkeypatch.setattr(
        "app.agents.decision_report_critic_agent.replay_frozen_release_policy",
        AsyncMock(return_value={"status": "passed", "checks": []}),
    )
    monkeypatch.setattr(
        "app.agents.decision_report_critic_agent.compare_persisted_release_record",
        AsyncMock(return_value={"status": "passed"}),
    )
    persist = AsyncMock()
    monkeypatch.setattr(agent, "_persist", persist)

    result = await agent.run(state)

    assert result["decision_report_verification"]["status"] == "passed"
    assert result["decision_report_verification"]["report_evaluation"]["status"] in {
        "pass", "warn", "fail"
    }
    assert result["structured_summary"]["decision_intelligence"]["verification"]["status"] == "passed"
    assert result["summary_markdown"].count("## Final Decision Intelligence") == 1
    assert result["agent_contracts"]["decision_report_critic"]["confidence_score"] == 95
    persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_critic_exception_persists_failure_marker(monkeypatch):
    state = _state()
    agent = DecisionReportCriticAgent()
    monkeypatch.setattr(agent, "mark_stage_running", AsyncMock())
    monkeypatch.setattr(agent, "mark_stage_done", AsyncMock())
    monkeypatch.setattr(agent, "broadcast_progress", AsyncMock())
    failure_marker = AsyncMock()
    monkeypatch.setattr(agent, "_persist_failure", failure_marker)

    def fail_review(*_args, **_kwargs):
        raise RuntimeError("critic unavailable")

    monkeypatch.setattr(
        "app.agents.decision_report_critic_agent.review_and_repair_decision_report",
        fail_review,
    )

    result = await agent.run(state)

    assert result["decision_report_verification"]["status"] == "failed"
    assert result["decision_intelligence"] is None
    failure_marker.assert_awaited_once()


@pytest.mark.asyncio
async def test_artifact_resolution_failure_rejects_publication(monkeypatch):
    state = _state(
        authorized_evidence_artifacts=[], evidence_authorization_errors=[]
    )
    state["decision_evidence_snapshot"] = {"content_sha256": "s" * 64}
    snapshot = {
        "schema_version": 3,
        "content_sha256": "s" * 64,
        "signature_key_id": "key-1",
        "canonical_decision": build_decision_intelligence(state),
        "run_evidence_bundle": build_run_evidence_bundle(state),
        "verification_context": {
            "known_test_ids": ["a", "b"],
            "completed_stages": state["completed_stages"],
            "skipped_stages": [],
            "agent_contracts": state["agent_contracts"],
        },
    }
    agent = DecisionReportCriticAgent()
    for method in ("mark_stage_running", "broadcast_progress", "log_decision"):
        monkeypatch.setattr(agent, method, AsyncMock())
    mark_done = AsyncMock()
    publish = AsyncMock()
    reject = AsyncMock()
    monkeypatch.setattr(agent, "mark_stage_done", mark_done)
    monkeypatch.setattr(agent, "_persist", publish)
    monkeypatch.setattr(agent, "_persist_failure", reject)
    monkeypatch.setattr(
        "app.agents.decision_report_critic_agent.load_decision_evidence_snapshot",
        AsyncMock(return_value=snapshot),
    )
    monkeypatch.setattr(
        "app.agents.decision_report_critic_agent.replay_frozen_release_policy",
        AsyncMock(return_value={"status": "passed", "checks": []}),
    )
    monkeypatch.setattr(
        "app.agents.decision_report_critic_agent.compare_persisted_release_record",
        AsyncMock(return_value={"status": "passed"}),
    )
    monkeypatch.setattr(
        "app.agents.decision_report_critic_agent.verify_bundle_evidence_artifacts",
        AsyncMock(return_value={
            "status": "failed",
            "failures": ["artifact_unresolved"],
            "verified_count": 0,
        }),
    )

    result = await agent.run(state)

    assert result["decision_report_verification"]["status"] == "failed"
    assert "authorized_evidence_artifacts" in result[
        "decision_report_verification"
    ]["unresolved_failures"]
    publish.assert_not_awaited()
    reject.assert_awaited_once()
    assert mark_done.await_args.kwargs["error"] == (
        "terminal decision verification failed closed"
    )


@pytest.mark.asyncio
async def test_policy_replay_mismatch_rejects_publication(monkeypatch):
    state = _state(
        authorized_evidence_artifacts=[], evidence_authorization_errors=[]
    )
    state["decision_evidence_snapshot"] = {"content_sha256": "s" * 64}
    snapshot = {
        "schema_version": 3,
        "content_sha256": "s" * 64,
        "signature_key_id": "key-1",
        "canonical_decision": build_decision_intelligence(state),
        "run_evidence_bundle": build_run_evidence_bundle(state),
        "verification_context": {
            "known_test_ids": ["a", "b"],
            "completed_stages": ["flaky_sentinel", "test_health", "release_risk"],
            "skipped_stages": [],
            "agent_contracts": {
                key: value
                for key, value in state["agent_contracts"].items()
                if key != "decision_report"
            },
        },
    }
    agent = DecisionReportCriticAgent()
    for method in ("mark_stage_running", "broadcast_progress", "log_decision"):
        monkeypatch.setattr(agent, method, AsyncMock())
    mark_done = AsyncMock()
    monkeypatch.setattr(agent, "mark_stage_done", mark_done)
    publish = AsyncMock()
    reject = AsyncMock()
    monkeypatch.setattr(agent, "_persist", publish)
    monkeypatch.setattr(agent, "_persist_failure", reject)
    monkeypatch.setattr(
        "app.agents.decision_report_critic_agent.load_decision_evidence_snapshot",
        AsyncMock(return_value=snapshot),
    )
    monkeypatch.setattr(
        "app.agents.decision_report_critic_agent.replay_frozen_release_policy",
        AsyncMock(return_value={"status": "failed", "checks": []}),
    )
    monkeypatch.setattr(
        "app.agents.decision_report_critic_agent.compare_persisted_release_record",
        AsyncMock(return_value={"status": "passed"}),
    )

    result = await agent.run(state)

    assert result["decision_report_verification"]["status"] == "failed"
    assert "release_policy_replay" in result["decision_report_verification"][
        "unresolved_failures"
    ]
    assert result["errors"] == ["terminal decision verification failed closed"]
    assert mark_done.await_args.kwargs["error"] == (
        "terminal decision verification failed closed"
    )
    publish.assert_not_awaited()
    reject.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_snapshot_v2_is_readable_but_cannot_be_newly_published():
    result = await _verify_snapshot_artifacts({
        "schema_version": 2,
        "run_evidence_bundle": {"schema_version": 1},
    })
    assert result["status"] == "failed"
    assert result["mode"] == "legacy_read_only"
    assert result["failures"] == ["legacy_evidence_not_artifact_authorized"]


@pytest.mark.asyncio
async def test_new_snapshot_requires_authorized_bundle_v2():
    result = await _verify_snapshot_artifacts({
        "schema_version": 3,
        "run_evidence_bundle": {"schema_version": 1},
    })
    assert result["status"] == "failed"
    assert result["failures"] == ["artifact_authority_schema_mismatch"]
