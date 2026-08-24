"""Deterministic planner and verifier for agent workflow execution.

R2 keeps the existing LangGraph topology intact while making the intended
agent path explicit, persisted, and auditable.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from decimal import Decimal, ROUND_DOWN
from typing import Any, Iterable

from app.core.config import settings
from app.services.agent_capability_registry import get_capability


PLANNER_SCHEMA_VERSION = 2
PLANNER_VERSION = "workflow-planner:v2"
VERIFIER_VERSION = "workflow-verifier:v1"
CLUSTER_PLANNER_SCHEMA_VERSION = 1
CLUSTER_PLANNER_VERSION = "cluster-investigation-planner:v1"

_OFFLINE_STAGES = ("ingestion", "anomaly_detection", "root_cause_analysis", "summary", "triage")
_LIVE_STAGES = ("ingestion", "summary")
_INVESTIGATION_STAGES = (
    "investigator_plan", "hypothesis_infra", "hypothesis_commit",
    "hypothesis_environment", "hypothesis_known_flaky", "hypothesis_regression",
    "investigator_synthesis",
)
_DEEP_STAGES = (
    "ingestion",
    "anomaly_detection",
    "failure_clustering",
    "cluster_investigation_dispatch",
    "root_cause_analysis",
    "cluster_investigation_join",
    "summary",
    "triage",
    "contract_validation",
    "log_intelligence",
    "regression_watchman",
    "change_ownership",
    "gap_detection",
    "report_refinement",
    "flaky_sentinel",
    "test_health",
    "release_risk",
    "decision_report",
    "decision_report_critic",
)
_FAILURE_ONLY_STAGES = {"anomaly_detection", "failure_clustering", "root_cause_analysis"}
_CORE_STAGES = {"ingestion", "summary", "decision_report", "decision_report_critic"}
# AIQ-P4 optional stages: present in the deep topology but only execute when
# AIQ_GAP_REFINEMENT_ENABLED is on; otherwise they early-return a skip delta.
_GAP_REFINEMENT_STAGES = {"gap_detection", "report_refinement"}
_CLUSTER_CHILD_STAGES = {
    "cluster_investigation_dispatch", "cluster_investigation_join",
}
_CLUSTER_BUDGET_KEYS = (
    "max_llm_calls", "max_tokens", "max_cost_usd", "max_seconds",
)
_CLUSTER_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,20}$")
_MAX_CLUSTER_CANDIDATES = 500


def _stage_order(workflow_type: str) -> tuple[str, ...]:
    if workflow_type == "deep":
        return _DEEP_STAGES
    if workflow_type == "live":
        return _LIVE_STAGES
    if workflow_type == "offline":
        return _OFFLINE_STAGES
    if workflow_type == "investigation":
        return _INVESTIGATION_STAGES
    raise ValueError(f"unsupported workflow_type: {workflow_type}")


def compute_workflow_plan_hash(plan: dict[str, Any]) -> str:
    """Hash the authoritative plan projection, excluding digest metadata."""
    projection = {key: value for key, value in plan.items() if key not in {"plan_id", "plan_sha256"}}
    canonical = json.dumps(projection, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def compute_cluster_investigation_plan_hash(plan: dict[str, Any]) -> str:
    """Hash a cluster expansion while excluding its digest metadata."""
    projection = {
        key: value for key, value in plan.items()
        if key not in {"expansion_id", "expansion_sha256"}
    }
    canonical = json.dumps(
        projection, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def cluster_investigation_plan_integrity_status(
    plan: Any,
) -> tuple[str, str | None]:
    if not isinstance(plan, dict) or not isinstance(plan.get("tasks"), list):
        return "failed", None
    if plan.get("schema_version") != CLUSTER_PLANNER_SCHEMA_VERSION:
        return "failed", None
    try:
        actual = compute_cluster_investigation_plan_hash(plan)
    except (TypeError, ValueError):
        return "failed", None
    return (
        "verified" if plan.get("expansion_sha256") == actual else "failed",
        actual,
    )


def workflow_plan_integrity_status(plan: Any) -> tuple[str, str | None]:
    if not isinstance(plan, dict) or not isinstance(plan.get("stages"), list):
        return "failed", None
    try:
        schema_version = int(plan.get("schema_version") or 1)
    except (TypeError, ValueError):
        return "failed", None
    if schema_version < 2:
        return "legacy_unhashed", None
    try:
        actual = compute_workflow_plan_hash(plan)
    except (TypeError, ValueError):
        return "failed", None
    return ("verified" if plan.get("plan_sha256") == actual else "failed"), actual


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


def _canonical_uuid(value: Any, label: str) -> str:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a UUID")
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{label} must be a UUID") from exc


def _nonnegative_budget_snapshot(value: Any) -> dict[str, int | float]:
    if not isinstance(value, dict) or set(value) != set(_CLUSTER_BUDGET_KEYS):
        raise ValueError(
            "aggregate_budget must contain exactly "
            + ", ".join(_CLUSTER_BUDGET_KEYS)
        )
    normalized: dict[str, int | float] = {}
    for key in ("max_llm_calls", "max_tokens", "max_seconds"):
        raw = value[key]
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise ValueError(f"aggregate_budget.{key} must be a nonnegative integer")
        normalized[key] = raw
    raw_cost = value["max_cost_usd"]
    if (
        isinstance(raw_cost, bool)
        or not isinstance(raw_cost, (int, float))
        or not math.isfinite(float(raw_cost))
        or raw_cost < 0
    ):
        raise ValueError("aggregate_budget.max_cost_usd must be nonnegative and finite")
    normalized["max_cost_usd"] = float(raw_cost)
    return normalized


def _allocate_integer(total: int, count: int) -> list[int]:
    if count <= 0:
        return []
    base, remainder = divmod(total, count)
    return [base + (1 if index < remainder else 0) for index in range(count)]


def _allocate_cost(total: float, count: int) -> list[float]:
    """Allocate whole micro-dollars, deliberately leaving sub-micro dust unused."""
    if count <= 0:
        return []
    micros = int(
        (Decimal(str(total)) * Decimal("1000000")).to_integral_value(
            rounding=ROUND_DOWN
        )
    )
    shares = [value / 1_000_000 for value in _allocate_integer(micros, count)]
    # The integer micros never over-allocate, but dividing each share back into
    # a float reintroduces representation error, so the parts can sum to
    # marginally MORE than the whole (0.100001 -> 0.10000100000000003). A budget
    # whose parts exceed their aggregate is an over-spend, so shave any drift off
    # the largest share until the invariant holds under plain left-to-right
    # summation — the way callers actually add these up.
    for _ in range(4):
        drift = sum(shares) - total
        if drift <= 0:
            break
        largest = max(range(count), key=lambda index: shares[index])
        shares[largest] = max(0.0, shares[largest] - drift)
    return shares


def compute_cluster_scope_sha256(
    project_id: Any,
    run_id: Any,
    parent_pipeline_run_id: Any,
    failure_cluster_id: Any,
    member_test_ids: Iterable[Any],
) -> str:
    """Hash the authoritative cluster scope using canonical UUID identities."""
    members = [
        _canonical_uuid(item, "member_test_id") for item in member_test_ids
    ]
    if not members or len(set(members)) != len(members):
        raise ValueError("member_test_ids must be non-empty and unique")
    scope = {
        "project_id": _canonical_uuid(project_id, "project_id"),
        "run_id": _canonical_uuid(run_id, "run_id"),
        "parent_pipeline_run_id": _canonical_uuid(
            parent_pipeline_run_id, "parent_pipeline_run_id"
        ),
        "failure_cluster_id": _canonical_uuid(
            failure_cluster_id, "failure_cluster_id"
        ),
        "member_test_ids": sorted(members),
    }
    return hashlib.sha256(
        json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_cluster_investigation_plan(
    *,
    parent_pipeline_run_id: Any,
    project_id: Any,
    run_id: Any,
    clusters: Any,
    aggregate_budget: Any,
    max_children: int = 1,
    max_members: int = 50,
) -> dict[str, Any]:
    """Build a deterministic, fail-closed cluster-child expansion.

    This pure planner accepts no labels, excerpts, or evidence payloads. The
    future dispatcher must re-resolve those from authoritative IDs.
    """
    parent_id = _canonical_uuid(parent_pipeline_run_id, "parent_pipeline_run_id")
    canonical_project_id = _canonical_uuid(project_id, "project_id")
    canonical_run_id = _canonical_uuid(run_id, "run_id")
    budget = _nonnegative_budget_snapshot(aggregate_budget)
    if isinstance(max_children, bool) or not isinstance(max_children, int) or max_children < 0:
        raise ValueError("max_children must be a nonnegative integer")
    if isinstance(max_members, bool) or not isinstance(max_members, int) or max_members < 1:
        raise ValueError("max_members must be a positive integer")
    if not isinstance(clusters, (list, tuple)):
        raise ValueError("clusters must be a bounded list or tuple")
    if len(clusters) > _MAX_CLUSTER_CANDIDATES:
        raise ValueError(
            f"clusters exceeds the {_MAX_CLUSTER_CANDIDATES} candidate scan limit"
        )

    parsed: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []
    for raw in clusters:
        raw_digest = hashlib.sha256(
            json.dumps(
                raw, sort_keys=True, default=str, separators=(",", ":")
            ).encode()
        ).hexdigest()
        try:
            if not isinstance(raw, dict) or set(raw) != {
                "failure_cluster_id", "cluster_id", "member_test_ids",
            }:
                raise ValueError
            failure_cluster_id = _canonical_uuid(
                raw["failure_cluster_id"], "failure_cluster_id"
            )
            cluster_id = raw["cluster_id"]
            members_raw = raw["member_test_ids"]
            if not isinstance(cluster_id, str) or not _CLUSTER_ID_RE.fullmatch(cluster_id):
                raise ValueError
            if not isinstance(members_raw, (list, tuple)) or not members_raw:
                raise ValueError
            members = [
                _canonical_uuid(item, "member_test_id") for item in members_raw
            ]
            if len(set(members)) != len(members):
                malformed.append({
                    "failure_cluster_id": failure_cluster_id,
                    "cluster_id": cluster_id,
                    "member_test_ids": sorted(set(members)),
                    "member_count": len(set(members)),
                    "candidate_sha256": raw_digest,
                    "selected": False,
                    "skip_reason": "duplicate_member_test_ids",
                    "rationale": "member_test_ids must be unique within a candidate",
                })
                continue
            members.sort()
            canonical_candidate = {
                "failure_cluster_id": failure_cluster_id,
                "cluster_id": cluster_id,
                "member_test_ids": members,
            }
            raw_digest = hashlib.sha256(
                json.dumps(
                    canonical_candidate,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            parsed.append({
                "failure_cluster_id": failure_cluster_id,
                "cluster_id": cluster_id,
                "member_test_ids": members,
                "member_count": len(members),
                "candidate_sha256": raw_digest,
            })
        except (KeyError, ValueError, TypeError):
            malformed.append({
                "candidate_sha256": raw_digest,
                "selected": False,
                "skip_reason": "candidate_malformed",
                "rationale": "candidate failed the strict authoritative cluster schema",
            })

    failure_id_counts: dict[str, int] = {}
    cluster_id_counts: dict[str, int] = {}
    member_counts: dict[str, int] = {}
    for item in parsed:
        failure_id = item["failure_cluster_id"]
        cluster_id = item["cluster_id"]
        failure_id_counts[failure_id] = failure_id_counts.get(failure_id, 0) + 1
        cluster_id_counts[cluster_id] = cluster_id_counts.get(cluster_id, 0) + 1
        for member in item["member_test_ids"]:
            member_counts[member] = member_counts.get(member, 0) + 1

    eligible: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = list(malformed)
    for item in parsed:
        reason: str | None = None
        rationale: str | None = None
        if failure_id_counts[item["failure_cluster_id"]] > 1:
            reason = "duplicate_failure_cluster_id"
            rationale = "failure_cluster_id occurs more than once"
        elif cluster_id_counts[item["cluster_id"]] > 1:
            reason = "duplicate_cluster_id"
            rationale = "cluster_id occurs more than once"
        elif any(member_counts[member] > 1 for member in item["member_test_ids"]):
            reason = "overlapping_member_test_ids"
            rationale = "one or more members occur in another cluster candidate"
        elif item["member_count"] > max_members:
            reason = "member_limit_exceeded"
            rationale = f"cluster exceeds max_members={max_members}"
        if reason:
            skipped.append({
                **item, "selected": False, "skip_reason": reason,
                "rationale": rationale,
            })
        else:
            eligible.append(item)

    eligible.sort(
        key=lambda item: (
            -item["member_count"], item["cluster_id"],
            item["failure_cluster_id"],
        )
    )
    # Every selected child needs at least one second of wall-clock authority.
    # Other hard-zero budgets intentionally permit deterministic, no-LLM
    # fallbacks, but a zero-second allocation cannot execute at all.
    time_limited_children = min(max_children, int(budget["max_seconds"]))
    selected_base = eligible[:time_limited_children]
    for item in eligible[time_limited_children:max_children]:
        skipped.append({
            **item,
            "selected": False,
            "skip_reason": "aggregate_time_budget_exhausted",
            "rationale": (
                "aggregate max_seconds cannot allocate at least one second "
                "to this child"
            ),
        })
    for item in eligible[max_children:]:
        skipped.append({
            **item,
            "selected": False,
            "skip_reason": "max_children_reached",
            "rationale": f"deterministic top-{max_children} child limit reached",
        })

    count = len(selected_base)
    calls = _allocate_integer(int(budget["max_llm_calls"]), count)
    tokens = _allocate_integer(int(budget["max_tokens"]), count)
    seconds = _allocate_integer(int(budget["max_seconds"]), count)
    costs = _allocate_cost(float(budget["max_cost_usd"]), count)
    capability = get_capability("cluster_investigation")
    selected: list[dict[str, Any]] = []
    for index, item in enumerate(selected_base):
        scope = {
            "project_id": canonical_project_id,
            "run_id": canonical_run_id,
            "failure_cluster_id": item["failure_cluster_id"],
            "cluster_id": item["cluster_id"],
            "member_test_ids": item["member_test_ids"],
        }
        scope_hash = compute_cluster_scope_sha256(
            canonical_project_id,
            canonical_run_id,
            parent_id,
            item["failure_cluster_id"],
            item["member_test_ids"],
        )
        spawn_key = hashlib.sha256(
            json.dumps({
                "parent_pipeline_run_id": parent_id,
                "cluster_scope_sha256": scope_hash,
                "capability_id": capability.capability_id,
                "planner_version": CLUSTER_PLANNER_VERSION,
            }, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        selected.append({
            **item,
            "selected": True,
            "skip_reason": None,
            "rationale": "selected by member_count descending then stable cluster identity",
            "cluster_scope": scope,
            "cluster_scope_sha256": scope_hash,
            "spawn_key": spawn_key,
            "task_key": f"cluster-investigation:{spawn_key}",
            "capability_id": capability.capability_id,
            "dependencies": [
                "failure_clustering", "cluster_investigation_dispatch",
            ],
            "permission": capability.permission,
            "sla": {
                "expected_latency_ms": capability.expected_latency_ms,
                "timeout_seconds": min(capability.timeout_seconds, seconds[index]),
            },
            "budget": {
                "max_llm_calls": calls[index],
                "max_tokens": tokens[index],
                "max_cost_usd": costs[index],
                "max_seconds": seconds[index],
                "max_retries": capability.max_retries,
            },
            "fallback": capability.fallback,
            "concurrency_class": capability.concurrency_class,
        })

    skipped.sort(key=lambda item: (
        str(item.get("cluster_id") or ""),
        str(item.get("failure_cluster_id") or ""),
        item["candidate_sha256"],
    ))
    skipped = [
        {
            "failure_cluster_id": item.get("failure_cluster_id"),
            "cluster_id": item.get("cluster_id"),
            "member_test_ids": list(item.get("member_test_ids") or []),
            "member_count": int(item.get("member_count") or 0),
            "candidate_sha256": item["candidate_sha256"],
            "cluster_scope_sha256": None,
            "spawn_key": None,
            "task_key": None,
            "capability_id": capability.capability_id,
            "selected": False,
            "skip_reason": item["skip_reason"],
            "rationale": item["rationale"],
            "dependencies": [
                "failure_clustering", "cluster_investigation_dispatch",
            ],
            "permission": capability.permission,
            "budget": {
                "max_llm_calls": 0,
                "max_tokens": 0,
                "max_cost_usd": 0.0,
                "max_seconds": 0,
                "max_retries": capability.max_retries,
            },
            "fallback": capability.fallback,
            "concurrency_class": capability.concurrency_class,
        }
        for item in skipped
    ]
    tasks = selected + skipped
    plan: dict[str, Any] = {
        "schema_version": CLUSTER_PLANNER_SCHEMA_VERSION,
        "planner_version": CLUSTER_PLANNER_VERSION,
        "parent_pipeline_run_id": parent_id,
        "project_id": canonical_project_id,
        "run_id": canonical_run_id,
        "aggregate_budget": budget,
        "limits": {"max_children": max_children, "max_members": max_members},
        "candidate_count": len(clusters),
        "selected_count": len(selected),
        "skipped_count": len(skipped),
        "selected": selected,
        "skipped": skipped,
        "tasks": tasks,
        "dependencies": [
            "failure_clustering", "cluster_investigation_dispatch",
        ],
        "fallback": "continue_without_cluster_children",
    }
    digest = compute_cluster_investigation_plan_hash(plan)
    plan["expansion_id"] = f"cluster-plan:{digest[:20]}"
    plan["expansion_sha256"] = digest
    return plan


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
        "log_intelligence": "log_findings",
        "regression_watchman": "regression_classification",
        "change_ownership": "change_ownership_findings",
        "release_risk": "release_decision",
        "decision_report": "decision_intelligence",
        "decision_report_critic": "decision_report_verification",
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


def _check_terminal_decision_verification(
    plan: dict[str, Any], final_state: dict[str, Any]
) -> dict[str, Any]:
    """A required terminal critic must pass, not merely emit a contract."""
    critic_required = any(
        isinstance(stage, dict)
        and stage.get("stage") == "decision_report_critic"
        and stage.get("planned")
        and stage.get("required")
        for stage in plan.get("stages", [])
    )
    verification = final_state.get("decision_report_verification") or {}
    observed = verification.get("status") if isinstance(verification, dict) else None
    passed = not critic_required or observed == "passed"
    return {
        "name": "terminal_decision_verification_passed",
        "status": "pass" if passed else "fail",
        "details": {"required": critic_required, "observed_status": observed},
    }


def build_workflow_plan(
    *,
    workflow_type: str,
    failed_test_ids: Iterable[Any] | None = None,
    analyses: dict[str, dict] | None = None,
    threshold: int | None = None,
    cluster_children_enabled: bool = False,
    cluster_children_aggregate_budget: dict[str, Any] | None = None,
    decision_graph_aggregate_budget: dict[str, Any] | None = None,
    contract_validation_enabled: bool = False,
    log_intelligence_enabled: bool = False,
    regression_watchman_enabled: bool = False,
    change_ownership_enabled: bool = False,
) -> dict[str, Any]:
    """Build the minimal useful agent path for the currently known state."""
    threshold = threshold if threshold is not None else settings.AI_CONFIDENCE_THRESHOLD
    if not isinstance(cluster_children_enabled, bool):
        raise ValueError("cluster_children_enabled must be a boolean")
    if not isinstance(contract_validation_enabled, bool):
        raise ValueError("contract_validation_enabled must be a boolean")
    if not isinstance(log_intelligence_enabled, bool):
        raise ValueError("log_intelligence_enabled must be a boolean")
    if not isinstance(regression_watchman_enabled, bool):
        raise ValueError("regression_watchman_enabled must be a boolean")
    if not isinstance(change_ownership_enabled, bool):
        raise ValueError("change_ownership_enabled must be a boolean")
    graph_budget = (
        _nonnegative_budget_snapshot(decision_graph_aggregate_budget)
        if decision_graph_aggregate_budget is not None
        else None
    )
    cluster_budget = _nonnegative_budget_snapshot(
        cluster_children_aggregate_budget
        if cluster_children_aggregate_budget is not None
        else {
            "max_llm_calls": 0,
            "max_tokens": 0,
            "max_cost_usd": 0.0,
            "max_seconds": 0,
        }
    )
    failed_ids = _as_sorted_strings(failed_test_ids)
    failures_known = failed_test_ids is not None
    all_green = failures_known and not failed_ids
    triageable = _triageable_ids(analyses, threshold=threshold)

    stages: list[dict[str, Any]] = []
    graph_stage_names = [
        stage for stage in _stage_order(workflow_type)
        if graph_budget is not None and get_capability(stage).expected_cost_usd > 0
    ]
    graph_calls = _allocate_integer(int(graph_budget["max_llm_calls"]), len(graph_stage_names)) if graph_budget is not None else []
    graph_tokens = _allocate_integer(int(graph_budget["max_tokens"]), len(graph_stage_names)) if graph_budget is not None else []
    graph_seconds = _allocate_integer(int(graph_budget["max_seconds"]), len(graph_stage_names)) if graph_budget is not None else []
    graph_costs = _allocate_cost(float(graph_budget["max_cost_usd"]), len(graph_stage_names)) if graph_budget is not None else []
    graph_budget_by_stage = {
        stage: {
            "max_llm_calls": graph_calls[index],
            "max_tokens": graph_tokens[index],
            "max_cost_usd": graph_costs[index],
            "max_seconds": graph_seconds[index],
            "max_retries": 0,
        }
        for index, stage in enumerate(graph_stage_names)
    }
    for stage in _stage_order(workflow_type):
        required = stage in _CORE_STAGES
        planned = True
        rationale = "required core workflow stage" if required else "stage adds diagnostic value"

        if stage in _CLUSTER_CHILD_STAGES:
            planned = True
            if cluster_children_enabled and not all_green:
                rationale = (
                    "durable cluster-child coordinator will expand eligible "
                    "clusters under the frozen aggregate budget"
                )
            elif not cluster_children_enabled:
                rationale = (
                    "durable coordinator records a no-op because cluster-child "
                    "expansion is explicitly disabled"
                )
            else:
                rationale = (
                    "durable coordinator records a no-op because the all-green "
                    "run has no failure clusters"
                )
        elif stage == "contract_validation":
            if contract_validation_enabled and not all_green:
                rationale = "project Contract Agent flag enabled for failed tests"
            elif not contract_validation_enabled:
                # "disabled OR no failures" conflated two different answers to
                # the operational question -- is this stage off, or was there
                # nothing for it to do? Only the first is fixable by changing a
                # flag, and a reader could not tell which they had.
                planned = False
                rationale = "Contract Agent feature flag is off for this project"
            else:
                planned = False
                rationale = "all-green run has no failed tests for Contract Agent"
        elif stage == "log_intelligence":
            if log_intelligence_enabled and not all_green:
                rationale = "project Log Intelligence flag enabled for failed tests"
            elif not log_intelligence_enabled:
                # "disabled OR no failures" conflated two different answers to
                # the operational question -- is this stage off, or was there
                # nothing for it to do? Only the first is fixable by changing a
                # flag, and a reader could not tell which they had.
                planned = False
                rationale = "Log Intelligence feature flag is off for this project"
            else:
                planned = False
                rationale = "all-green run has no failed tests for Log Intelligence"
        elif stage == "regression_watchman":
            if regression_watchman_enabled and not all_green:
                rationale = "project RegressionWatchman flag enabled for failed tests"
            elif not regression_watchman_enabled:
                # "disabled OR no failures" conflated two different answers to
                # the operational question -- is this stage off, or was there
                # nothing for it to do? Only the first is fixable by changing a
                # flag, and a reader could not tell which they had.
                planned = False
                rationale = "RegressionWatchman feature flag is off for this project"
            else:
                planned = False
                rationale = "all-green run has no failed tests for RegressionWatchman"
        elif stage == "change_ownership":
            if change_ownership_enabled and not all_green:
                rationale = "project Change/Ownership flag enabled for failed tests"
            elif not change_ownership_enabled:
                # "disabled OR no failures" conflated two different answers to
                # the operational question -- is this stage off, or was there
                # nothing for it to do? Only the first is fixable by changing a
                # flag, and a reader could not tell which they had.
                planned = False
                rationale = "Change/Ownership feature flag is off for this project"
            else:
                planned = False
                rationale = "all-green run has no failed tests for Change/Ownership"
        elif stage in _GAP_REFINEMENT_STAGES:
            if settings.AIQ_GAP_REFINEMENT_ENABLED:
                rationale = "AIQ-P4 gap/refinement stage enabled by feature flag"
            else:
                planned = False
                rationale = "AIQ_GAP_REFINEMENT_ENABLED is off — stage skipped"
        elif stage in _FAILURE_ONLY_STAGES and all_green:
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

        capability = get_capability(stage)
        stages.append({
            "stage": stage,
            "capability_id": capability.capability_id,
            "planned": planned,
            "required": required,
            "rationale": rationale,
            "dependencies": list(capability.dependencies),
            "required_evidence": list(capability.required_evidence),
            "permission": capability.permission,
            "sla": {
                "expected_latency_ms": capability.expected_latency_ms,
                "timeout_seconds": capability.timeout_seconds,
            },
            "budget": (
                graph_budget_by_stage.get(stage)
                or {
                    "max_llm_calls": 0 if graph_budget is not None else None,
                    "max_tokens": 0 if graph_budget is not None else None,
                    "max_cost_usd": 0.0 if graph_budget is not None else None,
                    "max_seconds": 0 if graph_budget is not None else None,
                    "max_retries": capability.max_retries,
                }
            ),
            "estimate": {
                "cost_usd": capability.expected_cost_usd,
                "latency_ms": capability.expected_latency_ms,
            },
            "fallback": capability.fallback,
            "concurrency_class": capability.concurrency_class,
        })

    plan = {
        "schema_version": PLANNER_SCHEMA_VERSION,
        "planner_version": PLANNER_VERSION,
        "workflow_type": workflow_type,
        "failure_count": len(failed_ids),
        "failures_known": failures_known,
        "triage_threshold": threshold,
        "triageable_test_ids": triageable,
        "cluster_children_enabled": cluster_children_enabled,
        "cluster_children_aggregate_budget": cluster_budget,
        "decision_graph_aggregate_budget": graph_budget,
        # Persisted so attach_workflow_plan_and_verification can rebuild an
        # explained plan with the SAME inputs. Without these the rebuild fell
        # back to the kwarg defaults (all False) and reported every specialist
        # as "feature flag is off" even while the agent had just run.
        "specialist_flags": {
            "contract_validation": contract_validation_enabled,
            "log_intelligence": log_intelligence_enabled,
            "regression_watchman": regression_watchman_enabled,
            "change_ownership": change_ownership_enabled,
        },
        "stages": stages,
    }
    digest = compute_workflow_plan_hash(plan)
    plan["plan_id"] = f"plan:{digest[:20]}"
    plan["plan_sha256"] = digest
    return plan


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
    checks.append(_check_terminal_decision_verification(plan, final_state))

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
    initial_plan = state.get("initial_workflow_plan") or state.get("workflow_plan")
    plan_inputs = initial_plan if isinstance(initial_plan, dict) else {}
    # Every input below must be recovered from the original plan. A kwarg left
    # to its default here does not raise -- it silently rewrites the plan with a
    # different answer, and the verifier then checks execution against a plan
    # that never applied. The specialist flags default to False, which is what
    # made four running agents report as "feature flag is off for this project".
    specialist_flags = plan_inputs.get("specialist_flags")
    if not isinstance(specialist_flags, dict):
        specialist_flags = {}
    explained_plan = build_workflow_plan(
        workflow_type=workflow_type,
        failed_test_ids=state.get("failed_test_ids"),
        analyses=state.get("analyses") or {},
        threshold=plan_inputs.get("triage_threshold"),
        cluster_children_enabled=bool(plan_inputs.get("cluster_children_enabled")),
        cluster_children_aggregate_budget=plan_inputs.get(
            "cluster_children_aggregate_budget"
        ),
        decision_graph_aggregate_budget=plan_inputs.get(
            "decision_graph_aggregate_budget"
        ),
        contract_validation_enabled=bool(specialist_flags.get("contract_validation")),
        log_intelligence_enabled=bool(specialist_flags.get("log_intelligence")),
        regression_watchman_enabled=bool(specialist_flags.get("regression_watchman")),
        change_ownership_enabled=bool(specialist_flags.get("change_ownership")),
    )
    state["initial_workflow_plan"] = initial_plan
    state["workflow_plan"] = explained_plan
    state["workflow_verification"] = verify_workflow_execution(explained_plan, state)
    return state
