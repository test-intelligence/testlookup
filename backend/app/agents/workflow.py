"""
LangGraph workflow orchestration for the multi-agent pipeline.

Improvements over v1:
  1. Conditional skip — if no failed tests, jump straight to summary (skip anomaly + analysis)
  2. Parallel fan-out — anomaly_detection and root_cause_analysis run concurrently after ingestion
     (both read from ingestion outputs; their state fields are non-overlapping with Annotated reducers)
  3. Conditional triage — skip triage stage when no analyses meet the confidence threshold
  4. Two compiled graphs: offline (5-stage) and live (summary-only, for post-live-run processing)
  5. Stage-level checkpointing — each stage's output is persisted to DB after completion,
     enabling resume from last successful stage on pipeline retry
"""
import asyncio
import contextlib
import hashlib
import importlib.metadata
import json
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Optional, cast

from langgraph.graph import END, StateGraph

from app.models.enums import PipelineRunStatus
from app.services.pipeline_lease import (
    LeaseLost,
    acquire_lease_fields,
    fence_or_raise,
    held_lease,
    release_lease_fields,
)
from app.services.pipeline_cancellation import PipelineCancelled, is_cancel_requested
from app.services.workflow_run_state import (
    DEGRADED,
    REVIEW_NOT_APPLICABLE,
    apply_transition,
    is_resumable,
    passes_without_review,
)

from app.agents.analysis_agent import AnalysisAgent
from app.agents.anomaly_agent import AnomalyDetectionAgent
from app.agents.cluster_agent import ClusterAgent
from app.agents.contract_agent import ContractAgent
from app.agents.log_intelligence_agent import LogIntelligenceAgent
from app.agents.regression_watchman import RegressionWatchman
from app.agents.change_ownership_agent import ChangeOwnershipAgent
from app.agents.defect_commander import DefectCommander
from app.agents.decision_report_agent import DecisionReportAgent
from app.agents.decision_report_critic_agent import DecisionReportCriticAgent
from app.agents.flaky_sentinel_agent import FlakySentinelAgent
from app.agents.gap_detection_agent import GapDetectionAgent
from app.agents.ingestion_agent import IngestionAgent
from app.agents.release_risk_agent import ReleaseRiskAgent
from app.agents.report_refinement_agent import ReportRefinementAgent
from app.agents.state import WorkflowState
from app.models.agent_contracts import degraded_contract
from app.services.run_input_fingerprint import detect_run_input_drift
from app.agents.summary_agent import SummaryAgent
from app.agents.test_health_agent import TestHealthAgent
from app.agents.triage_agent import DefectTriageAgent
from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.enums import ExecutionPath
from app.models.postgres import AgentPipelineRun, AgentStageResult, TestRun
from app.services.agent_planner import (
    _DEEP_STAGES as _PLANNER_DEEP_STAGES,
    _LIVE_STAGES as _PLANNER_LIVE_STAGES,
    attach_workflow_plan_and_verification,
    build_workflow_plan,
)
from app.services.agent_capability_registry import get_capability
from app.services.agent_step_tracing import trace_agent_step
from app.services.pipeline_event_log import emit_event
from app.services.evidence_sanitizer import (
    sanitize_persistence_payload,
    sanitize_reference_text,
)
from app.services.eval_provenance_service import current_eval_manifest_checksum
from app.core.metrics import pipeline_execution_context_persist_failures_total

import structlog

# WF-3: Stage classification for partial-completion logic
DEEP_REQUIRED_STAGES = frozenset({"ingestion", "anomaly_detection", "failure_clustering", "root_cause_analysis", "summary", "decision_report", "decision_report_critic"})
DEEP_OPTIONAL_STAGES = frozenset({"triage", "contract_validation", "log_intelligence", "regression_watchman", "change_ownership", "gap_detection", "report_refinement", "flaky_sentinel", "test_health", "release_risk"})

# Stages the wall-clock budget may never skip. Both are deterministic (no LLM
# call) and together they are what publishes a truncated run as a *report that
# names its own gaps* rather than as silence. Skipping them to reclaim a few
# seconds would convert an honest degraded answer into no answer.
_DEADLINE_EXEMPT_STAGES = frozenset({"decision_report", "decision_report_critic"})

logger = structlog.get_logger("agents.workflow")


def _safe_workflow_error(exc: BaseException, *, limit: int = 1200) -> str:
    safe, _, _ = sanitize_reference_text(str(exc), limit=limit)
    return f"{type(exc).__name__}: {safe}"

# These outputs carry the producing pipeline ID in PostgreSQL and/or the
# immutable Mongo evidence key. Reusing them in a different pipeline would
# make the terminal critic look for authority that cannot belong to the retry.
_PIPELINE_BOUND_CHECKPOINT_STAGES = frozenset({
    "failure_clustering",
    "cluster_investigation_dispatch",
    "cluster_investigation_join",
    "release_risk",
    "decision_report",
    "decision_report_critic",
})

# Singleton agent instances (stateless — safe to share across concurrent pipeline runs)
_ingestion     = IngestionAgent()
_anomaly       = AnomalyDetectionAgent()
_analysis      = AnalysisAgent()
_summary       = SummaryAgent()
_triage        = DefectTriageAgent()
_cluster       = ClusterAgent()
_contract_agent = ContractAgent()
_log_intelligence = LogIntelligenceAgent()
_regression_watchman = RegressionWatchman()
_change_ownership = ChangeOwnershipAgent()
_defect_commander = DefectCommander()
_gap_detection = GapDetectionAgent()
_report_refinement = ReportRefinementAgent()
_flaky_sentinel = FlakySentinelAgent()
_test_health   = TestHealthAgent()
_release_risk  = ReleaseRiskAgent()
_decision_report = DecisionReportAgent()
_decision_report_critic = DecisionReportCriticAgent()


def _canonical_checksum(data: Any) -> str:
    """Return a stable checksum for replay/audit comparisons."""
    try:
        payload = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        payload = json.dumps(str(data), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stage_input_checksum(state: Any) -> str:
    """Checksum a stage's INPUT state for the replay breadcrumb.

    ``pipeline_deadline_ts`` is excluded on purpose: it is scheduling metadata
    for one attempt -- a monotonic instant that necessarily differs between
    attempts -- so including it would make two attempts over byte-identical
    inputs record different input hashes, which is precisely the drift a replay
    hash exists to rule out.
    """
    if isinstance(state, dict):
        state = {
            k: v
            for k, v in state.items()
            if k not in ("pipeline_deadline_ts", "_fencing_token", "_attempt")
        }
    return _canonical_checksum(state)


def _runtime_version_snapshot() -> dict[str, str]:
    """Capture package versions that affect graph routing and agent output."""
    versions: dict[str, str] = {}
    for package in ("langgraph", "langchain", "langchain-core", "pydantic", "scikit-learn"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            continue
    # AI-F2: the prompt set is a versioned artifact too — a prompt edit changes
    # agent output exactly like a package bump. Stable digest over the
    # registered prompt id → version/hash map.
    try:
        from app.services.prompt_registry import registry_digest

        versions["prompt_registry"] = registry_digest()[:12]
    except Exception:  # pragma: no cover — snapshot must never break the pipeline
        pass
    return versions


def _checkpoint_restore_metadata(stage: AgentStageResult) -> dict[str, Any] | None:
    """Return trusted replay metadata for a completed checkpoint, if present."""
    result_data = stage.result_data if isinstance(stage.result_data, dict) else {}
    replay = result_data.get("_replay")
    if not isinstance(replay, dict):
        return None
    expected_checksum = replay.get("output_checksum_sha256")
    runtime_versions = replay.get("runtime_versions")
    if (
        not isinstance(expected_checksum, str)
        or len(expected_checksum) != 64
        or any(char not in "0123456789abcdef" for char in expected_checksum)
    ):
        return None
    if not isinstance(runtime_versions, dict) or not runtime_versions:
        return None
    attempt = replay.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        return None
    attempt_key = replay.get("attempt_idempotency_key")
    if (
        not isinstance(attempt_key, str)
        or len(attempt_key) != 64
        or any(char not in "0123456789abcdef" for char in attempt_key)
    ):
        return None
    checkpoint_data = stage.checkpoint_data
    if not isinstance(checkpoint_data, dict):
        return None
    actual_checksum = _canonical_checksum(checkpoint_data)
    if actual_checksum != expected_checksum:
        return None
    return {
        "source_pipeline_run_id": str(stage.pipeline_run_id),
        "stage_name": stage.stage_name,
        "input_checksum_sha256": replay.get("input_checksum_sha256"),
        "output_checksum_sha256": expected_checksum,
        "runtime_versions": runtime_versions,
        "attempt": attempt,
        "attempt_idempotency_key": attempt_key,
    }


def _prompt_registry_versions() -> dict[str, str]:
    """Never-raising wrapper over the registry's full version-tag map."""
    try:
        from app.services.prompt_registry import registry_versions

        return registry_versions()
    except Exception:  # pragma: no cover — stamping must never break the pipeline
        return {}


async def _resolve_analysis_mode_snapshot() -> dict[str, Any]:
    """Resolve analysis mode once so a pipeline is reproducible end-to-end."""
    from app.services.analysis_router import (
        refresh_analysis_mode_from_cache,
        resolve_analysis_mode_with_reason,
    )

    requested = settings.ANALYSIS_MODE.lower()
    try:
        from app.db.redis_client import get_redis  # noqa: PLC0415

        redis = get_redis()
        cached = await redis.get("config:analysis_mode")
        if isinstance(cached, bytes):
            cached = cached.decode("utf-8", errors="ignore")
        if cached in ("llm", "ml", "rules", "auto"):
            requested = cached
    except Exception:
        pass

    await refresh_analysis_mode_from_cache()
    resolved, resolution_reason = resolve_analysis_mode_with_reason()
    return {
        "requested": requested,
        "resolved": resolved,
        # Why this mode and not another. Previously this only ever reached a log
        # line, so a trail showing requested=auto / resolved=rules could not say
        # whether the LLM was unreachable, unconfigured, or simply out-ranked by
        # a trained ML model.
        "resolution_reason": resolution_reason,
        "provider": settings.LLM_PROVIDER,
        "model": settings.LLM_MODEL,
        # E7.4: recorded so a manual retry can SEE an AI_OFFLINE_MODE flip.
        # Without it, ``pipeline_retry_config`` compares the frozen snapshot
        # against the live setting for this field and they trivially agree --
        # the one config change most likely to motivate a retry would be the
        # one change it could not detect.
        "offline": bool(settings.AI_OFFLINE_MODE),
        "analysis_mode_env": settings.ANALYSIS_MODE,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }


# ── LangGraph node functions ──────────────────────────────────────────────────

async def ingestion_node(state: WorkflowState) -> dict:
    return await _ingestion.run(cast(dict[str, Any], state))


async def anomaly_node(state: WorkflowState) -> dict:
    return await _anomaly.run(cast(dict[str, Any], state))


async def analysis_node(state: WorkflowState) -> dict:
    # Fast-path guard: the unconditional add_edge("ingestion", "root_cause_analysis") means
    # this node is always scheduled. Return early when ingestion found no failures so we
    # don't burn LLM tokens on an all-green run.
    if not state.get("failed_test_ids"):
        pipeline_run_id = state.get("pipeline_run_id", "")
        logger.info(
            "fast_path_skip",
            stage="root_cause_analysis",
            pipeline_run_id=pipeline_run_id,
        )
        await _write_stage_skipped(
            pipeline_run_id,
            "root_cause_analysis",
            skipped_reason="No failed tests detected — analysis not required",
            execution_path=ExecutionPath.ALL_GREEN_SKIP,
        )
        return {
            "completed_stages": ["root_cause_analysis"],
            "skipped_stages": ["root_cause_analysis"],
            "execution_path": ExecutionPath.ALL_GREEN_SKIP,
            "errors": [],
            "current_stage": "summary",
        }
    return await _analysis.run(cast(dict[str, Any], state))


async def summary_node(state: WorkflowState) -> dict:
    return await _summary.run(cast(dict[str, Any], state))


async def triage_node(state: WorkflowState) -> dict:
    return await _triage.run(cast(dict[str, Any], state))


async def cluster_node(state: WorkflowState) -> dict:
    # Fast-path guard: the unconditional add_edge("ingestion", "failure_clustering") means
    # this node is always scheduled in deep mode. Return early for all-green runs.
    if not state.get("failed_test_ids"):
        pipeline_run_id = state.get("pipeline_run_id", "")
        logger.info(
            "fast_path_skip",
            stage="failure_clustering",
            pipeline_run_id=pipeline_run_id,
        )
        await _write_stage_skipped(
            pipeline_run_id,
            "failure_clustering",
            skipped_reason="No failed tests detected — clustering not required",
            execution_path=ExecutionPath.ALL_GREEN_SKIP,
        )
        return {
            "completed_stages": ["failure_clustering"],
            "skipped_stages": ["failure_clustering"],
            "execution_path": ExecutionPath.ALL_GREEN_SKIP,
            "errors": [],
            "current_stage": "summary",
        }
    return await _cluster.run(cast(dict[str, Any], state))


async def cluster_investigation_dispatch_node(state: WorkflowState) -> dict:
    """Freeze cluster authority, then stage idempotent child dispatches."""
    from app.agents.deep_persistence import persist_failure_cluster_snapshot
    from app.services.cluster_investigation_orchestrator import (
        stage_cluster_investigations,
    )

    await persist_failure_cluster_snapshot(
        state["test_run_id"],
        state["pipeline_run_id"],
        list(state.get("failure_clusters") or []),
    )
    plan = await stage_cluster_investigations(
        parent_pipeline_run_id=state["pipeline_run_id"],
        project_id=state["project_id"],
        run_id=state["test_run_id"],
        frozen_settings=dict(state.get("cluster_child_settings") or {}),
        cost_budget_decision={
            "action": state.get("_cost_budget_action"),
            "mode_override": state.get("_cost_budget_mode_override"),
            "block": bool(state.get("_cost_budget_block")),
            "rationale": state.get("_cost_budget_rationale"),
            "utilization_pct": state.get("_cost_budget_utilization_pct"),
        },
    )
    return {
        "cluster_investigation_plan": plan,
        "completed_stages": ["cluster_investigation_dispatch"],
        "current_stage": "cluster_investigation_join",
        "errors": [],
    }


async def cluster_investigation_join_node(state: WorkflowState) -> dict:
    """Join children, or schedule an immutable async report supersession."""
    from app.services.cluster_investigation_orchestrator import (
        wait_for_cluster_children,
    )

    settings_snapshot = dict(state.get("cluster_child_settings") or {})
    if state.get("async_decision_report_supersession_enabled") is True:
        from app.services.decision_report_supersession_service import (
            schedule_decision_report_supersession,
        )

        request = await schedule_decision_report_supersession(
            project_id=str(state["project_id"]),
            test_run_id=str(state["test_run_id"]),
            parent_pipeline_run_id=str(state["pipeline_run_id"]),
        )
        joined = {
            "status": "pending",
            "selected_count": 0,
            "completed_count": 0,
            "children": [],
            "stop_reasons": ["cluster_investigation_pending"],
            "supersession_request": request,
        }
        return {
            "cluster_investigation_results": joined,
            "completed_stages": ["cluster_investigation_join"],
            "current_stage": "summary",
            "stage_quality": "degraded",
            "errors": ["cluster investigations deferred to asynchronous report supersession"],
        }
    aggregate = settings_snapshot.get("aggregate_budget")
    timeout = int((aggregate or {}).get("max_seconds", 0) or 0)
    joined = await wait_for_cluster_children(
        parent_pipeline_run_id=state["pipeline_run_id"],
        timeout_seconds=timeout,
    )
    dispatch = state.get("cluster_investigation_plan") or {}
    capacity_skips = list(dispatch.get("dispatch_capacity_skips") or [])
    if capacity_skips:
        joined = {
            **joined,
            "status": "degraded",
            "stop_reasons": sorted({
                *(joined.get("stop_reasons") or []),
                "project_child_capacity_exhausted",
            }),
            "dispatch_capacity_skips": capacity_skips,
        }
    degraded = joined.get("status") == "degraded"
    return {
        "cluster_investigation_results": joined,
        "completed_stages": ["cluster_investigation_join"],
        "current_stage": "summary",
        "stage_quality": "degraded" if degraded else state.get("stage_quality"),
        "errors": [
            f"cluster investigations degraded: {', '.join(joined.get('stop_reasons') or [])}"
        ] if degraded else [],
    }


# Re-exported so the single definition lives beside the contract models both
# the nodes and the specialist agents import. Kept under the original name
# because the flag-disabled branches here already read that way.
_degraded_contract = degraded_contract


async def contract_validation_node(state: WorkflowState) -> dict:
    """Run the default-off, server-scoped API contract specialist.

    Both branches live on the agent: ``disabled_delta`` for the flag-off skip,
    ``run`` for the stage itself. The node stays a thin router so the agent
    owns its own stage record — ``run`` is what calls ``mark_stage_running`` /
    ``mark_stage_done``, and before it existed ``contract_validation`` produced
    findings while writing no lifecycle event at all.
    """
    if not state.get("contract_agent_enabled"):
        return _contract_agent.disabled_delta()
    return await _contract_agent.run(cast(dict[str, Any], state))


async def log_intelligence_node(state: WorkflowState) -> dict:
    """Run the default-off, bounded log/trace specialist per failure cluster.

    Both branches live on the agent: ``disabled_delta`` for the flag-off skip,
    ``run`` for the stage itself (cluster-representative selection, the bounded
    per-cluster investigation, and the no-context degraded contract from #842).
    """
    if not state.get("log_intelligence_enabled"):
        return _log_intelligence.disabled_delta()
    return await _log_intelligence.run(cast(dict[str, Any], state))


async def defect_commander_node(state: WorkflowState) -> dict:
    """Run the default-off defect promoter.

    The only MUTATING stage in the deep graph: it writes a Defect row and, when
    ``JIRA_ENABLED`` is set, files a ticket. It is therefore gated twice over —
    this project flag (absent = off, since a missing feature flag evaluates
    False) and ``JIRA_ENABLED`` (default False) for the outward-facing half.

    Both branches live on the agent, like the other specialists: ``run`` picks
    a cluster deterministically and drives the stage lifecycle, while
    ``disabled_delta`` records a skip WITHOUT a lifecycle call, so "off" never
    looks like "ran and promoted nothing".
    """
    if not state.get("defect_commander_enabled"):
        return _defect_commander.disabled_delta()
    return await _defect_commander.run(cast(dict[str, Any], state))


async def regression_watchman_node(state: WorkflowState) -> dict:
    """Run the default-off baseline/regression specialist for failed clusters."""
    if not state.get("regression_watchman_enabled"):
        return {
            "regression_classification": {},
            "skipped_stages": ["regression_watchman"],
            "completed_stages": ["regression_watchman"],
            "current_stage": "gap_detection",
            "errors": [],
        }
    if not state.get("failure_clusters"):
        return {
            "regression_classification": {},
            "completed_stages": ["regression_watchman"],
            "current_stage": "gap_detection",
            "errors": [],
        }
    result = await _regression_watchman.run(cast(dict[str, Any], state))
    return {
        "regression_classification": result.get("regression_classification", {}),
        "agent_contracts": result.get("agent_contracts", {}),
        "completed_stages": ["regression_watchman"],
        "current_stage": "gap_detection",
        "errors": list(result.get("errors") or []),
    }
async def change_ownership_node(state: WorkflowState) -> dict:
    """Run the default-off baseline/change and ownership specialist."""
    if not state.get("change_ownership_enabled"):
        from app.models.agent_contracts import ChangeOwnershipAgentOutput  # noqa: PLC0415

        result = {"status": "not_enough_evidence", "baseline_diff": {}, "ownership_resolutions": [], "summary": "Change/Ownership feature flag is disabled."}
        contracted = _degraded_contract(
            ChangeOwnershipAgentOutput,
            {"change_ownership_findings": result},
            agent_name="change_ownership",
            decision_reason="no_change_ownership_flag_disabled",
        )
        return {
            "change_ownership_findings": contracted.get("change_ownership_findings", result),
            "agent_contracts": contracted.get("agent_contracts", {}),
            "skipped_stages": ["change_ownership"],
            "completed_stages": ["change_ownership"],
            "current_stage": "gap_detection",
            "errors": [],
        }
    result = await _change_ownership.run(dict(state))
    from app.models.agent_contracts import ChangeOwnershipAgentOutput, validate_agent_contract
    contracted = validate_agent_contract(ChangeOwnershipAgentOutput, {"change_ownership_findings": result}, agent_name="change_ownership", fallback_used=result.get("status") != "complete", confidence=70 if result.get("status") == "complete" else 0, evidence_refs=([{"source": "regression_diff_service", "kind": "baseline_diff"}, {"source": "ownership_resolver_service", "kind": "cluster_ownership"}] if result.get("status") == "complete" else []), decision_reason=str(result.get("summary") or "change_and_ownership_evaluated"))
    return {"change_ownership_findings": contracted.get("change_ownership_findings", result), "agent_contracts": contracted.get("agent_contracts", {}), "completed_stages": ["change_ownership"], "current_stage": "gap_detection", "errors": [] if result.get("status") != "failed" else ["change_ownership_failed"]}
async def gap_detection_node(state: WorkflowState) -> dict:
    # AIQ-P4 optional stage. The node is always present so the graph topology is
    # identical whether the flag is on or off; when off it early-returns a skip
    # delta and the deterministic agent never runs.
    if not settings.AIQ_GAP_REFINEMENT_ENABLED:
        pipeline_run_id = state.get("pipeline_run_id", "")
        await _write_stage_skipped(
            pipeline_run_id,
            "gap_detection",
            skipped_reason="AIQ_GAP_REFINEMENT_ENABLED is off — gap detection not run",
            execution_path=ExecutionPath.CONDITIONAL_SKIP,
        )
        return {
            "completed_stages": ["gap_detection"],
            "skipped_stages": ["gap_detection"],
            "current_stage": "report_refinement",
        }
    return await _gap_detection.run(cast(dict[str, Any], state))


async def report_refinement_node(state: WorkflowState) -> dict:
    # AIQ-P4 optional stage. See gap_detection_node for the flag-gate rationale.
    if not settings.AIQ_GAP_REFINEMENT_ENABLED:
        pipeline_run_id = state.get("pipeline_run_id", "")
        await _write_stage_skipped(
            pipeline_run_id,
            "report_refinement",
            skipped_reason="AIQ_GAP_REFINEMENT_ENABLED is off — report refinement not run",
            execution_path=ExecutionPath.CONDITIONAL_SKIP,
        )
        return {
            "completed_stages": ["report_refinement"],
            "skipped_stages": ["report_refinement"],
            "current_stage": "flaky_sentinel",
        }
    return await _report_refinement.run(cast(dict[str, Any], state))


async def flaky_sentinel_node(state: WorkflowState) -> dict:
    return await _flaky_sentinel.run(cast(dict[str, Any], state))


async def test_health_node(state: WorkflowState) -> dict:
    return await _test_health.run(cast(dict[str, Any], state))


async def release_risk_node(state: WorkflowState) -> dict:
    return await _release_risk.run(cast(dict[str, Any], state))


async def decision_report_node(state: WorkflowState) -> dict:
    # F-8: specialists re-query the TestRun row in their own sessions rather
    # than receiving the bundle their capability spec claims as input, so the
    # row can move underneath them. Compare what ingestion saw with what is
    # true now, and record it as a WARNING on the bundle. Non-blocking on
    # purpose: an error-severity flag here is what stopped 24 reports from
    # publishing earlier today. Measure the rate first.
    drift = await detect_run_input_drift(cast(dict[str, Any], state))
    if drift:
        state["run_input_drift"] = drift  # type: ignore[typeddict-unknown-key]
    return await _decision_report.run(cast(dict[str, Any], state))


async def decision_report_critic_node(state: WorkflowState) -> dict:
    return await _decision_report_critic.run(cast(dict[str, Any], state))


# ── Routing functions (conditional edges) ────────────────────────────────────

def _append_route_decision(state: WorkflowState, payload: dict[str, Any]) -> None:
    """Mirror sync router decisions into state for durable PG metadata."""
    decisions = state.setdefault("_workflow_route_decisions", [])  # type: ignore[typeddict-unknown-key]
    if isinstance(decisions, list):
        decisions.append(payload)


def _emit_route_decision(
    state: WorkflowState,
    *,
    decision_point: str,
    chosen: str,
    rationale: str,
    alternatives: list[str] | None = None,
    context: dict | None = None,
) -> None:
    """Record a structured routing decision from a sync graph router.

    LangGraph calls routing functions synchronously, so we cannot await the
    Mongo write here. The decision is mirrored into workflow state so
    ``_mark_pipeline_done`` can persist it durably in Postgres execution
    metadata; the background Mongo write remains a low-latency timeline copy.
    """
    pipeline_run_id = state.get("pipeline_run_id", "")
    if not pipeline_run_id:
        return
    payload: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "decision_point": decision_point,
        "chosen": chosen,
        "rationale": rationale,
    }
    if alternatives:
        payload["alternatives"] = alternatives
    if context:
        payload["context"] = context
    _append_route_decision(state, payload)

    logger.info(
        "workflow_route_decision",
        pipeline_run_id=pipeline_run_id,
        decision_point=decision_point,
        chosen=chosen,
        rationale=rationale,
    )
    try:
        import asyncio as _asyncio
        _asyncio.create_task(
            emit_event(
                pipeline_run_id,
                "decision_made",
                stage_name="workflow",
                detail=payload,
            )
        )
    except RuntimeError:
        # No running loop (e.g. sync test harness). The state mirror above is
        # still persisted when the pipeline completes.
        pass


def _route_after_ingestion(state: WorkflowState) -> str:
    """
    Fast-path: if the run has no failures there is nothing to analyse.
    Route directly to summary, which will generate an "all green" report.
    Otherwise route to anomaly_detection (which runs in parallel with analysis
    via the branching edges added in the graph).
    """
    if not state.get("failed_test_ids"):
        _emit_route_decision(
            state,
            decision_point="route_after_ingestion",
            chosen="summary",
            rationale="no failed tests — skipping anomaly_detection and root_cause_analysis",
            alternatives=["anomaly_detection"],
            context={"total_tests": state.get("total_tests")},
        )
        return "summary"
    _emit_route_decision(
        state,
        decision_point="route_after_ingestion",
        chosen="anomaly_detection",
        rationale=f"{len(state.get('failed_test_ids', []))} failed tests — fan out to analysis",
    )
    return "anomaly_detection"


def _route_after_summary(state: WorkflowState) -> str:
    """
    Skip triage when no analyses reach the confidence threshold.
    Saves Jira API calls and reduces noise in the ticket tracker.
    Used by the offline pipeline only.
    """
    analyses = state.get("analyses", {})
    threshold = settings.AI_CONFIDENCE_THRESHOLD
    triageable = [
        tc_id for tc_id, a in analyses.items()
        if a.get("confidence_score", 0) >= threshold and not a.get("is_flaky", False)
    ]
    if not triageable:
        _emit_route_decision(
            state,
            decision_point="route_after_summary",
            chosen="end",
            rationale=(
                f"no analyses above confidence threshold {threshold} — skipping triage"
            ),
            alternatives=["triage"],
            context={
                "analyses_count": len(analyses),
                "threshold": threshold,
            },
        )
        return END
    _emit_route_decision(
        state,
        decision_point="route_after_summary",
        chosen="triage",
        rationale=f"{len(triageable)} analyses meet confidence ≥ {threshold}",
    )
    return "triage"


def _route_after_summary_deep(state: WorkflowState) -> str:
    """
    Deep pipeline variant: specialist stages ALWAYS run.
    Triage is skipped when no analyses meet the confidence threshold,
    but execution jumps directly to flaky_sentinel so the full
    flaky_sentinel → test_health → release_risk chain still executes.
    Never returns END so all specialist stages are guaranteed to run.
    """
    analyses = state.get("analyses", {})
    threshold = settings.AI_CONFIDENCE_THRESHOLD
    triageable = [
        tc_id for tc_id, a in analyses.items()
        if a.get("confidence_score", 0) >= threshold and not a.get("is_flaky", False)
    ]
    if not triageable:
        _emit_route_decision(
            state,
            decision_point="route_after_summary_deep",
            chosen="flaky_sentinel",
            rationale=(
                f"no analyses above confidence threshold {threshold} — skipping triage, "
                "continuing to specialist stages"
            ),
            alternatives=["triage"],
            context={
                "analyses_count": len(analyses),
                "threshold": threshold,
            },
        )
        return "flaky_sentinel"
    _emit_route_decision(
        state,
        decision_point="route_after_summary_deep",
        chosen="triage",
        rationale=f"{len(triageable)} analyses meet confidence ≥ {threshold}",
    )
    return "triage"


# ── Graph construction ────────────────────────────────────────────────────────

def _build_offline_graph() -> StateGraph:
    """
    Full offline pipeline with parallel anomaly + analysis fan-out.

    Graph topology:
                                ┌─ anomaly_detection ─┐
      ingestion ─(conditional)──┤                      ├─ summary ─(conditional)─ triage ─ END
                                └─ root_cause_analysis─┘
                   (no failures) └──────────────────────────────────────────────────────── summary ─ END
    """
    graph = StateGraph(WorkflowState)

    graph.add_node("ingestion",            _make_checkpointed_node(ingestion_node, "ingestion"))
    graph.add_node("anomaly_detection",    _make_checkpointed_node(anomaly_node, "anomaly_detection"))
    graph.add_node("root_cause_analysis",  _make_checkpointed_node(analysis_node, "root_cause_analysis"))
    graph.add_node("summary",              _make_checkpointed_node(summary_node, "summary"))
    graph.add_node("triage",               _make_checkpointed_node(triage_node, "triage"))


    graph.set_entry_point("ingestion")

    # Conditional routing after ingestion:
    # - failures found  → fan-out to BOTH anomaly_detection AND root_cause_analysis (parallel)
    # - no failures     → skip directly to summary
    graph.add_conditional_edges(
        "ingestion",
        _route_after_ingestion,
        {
            "anomaly_detection": "anomaly_detection",
            # The all-green branch routes through root_cause_analysis rather
            # than jumping straight to summary. `add_edge` below fires
            # unconditionally, so a direct ingestion→summary edge would reach
            # summary one hop earlier than the analysis branch and trigger it in
            # a SECOND superstep — running summary (and everything after it)
            # twice on green runs. analysis_node early-returns when there are no
            # failures, so this costs nothing and keeps every predecessor of
            # summary at the same depth.
            "summary":           "root_cause_analysis",
        },
    )

    # Parallel fan-out: ingestion also unconditionally fans out to root_cause_analysis.
    # NOTE: LangGraph's add_edge always fires regardless of conditional routing above,
    # so root_cause_analysis is always scheduled. analysis_node contains an early-return
    # guard that skips execution when failed_test_ids is empty (all-green fast-path).
    graph.add_edge("ingestion", "root_cause_analysis")

    # Fan-in: both parallel branches feed into summary
    graph.add_edge("anomaly_detection",   "summary")
    graph.add_edge("root_cause_analysis", "summary")

    # Conditional routing after summary: skip triage if no triageable analyses
    graph.add_conditional_edges(
        "summary",
        _route_after_summary,
        {
            "triage": "triage",
            END:      END,
        },
    )

    graph.add_edge("triage", END)

    return graph


def _build_deep_graph() -> StateGraph:
    """
    Extended deep-investigation pipeline with clustering, flaky sentinel, test health, and release risk.

    Graph topology:
                                    ┌─ anomaly_detection ─────────────────────────────────┐
      ingestion ─(conditional)──────┤                                                      ├─ summary ─(conditional)─ triage ─┐
                                    └─ root_cause_analysis ─┐                              │                                   ├─ flaky_sentinel ─ test_health ─ release_risk ─ decision_report ─ END
                                    └─ failure_clustering   ─┘  (fan-in to summary)        │  (no triage) ─────────────────────┘
                  (no failures) └──────────────────────────────────────────────────────────── summary ─ flaky_sentinel ─ test_health ─ release_risk ─ decision_report ─ END
    """
    graph = StateGraph(WorkflowState)

    graph.add_node("ingestion",            _make_checkpointed_node(ingestion_node, "ingestion"))
    graph.add_node("anomaly_detection",    _make_checkpointed_node(anomaly_node, "anomaly_detection"))
    graph.add_node("failure_clustering",   _make_checkpointed_node(cluster_node, "failure_clustering"))
    graph.add_node("cluster_investigation_dispatch", _make_checkpointed_node(
        cluster_investigation_dispatch_node, "cluster_investigation_dispatch"
    ))
    graph.add_node("cluster_investigation_join", _make_checkpointed_node(
        cluster_investigation_join_node, "cluster_investigation_join"
    ))
    graph.add_node("root_cause_analysis",  _make_checkpointed_node(analysis_node, "root_cause_analysis"))
    graph.add_node("summary",              _make_checkpointed_node(summary_node, "summary"))
    graph.add_node("triage",               _make_checkpointed_node(triage_node, "triage"))
    graph.add_node("contract_validation",  _make_checkpointed_node(contract_validation_node, "contract_validation"))
    graph.add_node("log_intelligence", _make_checkpointed_node(log_intelligence_node, "log_intelligence"))
    graph.add_node("regression_watchman", _make_checkpointed_node(regression_watchman_node, "regression_watchman"))
    graph.add_node("change_ownership", _make_checkpointed_node(change_ownership_node, "change_ownership"))
    graph.add_node("defect_commander", _make_checkpointed_node(defect_commander_node, "defect_commander"))

    graph.add_node("gap_detection",        _make_checkpointed_node(gap_detection_node, "gap_detection"))
    graph.add_node("report_refinement",    _make_checkpointed_node(report_refinement_node, "report_refinement"))
    graph.add_node("flaky_sentinel",       _make_checkpointed_node(flaky_sentinel_node, "flaky_sentinel"))
    graph.add_node("test_health",          _make_checkpointed_node(test_health_node, "test_health"))
    graph.add_node("release_risk",         _make_checkpointed_node(release_risk_node, "release_risk"))
    graph.add_node("decision_report",      _make_checkpointed_node(decision_report_node, "decision_report"))
    graph.add_node("decision_report_critic", _make_checkpointed_node(decision_report_critic_node, "decision_report_critic"))

    graph.set_entry_point("ingestion")

    # Conditional routing after ingestion
    graph.add_conditional_edges(
        "ingestion",
        _route_after_ingestion,
        {
            "anomaly_detection": "anomaly_detection",
            # Green runs route via root_cause_analysis, not straight to summary —
            # same reason as the offline graph: a direct edge arrives one
            # superstep ahead of the analysis branch and makes summary run twice.
            "summary":           "root_cause_analysis",
        },
    )

    # Parallel fan-out from ingestion (unconditional — see note in _build_offline_graph).
    # Both nodes carry fast-path guards: they return immediately when failed_test_ids is empty.
    graph.add_edge("ingestion", "root_cause_analysis")

    # Fan-in: the two analysis branches → summary. BOTH are one hop from
    # ingestion, so LangGraph schedules them in the same superstep and `summary`
    # runs exactly once.
    #
    # The clustering branch used to fan in here too, via
    #   ingestion → failure_clustering → dispatch → join → summary
    # which is THREE hops against the other branches' one. LangGraph triggers a
    # node once per superstep in which any predecessor completed, so `summary`
    # ran twice — and took the whole specialist chain with it. Observed on the
    # homelab 2026-08-16: `route_after_summary_deep` fired twice in a single
    # pipeline, every skipped specialist stage was recorded twice, and
    # `decision_report_verification` logged BOTH "passed" and "failed" 464 ms
    # apart, with the failing pass winning.
    #
    # Clustering is therefore sequenced after summary instead. Nothing is lost:
    # `SummaryAgent` never read the cluster output — it passes a literal
    # `cluster_count=0` — while the stages that DO consume clusters
    # (decision_report and its critic, via deep_findings) all run later in the
    # chain. The cost is that clustering no longer overlaps analysis; the
    # correctness of running each stage once is worth more than that overlap.
    graph.add_edge("anomaly_detection",   "summary")
    graph.add_edge("root_cause_analysis", "summary")

    # After summary: triage if triageable, else jump to gap_detection
    # (specialist stages always run in the deep pipeline). AIQ-P4 inserts
    # gap_detection → report_refinement ahead of flaky_sentinel; both are
    # flag-gated optional stages that early-return a skip delta when off, so the
    # topology is identical whether AIQ_GAP_REFINEMENT_ENABLED is on or off.
    graph.add_conditional_edges(
        "summary",
        _route_after_summary_deep,
        {
            "triage":         "triage",
            "flaky_sentinel": "failure_clustering",
        },
    )

    # Clustering, sequenced here rather than fanned out from ingestion — see the
    # note on the summary fan-in above. Single-predecessor edges throughout, so
    # each of these runs exactly once.
    graph.add_edge("triage",            "failure_clustering")
    graph.add_edge("failure_clustering", "cluster_investigation_dispatch")
    graph.add_edge("cluster_investigation_dispatch", "cluster_investigation_join")
    # F-9: the four read-only specialists are independent -- disjoint outputs
    # (contract_findings / log_findings / regression_classification /
    # change_ownership_findings), no cross-reads, and every field they SHARE
    # (completed_stages, skipped_stages, errors, current_stage, agent_contracts)
    # already carries a reducer. They ran serially, so the chain cost the SUM of
    # their latencies instead of the slowest one.
    #
    # The earlier parallel attempt double-executed, and the reason is recorded
    # in the summary fan-in note above: LangGraph schedules by superstep, so a
    # join whose predecessors sit at DIFFERENT depths fires twice -- running
    # everything downstream twice. The fix is not to avoid fan-out, it is to
    # keep every predecessor at the SAME depth. All four below are direct
    # successors of cluster_investigation_join, so gap_detection has four
    # predecessors at one depth and fires exactly once.
    for _specialist in (
        "contract_validation",
        "log_intelligence",
        "regression_watchman",
        "change_ownership",
        # Fifth member of the same fan-out, NOT a new depth. gap_detection's
        # predecessors must all sit one hop from cluster_investigation_join --
        # a predecessor at a different depth makes the join fire twice and
        # re-runs everything downstream (see the summary fan-in note above).
        "defect_commander",
    ):
        graph.add_edge("cluster_investigation_join", _specialist)
        graph.add_edge(_specialist, "gap_detection")
    graph.add_edge("gap_detection",     "report_refinement")
    graph.add_edge("report_refinement", "flaky_sentinel")
    graph.add_edge("flaky_sentinel",  "test_health")
    graph.add_edge("test_health",     "release_risk")
    graph.add_edge("release_risk",    "decision_report")
    graph.add_edge("decision_report", "decision_report_critic")
    graph.add_edge("decision_report_critic", END)

    return graph


def _build_live_graph() -> StateGraph:
    """
    Lightweight post-live-run graph: skip anomaly detection and full analysis,
    just generate a summary from the live monitor's aggregated state.
    Used when the live run consumer triggers a pipeline after run_complete.
    """
    graph = StateGraph(WorkflowState)

    graph.add_node("ingestion", _make_checkpointed_node(ingestion_node, "ingestion"))
    graph.add_node("summary",   _make_checkpointed_node(summary_node, "summary"))

    graph.set_entry_point("ingestion")
    graph.add_edge("ingestion", "summary")
    graph.add_edge("summary", END)

    return graph

# ── Stage checkpointing ──────────────────────────────────────────────────────

async def _checkpoint_stage(
    pipeline_run_id: str,
    stage_name: str,
    stage_output: dict,
    *,
    input_checksum_sha256: str | None = None,
    fencing_token: str | None = None,
) -> None:
    """
    Persist a stage's output dict to the AgentStageResult row so the pipeline
    can resume from this point if a later stage fails.

    Fenced (E7.3): if this worker's lease was reclaimed the write is refused,
    so a slow-but-alive attempt cannot overwrite the checkpoints of the
    attempt that replaced it.
    """
    if not pipeline_run_id:
        return
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select as sa_select  # noqa: PLC0415

            await fence_or_raise(db, pipeline_run_id, fencing_token)

            result = await db.execute(
                sa_select(AgentStageResult).where(
                    AgentStageResult.pipeline_run_id == pipeline_run_id,
                    AgentStageResult.stage_name == stage_name,
                )
            )
            stage = result.scalar_one_or_none()
            if stage and stage.status == "completed":
                # Store the state delta produced by this stage for potential replay.
                # The attempt receipt is deterministic and persisted with the
                # checkpoint so a retry cannot silently reuse a different output.
                checkpoint_data = _safe_serialize(stage_output)
                attempt = stage.attempt if isinstance(stage.attempt, int) and stage.attempt >= 1 else 1
                output_checksum = _canonical_checksum(checkpoint_data)
                attempt_key = _canonical_checksum({
                    "pipeline_run_id": str(pipeline_run_id),
                    "stage_name": stage_name,
                    "attempt": attempt,
                    "output_checksum_sha256": output_checksum,
                })
                stage.checkpoint_data = checkpoint_data
                replay_metadata = {
                    "input_checksum_sha256": input_checksum_sha256,
                    "output_checksum_sha256": output_checksum,
                    "runtime_versions": _runtime_version_snapshot(),
                    "attempt": attempt,
                    "attempt_idempotency_key": attempt_key,
                }
                prior_result_data = stage.result_data if isinstance(stage.result_data, dict) else {}
                stage.result_data = {
                    **prior_result_data,
                    "_replay": replay_metadata,
                }
                stage.idempotency_key = attempt_key
                await db.commit()
    except LeaseLost:
        # Never swallowed: the caller must stop, not carry on writing.
        raise
    except Exception as exc:
        logger.warning(
            "checkpoint_write_failed",
            pipeline_run_id=pipeline_run_id,
            stage_name=stage_name,
            error_type=type(exc).__name__,
        )


@lru_cache(maxsize=1)
def _state_field_reducers() -> dict[str, Any]:
    """The reducer each ``WorkflowState`` field declares, read from the schema.

    Taken from the annotations rather than a hand-kept list: a second copy of
    this mapping would drift the moment a field is added, which is precisely
    the class of defect this function exists to fix.
    """
    from typing import get_type_hints  # noqa: PLC0415

    reducers: dict[str, Any] = {}
    try:
        hints = get_type_hints(WorkflowState, include_extras=True)
    except Exception as exc:  # pragma: no cover - annotation resolution failure
        logger.warning("state_reducers_unavailable", error_type=type(exc).__name__)
        return reducers
    for name, annotation in hints.items():
        for item in getattr(annotation, "__metadata__", ()):
            if callable(item):
                reducers[name] = item
                break
    return reducers


def _merge_checkpoint_stage_state(merged: dict, data: dict) -> None:
    """Fold one stage's checkpoint into the restored state, honouring reducers.

    ``dict.update`` is last-writer-wins, which silently discards every
    accumulating field the state declares -- ``agent_contracts``, ``analyses``,
    ``deep_findings``, ``completed_stages``, ``stage_metrics``, ``errors``.
    Restoring N stages kept only the Nth stage's contributions.

    The visible symptom was narrower than the loss: the critic saw stages
    marked complete whose contract was missing, failed
    ``completed_agent_contracts_present``, and failed the run closed. Because
    ``_load_checkpoint`` restores from the latest *failed or partial* prior
    run, the next attempt restored the same way -- so a pipeline that had gone
    partial could never publish a decision report again.
    """
    reducers = _state_field_reducers()
    for key, value in data.items():
        if key not in merged:
            merged[key] = value
            continue
        reducer = reducers.get(key)
        if reducer is None:
            merged[key] = value
            continue
        try:
            merged[key] = reducer(merged[key], value)
        except Exception as exc:
            # Never lose the stage over a reducer mismatch, but say so -- a
            # silent fallback here restores the very bug this replaced.
            logger.warning(
                "checkpoint_reducer_failed",
                field=key,
                error_type=type(exc).__name__,
            )
            merged[key] = value


async def _load_checkpoint(
    test_run_id: str,
    workflow_type: str,
    *,
    pipeline_run_id: str | None = None,
) -> Optional[dict]:
    """
    Look for a prior failed pipeline run for the same test_run_id.
    If one exists with completed stages, return the merged checkpoint state
    so the new run can skip those stages.
    """
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select as sa_select  # noqa: PLC0415

            # Same-pipeline resumes use the claimed pipeline as authority;
            # new pipeline attempts retain the latest-failed lookup.
            if pipeline_run_id is not None:
                result = await db.execute(
                    sa_select(AgentPipelineRun).where(
                        AgentPipelineRun.id == pipeline_run_id,
                        AgentPipelineRun.test_run_id == test_run_id,
                        AgentPipelineRun.workflow_type == workflow_type,
                    )
                )
            else:
                result = await db.execute(
                    sa_select(AgentPipelineRun)
                    .where(
                        AgentPipelineRun.test_run_id == test_run_id,
                        AgentPipelineRun.workflow_type == workflow_type,
                        # Resumable = failed, or completed with degraded stages
                        # (the rows that used to be ``partial``); the metadata
                        # half of that test is applied in Python below.
                        AgentPipelineRun.status.in_(
                            [PipelineRunStatus.FAILED.value, PipelineRunStatus.COMPLETED.value]
                        ),
                    )
                    .order_by(AgentPipelineRun.started_at.desc())
                    .limit(1)
                )
            prev_run = result.scalar_one_or_none()
            if not prev_run:
                return None
            prev_status = getattr(prev_run, "status", None)
            if prev_status is not None and not is_resumable(
                prev_status, getattr(prev_run, "execution_metadata", None)
            ):
                # completed but NOT degraded: nothing failed, nothing to resume.
                return None

            # Load completed stage checkpoints
            stages_result = await db.execute(
                sa_select(AgentStageResult)
                .where(
                    AgentStageResult.pipeline_run_id == prev_run.id,
                    AgentStageResult.status == "completed",
                )
            )
            completed_stages = stages_result.scalars().all()

            if not completed_stages:
                return None

            # Merge only checkpoints whose stored replay authority still
            # matches the checkpoint body. Invalid rows are rerun, not fatal.
            merged_state: dict = {}
            checkpoint_stage_names: list[str] = []
            checkpoint_replay_metadata: dict[str, dict[str, Any]] = {}
            for stage in completed_stages:
                if stage.stage_name in _PIPELINE_BOUND_CHECKPOINT_STAGES:
                    continue
                metadata = _checkpoint_restore_metadata(stage)
                if metadata is None:
                    logger.info(
                        "checkpoint_restore_skipped",
                        previous_pipeline_run_id=str(prev_run.id),
                        stage_name=stage.stage_name,
                        reason="missing_or_mismatched_replay_metadata",
                    )
                    continue
                data = stage.checkpoint_data if isinstance(stage.checkpoint_data, dict) else {}
                _merge_checkpoint_stage_state(merged_state, data)
                checkpoint_stage_names.append(stage.stage_name)
                checkpoint_replay_metadata[stage.stage_name] = metadata

            if checkpoint_stage_names:
                merged_state["_checkpoint_replay_metadata"] = checkpoint_replay_metadata
                logger.info(
                    "checkpoint_loaded",
                    previous_pipeline_run_id=str(prev_run.id),
                    stages=checkpoint_stage_names,
                )
                merged_state["_checkpoint_stages"] = checkpoint_stage_names
                return merged_state

    except Exception as exc:
        logger.warning(
            "checkpoint_load_failed",
            test_run_id=test_run_id,
            error_type=type(exc).__name__,
        )

    return None


async def _claim_pipeline_resume(
    pipeline_run_id: str, *, expected_attempt: int | None = None
) -> dict[str, Any] | None:
    """Atomically claim a failed / retry_wait / degraded pipeline for same-ID replay.

    The row lock is the idempotency boundary: only one worker can transition a
    terminal pipeline back to ``running``. Completed stages remain immutable;
    every other stage is reset to ``pending`` and receives a new attempt number
    before the graph is invoked.
    """
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select as sa_select  # noqa: PLC0415

        result = await db.execute(
            sa_select(AgentPipelineRun)
            .where(AgentPipelineRun.id == pipeline_run_id)
            .with_for_update()
        )
        pipeline = result.scalar_one_or_none()
        if pipeline is None or not is_resumable(pipeline.status, pipeline.execution_metadata):
            return None
        # E7.4: a cancelled run is never resumable, by any path. Without this a
        # resume queued before the cancel (or a manual retry racing one) would
        # restart a run the operator was told had stopped.
        if bool(getattr(pipeline, "cancel_requested", False)):
            return None
        # E7.2: a scheduled retry names the attempt it was queued for. If the
        # row has moved on (cancelled, or a manual retry already consumed that
        # attempt), this claim is stale and must do nothing.
        current_row_attempt = int(getattr(pipeline, "attempt", None) or 1)
        if expected_attempt is not None and current_row_attempt + 1 != int(expected_attempt):
            return None

        ownership = await db.execute(
            sa_select(TestRun.project_id).where(TestRun.id == pipeline.test_run_id)
        )
        project_id = ownership.scalar_one_or_none()
        if project_id is None:
            return None

        metadata = dict(pipeline.execution_metadata or {})
        initial_plan = metadata.get("initial_workflow_plan")
        if not isinstance(initial_plan, dict) or not isinstance(initial_plan.get("stages"), list):
            # Never reconstruct a plan during resume; the original authority is
            # required to keep budgets/flags and task selection stable.
            return None
        mode_snapshot = metadata.get("analysis_mode_resolution")
        if not isinstance(mode_snapshot, dict) or not mode_snapshot.get("resolved"):
            # Analysis routing is part of the replay authority. Legacy rows that
            # never persisted it must take a new pipeline attempt instead --
            # unless it failed during setup. _create_pipeline_run writes the
            # pipeline and its planned stages in one commit; if those stages
            # exist and none ever started, the pipeline failed before the
            # snapshot was persisted and has no routing to keep stable. An
            # outbox-driven pipeline cannot take a new attempt (its id is
            # fixed), so it resumes with a freshly resolved mode (re-audit N27).
            # A row with no planned stages is not one of these, and stays closed.
            from sqlalchemy import func as sa_func  # noqa: PLC0415

            counts = await db.execute(
                sa_select(
                    sa_func.count(),
                    sa_func.count(AgentStageResult.started_at),
                ).where(AgentStageResult.pipeline_run_id == pipeline.id)
            )
            planned, started = counts.one()
            if not planned or started:
                return None
            mode_snapshot = None

        current_attempt = metadata.get("resume_attempt", 0)
        if isinstance(current_attempt, bool) or not isinstance(current_attempt, int) or current_attempt < 0:
            current_attempt = 0
        resume_attempt = current_attempt + 1
        metadata["resume_attempt"] = resume_attempt
        metadata["resume_started_at"] = datetime.now(timezone.utc).isoformat()
        metadata["resume_source_pipeline_run_id"] = str(pipeline.id)
        pipeline.execution_metadata = metadata
        apply_transition(pipeline, PipelineRunStatus.RUNNING)
        pipeline.started_at = datetime.now(timezone.utc)
        # E7.2: the row-level attempt counter (migration 0173). ``resume_attempt``
        # in metadata predates it and stays for the replay breadcrumbs.
        pipeline.attempt = current_row_attempt + 1
        pipeline.next_retry_at = None
        # E7.3: a fresh lease and a NEW fencing token. The token rotation is
        # what stops a previous holder that is still alive: its next write is
        # refused rather than landing underneath this attempt.
        resume_token, lease_fields = acquire_lease_fields()
        for _field, _value in lease_fields.items():
            setattr(pipeline, _field, _value)

        stage_result = await db.execute(
            sa_select(AgentStageResult)
            .where(AgentStageResult.pipeline_run_id == pipeline.id)
            .order_by(AgentStageResult.stage_name)
        )
        for stage in stage_result.scalars().all():
            if stage.status == "completed":
                continue
            prior_attempt = stage.attempt if isinstance(stage.attempt, int) and stage.attempt >= 1 else 1
            prior_key = stage.idempotency_key
            stage.attempt = prior_attempt + 1
            stage.status = "pending"
            stage.started_at = None
            stage.completed_at = None
            stage.error = None
            stage.stop_reason = None
            stage.skipped_reason = None
            stage.execution_path = None
            stage.idempotency_key = None
            prior_result_data = stage.result_data if isinstance(stage.result_data, dict) else {}
            stage.result_data = {
                **prior_result_data,
                "_resume": {
                    "resume_attempt": resume_attempt,
                    "previous_attempt": prior_attempt,
                    "previous_idempotency_key": prior_key,
                },
            }

        await db.commit()
        return {
            "fencing_token": resume_token,
            "attempt": current_row_attempt + 1,
            "eval_manifest_checksum": metadata.get("eval_manifest_checksum"),
            "test_run_id": str(pipeline.test_run_id),
            "project_id": str(project_id),
            "workflow_type": pipeline.workflow_type,
            "initial_workflow_plan": initial_plan,
            "cluster_child_settings": metadata.get("cluster_child_settings") or {},
            "async_decision_report_supersession_enabled": bool(
                metadata.get("async_decision_report_supersession_enabled", False)
            ),
            "contract_agent_settings": metadata.get("contract_agent_settings") or {"enabled": False},
            "log_intelligence_settings": metadata.get("log_intelligence_settings") or {"enabled": False},
            "regression_watchman_settings": metadata.get("regression_watchman_settings") or {"enabled": False},
            "change_ownership_settings": metadata.get("change_ownership_settings") or {"enabled": False},
            "analysis_mode_resolution": mode_snapshot,
            "cost_budget_decision": metadata.get("cost_budget_decision") or {},
            "resume_attempt": resume_attempt,
        }


async def resume_pipeline(
    pipeline_run_id: str,
    build_number: str = "resume",
    *,
    expected_attempt: int | None = None,
) -> dict:
    """Resume a failed / retry_wait / degraded pipeline under its existing identity."""
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select as sa_select  # noqa: PLC0415

        result = await db.execute(
            sa_select(AgentPipelineRun).where(AgentPipelineRun.id == pipeline_run_id)
        )
        pipeline = result.scalar_one_or_none()
        if pipeline is None:
            raise ValueError("pipeline_not_found")
        workflow_type = str(pipeline.workflow_type)

    if workflow_type == "deep":
        return await run_deep_pipeline(
            build_number=build_number,
            pipeline_run_id=pipeline_run_id,
            expected_attempt=expected_attempt,
        )
    return await run_offline_pipeline(
        build_number=build_number,
        workflow_type=workflow_type,
        pipeline_run_id=pipeline_run_id,
        expected_attempt=expected_attempt,
    )
async def _persist_execution_context(
    pipeline_run_id: str,
    mode_snapshot: dict[str, Any],
    cost_budget_snapshot: dict[str, Any] | None = None,
) -> None:
    """Persist mode routing before graph execution can fail."""
    if not pipeline_run_id or not isinstance(mode_snapshot, dict):
        return
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select as sa_select  # noqa: PLC0415

            result = await db.execute(
                sa_select(AgentPipelineRun).where(AgentPipelineRun.id == pipeline_run_id)
            )
            pipeline = result.scalar_one_or_none()
            if pipeline is None:
                return
            metadata = dict(pipeline.execution_metadata or {})
            metadata.update({
                "analysis_mode_requested": mode_snapshot.get("requested"),
                "analysis_mode_resolved": mode_snapshot.get("resolved"),
                "analysis_mode_resolution": mode_snapshot,
            })
            if cost_budget_snapshot is not None:
                metadata["cost_budget_decision"] = cost_budget_snapshot
            pipeline.execution_metadata = metadata
            await db.commit()
    except Exception as exc:
        pipeline_execution_context_persist_failures_total.inc()
        logger.warning(
            "execution_context_persist_failed",
            pipeline_run_id=pipeline_run_id,
            error_type=type(exc).__name__,
        )


async def _prepare_pipeline_cost_budget(
    *,
    pipeline_run_id: str,
    project_id: str,
    pipeline_setup: dict[str, Any],
    mode_snapshot: dict[str, Any],
    cost_budget_mode_override: str | None,
) -> dict[str, Any]:
    """Resolve and durably persist one cost decision for the whole pipeline."""
    persisted = pipeline_setup.get("cost_budget_decision")
    if (
        pipeline_setup.get("resume_attempt")
        and isinstance(persisted, dict)
        and persisted.get("action")
    ):
        snapshot = dict(persisted)
    elif cost_budget_mode_override in {"ml", "rules"}:
        snapshot = {
            "action": "PRECHECKED_DOWNGRADE",
            "mode_override": cost_budget_mode_override,
            "block": False,
            "rationale": "Cost budget was evaluated before durable task dispatch",
            "utilization_pct": None,
        }
    else:
        from app.services.llm_cost_budget import check_and_apply_cap

        decision = await check_and_apply_cap(project_id)
        snapshot = {
            "action": decision.action,
            "mode_override": decision.mode_override,
            "block": bool(decision.block),
            "rationale": decision.rationale,
            "utilization_pct": decision.utilization_pct,
        }

    await _persist_execution_context(pipeline_run_id, mode_snapshot, snapshot)
    return snapshot


def _apply_pipeline_cost_budget(
    state: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    """Apply the authoritative decision after any checkpoint state is restored."""
    state.update(
        {
            "_cost_budget_prechecked": True,
            "_cost_budget_action": snapshot.get("action"),
            "_cost_budget_rationale": snapshot.get("rationale"),
            "_cost_budget_utilization_pct": snapshot.get("utilization_pct"),
            "_cost_budget_mode_override": snapshot.get("mode_override"),
            "_cost_budget_block": bool(snapshot.get("block")),
        }
    )


async def _complete_cost_budget_block(
    *,
    pipeline_run_id: str,
    workflow_type: str,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """Complete a hard-blocked pipeline without entering any agent graph."""
    if not state.get("_cost_budget_block"):
        return None

    state.update(
        {
            "stage_quality": "cost_budget_blocked",
            "execution_path": ExecutionPath.CONDITIONAL_SKIP,
            "skipped_stages": list(
                (state.get("workflow_plan") or {}).get("stages") or []
            ),
            "errors": [],
            "current_stage": "completed",
            "cost_budget_blocked": True,
        }
    )
    await _mark_pipeline_done(
        pipeline_run_id,
        success=True,
        final_state=state,
        fencing_token=state.get("_fencing_token"),
    )
    await emit_event(
        pipeline_run_id,
        "pipeline_completed",
        detail={
            "workflow_type": workflow_type,
            "cost_budget_blocked": True,
            "budget_action": state.get("_cost_budget_action"),
        },
    )
    return state


def _safe_serialize(data: dict) -> dict:
    """Ensure checkpoint data is JSON-serializable (strip non-serializable values)."""
    try:
        json.dumps(data, default=str)
        return data
    except (TypeError, ValueError):
        # Fallback: convert everything through str
        cleaned = {}
        for k, v in data.items():
            try:
                json.dumps(v, default=str)
                cleaned[k] = v
            except (TypeError, ValueError):
                cleaned[k] = str(v)
        return cleaned


# ── Checkpointed node wrappers ──────────────────────────────────────────────

def _planner_stage_selection(
    state: WorkflowState,
    stage_name: str,
) -> tuple[bool, str]:
    """Read selection from the immutable starting plan.

    The graph topology is intentionally stable for checkpoint compatibility,
    but the frozen planner must still control whether an optional capability
    executes. Missing/legacy plan metadata remains backward-compatible and
    executes the stage; an explicit ``planned=false`` is authoritative.
    """
    plan = state.get("initial_workflow_plan") or state.get("workflow_plan")
    if not isinstance(plan, dict):
        return True, "legacy_plan_missing"
    stages = plan.get("stages")
    if not isinstance(stages, list):
        return True, "legacy_plan_stages_missing"
    for item in stages:
        if isinstance(item, dict) and item.get("stage") == stage_name:
            if item.get("planned") is False:
                rationale = item.get("rationale")
                return False, str(rationale or "planner_not_selected")[:500]
            return True, str(item.get("rationale") or "planner_selected")[:500]
    return True, "legacy_plan_stage_missing"


def _pipeline_deadline() -> float:
    """The ``time.monotonic()`` instant after which no new stage may start.

    Returns 0.0 when the budget is disabled. Computed once per pipeline attempt,
    so a resumed run gets a fresh budget (it is a fresh Celery task with a fresh
    soft limit) while no single attempt can outrun that limit.
    """
    budget = int(getattr(settings, "AI_PIPELINE_DEADLINE_SECONDS", 0) or 0)
    return time.monotonic() + budget if budget > 0 else 0.0


def _deadline_exhausted(state: WorkflowState, stage_name: str) -> bool:
    """True when the pipeline's wall-clock budget forbids STARTING this stage.

    Terminal synthesis is exempt: those stages are deterministic, cheap, and
    they are what turns a truncated run into a published report that names its
    own gaps instead of nothing at all. Skipping them to save seconds would
    trade a loud failure for a silent one.
    """
    if stage_name in _DEADLINE_EXEMPT_STAGES:
        return False
    deadline_ts = float(state.get("pipeline_deadline_ts") or 0.0)
    if deadline_ts <= 0:
        return False
    return time.monotonic() >= deadline_ts


async def _raise_if_pipeline_cancelled(pipeline_run_id: str) -> None:
    """Raise :class:`PipelineCancelled` when this run has been asked to stop.

    Its own read-only session: the caller is a graph node, not a request
    handler, and there is no ambient transaction to borrow. A database error
    here must not fail the stage -- an unreadable flag means "not cancelled",
    which is the same outcome as before cancellation existed.
    """
    if not pipeline_run_id:
        return
    try:
        async with AsyncSessionLocal() as db:
            cancelled = await is_cancel_requested(db, pipeline_run_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "cancel_flag_read_failed",
            pipeline_run_id=str(pipeline_run_id),
            error_type=type(exc).__name__,
        )
        return
    if cancelled:
        logger.info("stage_stopped_run_cancelled", pipeline_run_id=str(pipeline_run_id))
        raise PipelineCancelled(str(pipeline_run_id))


def _make_checkpointed_node(original_node, stage_name: str):
    """Wrap a node function so its output is checkpointed after successful execution."""
    async def execute(state: WorkflowState) -> dict[str, Any]:
        pipeline_run_id = state.get("pipeline_run_id", "")
        input_checksum = _stage_input_checksum(state)

        # E7.4: a cancelled run stops at its next stage boundary. Checked here,
        # before any of the skip paths, so cancellation costs at most the stage
        # already in flight -- and so a run whose remaining stages would all be
        # deadline-skipped still terminalises as cancelled rather than grinding
        # through them. Read-only; the raise is caught by the pipeline's own
        # error handler, which owns terminalising the row.
        await _raise_if_pipeline_cancelled(pipeline_run_id)

        # Wall-clock budget. Checked BEFORE the stage starts so the pipeline
        # stops taking on new work rather than being killed part-way through
        # it: the Celery soft limit turns an overrunning run into a whole-task
        # retry (twice, then the DLQ), which spends the queue on one run and
        # publishes a report that never mentions the two failed attempts.
        #
        # The skip is written to stage_errors, which is what
        # ``build_decision_intelligence`` folds into
        # ``missing_or_failed_specialists`` -- so the published report degrades
        # itself and names this stage without any change to the report agent.
        if _deadline_exhausted(state, stage_name):
            reason = "Pipeline wall-clock budget exhausted before this stage started"
            logger.warning(
                "stage_skipped_deadline_exhausted",
                pipeline_run_id=pipeline_run_id,
                stage_name=stage_name,
            )
            await _write_stage_skipped(
                pipeline_run_id,
                stage_name,
                skipped_reason=reason,
                execution_path=ExecutionPath.DEADLINE_SKIP,
                stop_reason="pipeline_deadline_exceeded",
            )
            await emit_event(
                pipeline_run_id,
                "stage_skipped",
                stage_name=stage_name,
                detail={
                    "reason": "pipeline_deadline_exceeded",
                    "rationale": reason,
                    "input_checksum_sha256": input_checksum,
                },
            )
            return {
                "completed_stages": [stage_name],
                "skipped_stages": [stage_name],
                "current_stage": stage_name,
                "execution_path": ExecutionPath.DEADLINE_SKIP,
                "stage_quality": "degraded",
                "stage_errors": {stage_name: [reason]},
                "errors": [],
            }

        selected, selection_reason = _planner_stage_selection(state, stage_name)
        if not selected:
            await _write_stage_skipped(
                pipeline_run_id,
                stage_name,
                skipped_reason=f"Planner did not select stage: {selection_reason}",
                execution_path=ExecutionPath.CONDITIONAL_SKIP,
                stop_reason="planner_not_selected",
            )
            await emit_event(
                pipeline_run_id,
                "stage_skipped",
                stage_name=stage_name,
                detail={
                    "reason": "planner_not_selected",
                    "rationale": selection_reason,
                    "input_checksum_sha256": input_checksum,
                },
            )
            return {
                "completed_stages": [stage_name],
                "skipped_stages": [stage_name],
                "current_stage": stage_name,
                "execution_path": ExecutionPath.CONDITIONAL_SKIP,
                "errors": [],
            }

        # Skip if this stage was loaded from a checkpoint
        checkpoint_stages = cast(list[str], state.get("_checkpoint_stages", []))
        if stage_name in checkpoint_stages:
            logger.info("stage_restored_from_checkpoint", stage_name=stage_name)
            await emit_event(
                pipeline_run_id, "checkpoint_restored",
                stage_name=stage_name,
                detail={
                    "restored_stage": stage_name,
                    "input_checksum_sha256": input_checksum,
                },
            )
            await _mark_stage_restored(pipeline_run_id, stage_name)
            return {"completed_stages": [stage_name], "current_stage": stage_name}

        stage_started_at = datetime.now(timezone.utc)
        fencing_token = cast(Optional[str], state.get("_fencing_token") or None)
        try:
            # E7.3: renew the lease from INSIDE the stage. A single long model
            # call must not look dead to the reaper, and a stage that never
            # calls the BaseAgent lifecycle hooks must still hold its lease.
            async with held_lease(pipeline_run_id, fencing_token) as lease_lost:
                node = asyncio.ensure_future(original_node(state))
                if lease_lost is None:
                    result = cast(dict[str, Any], await node)
                else:
                    watcher = asyncio.ensure_future(lease_lost.wait())
                    done, _pending = await asyncio.wait(
                        {node, watcher}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if node in done:
                        watcher.cancel()
                        result = cast(dict[str, Any], node.result())
                    else:
                        # The row was reclaimed while this stage ran. Stop now
                        # rather than finishing work that will be fenced out.
                        node.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await node
                        raise LeaseLost(pipeline_run_id, fencing_token)
        except LeaseLost:
            logger.warning(
                "stage_abandoned_lease_lost",
                stage_name=stage_name,
                pipeline_run_id=pipeline_run_id,
            )
            raise
        except Exception as exc:
            # Mark the individual stage as failed so it doesn't stay stuck in "running"
            error_msg = f"{stage_name} failed: {_safe_workflow_error(exc)}"
            logger.error(
                "stage_unhandled_exception",
                stage_name=stage_name,
                error_type=type(exc).__name__,
                exc_info=True,
            )
            try:
                await _mark_stage_failed(pipeline_run_id, stage_name, error_msg)
            except LeaseLost:
                logger.warning("mark_stage_failed_fenced_out", stage_name=stage_name)
            except Exception:
                logger.warning("mark_stage_failed_db_error", stage_name=stage_name)
            raise

        # Record that the node actually executed. The stage lifecycle
        # (mark_stage_running / mark_stage_done) is opt-in per BaseAgent
        # subclass, and three specialists plus the two plain cluster-investigation
        # node functions never call it -- so their rows sat at "pending" and the
        # end-of-run sweep relabelled them "not on active pipeline branch". The
        # pipeline's own completed_stages listed them as run the whole time.
        #
        # This must precede _checkpoint_stage: that helper only writes when the
        # row already reads "completed", so those same stages were silently
        # dropping their checkpoint data too.
        if pipeline_run_id and stage_name not in (result.get("skipped_stages") or []):
            await _mark_stage_executed(
                pipeline_run_id,
                stage_name,
                started_at=stage_started_at,
                fencing_token=fencing_token,
            )

        # Persist checkpoint
        if pipeline_run_id:
            await _checkpoint_stage(
                pipeline_run_id,
                stage_name,
                result,
                input_checksum_sha256=input_checksum,
                fencing_token=fencing_token,
            )

        return result

    async def wrapper(state: WorkflowState) -> dict[str, Any]:
        with trace_agent_step(stage_name, state):
            return await execute(state)

    wrapper.__name__ = original_node.__name__
    return wrapper


# Compile once at module load (compilation is expensive; instances are thread-safe)
_offline_app = _build_offline_graph().compile()
_live_app = _build_live_graph().compile()
_deep_app = _build_deep_graph().compile()


async def _persist_deep_outputs(
    test_run_id: str,
    pipeline_run_id: str,
    final_state: dict[str, Any],
) -> dict[str, Any]:
    """AI-F4: persist FailureCluster + DeepFinding rows for a deep run and
    populate ``state["deep_findings"]`` (which agent_memory_service consumes).

    Non-fatal: a persistence error must not fail an otherwise-successful
    pipeline — the state result is still returned to the caller.
    """
    try:
        from app.agents.deep_persistence import persist_deep_results

        findings = await persist_deep_results(test_run_id, pipeline_run_id, final_state)
        if findings:
            final_state["deep_findings"] = findings
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "deep_persistence_failed",
            test_run_id=str(test_run_id),
            error_type=type(exc).__name__,
        )
    return final_state


# ── Public entry points ───────────────────────────────────────────────────────

async def run_offline_pipeline(
    test_run_id: str | None = None,
    project_id: str | None = None,
    build_number: str = "resume",
    workflow_type: str = "offline",
    *,
    pipeline_run_id: str | None = None,
    create_if_missing: bool = False,
    cost_budget_mode_override: str | None = None,
    expected_attempt: int | None = None,
    rerun_of: str | None = None,
    invocation_stage: str | None = None,
    invocation_config_snapshot: dict[str, Any] | None = None,
    requested_by: str | None = None,
) -> dict:
    """
    Execute the full offline analysis pipeline for a completed test run.

    - Creates AgentPipelineRun + AgentStageResult tracking records
    - Selects the appropriate compiled graph (offline vs live)
    - Returns the final LangGraph state dict
    """
    if pipeline_run_id is not None:
        pipeline_setup = await _claim_pipeline_resume(pipeline_run_id, expected_attempt=expected_attempt)
        if pipeline_setup is None:
            async with AsyncSessionLocal() as db:
                existing = await db.get(AgentPipelineRun, pipeline_run_id)
            if existing is not None and existing.status == "completed":
                return {
                    "completed_stages": [],
                    "errors": [],
                    "duplicate": True,
                    "pipeline_run_id": str(existing.id),
                }
            if not create_if_missing or existing is not None:
                raise ValueError("pipeline_not_resumable")
            if not test_run_id or not project_id:
                raise ValueError("test_run_and_project_required")
            pipeline_setup = await _create_pipeline_run(
                pipeline_run_id, test_run_id, project_id, workflow_type,
                invocation_stage=invocation_stage,
                invocation_config_snapshot=invocation_config_snapshot,
                requested_by=requested_by,
            )
        test_run_id = pipeline_setup["test_run_id"]
        project_id = pipeline_setup["project_id"]
    else:
        if not test_run_id or not project_id:
            raise ValueError("test_run_and_project_required")
        pipeline_run_id = str(uuid.uuid4())
        pipeline_setup = await _create_pipeline_run(
            pipeline_run_id, test_run_id, project_id, workflow_type, rerun_of=rerun_of,
            invocation_stage=invocation_stage,
            invocation_config_snapshot=invocation_config_snapshot,
            requested_by=requested_by,
        )

    try:
        # Attempt to load a checksum-authorized checkpoint. Same-pipeline resume
        # reads only the atomically claimed pipeline; new attempts use the legacy
        # latest-failed source and intentionally get a new pipeline identity.
        checkpoint = await _load_checkpoint(
            test_run_id, workflow_type, pipeline_run_id=pipeline_run_id if pipeline_setup.get("resume_attempt") else None
        )
        if pipeline_setup.get("resume_attempt") and pipeline_setup.get("analysis_mode_resolution"):
            mode_snapshot = dict(pipeline_setup["analysis_mode_resolution"])
        else:
            mode_snapshot = await _resolve_analysis_mode_snapshot()
        cost_budget_snapshot = await _prepare_pipeline_cost_budget(
            pipeline_run_id=pipeline_run_id,
            project_id=project_id,
            pipeline_setup=pipeline_setup,
            mode_snapshot=mode_snapshot,
            cost_budget_mode_override=cost_budget_mode_override,
        )

        initial_state: WorkflowState = {
            "pipeline_run_id":    pipeline_run_id,
            "eval_manifest_checksum": str(pipeline_setup.get("eval_manifest_checksum") or ""),
            "test_run_id":        test_run_id,
            "project_id":         project_id,
            "build_number":       build_number,
            "workflow_type":      workflow_type,
            "pipeline_deadline_ts": _pipeline_deadline(),
            "_fencing_token": pipeline_setup.get("fencing_token"),
            "_attempt": int(pipeline_setup.get("attempt") or 1),
            # Stage outputs (initialised empty — agents populate these)
            "test_run_data":      None,
            "branch":             None,
            "failed_test_ids":    [],
            "total_tests":        0,
            "pass_rate":          0.0,
            "ingestion_enriched": False,
            "anomalies":          [],
            "is_regression":      False,
            "regression_tests":   [],
            "anomaly_summary":    None,
            "analyses":           {},
            "executive_summary":  None,
            "summary_markdown":   None,
            "structured_summary": None,
            "summary_provenance": None,
            "decision_intelligence": None,
            "decision_evidence_snapshot": None,
            "decision_report_verification": None,
            "triage_results":     [],
            # Deep pipeline state (empty for offline/live pipelines)
            "failure_clusters":   [],
            "cluster_map":        {},
            "cluster_child_settings": pipeline_setup["cluster_child_settings"],
            "async_decision_report_supersession_enabled": bool(
                pipeline_setup.get("async_decision_report_supersession_enabled", False)
            ),
            "contract_agent_enabled": bool(pipeline_setup.get("contract_agent_settings", {}).get("enabled", False)),
            "defect_commander_enabled": bool(pipeline_setup.get("defect_commander_settings", {}).get("enabled", False)),
            "contract_findings": None,
            "log_intelligence_enabled": bool(pipeline_setup.get("log_intelligence_settings", {}).get("enabled", False)),
            "log_findings": None,
            "regression_watchman_enabled": bool(pipeline_setup.get("regression_watchman_settings", {}).get("enabled", False)),
            "regression_classification": None,
            "change_ownership_enabled": bool(pipeline_setup.get("change_ownership_settings", {}).get("enabled", False)),
            "change_ownership_findings": None,
            "cluster_investigation_plan": None,
            "cluster_investigation_results": None,
            "deep_findings":      {},
            "flaky_findings":     [],
            "test_health_findings": [],
            "release_decision":   None,
            "errors":             [],
            "completed_stages":   [],
            "current_stage":      "ingestion",
            "stage_errors":       {},
            "stage_quality":      "normal",
            "low_confidence_count": 0,
            # Provenance / execution tracking
            "skipped_stages":     [],
            "execution_path":     ExecutionPath.EXECUTED,
            "fallback_used":      False,
            "tools_used":         [],
            "analysis_mode_requested": mode_snapshot["requested"],
            "analysis_mode_resolved": mode_snapshot["resolved"],
            "analysis_mode_resolution": mode_snapshot,
            "_workflow_route_decisions": [],
            "_checkpoint_stages": [],
            "_checkpoint_replay_metadata": {},
            "workflow_plan": pipeline_setup["initial_workflow_plan"],
            "workflow_verification": {},
            "agent_contracts": {},
            "schema_version":     2,
            "stage_metrics":      {},
        }
        initial_state["initial_workflow_plan"] = initial_state["workflow_plan"]

        # Merge checkpoint data into initial state (restored stage outputs)
        if checkpoint:
            checkpoint_stages = checkpoint.pop("_checkpoint_stages", [])
            initial_state.update(checkpoint)  # type: ignore[typeddict-item]
            initial_state["_checkpoint_stages"] = checkpoint_stages  # type: ignore[typeddict-unknown-key]
            logger.info(
                "pipeline_resuming_with_checkpoint_stages",
                pipeline_run_id=pipeline_run_id,
                checkpoint_stages=checkpoint_stages,
            )
        _apply_pipeline_cost_budget(cast(dict[str, Any], initial_state), cost_budget_snapshot)

        if workflow_type == "deep":
            app = _deep_app
        elif workflow_type == "live":
            app = _live_app
        else:
            app = _offline_app

        blocked_state = await _complete_cost_budget_block(
            pipeline_run_id=pipeline_run_id,
            workflow_type=workflow_type,
            state=cast(dict[str, Any], initial_state),
        )
    except Exception as exc:
        # Anything failing between creating (or claiming) this pipeline and
        # starting its graph left it 'running': the handler below wraps only
        # the graph, so every retry was then refused as not resumable
        # (re-audit N27, found on the homelab).
        error_msg = f"Pipeline setup error: {_safe_workflow_error(exc)}"
        # Marked first: a log call that raises (QA reproduced one with a
        # non-UTF-8 stdout) must not leave the pipeline 'running' again.
        await _mark_pipeline_done(
            pipeline_run_id,
            success=False,
            error=error_msg,
            fencing_token=pipeline_setup.get("fencing_token"),
        )
        _tag_failure_with_pipeline(exc, pipeline_run_id)
        try:
            logger.error("pipeline_setup_failed", error_type=type(exc).__name__, exc_info=True)
        except Exception:  # noqa: BLE001 -- the original error is what propagates
            pass
        raise
    if blocked_state is not None:
        return blocked_state

    try:
        logger.info(
            "starting_pipeline_for_run_build",
            workflow_type=workflow_type,
            pipeline_run_id=pipeline_run_id,
            test_run_id=test_run_id,
            build_number=build_number,
        )
        from app.services.llm_cost_reservation import cost_budget_scope

        # Re-audit M13: every LLM call in this graph reserves against this
        # project's monthly cap at the invocation boundary.
        with cost_budget_scope(cast(dict[str, Any], initial_state).get("project_id")):
            final_state = await cast(Any, app).ainvoke(initial_state)
        final_state = attach_workflow_plan_and_verification(
            cast(dict[str, Any], final_state),
            workflow_type=workflow_type,
        )
        if workflow_type == "deep":
            # AI-F4: persist clusters + synthesized findings so the
            # /deep-investigate endpoints serve real pipeline output.
            final_state = await _persist_deep_outputs(
                test_run_id, pipeline_run_id, final_state
            )
        await _mark_pipeline_done(
            pipeline_run_id,
            success=True,
            final_state=final_state,
            fencing_token=initial_state.get("_fencing_token"),
        )
        await emit_event(
            pipeline_run_id,
            "workflow_verified",
            stage_name="workflow",
            detail=final_state.get("workflow_verification", {}),
        )
        await emit_event(pipeline_run_id, "pipeline_completed", detail={
            "workflow_type": workflow_type,
            "stages_completed": final_state.get("completed_stages", []),
            "stages_skipped": final_state.get("skipped_stages", []),
            "analysis_mode_requested": final_state.get("analysis_mode_requested"),
            "analysis_mode_resolved": final_state.get("analysis_mode_resolved"),
            "error_count": len(final_state.get("errors", [])),
        })
        logger.info(
            "pipeline_complete",
            pipeline_run_id=pipeline_run_id,
            completed_stages=final_state.get("completed_stages"),
            error_count=len(final_state.get("errors", [])),
            skipped_stages=final_state.get("skipped_stages", []),
        )
        # Persist unified memory entries for historical recall (P3)
        await _persist_memory(project_id, test_run_id, pipeline_run_id, final_state)
        return cast(dict[str, Any], final_state)
    except Exception as exc:
        error_msg = f"Pipeline execution error: {_safe_workflow_error(exc)}"
        logger.error("pipeline_execution_failed", error_type=type(exc).__name__, exc_info=True)
        if workflow_type == "deep":
            from app.services.cluster_investigation_orchestrator import (
                cancel_cluster_children,
            )

            try:
                await cancel_cluster_children(
                    pipeline_run_id, reason="parent_pipeline_failed"
                )
            except Exception as cancel_exc:  # noqa: BLE001
                logger.error(
                    "cluster_child_cancel_failed",
                    error_type=type(cancel_exc).__name__,
                )
        await _mark_pipeline_done(
            pipeline_run_id,
            success=False,
            error=error_msg,
            fencing_token=initial_state.get("_fencing_token"),
        )
        _tag_failure_with_pipeline(exc, pipeline_run_id)
        await emit_event(pipeline_run_id, "error_occurred", detail={
            "workflow_type": workflow_type,
            "error": error_msg[:500],
        })
        raise


async def run_deep_pipeline(
    test_run_id: str | None = None,
    project_id: str | None = None,
    build_number: str = "resume",
    *,
    pipeline_run_id: str | None = None,
    cost_budget_mode_override: str | None = None,
    expected_attempt: int | None = None,
    rerun_of: str | None = None,
    create_if_missing: bool = False,
    invocation_stage: str | None = None,
    invocation_config_snapshot: dict[str, Any] | None = None,
    requested_by: str | None = None,
) -> dict:
    """
    Execute the deep investigation pipeline with clustering, flaky sentinel,
    test health analysis, and release risk assessment.
    """
    if pipeline_run_id is not None:
        pipeline_setup = await _claim_pipeline_resume(pipeline_run_id, expected_attempt=expected_attempt)
        if pipeline_setup is None:
            # E1.2: an API invocation mints its pipeline id up front, so its
            # first delivery creates the row under that id, as the offline path
            # already does for durable deliveries.
            async with AsyncSessionLocal() as db:
                existing = await db.get(AgentPipelineRun, pipeline_run_id)
            if not create_if_missing or existing is not None:
                raise ValueError("pipeline_not_resumable")
            if not test_run_id or not project_id:
                raise ValueError("test_run_and_project_required")
            pipeline_setup = await _create_pipeline_run(
                pipeline_run_id, test_run_id, project_id, "deep",
                invocation_stage=invocation_stage,
                invocation_config_snapshot=invocation_config_snapshot,
                requested_by=requested_by,
            )
        test_run_id = pipeline_setup["test_run_id"]
        project_id = pipeline_setup["project_id"]
    else:
        if not test_run_id or not project_id:
            raise ValueError("test_run_and_project_required")
        pipeline_run_id = str(uuid.uuid4())
        pipeline_setup = await _create_pipeline_run(
            pipeline_run_id, test_run_id, project_id, "deep", rerun_of=rerun_of,
            invocation_stage=invocation_stage,
            invocation_config_snapshot=invocation_config_snapshot,
            requested_by=requested_by,
        )

    try:
        checkpoint = await _load_checkpoint(
            test_run_id, "deep", pipeline_run_id=pipeline_run_id if pipeline_setup.get("resume_attempt") else None
        )
        if pipeline_setup.get("resume_attempt") and pipeline_setup.get("analysis_mode_resolution"):
            mode_snapshot = dict(pipeline_setup["analysis_mode_resolution"])
        else:
            mode_snapshot = await _resolve_analysis_mode_snapshot()
        cost_budget_snapshot = await _prepare_pipeline_cost_budget(
            pipeline_run_id=pipeline_run_id,
            project_id=project_id,
            pipeline_setup=pipeline_setup,
            mode_snapshot=mode_snapshot,
            cost_budget_mode_override=cost_budget_mode_override,
        )

        initial_state: WorkflowState = {
            "pipeline_run_id":    pipeline_run_id,
            "eval_manifest_checksum": str(pipeline_setup.get("eval_manifest_checksum") or ""),
            "test_run_id":        test_run_id,
            "project_id":         project_id,
            "build_number":       build_number,
            "workflow_type":      "deep",
            "pipeline_deadline_ts": _pipeline_deadline(),
            "_fencing_token": pipeline_setup.get("fencing_token"),
            "_attempt": int(pipeline_setup.get("attempt") or 1),
            "test_run_data":      None,
            "branch":             None,
            "failed_test_ids":    [],
            "total_tests":        0,
            "pass_rate":          0.0,
            "ingestion_enriched": False,
            "anomalies":          [],
            "is_regression":      False,
            "regression_tests":   [],
            "anomaly_summary":    None,
            "analyses":           {},
            "executive_summary":  None,
            "summary_markdown":   None,
            "structured_summary": None,
            "summary_provenance": None,
            "decision_intelligence": None,
            "decision_evidence_snapshot": None,
            "decision_report_verification": None,
            "triage_results":     [],
            "failure_clusters":   [],
            "cluster_map":        {},
            "cluster_child_settings": pipeline_setup["cluster_child_settings"],
            "async_decision_report_supersession_enabled": bool(
                pipeline_setup.get("async_decision_report_supersession_enabled", False)
            ),
            "contract_agent_enabled": bool(pipeline_setup.get("contract_agent_settings", {}).get("enabled", False)),
            "defect_commander_enabled": bool(pipeline_setup.get("defect_commander_settings", {}).get("enabled", False)),
            "contract_findings": None,
            "log_intelligence_enabled": bool(pipeline_setup.get("log_intelligence_settings", {}).get("enabled", False)),
            "log_findings": None,
            "regression_watchman_enabled": bool(pipeline_setup.get("regression_watchman_settings", {}).get("enabled", False)),
            "regression_classification": None,
            "change_ownership_enabled": bool(pipeline_setup.get("change_ownership_settings", {}).get("enabled", False)),
            "change_ownership_findings": None,
            "cluster_investigation_plan": None,
            "cluster_investigation_results": None,
            "deep_findings":      {},
            "flaky_findings":     [],
            "test_health_findings": [],
            "release_decision":   None,
            "errors":             [],
            "completed_stages":   [],
            "current_stage":      "ingestion",
            "stage_errors":       {},
            "stage_quality":      "normal",
            "low_confidence_count": 0,
            # Provenance / execution tracking
            "skipped_stages":     [],
            "execution_path":     ExecutionPath.EXECUTED,
            "fallback_used":      False,
            "tools_used":         [],
            "analysis_mode_requested": mode_snapshot["requested"],
            "analysis_mode_resolved": mode_snapshot["resolved"],
            "analysis_mode_resolution": mode_snapshot,
            "_workflow_route_decisions": [],
            "_checkpoint_stages": [],
            "_checkpoint_replay_metadata": {},
            "workflow_plan": pipeline_setup["initial_workflow_plan"],
            "workflow_verification": {},
            "agent_contracts": {},
            "schema_version":     2,
            "stage_metrics":      {},
        }
        initial_state["initial_workflow_plan"] = initial_state["workflow_plan"]

        if checkpoint:
            checkpoint_stages = checkpoint.pop("_checkpoint_stages", [])
            initial_state.update(checkpoint)  # type: ignore[typeddict-item]
            initial_state["_checkpoint_stages"] = checkpoint_stages  # type: ignore[typeddict-unknown-key]
            logger.info(
                "deep_pipeline_resuming_with_checkpoint_stages",
                pipeline_run_id=pipeline_run_id,
                checkpoint_stages=checkpoint_stages,
            )
        _apply_pipeline_cost_budget(cast(dict[str, Any], initial_state), cost_budget_snapshot)

        blocked_state = await _complete_cost_budget_block(
            pipeline_run_id=pipeline_run_id,
            workflow_type="deep",
            state=cast(dict[str, Any], initial_state),
        )
    except Exception as exc:
        # As run_offline_pipeline (re-audit N27): a setup failure after the
        # pipeline was created left it 'running' for the 30-minute reaper
        # (QA of N27, on the deep path).
        error_msg = f"Pipeline setup error: {_safe_workflow_error(exc)}"
        await _mark_pipeline_done(
            pipeline_run_id,
            success=False,
            error=error_msg,
            fencing_token=pipeline_setup.get("fencing_token"),
        )
        _tag_failure_with_pipeline(exc, pipeline_run_id)
        try:
            logger.error("pipeline_setup_failed", error_type=type(exc).__name__, exc_info=True)
        except Exception:  # noqa: BLE001 -- the original error is what propagates
            pass
        raise
    if blocked_state is not None:
        return blocked_state

    try:
        logger.info(
            "starting_deep_pipeline_for_run_build",
            pipeline_run_id=pipeline_run_id,
            test_run_id=test_run_id,
            build_number=build_number,
        )
        from app.services.llm_cost_reservation import cost_budget_scope

        # Re-audit M13: as the standard graph above.
        with cost_budget_scope(cast(dict[str, Any], initial_state).get("project_id")):
            final_state = await cast(Any, _deep_app).ainvoke(initial_state)
        final_state = attach_workflow_plan_and_verification(
            cast(dict[str, Any], final_state),
            workflow_type="deep",
        )
        # AI-F4: persist clusters + synthesized findings so the
        # /deep-investigate endpoints serve real pipeline output.
        final_state = await _persist_deep_outputs(
            test_run_id, pipeline_run_id, final_state
        )
        await _mark_pipeline_done(
            pipeline_run_id,
            success=True,
            final_state=final_state,
            fencing_token=initial_state.get("_fencing_token"),
        )
        await emit_event(
            pipeline_run_id,
            "workflow_verified",
            stage_name="workflow",
            detail=final_state.get("workflow_verification", {}),
        )
        await emit_event(pipeline_run_id, "pipeline_completed", detail={
            "workflow_type": "deep",
            "stages_completed": final_state.get("completed_stages", []),
            "stages_skipped": final_state.get("skipped_stages", []),
            "analysis_mode_requested": final_state.get("analysis_mode_requested"),
            "analysis_mode_resolved": final_state.get("analysis_mode_resolved"),
            "error_count": len(final_state.get("errors", [])),
        })
        logger.info(
            "deep_pipeline_complete",
            pipeline_run_id=pipeline_run_id,
            completed_stages=final_state.get("completed_stages"),
            error_count=len(final_state.get("errors", [])),
            skipped_stages=final_state.get("skipped_stages", []),
        )
        # Persist unified memory entries for historical recall (P3)
        await _persist_memory(project_id, test_run_id, pipeline_run_id, final_state)
        return cast(dict[str, Any], final_state)
    except Exception as exc:
        error_msg = f"Deep pipeline execution error: {_safe_workflow_error(exc)}"
        logger.error("deep_pipeline_execution_failed", error_type=type(exc).__name__, exc_info=True)
        from app.services.cluster_investigation_orchestrator import (
            cancel_cluster_children,
        )

        try:
            await cancel_cluster_children(
                pipeline_run_id, reason="parent_pipeline_failed"
            )
        except Exception as cancel_exc:  # noqa: BLE001
            logger.error(
                "cluster_child_cancel_failed",
                error_type=type(cancel_exc).__name__,
            )
        await _mark_pipeline_done(
            pipeline_run_id,
            success=False,
            error=error_msg,
            fencing_token=initial_state.get("_fencing_token"),
        )
        _tag_failure_with_pipeline(exc, pipeline_run_id)
        await emit_event(pipeline_run_id, "error_occurred", detail={
            "workflow_type": "deep",
            "error": error_msg[:500],
        })
        raise


# ── Stage-skip helper ─────────────────────────────────────────────────────────

async def _write_stage_skipped(
    pipeline_run_id: str,
    stage_name: str,
    skipped_reason: str,
    execution_path: ExecutionPath,
    stop_reason: str | None = None,
) -> None:
    """
    Update an AgentStageResult row to record why a stage was skipped.
    Called from fast-path guards inside node functions.
    """
    if not pipeline_run_id:
        return
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select as sa_select  # noqa: PLC0415

            result = await db.execute(
                sa_select(AgentStageResult).where(
                    AgentStageResult.pipeline_run_id == pipeline_run_id,
                    AgentStageResult.stage_name == stage_name,
                )
            )
            stage = result.scalar_one_or_none()
            if stage:
                stage.status = "skipped"
                stage.skipped_reason = skipped_reason
                stage.execution_path = execution_path.value
                if stop_reason:
                    stage.stop_reason = stop_reason
                await db.commit()
    except Exception as exc:
        logger.warning(
            "skipped_stage_write_failed",
            pipeline_run_id=pipeline_run_id,
            stage_name=stage_name,
            error_type=type(exc).__name__,
        )


async def _mark_stage_executed(
    pipeline_run_id: str,
    stage_name: str,
    *,
    started_at: datetime,
    fencing_token: str | None = None,
) -> None:
    """Backfill the row for a node that ran but wrote no stage record.

    Only ``pending``/``running`` rows are touched. An agent that already called
    ``mark_stage_done`` has written tokens, cost, confidence and evidence counts
    the wrapper cannot reconstruct, so its ``completed`` row is left exactly as
    it is -- this fills a gap, it does not become a second writer of the same
    fact. ``failed`` and self-reported ``skipped`` rows are likewise preserved.
    """
    if not pipeline_run_id:
        return
    try:
        async with AsyncSessionLocal() as db:
            await fence_or_raise(db, pipeline_run_id, fencing_token)
            from sqlalchemy import select as sa_select  # noqa: PLC0415

            result = await db.execute(
                sa_select(AgentStageResult).where(
                    AgentStageResult.pipeline_run_id == pipeline_run_id,
                    AgentStageResult.stage_name == stage_name,
                )
            )
            stage = result.scalar_one_or_none()
            if stage is None or stage.status not in ("pending", "running"):
                return
            stage.status = "completed"
            # A stage that never called mark_stage_running has no start; use the
            # wrapper's measurement so the row carries a real duration. Stages
            # that did mark themselves keep their own, earlier, timestamp.
            if stage.started_at is None:
                stage.started_at = started_at
            stage.completed_at = datetime.now(timezone.utc)
            await db.commit()
    except Exception as exc:
        logger.warning(
            "mark_stage_executed_error",
            pipeline_run_id=pipeline_run_id,
            stage_name=stage_name,
            error_type=type(exc).__name__,
        )


async def _mark_stage_failed(
    pipeline_run_id: str,
    stage_name: str,
    error: str,
) -> None:
    """Mark an AgentStageResult as failed when a node raises an unhandled exception."""
    if not pipeline_run_id:
        return
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select as sa_select  # noqa: PLC0415

            result = await db.execute(
                sa_select(AgentStageResult).where(
                    AgentStageResult.pipeline_run_id == pipeline_run_id,
                    AgentStageResult.stage_name == stage_name,
                )
            )
            stage = result.scalar_one_or_none()
            if stage:
                stage.status = "failed"
                stage.error = error[:2000]
                stage.completed_at = datetime.now(timezone.utc)
                await db.commit()
        await emit_event(
            pipeline_run_id, "stage_failed",
            stage_name=stage_name,
            detail={"error": error[:500]},
        )
    except Exception as exc:
        logger.warning(
            "mark_stage_failed_error",
            pipeline_run_id=pipeline_run_id,
            stage_name=stage_name,
            error_type=type(exc).__name__,
        )


async def _mark_stage_restored(pipeline_run_id: str, stage_name: str) -> None:
    """Mark a stage row as completed from checkpoint for audit visibility."""
    if not pipeline_run_id:
        return
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select as sa_select  # noqa: PLC0415

            result = await db.execute(
                sa_select(AgentStageResult).where(
                    AgentStageResult.pipeline_run_id == pipeline_run_id,
                    AgentStageResult.stage_name == stage_name,
                )
            )
            stage = result.scalar_one_or_none()
            if stage:
                now = datetime.now(timezone.utc)
                stage.status = "completed"
                stage.started_at = stage.started_at or now
                stage.completed_at = now
                prior_result = stage.result_data if isinstance(stage.result_data, dict) else {}
                replay = prior_result.get("_replay")
                if isinstance(replay, dict):
                    attempt = replay.get("attempt")
                    output_checksum = replay.get("output_checksum_sha256")
                    if isinstance(attempt, int) and attempt >= 1 and isinstance(output_checksum, str):
                        restored_key = _canonical_checksum({
                            "pipeline_run_id": str(pipeline_run_id),
                            "stage_name": stage_name,
                            "attempt": attempt,
                            "output_checksum_sha256": output_checksum,
                        })
                        replay = {
                            **replay,
                            "source_pipeline_run_id": replay.get(
                                "source_pipeline_run_id", "unknown"
                            ),
                            "restored_into_pipeline_run_id": str(pipeline_run_id),
                            "attempt_idempotency_key": restored_key,
                        }
                        stage.idempotency_key = restored_key
                stage.result_data = {
                    **prior_result,
                    "_replay": replay if isinstance(replay, dict) else prior_result.get("_replay"),
                    "restored_from_checkpoint": True,
                }
                stage.route_rationale = "Restored from previous pipeline checkpoint"
                await db.commit()
    except Exception as exc:
        logger.warning(
            "mark_stage_restored_error",
            pipeline_run_id=pipeline_run_id,
            stage_name=stage_name,
            error_type=type(exc).__name__,
        )


async def _persist_memory(
    project_id: str,
    test_run_id: str,
    pipeline_run_id: str,
    final_state: dict,
) -> None:
    """Persist unified memory entries after a successful pipeline run (P3)."""
    try:
        from app.services.agent_memory_service import persist_pipeline_memory  # noqa: PLC0415

        async with AsyncSessionLocal() as db:
            count = await persist_pipeline_memory(
                db, project_id, test_run_id, pipeline_run_id, final_state,
            )
            if count:
                logger.info(
                    "persisted_memory_entries",
                    count=count,
                    pipeline_run_id=pipeline_run_id,
                )
    except Exception as exc:
        # Memory persistence is non-critical — don't fail the pipeline
        logger.warning(
            "memory_persistence_failed",
            pipeline_run_id=pipeline_run_id,
            error_type=type(exc).__name__,
        )


# ── DB helpers ────────────────────────────────────────────────────────────────

_PIPELINE_STAGES = [
    "ingestion", "anomaly_detection", "root_cause_analysis", "summary", "triage",
]
# Derived from the planner, NOT hand-maintained beside it.
#
# This was a second copy of the same list, and the copies drifted the moment a
# stage was added: `defect_commander` went into the planner's _DEEP_STAGES and
# not into this one, so no AgentStageResult row was seeded for it. With the
# flag on, `mark_stage_running` would have found no row and the stage would
# have executed while recording nothing -- the exact "a stage that ran was
# recorded as never having run" defect that #840 and #844 were about, arriving
# through a list nobody thought of as a rule.
#
# One definition now. The seeding order does not matter (these only create
# `pending` rows); membership is the contract, and it is guarded by
# tests/regression/test_seeded_stages_match_the_planner.py.
_DEEP_PIPELINE_STAGES = list(_PLANNER_DEEP_STAGES)
_LIVE_PIPELINE_STAGES = list(_PLANNER_LIVE_STAGES)


async def _create_pipeline_run(
    pipeline_run_id: str,
    test_run_id: str,
    project_id: str,
    workflow_type: str,
    *,
    rerun_of: str | None = None,
    invocation_stage: str | None = None,
    invocation_config_snapshot: dict[str, Any] | None = None,
    requested_by: str | None = None,
) -> dict[str, Any]:
    if workflow_type == "deep":
        stages = _DEEP_PIPELINE_STAGES
    elif workflow_type == "live":
        stages = _LIVE_PIPELINE_STAGES
    else:
        stages = _PIPELINE_STAGES
    async with AsyncSessionLocal() as db:
        cluster_settings: dict[str, Any] = {
            "enabled": False,
            "feature_flag_enabled": False,
            "policy_enabled": False,
            "mode": "shadow",
            "max_children": 0,
            "max_members": 50,
            "max_active_per_project": 0,
            "max_children_per_day": 0,
            "aggregate_budget": {
                "max_llm_calls": 0,
                "max_tokens": 0,
                "max_cost_usd": 0.0,
                "max_seconds": 0,
            },
        }
        if workflow_type == "deep":
            from app.services.cluster_investigation_orchestrator import (
                resolve_cluster_child_settings,
            )

            cluster_settings = await resolve_cluster_child_settings(
                db, uuid.UUID(str(project_id))
            )
        from app.services.agent_investigation_service import (
            get_effective_policy,
            run_budget_from_policy,
        )

        from app.services.feature_flags import is_enabled
        async_report_supersession_enabled = await is_enabled(
            "async_decision_report_supersession",
            db=db,
            project_id=uuid.UUID(str(project_id)),
        ) if workflow_type == "deep" else False
        contract_agent_enabled = await is_enabled("contract_validation", db=db, project_id=uuid.UUID(str(project_id)))
        log_intelligence_enabled = await is_enabled("log_intelligence", db=db, project_id=uuid.UUID(str(project_id)))
        regression_watchman_enabled = await is_enabled("regression_watchman", db=db, project_id=uuid.UUID(str(project_id)))
        change_ownership_enabled = await is_enabled("change_ownership", db=db, project_id=uuid.UUID(str(project_id)))
        # MUTATING stage — a missing flag row evaluates False, so absent = off.
        defect_commander_enabled = await is_enabled("defect_commander", db=db, project_id=uuid.UUID(str(project_id)))

        policy = await get_effective_policy(db, uuid.UUID(str(project_id)))
        run_budget = run_budget_from_policy(policy)
        from app.services.agent_config_resolver import (
            FrozenAgentConfig,
            resolve_frozen_for_project,
        )
        from app.services.agent_config_service import config_versions as _agent_config_versions

        # E4.1: freeze each configured agent's config_version, so a later
        # config change never alters how this run is interpreted.
        agent_config_versions = await _agent_config_versions(db, uuid.UUID(str(project_id)))
        frozen_config = None
        frozen_snapshot = None
        if invocation_config_snapshot is not None:
            frozen_snapshot = FrozenAgentConfig.model_validate(
                invocation_config_snapshot
            )
            if invocation_stage is None:
                raise ValueError("invocation_stage_required_for_config_snapshot")
            expected_agent_id = get_capability(invocation_stage).capability_id
            if frozen_snapshot.agent_id != expected_agent_id:
                raise ValueError("invocation_config_snapshot_agent_mismatch")
            # The accepted values are durable, but safety ceilings remain
            # one-way. Re-resolve immediately before the run is created so an
            # offline flip or a lowered attempt/timeout ceiling wins even when
            # it happened between API acceptance and worker execution.
            frozen_config = (
                await resolve_frozen_for_project(
                    frozen_snapshot.model_dump(mode="json"),
                    expected_agent_id=expected_agent_id,
                )
            ).config
            agent_config_versions[expected_agent_id] = frozen_snapshot.config_version
            run_budget = {
                **run_budget,
                "max_llm_calls": min(
                    int(run_budget["max_llm_calls"]),
                    frozen_config.budget.max_llm_calls_per_run,
                ),
                "max_tokens": min(
                    int(run_budget["max_tokens"]),
                    frozen_config.budget.max_tokens_per_run,
                ),
                "max_cost_usd": min(
                    float(run_budget["max_cost_usd"]),
                    frozen_config.budget.max_cost_usd_per_run,
                ),
                "max_seconds": min(
                    int(run_budget["max_seconds"]),
                    frozen_config.timeout_seconds,
                ),
            }
        initial_plan = build_workflow_plan(
            workflow_type=workflow_type,
            cluster_children_enabled=bool(cluster_settings["enabled"]),
            cluster_children_aggregate_budget=dict(
                cluster_settings["aggregate_budget"]
            ),
            decision_graph_aggregate_budget=dict(run_budget),
            contract_validation_enabled=contract_agent_enabled,
            log_intelligence_enabled=log_intelligence_enabled,
            regression_watchman_enabled=regression_watchman_enabled,
            change_ownership_enabled=change_ownership_enabled,
            defect_commander_enabled=defect_commander_enabled,
            invocation_stage=invocation_stage,
        )
        if frozen_config is not None and invocation_stage is not None:
            for planned_stage in initial_plan.get("stages", []):
                if planned_stage.get("stage") != invocation_stage:
                    continue
                allocated = dict(planned_stage.get("budget") or {})
                allocated.update(
                    {
                        "max_llm_calls": min(
                            int(allocated.get("max_llm_calls") or 0),
                            frozen_config.budget.max_llm_calls_per_run,
                        ),
                        "max_tokens": min(
                            int(allocated.get("max_tokens") or 0),
                            frozen_config.budget.max_tokens_per_run,
                        ),
                        "max_cost_usd": min(
                            float(allocated.get("max_cost_usd") or 0.0),
                            frozen_config.budget.max_cost_usd_per_run,
                        ),
                        "timeout_seconds": min(
                            int(allocated.get("timeout_seconds") or 0),
                            frozen_config.timeout_seconds,
                        ),
                    }
                )
                planned_stage["budget"] = allocated
                break
        _lease_token, _lease_fields = acquire_lease_fields()
        eval_checksum = current_eval_manifest_checksum()
        db.add(AgentPipelineRun(
            id=pipeline_run_id,
            **_lease_fields,
            test_run_id=test_run_id,
            requested_by=uuid.UUID(str(requested_by)) if requested_by else None,
            workflow_type=workflow_type,
            # E7.4: set when a manual retry could not resume the previous run
            # (its frozen config no longer matches the live one).
            rerun_of=uuid.UUID(str(rerun_of)) if rerun_of else None,
            status="running",
            max_attempts=(
                frozen_config.retry.max_attempts if frozen_config is not None else 5
            ),
            review_policy=(
                frozen_config.review.policy
                if frozen_config is not None
                else "human_required"
            ),
            started_at=datetime.now(timezone.utc),
            execution_metadata={
                "eval_manifest_checksum": eval_checksum,
                "initial_workflow_plan": initial_plan,
                "cluster_child_settings": cluster_settings,
                "async_decision_report_supersession_enabled": async_report_supersession_enabled,
                "contract_agent_settings": {"enabled": contract_agent_enabled},
                "log_intelligence_settings": {"enabled": log_intelligence_enabled},
                "regression_watchman_settings": {"enabled": regression_watchman_enabled},
                "change_ownership_settings": {"enabled": change_ownership_enabled},
                "defect_commander_settings": {"enabled": defect_commander_enabled},
                "run_budget": run_budget,
                "agent_config_versions": agent_config_versions,
                "resolved_agent_configs": (
                    {
                        frozen_snapshot.agent_id: frozen_snapshot.model_dump(
                            mode="json"
                        )
                    }
                    if frozen_snapshot is not None
                    else {}
                ),
                "budget_spend": {
                    "llm_calls": 0,
                    "tokens": 0,
                    "cost_usd": 0.0,
                    "reservations": {},
                    "completed_reservations": {},
                    "budget_stop_reasons": [],
                },
            },
        ))
        plan_stages = {
            str(item.get("stage")): item
            for item in initial_plan.get("stages", [])
            if isinstance(item, dict)
        }
        for stage in stages:
            planned = plan_stages.get(stage, {})
            capability = get_capability(stage)
            db.add(AgentStageResult(
                pipeline_run_id=pipeline_run_id,
                task_key=stage,
                capability_id=capability.capability_id,
                selected=bool(planned.get("planned", True)),
                required=bool(planned.get("required", False)),
                dependencies=list(planned.get("dependencies") or capability.dependencies),
                allocated_budget=planned.get("budget"),
                route_rationale=str(planned.get("rationale") or "")[:2000],
                stage_name=stage,
                status="pending",
            ))
        await db.commit()
        return {
            # run_offline_pipeline's durable path reads these, as it does from
            # _claim_pipeline_resume. Missing, every outbox-driven pipeline died
            # with KeyError before its first stage (re-audit N27).
            "test_run_id": str(test_run_id),
            "project_id": str(project_id),
            "attempt": 1,
            "eval_manifest_checksum": eval_checksum,
            "initial_workflow_plan": initial_plan,
            "cluster_child_settings": cluster_settings,
            "async_decision_report_supersession_enabled": async_report_supersession_enabled,
                "contract_agent_settings": {"enabled": contract_agent_enabled},
                "log_intelligence_settings": {"enabled": log_intelligence_enabled},
                "regression_watchman_settings": {"enabled": regression_watchman_enabled},
                "change_ownership_settings": {"enabled": change_ownership_enabled},
                "defect_commander_settings": {"enabled": defect_commander_enabled},
        }


def _tag_failure_with_pipeline(exc: BaseException, pipeline_run_id: str | None) -> None:
    """Attach the pipeline id to an exception about to propagate (E7.2).

    The Celery task that catches it needs the row to schedule a retry; a
    lookup by (test_run_id, workflow_type) would work but can pick the wrong
    row when two pipelines for one run overlap. Attribute assignment can fail
    on exotic exception types; that is not worth failing the failure over.
    """
    try:
        exc.pipeline_run_id = str(pipeline_run_id) if pipeline_run_id else None  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


async def _mark_pipeline_done(
    pipeline_run_id: str,
    success: bool,
    error: Optional[str] = None,
    final_state: Optional[dict] = None,
    fencing_token: Optional[str] = None,
) -> None:
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select as sa_select, update as sa_update  # noqa: PLC0415
        # One import for both branches. Imported only inside the final_state
        # branch, the name was unbound in the else branch, so marking a
        # pipeline failed without a final state raised UnboundLocalError and
        # left it 'running' behind a masked error (re-audit N28).
        from app.services.pipeline_budget_service import (  # noqa: PLC0415
            reconcile_pipeline_budget,
            retry_pending_stage_settlements,
        )

        query = sa_select(AgentPipelineRun).where(AgentPipelineRun.id == pipeline_run_id)
        if fencing_token is not None:
            query = query.where(AgentPipelineRun.fencing_token == fencing_token)
        result = await db.execute(query.with_for_update())
        run = result.scalar_one_or_none()
        if run is None and fencing_token is not None:
            # A reaper rotated the lease while this worker was paused. Finalize
            # is a write just like a stage checkpoint: the stale holder must not
            # complete or fail the newer attempt.
            from app.services.pipeline_lease import LeaseLost  # noqa: PLC0415

            raise LeaseLost(pipeline_run_id, fencing_token)
        if run:
            summary_completed = False
            if success:
                # WF-3: Check for partial completion — some stages may have failed
                # while the pipeline overall didn't raise an exception
                stage_results = await db.execute(
                    sa_select(AgentStageResult).where(
                        AgentStageResult.pipeline_run_id == pipeline_run_id,
                    )
                )
                stages = stage_results.scalars().all()
                has_failed_stages = any(s.status == "failed" for s in stages)
                summary_completed = any(
                    s.stage_name == "summary" and s.status == "completed"
                    for s in stages
                )
                # E7.1: no more ``partial``. A graph that finished with failed
                # stages is ``completed`` and carries stage_quality=degraded in
                # execution_metadata (stamped inside the metadata rebuild below
                # so the rebuild cannot drop it).
                apply_transition(run, PipelineRunStatus.COMPLETED, error=error)
            else:
                apply_transition(run, PipelineRunStatus.FAILED, error=error)
            has_degraded_stages = bool(success and has_failed_stages)
            # E7.5: a clean run that produced no report has nothing for a human
            # to accept, so it settles ``completed -> passed`` in this same
            # transaction (architecture section 7.1) and a client can wait for
            # ``passed | failed``. A report-producing run rests at ``completed``
            # until its review is accepted (E8). "Ran" means the stage row
            # reached completed -- skipped and failed stages produced nothing.
            if success and not has_degraded_stages and passes_without_review(
                None, [s.stage_name for s in stages if s.status == "completed"]
            ):
                apply_transition(run, PipelineRunStatus.PASSED)
                run.review_policy = REVIEW_NOT_APPLICABLE
            # E8.1: a report-producing run that finished is a draft until a human
            # accepts it. The request is staged in THIS transaction, so a
            # completed report never exists without one and a request never
            # exists for a run that did not finish. A failure here is contained
            # by the service's savepoint and must not strand the run as running.
            if success and run.status == PipelineRunStatus.COMPLETED.value:
                from app.services import review_request_service  # noqa: PLC0415

                produced = review_request_service.report_stages(stages)
                if produced:
                    try:
                        review_project_id = (final_state or {}).get("project_id") or (
                            await db.execute(
                                sa_select(TestRun.project_id).where(TestRun.id == run.test_run_id)
                            )
                        ).scalar_one_or_none()
                        await review_request_service.stage_run_review_request(
                            db,
                            run=run,
                            project_id=review_project_id,
                            report_stage_names=produced,
                            evidence_bundle_sha256=review_request_service.evidence_hash_from(final_state),
                            requested_by=run.requested_by,
                        )
                    except Exception as review_exc:  # noqa: BLE001
                        logger.warning(
                            "review_request_stage_failed",
                            pipeline_run_id=pipeline_run_id,
                            error_type=type(review_exc).__name__,
                        )
            # E7.3: a terminal row holds no lease. Leaving one set would make
            # the reaper's "running past its lease" predicate meaningless and
            # would keep a dead worker's token fencing out later writers.
            for _field, _value in release_lease_fields().items():
                setattr(run, _field, _value)
            if final_state:
                prior_metadata = dict(run.execution_metadata or {})
                # Materialize report recommendations as approval-gated action
                # proposals in the same transaction as pipeline completion.
                # This is intentionally best-effort for legacy/failed reports;
                # a malformed proposal must not hide the terminal pipeline.
                try:
                    from app.services.agent_action_ledger_service import (
                        persist_report_action_proposals,
                    )

                    decision = final_state.get("decision_intelligence") or {}
                    proposed_actions = (
                        decision.get("proposed_actions")
                        if isinstance(decision, dict)
                        else []
                    )
                    await persist_report_action_proposals(
                        db,
                        project_id=uuid.UUID(str(run_project_id))
                        if (run_project_id := getattr(run, "project_id", None))
                        else uuid.UUID(str(final_state["project_id"])),
                        test_run_id=uuid.UUID(str(final_state["test_run_id"])),
                        pipeline_run_id=uuid.UUID(str(pipeline_run_id)),
                        proposed_actions=proposed_actions,
                    )
                except Exception as action_exc:  # noqa: BLE001
                    logger.warning(
                        "action_proposal_persistence_failed",
                        pipeline_run_id=pipeline_run_id,
                        error_type=type(action_exc).__name__,
                    )
                budget_metadata = {
                    "run_budget": prior_metadata.get("run_budget"),
                    "budget_spend": prior_metadata.get("budget_spend", {}),
                    "pending_settlements": prior_metadata.get("pending_settlements", {}),
                }
                retry_pending_stage_settlements(budget_metadata)
                if not success or budget_metadata.get("run_budget"):
                    reconcile_pipeline_budget(
                        budget_metadata,
                        reason="pipeline_terminalized" if success else "pipeline_failed",
                    )
                run.execution_metadata = {
                    "eval_manifest_checksum": prior_metadata.get(
                        "eval_manifest_checksum"
                    ),
                    "tools_used": final_state.get("tools_used", []),
                    "schema_version": final_state.get("schema_version", 2),
                    "fallback_used": final_state.get("fallback_used", False),
                    "skipped_stages": final_state.get("skipped_stages", []),
                    "execution_path": str(final_state.get("execution_path", ExecutionPath.EXECUTED)),
                    "analysis_mode_requested": final_state.get("analysis_mode_requested"),
                    "analysis_mode_resolved": final_state.get("analysis_mode_resolved"),
                    "analysis_mode_resolution": final_state.get("analysis_mode_resolution", {}),
                    # The cap is a pipeline-wide execution decision. A partial
                    # pipeline may later resume under this same identity, so
                    # terminalization must not discard the snapshot persisted
                    # before graph execution and let replay re-evaluate into a
                    # less restrictive mode.
                    "cost_budget_decision": prior_metadata.get(
                        "cost_budget_decision", {}
                    ),
                    "checkpoint_stages": final_state.get("_checkpoint_stages", []),
                    "checkpoint_replay_metadata": final_state.get(
                        "_checkpoint_replay_metadata", {}
                    ),
                    "workflow_route_decisions": final_state.get("_workflow_route_decisions", []),
                    "workflow_plan": final_state.get("workflow_plan", {}),
                    "initial_workflow_plan": final_state.get("initial_workflow_plan", {}),
                    "workflow_verification": final_state.get("workflow_verification", {}),
                    "agent_contracts": final_state.get("agent_contracts", {}),
                    "cluster_child_settings": final_state.get(
                        "cluster_child_settings", {}
                    ),
                    "async_decision_report_supersession_enabled": bool(
                        final_state.get("async_decision_report_supersession_enabled", False)
                    ),
                    "cluster_investigation_plan": sanitize_persistence_payload(
                        final_state.get("cluster_investigation_plan")
                    )[0],
                    "cluster_investigation_results": sanitize_persistence_payload(
                        final_state.get("cluster_investigation_results")
                    )[0],
                    **({"stage_quality": DEGRADED} if has_degraded_stages else {}),
                    "final_state_checksum_sha256": _canonical_checksum(final_state),
                    "runtime_versions": _runtime_version_snapshot(),
                    "prompt_versions": _prompt_registry_versions(),
                    "resume_attempt": prior_metadata.get("resume_attempt", 0),
                    "resume_started_at": prior_metadata.get("resume_started_at"),
                    "resume_source_pipeline_run_id": prior_metadata.get(
                        "resume_source_pipeline_run_id"
                    ),
                    "run_budget": prior_metadata.get("run_budget"),
                    "budget_spend": budget_metadata.get("budget_spend", {}),
                    "pending_settlements": budget_metadata.get("pending_settlements", {}),
                }
            else:
                prior_metadata = dict(run.execution_metadata or {})
                reconcile_pipeline_budget(
                    prior_metadata,
                    reason="pipeline_failed" if not success else "pipeline_terminalized",
                )
                run.execution_metadata = prior_metadata
            if success and summary_completed and final_state:
                from app.services.run_downstream_outbox import (
                    stage_ai_summary_notification_operation,
                )

                await stage_ai_summary_notification_operation(
                    db,
                    run_id=uuid.UUID(str(run.test_run_id)),
                    project_id=uuid.UUID(str(final_state["project_id"])),
                    build_number=str(final_state.get("build_number") or ""),
                )
        # Mark stages that were never reached (still "pending") as "skipped".
        # Also tag them with a conditional_skip execution_path so the UI shows
        # "not on active pipeline branch" rather than a generic skipped badge.
        await db.execute(
            sa_update(AgentStageResult)
            .where(
                AgentStageResult.pipeline_run_id == pipeline_run_id,
                AgentStageResult.status == "pending",
            )
            .values(
                status="skipped",
                execution_path=ExecutionPath.CONDITIONAL_SKIP.value,
                skipped_reason="Stage not on active pipeline branch",
            )
        )
        await db.commit()
