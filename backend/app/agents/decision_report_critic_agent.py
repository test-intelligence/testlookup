"""Independent terminal verification and bounded repair for decision reports."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import structlog

from app.agents.base import BaseAgent
from app.agents.decision_report_agent import (
    DECISION_REPORT_SCHEMA_VERSION,
    SUMMARY_DOCUMENT_SCHEMA_VERSION,
    _as_dict,
    _as_dict_list,
    build_decision_intelligence,
    compute_decision_evidence_hash,
    decision_evidence_projection,
    render_decision_markdown,
)
from app.agents.consistency import check_release_consistency
from app.db.mongo import Collections, get_mongo_db
from app.models.agent_contracts import (
    DecisionReportCriticAgentOutput,
    validate_agent_contract,
)
from app.services.decision_evidence_snapshot import load_decision_evidence_snapshot
from app.services.decision_report_service import (
    publish_decision_report,
    record_decision_report_attempt,
)
from app.services.privacy_service import sanitize_for_persistence
from app.services.evidence_artifact_service import verify_bundle_evidence_artifacts
from app.services.release_policy_replay import (
    compare_persisted_release_record,
    replay_frozen_release_policy,
)
from app.services.decision_report_eval_service import evaluate_decision_report_quality

logger = structlog.get_logger("agents.decision_report_critic")

CRITIC_SCHEMA_VERSION = 1
_FINAL_MARKDOWN_MARKER = "## Final Decision Intelligence"


async def _verify_snapshot_artifacts(snapshot: dict[str, Any]) -> dict[str, Any]:
    bundle = _as_dict(snapshot.get("run_evidence_bundle"))
    if snapshot.get("schema_version") == 2 and bundle.get("schema_version") == 1:
        # Deployed legacy snapshots remain readable, but a newly executing
        # critic may not publish them under the stronger artifact-authority
        # claim. Previously published reports remain available via the API.
        return {
            "status": "failed",
            "mode": "legacy_read_only",
            "failures": ["legacy_evidence_not_artifact_authorized"],
            "verified_count": 0,
        }
    if snapshot.get("schema_version") != 3 or bundle.get("schema_version") != 2:
        return {
            "status": "failed",
            "mode": "artifact_authority_required",
            "failures": ["artifact_authority_schema_mismatch"],
            "verified_count": 0,
        }
    return await verify_bundle_evidence_artifacts(bundle)


def _check(name: str, passed: bool, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "status": "pass" if passed else "fail", "detail": detail or {}}


def _known_test_ids(state: dict[str, Any]) -> set[str]:
    return {
        *(str(item) for item in (state.get("failed_test_ids") or [])),
        *(str(item) for item in _as_dict(state.get("analyses"))),
    }


def _referential_violations(
    state: dict[str, Any], decision: dict[str, Any]
) -> dict[str, list[str]]:
    known = _known_test_ids(state)
    if not known:
        return {}
    invalid: dict[str, list[str]] = {}
    cluster_ids: set[str] = set()
    for cluster in _as_dict_list(decision.get("failure_clusters")):
        cluster_id = str(cluster.get("cluster_id") or "")
        if cluster_id:
            cluster_ids.add(cluster_id)
        bad = [str(item) for item in (cluster.get("member_test_ids") or []) if str(item) not in known]
        if bad:
            invalid[f"cluster:{cluster_id or 'unknown'}"] = bad
    for field in ("flaky_findings", "test_health_findings", "contract_findings"):
        bad = [
            str(item.get("test_case_id"))
            for item in _as_dict_list(decision.get(field))
            if item.get("test_case_id") is not None
            and str(item.get("test_case_id")) not in known
        ]
        if bad:
            invalid[field] = bad
    orphan_findings = [
        str(cluster_id)
        for cluster_id in _as_dict(decision.get("deep_findings"))
        if str(cluster_id) not in cluster_ids
    ]
    if orphan_findings:
        invalid["deep_findings"] = orphan_findings
    return invalid


def _missing_contracts(state: dict[str, Any]) -> list[str]:
    contracts = _as_dict(state.get("agent_contracts"))
    completed = {str(item) for item in (state.get("completed_stages") or [])}
    skipped = {str(item) for item in (state.get("skipped_stages") or [])}
    required = {
        stage
        for stage in (
            "summary",
            "failure_clustering",
            "gap_detection",
            "report_refinement",
            "flaky_sentinel",
            "test_health",
            "release_risk",
            "decision_report",
            "contract_validation",
            "log_intelligence",
            "regression_watchman",
            "change_ownership",
        )
        if stage in completed and stage not in skipped
    }
    return sorted(stage for stage in required if not _as_dict(contracts.get(stage)))


def _invalid_contracts(state: dict[str, Any]) -> list[str]:
    contracts = _as_dict(state.get("agent_contracts"))
    invalid: list[str] = []
    for stage, raw in contracts.items():
        contract = _as_dict(raw)
        if not contract:
            continue
        structurally_valid = (
            contract.get("schema_version") == 1
            and contract.get("agent_name") == stage
            and type(contract.get("confidence_score")) is int
            and isinstance(contract.get("fallback_used"), bool)
            and isinstance(contract.get("decision_reason"), str)
            and bool(contract.get("decision_reason"))
            and isinstance(contract.get("output_keys"), list)
            and isinstance(contract.get("evidence_refs"), list)
            and "contract_validation_error" not in contract
        )
        if not structurally_valid:
            invalid.append(str(stage))
    return sorted(invalid)


def _comparison_projection(decision: dict[str, Any]) -> dict[str, Any]:
    """Canonical source payload, excluding conservative critic-owned flags."""
    projection = decision_evidence_projection(decision)
    projection.pop("status", None)
    quality = deepcopy(_as_dict(projection.get("quality_review")))
    quality.pop("requires_human_review", None)
    projection["quality_review"] = quality
    return projection


def evaluate_decision_report(
    state: dict[str, Any],
    decision: dict[str, Any],
    *,
    authoritative_decision: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate a draft without mutating it."""
    authoritative = (
        deepcopy(authoritative_decision)
        if authoritative_decision is not None
        else build_decision_intelligence(state)
    )
    expected_missing = (
        _as_dict(authoritative.get("quality_review")).get("missing_or_failed_specialists")
        or []
    )
    actual_missing = (
        _as_dict(decision.get("quality_review")).get("missing_or_failed_specialists")
        or []
    )
    expected_conflicts = _as_dict(authoritative.get("quality_review")).get("contradictions") or []
    actual_conflicts = _as_dict(decision.get("quality_review")).get("contradictions") or []
    data_quality_errors = [
        str(item.get("code") or "unknown")
        for item in _as_dict_list(
            _as_dict(decision.get("quality_review")).get("data_quality_flags")
        )
        if item.get("severity") == "error"
    ]
    invalid_refs = _referential_violations(state, decision)
    missing_contracts = _missing_contracts(state)
    invalid_contracts = _invalid_contracts(state)
    evidence_fields = (
        "failure_clusters",
        "deep_findings",
        "flaky_findings",
        "test_health_findings",
        "contract_findings",
        "log_findings",
        "regression_classification",
        "change_ownership_findings",
        "cluster_investigation_results",
    )
    changed_evidence_fields = [
        field for field in evidence_fields
        if decision.get(field) != authoritative.get(field)
    ]
    canonical_payload_matches = _comparison_projection(decision) == _comparison_projection(
        authoritative
    )
    release_consistency = check_release_consistency(decision.get("release_decision"))
    release_errors = [
        check.name
        for check in release_consistency.checks
        if not check.passed and check.severity == "error"
    ]
    return [
        _check(
            "schema_supported",
            decision.get("schema_version") == DECISION_REPORT_SCHEMA_VERSION,
            {"observed": decision.get("schema_version"), "expected": DECISION_REPORT_SCHEMA_VERSION},
        ),
        _check(
            "deterministic_metrics_match",
            _as_dict(decision.get("metrics")) == _as_dict(authoritative.get("metrics")),
        ),
        _check(
            "metric_data_quality",
            not data_quality_errors,
            {"error_flags": data_quality_errors},
        ),
        _check(
            "release_policy_match",
            _as_dict(decision.get("release_decision"))
            == _as_dict(authoritative.get("release_decision")),
        ),
        _check(
            "release_decision_internally_consistent",
            not release_errors,
            {"failed_error_checks": release_errors},
        ),
        _check(
            "specialist_payloads_match",
            not changed_evidence_fields,
            {"changed_fields": changed_evidence_fields},
        ),
        _check(
            "canonical_decision_payload_match",
            canonical_payload_matches,
        ),
        _check(
            "evidence_fingerprint_match",
            decision.get("evidence_bundle_sha256") == compute_decision_evidence_hash(decision),
            {
                "expected": compute_decision_evidence_hash(decision),
                "observed": decision.get("evidence_bundle_sha256"),
            },
        ),
        _check(
            "specialist_gaps_disclosed",
            sorted(map(str, actual_missing)) == sorted(map(str, expected_missing)),
            {"expected": expected_missing, "observed": actual_missing},
        ),
        _check(
            "contradictions_disclosed",
            actual_conflicts == expected_conflicts,
            {"expected_count": len(expected_conflicts), "observed_count": len(actual_conflicts)},
        ),
        _check(
            "referential_integrity",
            not invalid_refs,
            {"invalid_references": invalid_refs},
        ),
        _check(
            "completed_agent_contracts_present",
            not missing_contracts,
            {"missing_contracts": missing_contracts},
        ),
        _check(
            "agent_contracts_well_formed",
            not invalid_contracts,
            {"invalid_contracts": invalid_contracts},
        ),
        _check(
            "status_matches_evidence",
            decision.get("status") == authoritative.get("status")
            or decision.get("status") == "degraded",
            {"expected": authoritative.get("status"), "observed": decision.get("status")},
        ),
        _check(
            "human_review_is_conservative",
            bool(_as_dict(decision.get("quality_review")).get("requires_human_review"))
            or not (
                bool(
                    _as_dict(authoritative.get("quality_review")).get(
                        "requires_human_review"
                    )
                )
                or bool(missing_contracts)
            ),
        ),
    ]


def _repair_references(state: dict[str, Any], decision: dict[str, Any]) -> None:
    known = _known_test_ids(state)
    if not known:
        return
    valid_clusters: list[dict[str, Any]] = []
    valid_cluster_ids: set[str] = set()
    for raw in _as_dict_list(decision.get("failure_clusters")):
        cluster = deepcopy(raw)
        members = [str(item) for item in (cluster.get("member_test_ids") or []) if str(item) in known]
        if not members:
            continue
        cluster["member_test_ids"] = members
        valid_clusters.append(cluster)
        if cluster.get("cluster_id"):
            valid_cluster_ids.add(str(cluster["cluster_id"]))
    decision["failure_clusters"] = valid_clusters
    for field in ("flaky_findings", "test_health_findings"):
        decision[field] = [
            item
            for item in _as_dict_list(decision.get(field))
            if item.get("test_case_id") is None or str(item.get("test_case_id")) in known
        ]
    contract = _as_dict(decision.get("contract_findings"))
    if contract:
        contract["violations"] = [
            item for item in _as_dict_list(contract.get("violations"))
            if item.get("test_case_id") is None or str(item.get("test_case_id")) in known
        ]
        decision["contract_findings"] = contract
    decision["deep_findings"] = {
        str(cluster_id): finding
        for cluster_id, finding in _as_dict(decision.get("deep_findings")).items()
        if str(cluster_id) in valid_cluster_ids
    }


def review_and_repair_decision_report(
    state: dict[str, Any],
    draft: dict[str, Any],
    *,
    authoritative_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run critic checks, perform at most one deterministic repair, and recheck."""
    report = deepcopy(draft)
    initial_checks = evaluate_decision_report(
        state,
        report,
        authoritative_decision=authoritative_decision,
    )
    failures = {item["name"] for item in initial_checks if item["status"] == "fail"}
    repairs: list[str] = []
    authoritative = (
        deepcopy(authoritative_decision)
        if authoritative_decision is not None
        else build_decision_intelligence(state)
    )

    if failures:
        if "canonical_decision_payload_match" in failures:
            for field, value in decision_evidence_projection(authoritative).items():
                report[field] = deepcopy(value)
            repairs.append("restore_canonical_decision_payload")
        if "schema_supported" in failures:
            report["schema_version"] = DECISION_REPORT_SCHEMA_VERSION
            repairs.append("reset_schema_version")
        if "deterministic_metrics_match" in failures:
            report["metrics"] = authoritative["metrics"]
            repairs.append("restore_deterministic_metrics")
        if "release_policy_match" in failures:
            report["release_decision"] = authoritative["release_decision"]
            repairs.append("restore_release_policy_decision")
        if "specialist_payloads_match" in failures:
            for field in (
                "failure_clusters",
                "deep_findings",
                "flaky_findings",
                "test_health_findings",
                "contract_findings",
                "log_findings",
                "regression_classification",
                "change_ownership_findings",
                "cluster_investigation_results",
            ):
                report[field] = deepcopy(authoritative[field])
            repairs.append("restore_specialist_payloads")
        if "specialist_gaps_disclosed" in failures:
            report.setdefault("quality_review", {})["missing_or_failed_specialists"] = (
                authoritative["quality_review"]["missing_or_failed_specialists"]
            )
            repairs.append("restore_specialist_gap_disclosure")
        if "contradictions_disclosed" in failures:
            report.setdefault("quality_review", {})["contradictions"] = (
                authoritative["quality_review"]["contradictions"]
            )
            repairs.append("restore_contradiction_disclosure")
        if "referential_integrity" in failures:
            _repair_references(state, report)
            repairs.append("remove_orphan_references")
        if "status_matches_evidence" in failures:
            report["status"] = authoritative["status"]
            repairs.append("restore_evidence_status")
        if failures.intersection({
            "evidence_fingerprint_match",
            "deterministic_metrics_match",
            "release_policy_match",
            "specialist_payloads_match",
            "canonical_decision_payload_match",
            "referential_integrity",
        }):
            report["evidence_bundle_sha256"] = compute_decision_evidence_hash(report)
            repairs.append("recompute_evidence_fingerprint")

    quality = report.setdefault("quality_review", {})
    missing_contracts = _missing_contracts(state)
    invalid_contracts = _invalid_contracts(state)
    quality["requires_human_review"] = bool(
        quality.get("requires_human_review")
        or repairs
        or missing_contracts
        or invalid_contracts
    )
    if missing_contracts or invalid_contracts:
        report["status"] = "degraded"
    report["evidence_bundle_sha256"] = compute_decision_evidence_hash(report)
    final_checks = evaluate_decision_report(
        state,
        report,
        authoritative_decision=authoritative,
    )
    final_failures = [item["name"] for item in final_checks if item["status"] == "fail"]
    if final_failures and report["status"] != "degraded":
        report["status"] = "degraded"
        quality["requires_human_review"] = True
        report["evidence_bundle_sha256"] = compute_decision_evidence_hash(report)
        final_checks = evaluate_decision_report(
            state,
            report,
            authoritative_decision=authoritative,
        )
        final_failures = [item["name"] for item in final_checks if item["status"] == "fail"]
    report["verification"] = {
        "schema_version": CRITIC_SCHEMA_VERSION,
        "status": "passed" if not final_failures else "failed",
        "repair_attempted": bool(failures),
        "repairs": repairs,
        "initial_checks": initial_checks,
        "checks": final_checks,
        "unresolved_failures": final_failures,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    return report


def _snapshot_verification_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    context = _as_dict(snapshot.get("verification_context"))
    known_ids = [str(item) for item in (context.get("known_test_ids") or [])]
    return {
        "failed_test_ids": known_ids,
        "analyses": {},
        "completed_stages": context.get("completed_stages") or [],
        "skipped_stages": context.get("skipped_stages") or [],
        "agent_contracts": context.get("agent_contracts") or {},
    }


def _attach_durable_verification(
    report: dict[str, Any],
    *,
    authority_state: dict[str, Any],
    authoritative_decision: dict[str, Any],
    snapshot: dict[str, Any],
    snapshot_metadata: dict[str, Any],
    policy_replay: dict[str, Any],
    release_record: dict[str, Any],
    artifact_verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact_verification = artifact_verification or {
        "status": "passed",
        "failures": [],
        "verified_count": 0,
    }
    verification = deepcopy(_as_dict(report.get("verification")))
    bundle_metric_snapshot = _as_dict(
        _as_dict(snapshot.get("run_evidence_bundle")).get("metric_snapshot")
    )
    metric_snapshot_bound = (
        bool(bundle_metric_snapshot)
        and _as_dict(report.get("metric_snapshot")) == bundle_metric_snapshot
        and _as_dict(report.get("metrics"))
        == _as_dict(bundle_metric_snapshot.get("values"))
    )
    run_bundle_hash_bound = (
        report.get("run_evidence_bundle_sha256")
        == _as_dict(snapshot.get("run_evidence_bundle")).get("content_sha256")
    )
    external_checks = [
        _check(
            "durable_snapshot_binding",
            snapshot_metadata.get("content_sha256") == snapshot.get("content_sha256"),
            {
                "expected": snapshot_metadata.get("content_sha256"),
                "loaded": snapshot.get("content_sha256"),
            },
        ),
        _check(
            "metric_snapshot_binding",
            metric_snapshot_bound,
            {
                "definition_version": bundle_metric_snapshot.get("definition_version"),
                "content_sha256": bundle_metric_snapshot.get("content_sha256"),
            },
        ),
        _check(
            "run_evidence_bundle_binding",
            run_bundle_hash_bound,
            {
                "expected": _as_dict(snapshot.get("run_evidence_bundle")).get(
                    "content_sha256"
                ),
                "observed": report.get("run_evidence_bundle_sha256"),
            },
        ),
        _check(
            "release_policy_replay",
            policy_replay.get("status") == "passed",
            {"replay": policy_replay},
        ),
        _check(
            "persisted_release_record_match",
            release_record.get("status") == "passed",
            {"comparison": release_record},
        ),
        _check(
            "authorized_evidence_artifacts",
            artifact_verification.get("status") == "passed",
            {"verification": artifact_verification},
        ),
    ]
    authorized_evidence_ids = {
        str(item)
        for item in (
            report.get("run_evidence_bundle_sha256"),
            report.get("evidence_bundle_sha256"),
            snapshot.get("content_sha256"),
            "metric_snapshot",
        )
        if item
    }
    bundle = _as_dict(snapshot.get("run_evidence_bundle"))
    for reference in _as_dict_list(bundle.get("evidence_refs")):
        for key in ("evidence_id", "artifact_id"):
            if reference.get(key):
                authorized_evidence_ids.add(str(reference[key]))
    report_evaluation = evaluate_decision_report_quality(
        report,
        authorized_evidence_ids=authorized_evidence_ids,
    )
    external_checks.append(_check(
        "report_level_quality_evaluation",
        report_evaluation.get("status") != "fail",
        report_evaluation,
    ))
    if any(item["status"] == "fail" for item in external_checks):
        report["status"] = "degraded"
        report.setdefault("quality_review", {})["requires_human_review"] = True
    report["evidence_bundle_sha256"] = compute_decision_evidence_hash(report)
    core_checks = evaluate_decision_report(
        authority_state,
        report,
        authoritative_decision=authoritative_decision,
    )
    checks = [*core_checks, *external_checks]
    unresolved = [item["name"] for item in checks if item["status"] == "fail"]
    verification.update({
        "status": "passed" if not unresolved else "failed",
        "checks": checks,
        "unresolved_failures": unresolved,
        "durable_snapshot": {
            "schema_version": snapshot.get("schema_version"),
            "content_sha256": snapshot.get("content_sha256"),
            "signature_key_id": snapshot.get("signature_key_id"),
        },
        "policy_replay": policy_replay,
        "release_record_comparison": release_record,
        "artifact_verification": artifact_verification,
        "report_evaluation": report_evaluation,
    })
    report["verification"] = verification
    return report


def _replace_final_markdown(markdown: str, decision: dict[str, Any]) -> str:
    preliminary = str(markdown or "").split(_FINAL_MARKDOWN_MARKER, 1)[0].rstrip()
    rendered = render_decision_markdown(decision)
    return "\n\n".join(part for part in (preliminary, rendered) if part)


class DecisionReportCriticAgent(BaseAgent):
    stage_name = "decision_report_critic"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        pipeline_run_id = str(state["pipeline_run_id"])
        project_id = str(state["project_id"])
        await self.mark_stage_running(pipeline_run_id, input_keys=list(state.keys()))
        await self.broadcast_progress(
            project_id,
            {"status": "running", "message": "Verifying final decision report…"},
        )
        try:
            snapshot_metadata = _as_dict(state.get("decision_evidence_snapshot"))
            snapshot = await load_decision_evidence_snapshot(
                test_run_id=str(state["test_run_id"]),
                pipeline_run_id=pipeline_run_id,
                project_id=project_id,
            )
            authority_state = _snapshot_verification_state(snapshot)
            authority_state["completed_stages"] = [
                *authority_state["completed_stages"],
                "decision_report",
            ]
            authoritative_decision = _as_dict(snapshot.get("canonical_decision"))
            final = review_and_repair_decision_report(
                authority_state,
                _as_dict(state.get("decision_intelligence")),
                authoritative_decision=authoritative_decision,
            )
            policy_replay, release_record, artifact_verification = await asyncio.gather(
                replay_frozen_release_policy(snapshot),
                compare_persisted_release_record(snapshot),
                _verify_snapshot_artifacts(snapshot),
            )
            final = _attach_durable_verification(
                final,
                authority_state=authority_state,
                authoritative_decision=authoritative_decision,
                snapshot=snapshot,
                snapshot_metadata=snapshot_metadata,
                policy_replay=policy_replay,
                release_record=release_record,
                artifact_verification=artifact_verification,
            )
            verification = _as_dict(final.get("verification"))
            await self.log_decision(
                pipeline_run_id,
                "decision_report_verification",
                str(verification.get("status") or "failed"),
                "terminal report passed independent checks"
                if verification.get("status") == "passed"
                else "terminal report retains unresolved verification failures",
                alternatives=["passed", "failed"],
                context={
                    "repairs": verification.get("repairs") or [],
                    "unresolved_failures": verification.get("unresolved_failures") or [],
                },
            )
            structured = deepcopy(_as_dict(state.get("structured_summary")))
            structured["decision_intelligence"] = final
            markdown = _replace_final_markdown(str(state.get("summary_markdown") or ""), final)
            passed = verification.get("status") == "passed"
            if passed:
                await self._persist(state, final, markdown)
            else:
                rejection_error = "terminal decision verification failed closed"
                await self._persist_failure(
                    state,
                    rejection_error,
                    verification=verification,
                )
            confidence = 95 if passed and not verification.get("repairs") else 75 if passed else 25
            result_data = {
                "verification_status": verification.get("status"),
                "repair_count": len(verification.get("repairs") or []),
                "unresolved_failures": verification.get("unresolved_failures") or [],
            }
            await self.mark_stage_done(
                pipeline_run_id,
                result_data=result_data,
                error=None if passed else rejection_error,
                confidence_score=confidence,
                evidence_count=len(verification.get("checks") or []),
            )
            await self.broadcast_progress(
                project_id,
                {
                    "status": "completed" if passed else "failed",
                    "message": (
                        "Decision report verification complete"
                        if passed
                        else "Decision report rejected by terminal verification"
                    ),
                },
            )
            return validate_agent_contract(
                DecisionReportCriticAgentOutput,
                {
                    "decision_intelligence": final,
                    "decision_report_verification": verification,
                    "structured_summary": structured,
                    "summary_markdown": markdown,
                    "completed_stages": [self.stage_name],
                    "errors": [] if passed else [rejection_error],
                    "current_stage": "done",
                },
                agent_name=self.stage_name,
                confidence=confidence,
                fallback_used=not passed,
                evidence_refs=[
                    {"type": "decision_evidence_bundle", "id": final.get("evidence_bundle_sha256")},
                    {
                        "type": "critic_check_set",
                        "id": f"{pipeline_run_id}:{str(final.get('evidence_bundle_sha256'))[:16]}",
                    },
                ],
                decision_reason=f"decision_report_verification_{verification.get('status')}",
            )
        except Exception as exc:
            error = f"Decision report critic error: {exc}"
            logger.error("decision_report_critic_failed", error=str(exc), exc_info=True)
            try:
                await self._persist_failure(state, error)
            except Exception as persist_exc:
                logger.error(
                    "decision_report_critic_failure_marker_failed",
                    error=str(persist_exc),
                )
            await self.mark_stage_done(pipeline_run_id, error=error)
            return validate_agent_contract(
                DecisionReportCriticAgentOutput,
                {
                    "decision_report_verification": {
                        "schema_version": CRITIC_SCHEMA_VERSION,
                        "status": "failed",
                        "unresolved_failures": ["critic_exception"],
                    },
                    "completed_stages": [self.stage_name],
                    "errors": [error],
                    "current_stage": "done",
                },
                agent_name=self.stage_name,
                confidence=0,
                fallback_used=True,
                decision_reason="decision_report_critic_exception",
            )
    async def _persist(
        self, state: dict[str, Any], decision: dict[str, Any], markdown: str
    ) -> None:
        db = get_mongo_db()
        report = await publish_decision_report(
            db, state=state, decision=decision, markdown=markdown,
        )
        try:
            await db[Collections.RUN_SUMMARIES].update_one(
                {"test_run_id": str(state["test_run_id"])},
                {"$set": {
                    "test_run_id": str(state["test_run_id"]),
                    "project_id": str(state["project_id"]),
                    "build_number": state.get("build_number"),
                    "decision_intelligence": decision,
                    "decision_report_verification": decision.get("verification"),
                    "markdown_report": markdown,
                    "schema_version": SUMMARY_DOCUMENT_SCHEMA_VERSION,
                    "finalized_at": datetime.now(timezone.utc),
                    "finalized_by": self.stage_name,
                    "latest_decision_attempt": {
                        "pipeline_run_id": str(state["pipeline_run_id"]),
                        "status": "published",
                        "verification_status": "passed",
                        "at": datetime.now(timezone.utc),
                    },
                    "decision_report": {
                        "report_id": report["report_id"],
                        "report_version": report["report_version"],
                        "supersedes_report_id": report.get("supersedes_report_id"),
                        "status": report["status"],
                        "generated_at": report["generated_at"],
                    },
                }},
                upsert=True,
            )
        except Exception as exc:
            logger.warning(
                "decision_report_summary_projection_failed",
                error_type=type(exc).__name__,
            )
        try:
            from app.services.release_decision_webhook import (
                TRIGGER_AGENT,
                emit_release_decided,
            )

            await emit_release_decided(
                state["test_run_id"],
                trigger=TRIGGER_AGENT,
                pipeline_run_id=state["pipeline_run_id"],
                evidence_bundle_sha256=report.get("evidence_bundle_sha256"),
            )
        except Exception as exc:  # noqa: BLE001 -- outbound publication is best-effort
            logger.warning(
                "release_decided_webhook_after_report_failed",
                error_type=type(exc).__name__,
            )

    async def _persist_failure(
        self,
        state: dict[str, Any],
        error: str,
        *,
        verification: dict[str, Any] | None = None,
    ) -> None:
        """Expose critic failure without publishing an unverified decision draft."""
        db = get_mongo_db()
        safe_error = sanitize_for_persistence(error)
        attempt = await record_decision_report_attempt(
            db,
            state=state,
            status="failed" if (verification or {}).get("status") == "failed" else "rejected",
            reason=safe_error,
            verification=verification or {
                "schema_version": CRITIC_SCHEMA_VERSION,
                "status": "failed",
                "unresolved_failures": ["critic_exception"],
            },
        )
        try:
            await db[Collections.RUN_SUMMARIES].update_one(
                {"test_run_id": str(state["test_run_id"])},
                {"$set": {
                    "test_run_id": str(state["test_run_id"]),
                    "project_id": str(state["project_id"]),
                    "build_number": state.get("build_number"),
                    "schema_version": SUMMARY_DOCUMENT_SCHEMA_VERSION,
                    "decision_report_verification": verification or {
                        "schema_version": CRITIC_SCHEMA_VERSION,
                        "status": "failed",
                        "unresolved_failures": ["critic_exception"],
                        "error": safe_error,
                        "verified_at": datetime.now(timezone.utc),
                    },
                    "verification_failed_at": datetime.now(timezone.utc),
                    "verification_failed_by": self.stage_name,
                    "verification_failure_reason": safe_error,
                    "latest_decision_attempt": {
                        "pipeline_run_id": str(state["pipeline_run_id"]),
                        "status": "rejected",
                        "verification_status": "failed",
                        "at": datetime.now(timezone.utc),
                    },
                    "decision_report_attempt": {
                        "attempt_id": attempt["attempt_id"],
                        "status": attempt["status"],
                        "attempted_at": attempt["attempted_at"],
                        "supersedes_report_id": attempt.get("supersedes_report_id"),
                    },
                }},
                upsert=True,
            )
        except Exception as exc:
            logger.warning(
                "decision_report_failure_projection_failed",
                error_type=type(exc).__name__,
            )
