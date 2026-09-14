"""G4 workflow replay evaluation and publish enforcement (E9.5).

Historical stage outputs are copied into a cache keyed exactly by
``(agent_id, prompt_version, input_hash)``. A candidate definition never
silently substitutes a near match: absent keys are reported as ``unmeasured``
and lower the coverage metric.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import workflow_eval_coverage
from app.models.postgres import (
    AIEvalGateRun,
    AgentPipelineRun,
    AgentStageResult,
    ReviewRequest,
    TestRun,
    WorkflowDefinition,
    WorkflowReplayCorpus,
)
from app.services.eval_verdict import EvalVerdict

MIN_REPLAY_RUNS = 20
MIN_COVERAGE = 0.80
REGRESSION_TOLERANCE = 0.05


class WorkflowEvaluationConflict(ValueError):
    """The requested evaluation or publication violates a G4 invariant."""


@dataclass(frozen=True)
class ReplayEntry:
    agent_id: str
    prompt_version: str
    input_hash: str
    output: Mapping[str, Any]
    stage_status: str = "completed"
    degraded: bool = False
    cost_usd: float = 0.0
    latency_ms: int = 0


@dataclass(frozen=True)
class ReplayCase:
    case_id: str
    project_id: uuid.UUID
    test_run_id: uuid.UUID
    prompt_version: str
    entries: tuple[ReplayEntry, ...]
    baseline_plan_passed: bool
    baseline_degraded: bool
    reviewer_rejected: bool
    baseline_cost_usd: float
    baseline_latency_ms: int


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _checksum(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def prompt_version_for(metadata: Mapping[str, Any]) -> str:
    """Return a stable version of the prompt snapshot frozen on a run."""
    versions = metadata.get("prompt_versions")
    return _checksum(versions if isinstance(versions, Mapping) else {})


def replay_input_hash(project_id: uuid.UUID, test_run_id: uuid.UUID, step_id: str) -> str:
    """Hash the historical input identity and candidate step boundary."""
    return _checksum({
        "project_id": str(project_id),
        "test_run_id": str(test_run_id),
        "step_id": step_id,
    })


def _rate(values: Sequence[bool]) -> Optional[float]:
    return sum(int(value) for value in values) / len(values) if values else None


def _average(values: Sequence[float | int]) -> Optional[float]:
    return sum(float(value) for value in values) / len(values) if values else None


def evaluate_replay(
    *,
    project_id: uuid.UUID,
    workflow_id: str,
    version: int,
    definition: Mapping[str, Any],
    cases: Sequence[ReplayCase],
) -> dict[str, Any]:
    """Evaluate a candidate graph using exact historical cache entries."""
    steps = list(definition.get("steps") or [])
    if not steps:
        raise WorkflowEvaluationConflict("workflow definition has no steps")

    measured = 0
    expected = len(cases) * len(steps)
    candidate_plan: list[bool] = []
    candidate_degraded: list[bool] = []
    candidate_rejected: list[bool] = []
    candidate_costs: list[float] = []
    candidate_latencies: list[int] = []
    case_results: list[dict[str, Any]] = []

    for case in cases:
        cache = {
            (entry.agent_id, entry.prompt_version, entry.input_hash): entry
            for entry in case.entries
        }
        step_results: list[dict[str, Any]] = []
        matched: list[ReplayEntry] = []
        for step in steps:
            step_id = str(step.get("id") or "")
            agent_id = str(step.get("agent_id") or "")
            input_hash = replay_input_hash(case.project_id, case.test_run_id, step_id)
            entry = cache.get((agent_id, case.prompt_version, input_hash))
            if entry is None:
                step_results.append({
                    "step_id": step_id,
                    "agent_id": agent_id,
                    "measurement": "unmeasured",
                    "input_hash": input_hash,
                })
                continue
            measured += 1
            matched.append(entry)
            step_results.append({
                "step_id": step_id,
                "agent_id": agent_id,
                "measurement": "replayed",
                "input_hash": input_hash,
                "stage_status": entry.stage_status,
            })

        complete = len(matched) == len(steps)
        plan_passed = complete and all(entry.stage_status == "completed" for entry in matched)
        degraded = bool(matched) and any(
            entry.degraded or entry.stage_status != "completed" for entry in matched
        )
        candidate_plan.append(plan_passed)
        candidate_degraded.append(degraded)
        candidate_rejected.append(case.reviewer_rejected)
        candidate_costs.append(sum(entry.cost_usd for entry in matched))
        candidate_latencies.append(sum(entry.latency_ms for entry in matched))
        case_results.append({
            "case_id": case.case_id,
            "plan_verified": plan_passed,
            "degraded": degraded,
            "reviewer_rejected": case.reviewer_rejected,
            "steps": step_results,
        })

    coverage = measured / expected if expected else 0.0
    baseline_plan_rate = _rate([case.baseline_plan_passed for case in cases])
    baseline_degraded_rate = _rate([case.baseline_degraded for case in cases])
    baseline_reject_rate = _rate([case.reviewer_rejected for case in cases])
    candidate_plan_rate = _rate(candidate_plan)
    candidate_degraded_rate = _rate(candidate_degraded)
    candidate_reject_rate = _rate(candidate_rejected)

    regressions: list[dict[str, Any]] = []
    comparisons = (
        ("plan_verification_pass_rate", baseline_plan_rate, candidate_plan_rate, "lower"),
        ("degraded_rate", baseline_degraded_rate, candidate_degraded_rate, "higher"),
        ("reviewer_reject_rate", baseline_reject_rate, candidate_reject_rate, "higher"),
    )
    for name, baseline, candidate, direction in comparisons:
        if baseline is None or candidate is None:
            continue
        delta = candidate - baseline
        regressed = (
            delta < -REGRESSION_TOLERANCE
            if direction == "lower"
            else delta > REGRESSION_TOLERANCE
        )
        if regressed:
            regressions.append({
                "metric": name,
                "baseline": baseline,
                "candidate": candidate,
                "delta": delta,
                "tolerance": REGRESSION_TOLERANCE,
            })

    if len(cases) < MIN_REPLAY_RUNS or coverage < MIN_COVERAGE:
        verdict = EvalVerdict.INSUFFICIENT_SAMPLES
        reason = (
            f"workflow replay needs at least {MIN_REPLAY_RUNS} runs and "
            f"{MIN_COVERAGE:.0%} coverage; got {len(cases)} runs and {coverage:.1%}"
        )
    elif regressions:
        verdict = EvalVerdict.FAIL
        reason = "candidate workflow regresses the replay baseline"
    else:
        verdict = EvalVerdict.PASS
        reason = "candidate workflow is non-regressing on the replay corpus"

    metrics = [
        {"name": "coverage", "value": coverage, "n": expected, "kind": "validity"},
        {
            "name": "plan_verification_pass_rate",
            "value": candidate_plan_rate,
            "baseline": baseline_plan_rate,
            "n": len(cases),
            "kind": "ground_truth",
        },
        {
            "name": "degraded_rate",
            "value": candidate_degraded_rate,
            "baseline": baseline_degraded_rate,
            "n": len(cases),
            "kind": "ground_truth",
        },
        {
            "name": "reviewer_reject_rate",
            "value": candidate_reject_rate,
            "baseline": baseline_reject_rate,
            "n": len(cases),
            "kind": "ground_truth",
        },
        {
            "name": "mean_cost_usd",
            "value": _average(candidate_costs),
            "baseline": _average([case.baseline_cost_usd for case in cases]),
            "n": len(cases),
            "kind": "cost",
        },
        {
            "name": "mean_latency_ms",
            "value": _average(candidate_latencies),
            "baseline": _average([case.baseline_latency_ms for case in cases]),
            "n": len(cases),
            "kind": "cost",
        },
    ]
    manifest = {
        "schema_version": 1,
        "gate": "G4",
        "project_id": str(project_id),
        "workflow_ref": f"{workflow_id}@{version}",
        "definition_sha256": _checksum(definition),
        "case_ids": [case.case_id for case in cases],
    }
    manifest_checksum = _checksum(manifest)
    return {
        "verdict": verdict.value,
        "status": verdict.value,
        "reason": reason,
        "workflow_id": workflow_id,
        "version": version,
        "sample_count": len(cases),
        "measured_steps": measured,
        "expected_steps": expected,
        "coverage": coverage,
        "manifest_checksum": manifest_checksum,
        "manifest": {**manifest, "manifest_checksum_sha256": manifest_checksum},
        "per_gate": {"G4": metrics},
        "regressions": regressions,
        "cases": case_results,
    }


def _latency_ms(stage: AgentStageResult) -> int:
    if stage.started_at is None or stage.completed_at is None:
        return 0
    return max(0, int((stage.completed_at - stage.started_at).total_seconds() * 1000))


async def _load_replay_cases(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    base: str,
    sample_limit: int,
) -> list[ReplayCase]:
    runs = (await db.execute(
        select(AgentPipelineRun)
        .join(TestRun, TestRun.id == AgentPipelineRun.test_run_id)
        .where(
            TestRun.project_id == project_id,
            AgentPipelineRun.workflow_type == base,
            AgentPipelineRun.status.in_(("completed", "passed", "failed")),
        )
        .order_by(AgentPipelineRun.created_at.desc())
        .limit(sample_limit)
    )).scalars().all()
    if not runs:
        return []

    run_ids = [run.id for run in runs]
    stages = (await db.execute(
        select(AgentStageResult).where(AgentStageResult.pipeline_run_id.in_(run_ids))
    )).scalars().all()
    reviews = (await db.execute(
        select(ReviewRequest).where(ReviewRequest.pipeline_run_id.in_(run_ids))
    )).scalars().all()
    rejected = {
        review.pipeline_run_id
        for review in reviews
        if review.state == "rejected" and review.pipeline_run_id is not None
    }
    stages_by_run: dict[uuid.UUID, list[AgentStageResult]] = {run_id: [] for run_id in run_ids}
    for stage in stages:
        stages_by_run.setdefault(stage.pipeline_run_id, []).append(stage)

    existing = (await db.execute(
        select(WorkflowReplayCorpus).where(
            WorkflowReplayCorpus.project_id == project_id,
        )
    )).scalars().all()
    cache: dict[tuple[str, str, str], WorkflowReplayCorpus] = {
        (row.agent_id, row.prompt_version, row.input_hash): row for row in existing
    }

    cases: list[ReplayCase] = []
    for run in runs:
        metadata = run.execution_metadata if isinstance(run.execution_metadata, Mapping) else {}
        prompt_version = prompt_version_for(metadata)
        run_stages = stages_by_run.get(run.id, [])
        entries: list[ReplayEntry] = []
        run_degraded = metadata.get("stage_quality") == "degraded"
        for stage in run_stages:
            if stage.result_data is None or not stage.capability_id:
                continue
            input_hash = replay_input_hash(project_id, run.test_run_id, stage.stage_name)
            key = (stage.capability_id, prompt_version, input_hash)
            row = cache.get(key)
            if row is None:
                row = WorkflowReplayCorpus(
                    project_id=project_id,
                    pipeline_run_id=run.id,
                    test_run_id=run.test_run_id,
                    step_id=stage.stage_name,
                    agent_id=stage.capability_id,
                    prompt_version=prompt_version,
                    input_hash=input_hash,
                    output=stage.result_data,
                    stage_status=stage.status,
                    degraded=run_degraded or stage.status != "completed",
                    cost_usd=max(0.0, float(stage.cost_usd or 0.0)),
                    latency_ms=_latency_ms(stage),
                )
                db.add(row)
                cache[key] = row
            entries.append(ReplayEntry(
                agent_id=row.agent_id,
                prompt_version=row.prompt_version,
                input_hash=row.input_hash,
                output=row.output,
                stage_status=row.stage_status,
                degraded=row.degraded,
                cost_usd=float(row.cost_usd),
                latency_ms=row.latency_ms,
            ))

        verification = metadata.get("workflow_verification")
        verification_status = verification.get("status") if isinstance(verification, Mapping) else None
        cases.append(ReplayCase(
            case_id=str(run.id),
            project_id=project_id,
            test_run_id=run.test_run_id,
            prompt_version=prompt_version,
            entries=tuple(entries),
            baseline_plan_passed=verification_status == "passed",
            baseline_degraded=run_degraded or any(stage.status != "completed" for stage in run_stages),
            reviewer_rejected=run.id in rejected,
            baseline_cost_usd=sum(max(0.0, float(stage.cost_usd or 0.0)) for stage in run_stages),
            baseline_latency_ms=sum(_latency_ms(stage) for stage in run_stages),
        ))
    await db.flush()
    return cases


async def evaluate_definition(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    row: WorkflowDefinition,
    sample_limit: int,
    evaluated_by: Optional[uuid.UUID],
) -> dict[str, Any]:
    """Populate exact replay entries, run G4, and persist its gate evidence."""
    if not MIN_REPLAY_RUNS <= sample_limit <= 100:
        raise WorkflowEvaluationConflict(
            f"sample_limit must be between {MIN_REPLAY_RUNS} and 100"
        )
    lock_key = f"workflow-evaluation:{project_id}:{row.workflow_id}:{row.version}"
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": lock_key},
    )
    cases = await _load_replay_cases(
        db,
        project_id=project_id,
        base=row.base,
        sample_limit=sample_limit,
    )
    result = evaluate_replay(
        project_id=project_id,
        workflow_id=row.workflow_id,
        version=row.version,
        definition=row.definition,
        cases=cases,
    )
    gate = AIEvalGateRun(
        change_id=f"workflow:{project_id}:{row.workflow_id}@{row.version}",
        status=result["verdict"],
        manifest_checksum_sha256=result["manifest_checksum"],
        manifest=result["manifest"],
        gate_results=result["per_gate"]["G4"],
        blocking_gates=[] if result["verdict"] != EvalVerdict.FAIL.value else [{"gate": "G4"}],
        version_changes=result["regressions"],
        evaluated_by=evaluated_by,
        gate_type="workflow_evaluation",
        project_id=project_id,
        agent_id=None,
        sample_count=result["sample_count"],
    )
    db.add(gate)
    await db.flush()
    now = datetime.now(timezone.utc)
    row.eval_verdict = result["verdict"]
    row.eval_coverage = result["coverage"]
    row.eval_gate_run_id = gate.id
    row.evaluated_at = now
    row.eval_regression_accepted = False
    row.eval_regression_reason = None
    row.eval_regression_accepted_by = None
    row.eval_regression_accepted_at = None
    await db.flush()
    workflow_eval_coverage.labels(
        project_id=str(project_id), workflow_id=row.workflow_id
    ).set(result["coverage"])
    result["gate_run_id"] = str(gate.id)
    return result


def enforce_publish_gate(
    row: WorkflowDefinition,
    *,
    accept_regression: bool,
    reason: Optional[str],
    accepted_by: Optional[uuid.UUID],
) -> None:
    """Refuse only measured regressions; insufficient evidence remains visible."""
    clean_reason = (reason or "").strip()
    if accept_regression and not clean_reason:
        raise WorkflowEvaluationConflict("accept_regression requires a non-empty reason")
    if row.eval_verdict != EvalVerdict.FAIL.value:
        return
    if not accept_regression:
        raise WorkflowEvaluationConflict(
            "workflow evaluation regressed; publish requires accept_regression=true and a reason"
        )
    row.eval_regression_accepted = True
    row.eval_regression_reason = clean_reason
    row.eval_regression_accepted_by = accepted_by
    row.eval_regression_accepted_at = datetime.now(timezone.utc)


__all__ = [
    "MIN_COVERAGE",
    "MIN_REPLAY_RUNS",
    "ReplayCase",
    "ReplayEntry",
    "WorkflowEvaluationConflict",
    "enforce_publish_gate",
    "evaluate_definition",
    "evaluate_replay",
    "prompt_version_for",
    "replay_input_hash",
]
