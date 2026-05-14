"""Deterministic replay reconstruction for agent pipeline audit trails."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AgentPipelineRun, AgentStageResult
from app.services.pipeline_event_log import get_pipeline_timeline


_TERMINAL_STAGE_EVENTS = {
    "completed": "stage_completed",
    "failed": "stage_failed",
    "skipped": "stage_skipped",
}


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _event_sort_key(event: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        _iso(event.get("timestamp")) or _iso((event.get("detail") or {}).get("at")) or "",
        str(event.get("event_type") or ""),
        str(event.get("stage_name") or ""),
        str(event.get("test_case_id") or ""),
    )


def _stage_sort_key(stage: AgentStageResult) -> tuple[str, str]:
    return (_iso(stage.started_at) or "", stage.stage_name)


def _normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    detail = event.get("detail") or {}
    return {
        "event_type": event.get("event_type", "unknown"),
        "stage_name": event.get("stage_name"),
        "test_case_id": event.get("test_case_id"),
        "timestamp": _iso(event.get("timestamp")),
        "detail": detail,
    }


def _workflow_route_decisions(pipeline: AgentPipelineRun) -> list[dict[str, Any]]:
    metadata = pipeline.execution_metadata or {}
    decisions = metadata.get("workflow_route_decisions") or []
    return sorted(
        [d for d in decisions if isinstance(d, dict)],
        key=lambda d: (
            str(d.get("at") or ""),
            str(d.get("decision_point") or ""),
            str(d.get("chosen") or ""),
        ),
    )


def _synthesized_route_events(pipeline: AgentPipelineRun) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for decision in _workflow_route_decisions(pipeline):
        events.append({
            "event_type": "decision_made",
            "stage_name": "workflow",
            "test_case_id": None,
            "timestamp": decision.get("at"),
            "detail": decision,
            "source": "postgres_execution_metadata",
        })
    return events


def _stage_replay_metadata(stage: AgentStageResult) -> dict[str, Any]:
    result_data = stage.result_data or {}
    replay = result_data.get("_replay") if isinstance(result_data, dict) else None
    return replay if isinstance(replay, dict) else {}


def _stage_summary(stage: AgentStageResult) -> dict[str, Any]:
    replay = _stage_replay_metadata(stage)
    result_data = stage.result_data if isinstance(stage.result_data, dict) else {}
    return {
        "stage_name": stage.stage_name,
        "status": stage.status,
        "started_at": _iso(stage.started_at),
        "completed_at": _iso(stage.completed_at),
        "input_checksum_sha256": replay.get("input_checksum_sha256"),
        "output_checksum_sha256": replay.get("output_checksum_sha256"),
        "runtime_versions": replay.get("runtime_versions") or {},
        "checkpoint_available": bool(stage.checkpoint_data),
        "restored_from_checkpoint": bool(result_data.get("restored_from_checkpoint")),
        "decision_count": len(stage.decision_log or []),
    }


def _event_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("event_type") or "unknown")
        counts[event_type] = counts.get(event_type, 0) + 1
    return dict(sorted(counts.items()))


def _integrity_report(
    pipeline: AgentPipelineRun,
    stages: list[AgentStageResult],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    event_pairs = {
        (event.get("stage_name"), event.get("event_type"))
        for event in events
        if event.get("stage_name")
    }
    missing_start_events: list[str] = []
    missing_terminal_events: list[str] = []
    missing_replay_checksums: list[str] = []
    missing_checkpoints: list[str] = []

    for stage in stages:
        if stage.started_at and (stage.stage_name, "stage_started") not in event_pairs:
            missing_start_events.append(stage.stage_name)
        terminal_event = _TERMINAL_STAGE_EVENTS.get(stage.status)
        if terminal_event and (stage.stage_name, terminal_event) not in event_pairs:
            missing_terminal_events.append(stage.stage_name)
        replay = _stage_replay_metadata(stage)
        if stage.status == "completed" and not replay.get("output_checksum_sha256"):
            missing_replay_checksums.append(stage.stage_name)
        if stage.status == "completed" and not stage.checkpoint_data:
            missing_checkpoints.append(stage.stage_name)

    metadata = pipeline.execution_metadata or {}
    audit_gaps = {
        "missing_start_events": missing_start_events,
        "missing_terminal_events": missing_terminal_events,
        "missing_replay_checksums": missing_replay_checksums,
        "missing_checkpoints": missing_checkpoints,
        "missing_final_state_checksum": not bool(metadata.get("final_state_checksum_sha256")),
    }
    replayable = not any(
        bool(value)
        for value in audit_gaps.values()
    )
    return {
        "replayable": replayable,
        "audit_gaps": audit_gaps,
    }


def build_replay_integrity_summary(
    pipeline: AgentPipelineRun,
    stages: list[AgentStageResult],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return only the replay integrity portion for timeline surfaces."""
    normalized_events = [_normalize_event(event) for event in events]
    if not any(
        event.get("event_type") == "decision_made" and event.get("stage_name") == "workflow"
        for event in normalized_events
    ):
        normalized_events.extend(_synthesized_route_events(pipeline))
    return _integrity_report(pipeline, stages, normalized_events)


async def build_pipeline_replay(
    db: AsyncSession,
    pipeline_id: uuid.UUID,
) -> Optional[dict[str, Any]]:
    """Build a deterministic replay document from Postgres and event-log data."""
    pipeline_result = await db.execute(
        select(AgentPipelineRun).where(AgentPipelineRun.id == pipeline_id)
    )
    pipeline = pipeline_result.scalar_one_or_none()
    if pipeline is None:
        return None

    stage_result = await db.execute(
        select(AgentStageResult).where(AgentStageResult.pipeline_run_id == pipeline_id)
    )
    stages = sorted(stage_result.scalars().all(), key=_stage_sort_key)

    events = [_normalize_event(event) for event in await get_pipeline_timeline(str(pipeline_id))]
    if not any(
        event.get("event_type") == "decision_made" and event.get("stage_name") == "workflow"
        for event in events
    ):
        events.extend(_synthesized_route_events(pipeline))
    events = sorted(events, key=_event_sort_key)

    metadata = pipeline.execution_metadata or {}
    integrity = _integrity_report(pipeline, stages, events)

    return {
        "schema_version": 1,
        "pipeline_run_id": str(pipeline.id),
        "test_run_id": str(pipeline.test_run_id),
        "workflow_type": pipeline.workflow_type,
        "status": pipeline.status,
        "started_at": _iso(pipeline.started_at),
        "completed_at": _iso(pipeline.completed_at),
        "analysis_mode_requested": metadata.get("analysis_mode_requested"),
        "analysis_mode_resolved": metadata.get("analysis_mode_resolved"),
        "analysis_mode_resolution": metadata.get("analysis_mode_resolution") or {},
        "final_state_checksum_sha256": metadata.get("final_state_checksum_sha256"),
        "runtime_versions": metadata.get("runtime_versions") or {},
        "workflow_plan": metadata.get("workflow_plan") or {},
        "workflow_verification": metadata.get("workflow_verification") or {},
        "route_decisions": _workflow_route_decisions(pipeline),
        "stage_replay": [_stage_summary(stage) for stage in stages],
        "events": events,
        "event_counts": _event_counts(events),
        **integrity,
    }
