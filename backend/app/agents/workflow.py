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
import hashlib
import importlib.metadata
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, cast

from langgraph.graph import END, StateGraph

from app.agents.analysis_agent import AnalysisAgent
from app.agents.anomaly_agent import AnomalyDetectionAgent
from app.agents.cluster_agent import ClusterAgent
from app.agents.flaky_sentinel_agent import FlakySentinelAgent
from app.agents.gap_detection_agent import GapDetectionAgent
from app.agents.ingestion_agent import IngestionAgent
from app.agents.release_risk_agent import ReleaseRiskAgent
from app.agents.report_refinement_agent import ReportRefinementAgent
from app.agents.state import WorkflowState
from app.agents.summary_agent import SummaryAgent
from app.agents.test_health_agent import TestHealthAgent
from app.agents.triage_agent import DefectTriageAgent
from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.enums import ExecutionPath
from app.models.postgres import AgentPipelineRun, AgentStageResult
from app.services.agent_planner import (
    attach_workflow_plan_and_verification,
    build_workflow_plan,
)
from app.services.pipeline_event_log import emit_event

import structlog

# WF-3: Stage classification for partial-completion logic
DEEP_REQUIRED_STAGES = frozenset({"ingestion", "anomaly_detection", "failure_clustering", "root_cause_analysis", "summary"})
DEEP_OPTIONAL_STAGES = frozenset({"triage", "gap_detection", "report_refinement", "flaky_sentinel", "test_health", "release_risk"})

logger = structlog.get_logger("agents.workflow")

# Singleton agent instances (stateless — safe to share across concurrent pipeline runs)
_ingestion     = IngestionAgent()
_anomaly       = AnomalyDetectionAgent()
_analysis      = AnalysisAgent()
_summary       = SummaryAgent()
_triage        = DefectTriageAgent()
_cluster       = ClusterAgent()
_gap_detection = GapDetectionAgent()
_report_refinement = ReportRefinementAgent()
_flaky_sentinel = FlakySentinelAgent()
_test_health   = TestHealthAgent()
_release_risk  = ReleaseRiskAgent()


def _canonical_checksum(data: Any) -> str:
    """Return a stable checksum for replay/audit comparisons."""
    try:
        payload = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        payload = json.dumps(str(data), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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


def _prompt_registry_versions() -> dict[str, str]:
    """Never-raising wrapper over the registry's full version-tag map."""
    try:
        from app.services.prompt_registry import registry_versions

        return registry_versions()
    except Exception:  # pragma: no cover — stamping must never break the pipeline
        return {}


async def _resolve_analysis_mode_snapshot() -> dict[str, Any]:
    """Resolve analysis mode once so a pipeline is reproducible end-to-end."""
    from app.services.analysis_router import get_analysis_mode, refresh_analysis_mode_from_cache

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
    resolved = get_analysis_mode()
    return {
        "requested": requested,
        "resolved": resolved,
        "provider": settings.LLM_PROVIDER,
        "model": settings.LLM_MODEL,
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
            "summary":           "summary",
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
                                    └─ root_cause_analysis ─┐                              │                                   ├─ flaky_sentinel ─ test_health ─ release_risk ─ END
                                    └─ failure_clustering   ─┘  (fan-in to summary)        │  (no triage) ─────────────────────┘
                  (no failures) └──────────────────────────────────────────────────────────── summary ─ flaky_sentinel ─ test_health ─ release_risk ─ END
    """
    graph = StateGraph(WorkflowState)

    graph.add_node("ingestion",            _make_checkpointed_node(ingestion_node, "ingestion"))
    graph.add_node("anomaly_detection",    _make_checkpointed_node(anomaly_node, "anomaly_detection"))
    graph.add_node("failure_clustering",   _make_checkpointed_node(cluster_node, "failure_clustering"))
    graph.add_node("root_cause_analysis",  _make_checkpointed_node(analysis_node, "root_cause_analysis"))
    graph.add_node("summary",              _make_checkpointed_node(summary_node, "summary"))
    graph.add_node("triage",               _make_checkpointed_node(triage_node, "triage"))
    graph.add_node("gap_detection",        _make_checkpointed_node(gap_detection_node, "gap_detection"))
    graph.add_node("report_refinement",    _make_checkpointed_node(report_refinement_node, "report_refinement"))
    graph.add_node("flaky_sentinel",       _make_checkpointed_node(flaky_sentinel_node, "flaky_sentinel"))
    graph.add_node("test_health",          _make_checkpointed_node(test_health_node, "test_health"))
    graph.add_node("release_risk",         _make_checkpointed_node(release_risk_node, "release_risk"))

    graph.set_entry_point("ingestion")

    # Conditional routing after ingestion
    graph.add_conditional_edges(
        "ingestion",
        _route_after_ingestion,
        {
            "anomaly_detection": "anomaly_detection",
            "summary":           "summary",
        },
    )

    # Parallel fan-out from ingestion (unconditional — see note in _build_offline_graph).
    # Both nodes carry fast-path guards: they return immediately when failed_test_ids is empty.
    graph.add_edge("ingestion", "root_cause_analysis")
    graph.add_edge("ingestion", "failure_clustering")

    # Fan-in: all three parallel branches → summary
    graph.add_edge("anomaly_detection",   "summary")
    graph.add_edge("root_cause_analysis", "summary")
    graph.add_edge("failure_clustering",  "summary")

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
            "flaky_sentinel": "gap_detection",
        },
    )

    # Specialist stages run sequentially after triage (or directly after summary)
    graph.add_edge("triage",            "gap_detection")
    graph.add_edge("gap_detection",     "report_refinement")
    graph.add_edge("report_refinement", "flaky_sentinel")
    graph.add_edge("flaky_sentinel",  "test_health")
    graph.add_edge("test_health",     "release_risk")
    graph.add_edge("release_risk",    END)

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
) -> None:
    """
    Persist a stage's output dict to the AgentStageResult row so the pipeline
    can resume from this point if a later stage fails.
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
            if stage and stage.status == "completed":
                # Store the state delta produced by this stage for potential replay
                stage.checkpoint_data = _safe_serialize(stage_output)
                replay_metadata = {
                    "input_checksum_sha256": input_checksum_sha256,
                    "output_checksum_sha256": _canonical_checksum(stage_output),
                    "runtime_versions": _runtime_version_snapshot(),
                }
                stage.result_data = {
                    **(stage.result_data or {}),
                    "_replay": replay_metadata,
                }
                await db.commit()
    except Exception as exc:
        logger.warning(
            "checkpoint_write_failed",
            pipeline_run_id=pipeline_run_id,
            stage_name=stage_name,
            error=str(exc),
        )


async def _load_checkpoint(test_run_id: str, workflow_type: str) -> Optional[dict]:
    """
    Look for a prior failed pipeline run for the same test_run_id.
    If one exists with completed stages, return the merged checkpoint state
    so the new run can skip those stages.
    """
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select as sa_select  # noqa: PLC0415

            # Find most recent failed pipeline for this test run
            result = await db.execute(
                sa_select(AgentPipelineRun)
                .where(
                    AgentPipelineRun.test_run_id == test_run_id,
                    AgentPipelineRun.workflow_type == workflow_type,
                    AgentPipelineRun.status.in_(["failed", "partial"]),
                )
                .order_by(AgentPipelineRun.started_at.desc())
                .limit(1)
            )
            prev_run = result.scalar_one_or_none()
            if not prev_run:
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

            # Merge all completed stage outputs into a single state dict
            merged_state: dict = {}
            checkpoint_stage_names: list[str] = []
            for stage in completed_stages:
                if stage.checkpoint_data:
                    data = stage.checkpoint_data if isinstance(stage.checkpoint_data, dict) else {}
                    merged_state.update(data)
                    checkpoint_stage_names.append(stage.stage_name)

            if checkpoint_stage_names:
                logger.info(
                    "Loaded checkpoint from previous run %s: stages=%s",
                    prev_run.id, checkpoint_stage_names,
                )
                merged_state["_checkpoint_stages"] = checkpoint_stage_names
                return merged_state

    except Exception as exc:
        logger.warning(
            "checkpoint_load_failed",
            test_run_id=test_run_id,
            error=str(exc),
        )

    return None


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

def _make_checkpointed_node(original_node, stage_name: str):
    """Wrap a node function so its output is checkpointed after successful execution."""
    async def wrapper(state: WorkflowState) -> dict[str, Any]:
        pipeline_run_id = state.get("pipeline_run_id", "")
        input_checksum = _canonical_checksum(state)

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

        try:
            result = cast(dict[str, Any], await original_node(state))
        except Exception as exc:
            # Mark the individual stage as failed so it doesn't stay stuck in "running"
            error_msg = f"{stage_name} failed: {exc}"
            logger.error(
                "stage_unhandled_exception",
                stage_name=stage_name,
                error=str(exc),
                exc_info=True,
            )
            try:
                await _mark_stage_failed(pipeline_run_id, stage_name, error_msg)
            except Exception:
                logger.warning("mark_stage_failed_db_error", stage_name=stage_name)
            raise

        # Persist checkpoint
        if pipeline_run_id:
            await _checkpoint_stage(
                pipeline_run_id,
                stage_name,
                result,
                input_checksum_sha256=input_checksum,
            )

        return result

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
            error=str(exc),
        )
    return final_state


# ── Public entry points ───────────────────────────────────────────────────────

async def run_offline_pipeline(
    test_run_id: str,
    project_id: str,
    build_number: str,
    workflow_type: str = "offline",
) -> dict:
    """
    Execute the full offline analysis pipeline for a completed test run.

    - Creates AgentPipelineRun + AgentStageResult tracking records
    - Selects the appropriate compiled graph (offline vs live)
    - Returns the final LangGraph state dict
    """
    pipeline_run_id = str(uuid.uuid4())
    await _create_pipeline_run(pipeline_run_id, test_run_id, workflow_type)

    # Attempt to load checkpoint from a previous failed run
    checkpoint = await _load_checkpoint(test_run_id, workflow_type)
    mode_snapshot = await _resolve_analysis_mode_snapshot()

    initial_state: WorkflowState = {
        "pipeline_run_id":    pipeline_run_id,
        "test_run_id":        test_run_id,
        "project_id":         project_id,
        "build_number":       build_number,
        "workflow_type":      workflow_type,
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
        "triage_results":     [],
        # Deep pipeline state (empty for offline/live pipelines)
        "failure_clusters":   [],
        "cluster_map":        {},
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
        "workflow_plan": build_workflow_plan(workflow_type=workflow_type),
        "workflow_verification": {},
        "agent_contracts": {},
        "schema_version":     2,
        "stage_metrics":      {},
    }

    # Merge checkpoint data into initial state (restored stage outputs)
    if checkpoint:
        checkpoint_stages = checkpoint.pop("_checkpoint_stages", [])
        initial_state.update(checkpoint)  # type: ignore[typeddict-item]
        initial_state["_checkpoint_stages"] = checkpoint_stages  # type: ignore[typeddict-unknown-key]
        logger.info(
            "Pipeline %s resuming with checkpoint: stages=%s",
            pipeline_run_id, checkpoint_stages,
        )

    if workflow_type == "deep":
        app = _deep_app
    elif workflow_type == "live":
        app = _live_app
    else:
        app = _offline_app

    try:
        logger.info(
            "Starting %s pipeline %s for run %s (build=%s)",
            workflow_type, pipeline_run_id, test_run_id, build_number,
        )
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
        await _mark_pipeline_done(pipeline_run_id, success=True, final_state=final_state)
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
            "Pipeline %s complete. stages=%s errors=%d skipped=%s",
            pipeline_run_id,
            final_state.get("completed_stages"),
            len(final_state.get("errors", [])),
            final_state.get("skipped_stages", []),
        )
        # Persist unified memory entries for historical recall (P3)
        await _persist_memory(project_id, test_run_id, pipeline_run_id, final_state)
        return cast(dict[str, Any], final_state)
    except Exception as exc:
        error_msg = f"Pipeline execution error: {exc}"
        logger.error(error_msg, exc_info=True)
        await _mark_pipeline_done(pipeline_run_id, success=False, error=error_msg)
        await emit_event(pipeline_run_id, "error_occurred", detail={
            "workflow_type": workflow_type,
            "error": str(exc)[:500],
        })
        raise


async def run_deep_pipeline(
    test_run_id: str,
    project_id: str,
    build_number: str,
) -> dict:
    """
    Execute the deep investigation pipeline with clustering, flaky sentinel,
    test health analysis, and release risk assessment.
    """
    pipeline_run_id = str(uuid.uuid4())
    await _create_pipeline_run(pipeline_run_id, test_run_id, "deep")

    checkpoint = await _load_checkpoint(test_run_id, "deep")
    mode_snapshot = await _resolve_analysis_mode_snapshot()

    initial_state: WorkflowState = {
        "pipeline_run_id":    pipeline_run_id,
        "test_run_id":        test_run_id,
        "project_id":         project_id,
        "build_number":       build_number,
        "workflow_type":      "deep",
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
        "triage_results":     [],
        "failure_clusters":   [],
        "cluster_map":        {},
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
        "workflow_plan": build_workflow_plan(workflow_type="deep"),
        "workflow_verification": {},
        "agent_contracts": {},
        "schema_version":     2,
        "stage_metrics":      {},
    }

    if checkpoint:
        checkpoint_stages = checkpoint.pop("_checkpoint_stages", [])
        initial_state.update(checkpoint)  # type: ignore[typeddict-item]
        initial_state["_checkpoint_stages"] = checkpoint_stages  # type: ignore[typeddict-unknown-key]
        logger.info(
            "Deep pipeline %s resuming with checkpoint: stages=%s",
            pipeline_run_id, checkpoint_stages,
        )

    try:
        logger.info(
            "Starting deep pipeline %s for run %s (build=%s)",
            pipeline_run_id, test_run_id, build_number,
        )
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
        await _mark_pipeline_done(pipeline_run_id, success=True, final_state=final_state)
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
            "Deep pipeline %s complete. stages=%s errors=%d skipped=%s",
            pipeline_run_id,
            final_state.get("completed_stages"),
            len(final_state.get("errors", [])),
            final_state.get("skipped_stages", []),
        )
        # Persist unified memory entries for historical recall (P3)
        await _persist_memory(project_id, test_run_id, pipeline_run_id, final_state)
        return cast(dict[str, Any], final_state)
    except Exception as exc:
        error_msg = f"Deep pipeline execution error: {exc}"
        logger.error(error_msg, exc_info=True)
        await _mark_pipeline_done(pipeline_run_id, success=False, error=error_msg)
        await emit_event(pipeline_run_id, "error_occurred", detail={
            "workflow_type": "deep",
            "error": str(exc)[:500],
        })
        raise


# ── Stage-skip helper ─────────────────────────────────────────────────────────

async def _write_stage_skipped(
    pipeline_run_id: str,
    stage_name: str,
    skipped_reason: str,
    execution_path: ExecutionPath,
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
                await db.commit()
    except Exception as exc:
        logger.warning(
            "skipped_stage_write_failed",
            pipeline_run_id=pipeline_run_id,
            stage_name=stage_name,
            error=str(exc),
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
            error=str(exc),
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
                stage.result_data = {
                    **(stage.result_data or {}),
                    "restored_from_checkpoint": True,
                }
                stage.route_rationale = "Restored from previous pipeline checkpoint"
                await db.commit()
    except Exception as exc:
        logger.warning(
            "mark_stage_restored_error",
            pipeline_run_id=pipeline_run_id,
            stage_name=stage_name,
            error=str(exc),
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
            error=str(exc),
        )


# ── DB helpers ────────────────────────────────────────────────────────────────

_PIPELINE_STAGES = [
    "ingestion", "anomaly_detection", "root_cause_analysis", "summary", "triage",
]
_DEEP_PIPELINE_STAGES = [
    "ingestion", "anomaly_detection", "failure_clustering", "root_cause_analysis",
    "summary", "triage", "gap_detection", "report_refinement",
    "flaky_sentinel", "test_health", "release_risk",
]
_LIVE_PIPELINE_STAGES = [
    "ingestion", "summary",
]


async def _create_pipeline_run(
    pipeline_run_id: str, test_run_id: str, workflow_type: str
) -> None:
    if workflow_type == "deep":
        stages = _DEEP_PIPELINE_STAGES
    elif workflow_type == "live":
        stages = _LIVE_PIPELINE_STAGES
    else:
        stages = _PIPELINE_STAGES
    async with AsyncSessionLocal() as db:
        db.add(AgentPipelineRun(
            id=pipeline_run_id,
            test_run_id=test_run_id,
            workflow_type=workflow_type,
            status="running",
            started_at=datetime.now(timezone.utc),
        ))
        for stage in stages:
            db.add(AgentStageResult(
                pipeline_run_id=pipeline_run_id,
                stage_name=stage,
                status="pending",
            ))
        await db.commit()


async def _mark_pipeline_done(
    pipeline_run_id: str,
    success: bool,
    error: Optional[str] = None,
    final_state: Optional[dict] = None,
) -> None:
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select as sa_select, update as sa_update  # noqa: PLC0415

        result = await db.execute(
            sa_select(AgentPipelineRun).where(AgentPipelineRun.id == pipeline_run_id)
        )
        run = result.scalar_one_or_none()
        if run:
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
                run.status = "partial" if has_failed_stages else "completed"
            else:
                run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)
            if error:
                run.error = error[:2000]
            if final_state:
                run.execution_metadata = {
                    "tools_used": final_state.get("tools_used", []),
                    "schema_version": final_state.get("schema_version", 2),
                    "fallback_used": final_state.get("fallback_used", False),
                    "skipped_stages": final_state.get("skipped_stages", []),
                    "execution_path": str(final_state.get("execution_path", ExecutionPath.EXECUTED)),
                    "analysis_mode_requested": final_state.get("analysis_mode_requested"),
                    "analysis_mode_resolved": final_state.get("analysis_mode_resolved"),
                    "analysis_mode_resolution": final_state.get("analysis_mode_resolution", {}),
                    "checkpoint_stages": final_state.get("_checkpoint_stages", []),
                    "workflow_route_decisions": final_state.get("_workflow_route_decisions", []),
                    "workflow_plan": final_state.get("workflow_plan", {}),
                    "workflow_verification": final_state.get("workflow_verification", {}),
                    "agent_contracts": final_state.get("agent_contracts", {}),
                    "final_state_checksum_sha256": _canonical_checksum(final_state),
                    "runtime_versions": _runtime_version_snapshot(),
                    # AI-F2: full id → v<version>:<hash12> map of every
                    # registered prompt, recorded once per pipeline so any
                    # verdict from this run can be replayed against the exact
                    # prompt bytes that produced it.
                    "prompt_versions": _prompt_registry_versions(),
                }

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
