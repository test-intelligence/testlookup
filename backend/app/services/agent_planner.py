"""Deterministic planner and verifier for agent workflow execution.

R2 keeps the existing LangGraph topology intact while making the intended
agent path explicit, persisted, and auditable.
"""
from __future__ import annotations

from typing import Any, Iterable

from app.core.config import settings


PLANNER_SCHEMA_VERSION = 1
PLANNER_VERSION = "workflow-planner:v1"
VERIFIER_VERSION = "workflow-verifier:v1"

_OFFLINE_STAGES = ("ingestion", "anomaly_detection", "root_cause_analysis", "summary", "triage")
_LIVE_STAGES = ("ingestion", "summary")
_DEEP_STAGES = (
    "ingestion",
    "anomaly_detection",
    "failure_clustering",
    "root_cause_analysis",
    "summary",
    "triage",
    "flaky_sentinel",
    "test_health",
    "release_risk",
)
_FAILURE_ONLY_STAGES = {"anomaly_detection", "failure_clustering", "root_cause_analysis"}
_CORE_STAGES = {"ingestion", "summary"}


def _stage_order(workflow_type: str) -> tuple[str, ...]:
    if workflow_type == "deep":
        return _DEEP_STAGES
    if workflow_type == "live":
        return _LIVE_STAGES
    return _OFFLINE_STAGES


def _as_sorted_strings(values: Iterable[Any] | None) -> list[str]:
    return sorted(str(value) for value in (values or []))


def _triageable_ids(
    analyses: dict[str, dict] | None,
    *,
    threshold: int,
) -> list[str]:
    triageable: list[str] = []
    for test_id, analysis in sorted((analyses or {}).items(), key=lambda item: str(item[0])):
        try:
            confidence = int(analysis.get("confidence_score") or 0)
        except (TypeError, ValueError):
            confidence = 0
        if confidence >= threshold and not analysis.get("is_flaky", False):
            triageable.append(str(test_id))
    return triageable


def _check_contract_evidence_support(final_state: dict[str, Any]) -> dict[str, Any]:
    """Verify agent contracts expose evidence refs for non-empty outputs."""
    contracts = final_state.get("agent_contracts") or {}
    output_state_keys = {
        "root_cause_analysis": "analyses",
        "anomaly_detection": "anomalies",
        "failure_clustering": "failure_clusters",
        "summary": "structured_summary",
        "triage": "triage_results",
        "flaky_sentinel": "flaky_findings",
        "test_health": "test_health_findings",
        "release_risk": "release_decision",
    }
    missing_contracts: list[str] = []
    missing_evidence: list[str] = []

    for agent_name, state_key in sorted(output_state_keys.items()):
        output_value = final_state.get(state_key)
        has_output = bool(output_value)
        contract = contracts.get(agent_name)
        if has_output and not contract:
            missing_contracts.append(agent_name)
            continue
        if not has_output or not contract:
            continue
        decision_reason = str(contract.get("decision_reason") or "")
        fallback_used = bool(contract.get("fallback_used"))
        evidence_refs = contract.get("evidence_refs") or []
        no_evidence_ok = (
            fallback_used
            or "no_" in decision_reason
            or "blocked" in decision_reason
        )
        if not evidence_refs and not no_evidence_ok:
            missing_evidence.append(agent_name)

    status = "fail" if missing_contracts else "warn" if missing_evidence else "pass"
    return {
        "name": "contract_evidence_support",
        "status": status,
        "details": {
            "missing_contracts": missing_contracts,
            "missing_evidence_refs": missing_evidence,
            "contract_count": len(contracts),
        },
    }


def _check_summary_provenance(final_state: dict[str, Any]) -> dict[str, Any]:
    """Ensure generated summaries carry replayable provenance fingerprints."""
    structured = final_state.get("structured_summary") or {}
    provenance = final_state.get("summary_provenance") or structured.get("_provenance") or {}
    required = [
        "context_sha256",
        "safe_context_sha256",
        "input_fingerprints",
        "prompt_versions",
        "model_config_snapshot",
    ]
    missing = [key for key in required if not provenance.get(key)]
    has_summary = bool(structured or final_state.get("summary_markdown"))
    return {
        "name": "summary_provenance_present",
        "status": "pass" if (not has_summary or not missing) else "warn",
        "details": {"missing": missing, "has_summary": has_summary},
    }


def _check_mutating_action_policy_alignment(final_state: dict[str, Any]) -> dict[str, Any]:
    """Verify mutating actions are policy-gated or explicitly executed."""
    violations: list[dict[str, Any]] = []
    for result in final_state.get("triage_results") or []:
        if not isinstance(result, dict):
            continue
        mutating_action = result.get("mutating_action")
        action = result.get("action")
        ticket_key = result.get("ticket_key")
        approval_status = result.get("approval_status")
        requires_approval = result.get("requires_approval")
        if mutating_action == "jira_ticket_creation":
            if approval_status != "pending_review" or requires_approval is not True:
                violations.append({
                    "test_case_id": result.get("test_case_id"),
                    "reason": "jira mutation was not staged for review",
                })
        elif ticket_key and approval_status != "executed":
            violations.append({
                "test_case_id": result.get("test_case_id"),
                "action": action,
                "reason": "ticket-bearing result lacks executed approval status",
            })

    return {
        "name": "mutating_actions_policy_aligned",
        "status": "pass" if not violations else "fail",
        "details": {"violations": violations},
    }


def _check_release_decision_policy_trace(final_state: dict[str, Any]) -> dict[str, Any]:
    """Verify release decisions are tied to deterministic score/policy data."""
    decision = final_state.get("release_decision")
    if not decision:
        return {
            "name": "release_decision_policy_trace",
            "status": "pass",
            "details": {"release_decision_present": False},
        }

    missing: list[str] = []
    for key in ("recommendation", "risk_score", "score_model_version"):
        if decision.get(key) in (None, ""):
            missing.append(key)
    if decision.get("policy_id") and not decision.get("policy_evaluation"):
        missing.append("policy_evaluation")

    return {
        "name": "release_decision_policy_trace",
        "status": "pass" if not missing else "warn",
        "details": {
            "missing": missing,
            "policy_id": decision.get("policy_id"),
            "has_policy_evaluation": bool(decision.get("policy_evaluation")),
        },
    }


def build_workflow_plan(
    *,
    workflow_type: str,
    failed_test_ids: Iterable[Any] | None = None,
    analyses: dict[str, dict] | None = None,
    threshold: int | None = None,
) -> dict[str, Any]:
    """Build the minimal useful agent path for the currently known state."""
    threshold = threshold if threshold is not None else settings.AI_CONFIDENCE_THRESHOLD
    failed_ids = _as_sorted_strings(failed_test_ids)
    failures_known = failed_test_ids is not None
    all_green = failures_known and not failed_ids
    triageable = _triageable_ids(analyses, threshold=threshold)

    stages: list[dict[str, Any]] = []
    for stage in _stage_order(workflow_type):
        required = stage in _CORE_STAGES
        planned = True
        rationale = "required core workflow stage" if required else "stage adds diagnostic value"

        if stage in _FAILURE_ONLY_STAGES and all_green:
            planned = False
            rationale = "all-green run has no failed tests for this specialist stage"
        elif stage == "triage":
            if all_green:
                planned = False
                rationale = "all-green run has no defects to triage"
            elif analyses is not None and not triageable:
                planned = False
                rationale = (
                    f"no analyses meet confidence threshold {threshold} for defect triage"
                )
            elif workflow_type == "deep":
                rationale = "deep workflow continues to specialist stages after triage decision"
            else:
                rationale = f"{len(triageable)} analysis result(s) meet triage threshold"
        elif workflow_type == "deep" and stage in {"flaky_sentinel", "test_health", "release_risk"}:
            rationale = "deep workflow specialist stage is intentionally included"

        stages.append({
            "stage": stage,
            "planned": planned,
            "required": required,
            "rationale": rationale,
        })

    return {
        "schema_version": PLANNER_SCHEMA_VERSION,
        "planner_version": PLANNER_VERSION,
        "workflow_type": workflow_type,
        "failure_count": len(failed_ids),
        "failures_known": failures_known,
        "triage_threshold": threshold,
        "triageable_test_ids": triageable,
        "stages": stages,
    }


def verify_workflow_execution(
    plan: dict[str, Any],
    final_state: dict[str, Any],
) -> dict[str, Any]:
    """Verify that actual execution is explainable against the workflow plan."""
    completed = set(_as_sorted_strings(final_state.get("completed_stages")))
    skipped = set(_as_sorted_strings(final_state.get("skipped_stages")))
    analyses = final_state.get("analyses") or {}
    route_decisions = final_state.get("_workflow_route_decisions") or []
    checks: list[dict[str, Any]] = []

    planned_stages = [
        stage for stage in plan.get("stages", [])
        if isinstance(stage, dict) and stage.get("planned")
    ]
    unplanned_stages = [
        stage for stage in plan.get("stages", [])
        if isinstance(stage, dict) and not stage.get("planned")
    ]

    missing_planned = [
        str(stage.get("stage"))
        for stage in planned_stages
        if stage.get("required") and str(stage.get("stage")) not in completed
    ]
    checks.append({
        "name": "required_planned_stages_completed",
        "status": "pass" if not missing_planned else "fail",
        "details": {"missing": missing_planned},
    })

    unexplained_unplanned = [
        str(stage.get("stage"))
        for stage in unplanned_stages
        if str(stage.get("stage")) in completed and str(stage.get("stage")) not in skipped
    ]
    checks.append({
        "name": "unplanned_stages_not_executed",
        "status": "pass" if not unexplained_unplanned else "warn",
        "details": {"completed_without_skip_marker": unexplained_unplanned},
    })

    needs_route_rationale = bool(unplanned_stages or skipped)
    checks.append({
        "name": "route_rationale_persisted",
        "status": "pass" if (not needs_route_rationale or route_decisions) else "warn",
        "details": {"route_decision_count": len(route_decisions)},
    })

    all_green = bool(plan.get("failures_known")) and int(plan.get("failure_count") or 0) == 0
    all_green_has_analysis = bool(analyses)
    checks.append({
        "name": "all_green_skips_analysis_work",
        "status": "pass" if (not all_green or not all_green_has_analysis) else "fail",
        "details": {"analysis_count": len(analyses), "all_green": all_green},
    })
    checks.append(_check_contract_evidence_support(final_state))
    checks.append(_check_summary_provenance(final_state))
    checks.append(_check_mutating_action_policy_alignment(final_state))
    checks.append(_check_release_decision_policy_trace(final_state))

    failed = [check for check in checks if check["status"] == "fail"]
    warned = [check for check in checks if check["status"] == "warn"]
    status = "failed" if failed else "warning" if warned else "passed"

    return {
        "schema_version": PLANNER_SCHEMA_VERSION,
        "verifier_version": VERIFIER_VERSION,
        "status": status,
        "checks": checks,
    }


def attach_workflow_plan_and_verification(
    final_state: dict[str, Any],
    *,
    workflow_type: str,
) -> dict[str, Any]:
    """Attach final planner and verifier records to a workflow state copy."""
    state = dict(final_state)
    plan = build_workflow_plan(
        workflow_type=workflow_type,
        failed_test_ids=state.get("failed_test_ids"),
        analyses=state.get("analyses") or {},
    )
    state["workflow_plan"] = plan
    state["workflow_verification"] = verify_workflow_execution(plan, state)
    return state
