"""Compare two agent pipeline runs stage by stage (W14 AGT-05).

A read: no row, no ledger entry, no cache key is written. The router checks
access to BOTH pipelines (and that they share a project) before calling
:func:`compare_pipelines`; this module trusts the two rows it is handed.

Unknown is never "changed": a field is compared only when both sides
reported it, a checksum pair only when both checksums exist, and a delta is
given only when both values are known. A reported ``0.0`` cost stays
``0.0`` (it is a measured value, not an absent one).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_ops_vocab import COMPARE_FIELDS, MAX_COMPARE_STAGES
from app.models.postgres import AgentPipelineRun, AgentStageResult
from app.services.workflow_run_state import public_status

RELEASE_RISK_STAGE = "release_risk"


def _seconds_between(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 3)


def _stage_sort_key(stage: Any) -> tuple[str, str]:
    """The replay document's stage order (``pipeline_replay_service``):
    ``started_at`` (a stage that never started first), then the name."""
    started = getattr(stage, "started_at", None)
    return (started.isoformat() if isinstance(started, datetime) else "", str(stage.stage_name))


def _output_checksum(stage: Any) -> Optional[str]:
    result_data = stage.result_data if isinstance(stage.result_data, dict) else {}
    replay = result_data.get("_replay")
    if not isinstance(replay, dict):
        return None
    value = replay.get("output_checksum_sha256")
    return str(value) if value else None


def stage_cell(stage: AgentStageResult) -> dict:
    """One side of one stage row (``PipelineCompareStageCell``)."""
    confidence = stage.confidence_score
    cost = stage.cost_usd
    return {
        "status": stage.status,
        "started_at": stage.started_at,
        "completed_at": stage.completed_at,
        "duration_seconds": _seconds_between(stage.started_at, stage.completed_at),
        "total_tokens": stage.total_tokens,
        "cost_usd": float(cost) if cost is not None else None,
        "fallback_used": stage.fallback_used,
        "confidence_score": float(confidence) if confidence is not None else None,
        "execution_path": stage.execution_path,
        "output_checksum_sha256": _output_checksum(stage),
    }


def _last_per_name(stages: Sequence[Any]) -> dict[str, Any]:
    """The LAST row per stage name in the replay order, keyed in the order of
    the kept rows (a retried stage is compared by its latest attempt)."""
    kept: dict[str, Any] = {}
    for stage in sorted(stages, key=_stage_sort_key):
        kept.pop(stage.stage_name, None)
        kept[stage.stage_name] = stage
    return kept


def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return b - a


def _row(stage_name: str, a: Optional[dict], b: Optional[dict]) -> dict:
    changed: list[str] = []
    if a is None or b is None:
        changed = ["presence"]
    else:
        for field in COMPARE_FIELDS:
            left, right = a.get(field), b.get(field)
            if left is not None and right is not None and left != right:
                changed.append(field)
    checksum_equal: Optional[bool] = None
    if a is not None and b is not None:
        left_sum, right_sum = a.get("output_checksum_sha256"), b.get("output_checksum_sha256")
        if left_sum and right_sum:
            checksum_equal = left_sum == right_sum
    tokens_delta = None
    cost_delta = None
    duration_delta = None
    if a is not None and b is not None:
        duration = _delta(a.get("duration_seconds"), b.get("duration_seconds"))
        duration_delta = round(duration, 3) if duration is not None else None
        tokens = _delta(a.get("total_tokens"), b.get("total_tokens"))
        tokens_delta = int(tokens) if tokens is not None else None
        cost = _delta(a.get("cost_usd"), b.get("cost_usd"))
        cost_delta = round(cost, 6) if cost is not None else None
    return {
        "stage_name": stage_name,
        "a": a,
        "b": b,
        "changed": changed,
        "output_checksum_equal": checksum_equal,
        "duration_delta_seconds": duration_delta,
        "tokens_delta": tokens_delta,
        "cost_delta_usd": cost_delta,
    }


def compare_rows(a_stages: Sequence, b_stages: Sequence) -> list[dict]:
    """Join the two sides on ``stage_name`` (the LAST row per name): a's
    order first, then the stages only b has, in b's order."""
    left = _last_per_name(a_stages)
    right = _last_per_name(b_stages)
    rows = [
        _row(name, stage_cell(stage), stage_cell(right[name]) if name in right else None)
        for name, stage in left.items()
    ]
    rows.extend(
        _row(name, None, stage_cell(stage))
        for name, stage in right.items()
        if name not in left
    )
    return rows


def compare_output(rows: Sequence[dict]) -> str:
    """``COMPARE_OUTPUT_STATES``: ``changed`` when a shared stage's checksums
    differ or a stage exists on one side only; ``unknown`` when no shared stage
    recorded both checksums; else ``identical``."""
    if any(row.get("output_checksum_equal") is False for row in rows):
        return "changed"
    if any("presence" in (row.get("changed") or []) for row in rows):
        return "changed"
    if not any(row.get("output_checksum_equal") is True for row in rows):
        return "unknown"
    return "identical"


def _sum_reported(values: Sequence[Any]) -> Optional[float]:
    reported = [float(v) for v in values if v is not None]
    if not reported:
        return None
    return float(sum(reported))


def _release_verdict(stages: Sequence[Any]) -> Optional[str]:
    """The ``release_risk`` stage's ``result_data.recommendation`` (its last
    row); ``None`` when the pipeline has no such stage or it said nothing."""
    kept = _last_per_name(stages).get(RELEASE_RISK_STAGE)
    if kept is None or not isinstance(kept.result_data, dict):
        return None
    value = kept.result_data.get("recommendation")
    return str(value) if value not in (None, "") else None


def side(pipeline: AgentPipelineRun, stages: Sequence[Any], *, truncated: bool) -> dict:
    """``PipelineCompareSide`` for one pipeline and its (capped) stage rows."""
    metadata = pipeline.execution_metadata if isinstance(pipeline.execution_metadata, dict) else {}
    tokens = _sum_reported([s.total_tokens for s in stages])
    cost = _sum_reported([s.cost_usd for s in stages])
    return {
        "pipeline_run_id": pipeline.id,
        "test_run_id": pipeline.test_run_id,
        "workflow_type": pipeline.workflow_type,
        "workflow_ref": metadata.get("workflow_ref"),
        "status": pipeline.status,
        "public_status": public_status(pipeline.status),
        "attempt": pipeline.attempt,
        "rerun_of": pipeline.rerun_of,
        "created_at": pipeline.created_at,
        "started_at": pipeline.started_at,
        "completed_at": pipeline.completed_at,
        "duration_seconds": _seconds_between(pipeline.started_at, pipeline.completed_at),
        "analysis_mode_resolved": metadata.get("analysis_mode_resolved"),
        "release_verdict": _release_verdict(stages),
        "total_tokens": int(tokens) if tokens is not None else None,
        "cost_usd": round(float(cost), 6) if cost is not None else None,
        "stages_truncated": truncated,
    }


def stage_statement(pipeline_ids: Sequence[Any], *, per_side: int = MAX_COMPARE_STAGES + 1):
    """ONE statement for both sides: each pipeline's first ``per_side`` stage
    rows in the replay order (a window per pipeline, so a long pipeline cannot
    crowd the other side out)."""
    rank = (
        func.row_number()
        .over(
            partition_by=AgentStageResult.pipeline_run_id,
            order_by=(
                AgentStageResult.started_at.asc().nulls_first(),
                AgentStageResult.stage_name.asc(),
                AgentStageResult.id.asc(),
            ),
        )
        .label("stage_rank")
    )
    ranked = (
        select(AgentStageResult.id.label("stage_id"), rank)
        .where(AgentStageResult.pipeline_run_id.in_(list(pipeline_ids)))
        .subquery("ranked_stages")
    )
    return (
        select(AgentStageResult)
        .join(ranked, ranked.c.stage_id == AgentStageResult.id)
        .where(ranked.c.stage_rank <= per_side)
        .order_by(AgentStageResult.pipeline_run_id, ranked.c.stage_rank)
    )


def build_response(a: AgentPipelineRun, b: AgentPipelineRun, stages: Sequence[Any]) -> dict:
    """The ``PipelineCompareResponse`` body from the two rows and the stage
    rows of :func:`stage_statement` (pure: unit-tested without a session)."""
    a_all = [s for s in stages if s.pipeline_run_id == a.id]
    b_all = [s for s in stages if s.pipeline_run_id == b.id]
    a_stages = sorted(a_all, key=_stage_sort_key)[:MAX_COMPARE_STAGES]
    b_stages = sorted(b_all, key=_stage_sort_key)[:MAX_COMPARE_STAGES]
    rows = compare_rows(a_stages, b_stages)
    return {
        "a": side(a, a_stages, truncated=len(a_all) > MAX_COMPARE_STAGES),
        "b": side(b, b_stages, truncated=len(b_all) > MAX_COMPARE_STAGES),
        "same_test_run": a.test_run_id == b.test_run_id,
        "output": compare_output(rows),
        "changed_stages": sum(1 for row in rows if row["changed"]),
        "total": len(rows),
        "stages": rows,
    }


async def compare_pipelines(db: AsyncSession, a: AgentPipelineRun, b: AgentPipelineRun) -> dict:
    """Compare two pipelines the caller may read (checked by the router):
    ONE statement for both sides' stage rows; writes nothing."""
    result = await db.execute(stage_statement([a.id, b.id]))
    return build_response(a, b, list(result.scalars().all()))
