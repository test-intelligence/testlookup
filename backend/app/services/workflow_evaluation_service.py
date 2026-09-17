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
PUBLISH_REPLAY_RUNS = 100
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
    step_id: str = ""
    authority_sha256: str = ""
    input_checksum_sha256: str = ""
    output_checksum_sha256: str = ""
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


def replay_input_hash(
    project_id: uuid.UUID,
    input_checksum_sha256: str,
    step_id: str,
    authority_sha256: str | None = None,
) -> str:
    """Hash the recorded stage input and the complete execution boundary."""
    return _checksum({
        "project_id": str(project_id),
        "input_checksum_sha256": input_checksum_sha256,
        "step_id": step_id,
        "execution_authority_sha256": authority_sha256,
    })


def step_execution_authority(
    *,
    definition: Mapping[str, Any],
    step_id: str,
    resolved_agent_configs: Mapping[str, Any],
    endpoint_authority_fingerprints: Mapping[str, Any],
    prompt_versions: Mapping[str, Any],
    runtime_versions: Mapping[str, Any],
    workflow_plan_sha256: str,
    workflow_runtime_authority_sha256: str,
    planning_context: Mapping[str, Any],
) -> str:
    """Bind replay evidence to every behavior authority that can change output."""
    steps = [step for step in definition.get("steps") or [] if isinstance(step, Mapping)]
    step = next((item for item in steps if str(item.get("id") or "") == step_id), {})
    agent_id = str(step.get("agent_id") or "")
    return _checksum({
        "base": definition.get("base"),
        "step": step,
        "edges": definition.get("edges") or [],
        "loops": definition.get("loops") or [],
        "retry_policy": definition.get("retry_policy") or {},
        "review_policy": definition.get("review_policy"),
        "deadline_seconds": definition.get("deadline_seconds"),
        "resolved_agent_config": resolved_agent_configs.get(agent_id),
        "endpoint_authority_fingerprint": endpoint_authority_fingerprints.get(agent_id),
        "prompt_versions": prompt_versions,
        "runtime_versions": runtime_versions,
        "workflow_plan_sha256": workflow_plan_sha256,
        "workflow_runtime_authority_sha256": workflow_runtime_authority_sha256,
        "planning_context": planning_context,
    })


def _corpus_evidence(cases: Sequence[ReplayCase]) -> str:
    """Digest every fact used by G4, without embedding raw stage outputs."""
    return _checksum([
        {
            "case_id": case.case_id,
            "project_id": str(case.project_id),
            "test_run_id": str(case.test_run_id),
            "prompt_version": case.prompt_version,
            "entries": [
                {
                    "agent_id": entry.agent_id,
                    "step_id": entry.step_id,
                    "prompt_version": entry.prompt_version,
                    "input_hash": entry.input_hash,
                    "authority_sha256": entry.authority_sha256,
                    "input_checksum_sha256": entry.input_checksum_sha256,
                    "output_checksum_sha256": (
                        entry.output_checksum_sha256 or _checksum(entry.output)
                    ),
                    "stage_status": entry.stage_status,
                    "degraded": entry.degraded,
                    "cost_usd": entry.cost_usd,
                    "latency_ms": entry.latency_ms,
                }
                for entry in sorted(
                    case.entries,
                    key=lambda item: (item.step_id, item.agent_id, item.input_hash),
                )
            ],
            "baseline_plan_passed": case.baseline_plan_passed,
            "baseline_degraded": case.baseline_degraded,
            "reviewer_rejected": case.reviewer_rejected,
            "baseline_cost_usd": case.baseline_cost_usd,
            "baseline_latency_ms": case.baseline_latency_ms,
        }
        for case in sorted(cases, key=lambda item: item.case_id)
    ])


def _rate(values: Sequence[bool]) -> Optional[float]:
    return sum(int(value) for value in values) / len(values) if values else None


def _average(values: Sequence[float | int]) -> Optional[float]:
    return sum(float(value) for value in values) / len(values) if values else None


def replay_corpus_lock_key(project_id: uuid.UUID, base: str) -> str:
    """Serialize writers that share the replay cache's unique-key domain."""
    return f"workflow-replay-corpus:{project_id}:{base}"


def evaluate_replay(
    *,
    project_id: uuid.UUID,
    workflow_id: str,
    version: int,
    definition: Mapping[str, Any],
    cases: Sequence[ReplayCase],
    candidate_step_authority: Mapping[str, str] | None = None,
    sample_limit: int | None = None,
) -> dict[str, Any]:
    """Evaluate a candidate graph using exact historical cache entries."""
    steps = list(definition.get("steps") or [])
    if not steps:
        raise WorkflowEvaluationConflict("workflow definition has no steps")
    topology_measured = not (
        definition.get("loops")
        or any(
            isinstance(edge, Mapping)
            and (edge.get("when") is not None or edge.get("join") is not None)
            for edge in definition.get("edges") or []
        )
        or any(
            isinstance(step, Mapping) and bool(step.get("reviews"))
            for step in steps
        )
    )

    measured = 0
    authority_mismatches = 0
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
            authority = (candidate_step_authority or {}).get(step_id)
            historical_entry = next(
                (
                    entry
                    for entry in case.entries
                    if entry.agent_id == agent_id and entry.step_id == step_id
                ),
                None,
            )
            input_checksum = (
                historical_entry.input_checksum_sha256 if historical_entry else ""
            )
            input_hash = replay_input_hash(case.project_id, input_checksum, step_id, authority)
            entry = cache.get((agent_id, case.prompt_version, input_hash))
            if entry is None:
                authority_mismatch = bool(
                    authority
                    and any(
                        candidate.agent_id == agent_id
                        and candidate.step_id == step_id
                        and candidate.authority_sha256 != authority
                        for candidate in case.entries
                    )
                )
                authority_mismatches += int(authority_mismatch)
                step_results.append({
                    "step_id": step_id,
                    "agent_id": agent_id,
                    "measurement": "unmeasured",
                    "input_hash": input_hash,
                    "reason": (
                        "execution_authority_mismatch"
                        if authority_mismatch
                        else "no_exact_replay_entry"
                    ),
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

    if authority_mismatches:
        verdict = EvalVerdict.FAIL
        reason = (
            "candidate model, tool, config, prompt, runtime, or topology authority "
            "differs from replay evidence; publish requires reasoned acceptance"
        )
    elif not topology_measured:
        verdict = EvalVerdict.INSUFFICIENT_SAMPLES
        reason = (
            "workflow replay does not measure branch, join, loop, or reviewer routing; "
            "publication remains low-coverage evidence"
        )
    elif len(cases) < MIN_REPLAY_RUNS or coverage < MIN_COVERAGE:
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
        "execution_authority_sha256": _checksum(candidate_step_authority or {}),
        "corpus_evidence_sha256": _corpus_evidence(cases),
        "sample_limit": sample_limit if sample_limit is not None else len(cases),
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
        "topology_measured": topology_measured,
        "authority_mismatch_steps": authority_mismatches,
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


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _stage_replay_receipt(stage: AgentStageResult) -> Mapping[str, Any] | None:
    """Validate the runtime's durable input/output receipt for one attempt."""
    result_data = stage.result_data if isinstance(stage.result_data, Mapping) else {}
    replay = result_data.get("_replay")
    checkpoint = stage.checkpoint_data
    if not isinstance(replay, Mapping) or not isinstance(checkpoint, Mapping):
        return None
    input_checksum = replay.get("input_checksum_sha256")
    output_checksum = replay.get("output_checksum_sha256")
    runtime_versions = replay.get("runtime_versions")
    attempt = replay.get("attempt")
    attempt_key = replay.get("attempt_idempotency_key")
    if (
        not _is_sha256(input_checksum)
        or not _is_sha256(output_checksum)
        or not isinstance(runtime_versions, Mapping)
        or not runtime_versions
        or isinstance(attempt, bool)
        or not isinstance(attempt, int)
        or attempt < 1
        or attempt != stage.attempt
        or not _is_sha256(attempt_key)
        or _checksum(checkpoint) != output_checksum
        or _checksum({
            "pipeline_run_id": str(stage.pipeline_run_id),
            "stage_name": stage.stage_name,
            "attempt": attempt,
            "output_checksum_sha256": output_checksum,
        }) != attempt_key
    ):
        return None
    return replay


def _stage_selection_key(stage: AgentStageResult) -> tuple[int, int, float, float, str]:
    """Prefer a terminal latest attempt, with stable timestamps and id ties."""
    terminal = stage.status in {"completed", "failed", "skipped"}
    return (
        int(terminal),
        int(stage.attempt or 0),
        stage.completed_at.timestamp() if stage.completed_at is not None else float("-inf"),
        stage.started_at.timestamp() if stage.started_at is not None else float("-inf"),
        str(stage.id),
    )


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
        .order_by(AgentPipelineRun.created_at.desc(), AgentPipelineRun.id.desc())
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
    selected_stages: dict[tuple[uuid.UUID, str], AgentStageResult] = {}
    for stage in sorted(stages, key=_stage_selection_key):
        selected_stages[(stage.pipeline_run_id, stage.stage_name)] = stage
    stages_by_run: dict[uuid.UUID, list[AgentStageResult]] = {run_id: [] for run_id in run_ids}
    for (run_id, _step_id), stage in selected_stages.items():
        stages_by_run.setdefault(run_id, []).append(stage)

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
        historical_definition = metadata.get("workflow_definition")
        historical_configs = metadata.get("resolved_agent_configs")
        historical_endpoints = metadata.get("endpoint_authority_fingerprints")
        historical_prompts = metadata.get("prompt_versions")
        historical_plan_sha256 = metadata.get("workflow_plan_sha256")
        historical_runtime_authority = metadata.get(
            "workflow_runtime_authority_sha256"
        )
        run_stages = sorted(
            stages_by_run.get(run.id, []), key=lambda item: (item.stage_name, str(item.id))
        )
        entries: list[ReplayEntry] = []
        run_degraded = metadata.get("stage_quality") == "degraded"
        for stage in run_stages:
            if stage.result_data is None or not stage.capability_id:
                continue
            receipt = _stage_replay_receipt(stage)
            if receipt is None:
                continue
            receipt_runtime = receipt.get("runtime_versions")
            planning_context = {
                "cluster_child_settings": metadata.get("cluster_child_settings"),
                "async_decision_report_supersession_enabled": metadata.get(
                    "async_decision_report_supersession_enabled", False
                ),
                "contract_agent_settings": metadata.get("contract_agent_settings"),
                "log_intelligence_settings": metadata.get("log_intelligence_settings"),
                "regression_watchman_settings": metadata.get(
                    "regression_watchman_settings"
                ),
                "change_ownership_settings": metadata.get("change_ownership_settings"),
                "defect_commander_settings": metadata.get("defect_commander_settings"),
                "run_budget": metadata.get("run_budget"),
            }
            authority = step_execution_authority(
                definition=(
                    historical_definition
                    if isinstance(historical_definition, Mapping)
                    else {}
                ),
                step_id=stage.stage_name,
                resolved_agent_configs=(
                    historical_configs if isinstance(historical_configs, Mapping) else {}
                ),
                endpoint_authority_fingerprints=(
                    historical_endpoints if isinstance(historical_endpoints, Mapping) else {}
                ),
                prompt_versions=(
                    historical_prompts if isinstance(historical_prompts, Mapping) else {}
                ),
                runtime_versions=(receipt_runtime if isinstance(receipt_runtime, Mapping) else {}),
                workflow_plan_sha256=(
                    historical_plan_sha256 if isinstance(historical_plan_sha256, str) else ""
                ),
                workflow_runtime_authority_sha256=(
                    historical_runtime_authority
                    if isinstance(historical_runtime_authority, str)
                    else ""
                ),
                planning_context=planning_context,
            )
            input_hash = replay_input_hash(
                project_id,
                str(receipt["input_checksum_sha256"]),
                stage.stage_name,
                authority,
            )
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
                    output=dict(stage.checkpoint_data),
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
                step_id=stage.stage_name,
                authority_sha256=authority,
                input_checksum_sha256=str(receipt["input_checksum_sha256"]),
                output_checksum_sha256=_checksum(row.output),
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


async def _candidate_step_authority(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    row: WorkflowDefinition,
) -> dict[str, str]:
    from app.agents.workflow import (
        _prompt_registry_versions,
        _runtime_version_snapshot,
        _workflow_runtime_authority_checksum,
    )
    from app.services.agent_capability_registry import get_capability
    from app.services.agent_config_resolver import (
        endpoint_authority_fingerprint,
        freeze_for_invocation,
        resolve_for_project,
    )
    from app.services.agent_investigation_service import (
        get_effective_policy,
        run_budget_from_policy,
    )
    from app.services.agent_planner import build_workflow_plan, compute_workflow_plan_hash
    from app.services.cluster_investigation_orchestrator import (
        resolve_cluster_child_settings,
    )
    from app.services.feature_flags import is_enabled
    from app.services.workflow_definition_service import body_from_item

    runtime_definition = body_from_item(row).model_dump(mode="json", by_alias=True)
    steps = [
        step
        for step in runtime_definition.get("steps") or []
        if isinstance(step, Mapping)
    ]
    configs: dict[str, Any] = {}
    workflow_configs: dict[str, dict[str, Any]] = {}
    endpoint_fingerprints: dict[str, str] = {}
    for agent_id in dict.fromkeys(str(step.get("agent_id") or "") for step in steps):
        resolved = await resolve_for_project(db, project_id, agent_id)
        snapshot = freeze_for_invocation(resolved)
        configs[agent_id] = snapshot
        workflow_configs[agent_id] = dict(snapshot["config"])
        endpoint_fingerprints[agent_id] = endpoint_authority_fingerprint(resolved)

    base = str(runtime_definition.get("base") or "")
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
    if base == "deep":
        cluster_settings = await resolve_cluster_child_settings(db, project_id)
    async_supersession = (
        await is_enabled(
            "async_decision_report_supersession", db=db, project_id=project_id
        )
        if base == "deep"
        else False
    )
    specialist_flags = {
        "contract_validation": await is_enabled(
            "contract_validation", db=db, project_id=project_id
        ),
        "log_intelligence": await is_enabled(
            "log_intelligence", db=db, project_id=project_id
        ),
        "regression_watchman": await is_enabled(
            "regression_watchman", db=db, project_id=project_id
        ),
        "change_ownership": await is_enabled(
            "change_ownership", db=db, project_id=project_id
        ),
        "defect_commander": await is_enabled(
            "defect_commander", db=db, project_id=project_id
        ),
    }
    run_budget = run_budget_from_policy(await get_effective_policy(db, project_id))
    plan = build_workflow_plan(
        workflow_type=base,
        cluster_children_enabled=bool(cluster_settings["enabled"]),
        cluster_children_aggregate_budget=dict(cluster_settings["aggregate_budget"]),
        decision_graph_aggregate_budget=dict(run_budget),
        contract_validation_enabled=specialist_flags["contract_validation"],
        log_intelligence_enabled=specialist_flags["log_intelligence"],
        regression_watchman_enabled=specialist_flags["regression_watchman"],
        change_ownership_enabled=specialist_flags["change_ownership"],
        defect_commander_enabled=specialist_flags["defect_commander"],
    )
    by_capability = {
        str(item.get("stage")): dict(item)
        for item in plan.get("stages", [])
        if isinstance(item, dict)
    }
    incoming: dict[str, list[str]] = {
        str(step.get("id") or ""): [] for step in steps
    }
    for edge in runtime_definition.get("edges") or []:
        if not isinstance(edge, Mapping):
            continue
        sources = edge.get("from")
        source_list = sources if isinstance(sources, list) else [sources]
        target = str(edge.get("to") or "")
        if target in incoming:
            incoming[target].extend(str(source) for source in source_list)
    selected_plan: list[dict[str, Any]] = []
    for step in steps:
        step_id = str(step.get("id") or "")
        agent_id = str(step.get("agent_id") or "")
        capability_stage = agent_id.removeprefix("agent.").removesuffix(".v1")
        planned = dict(by_capability.get(capability_stage) or {})
        planned.update({
            "stage": step_id,
            "capability_id": agent_id,
            "tools": list(step.get("tools") or []),
            "dependencies": incoming[step_id],
            "planned": bool(planned.get("planned", True))
            and bool(workflow_configs[agent_id]["enabled"]),
            "required": bool(planned.get("required", False)),
        })
        # Fail closed if the registry changes between validation and evaluation.
        get_capability(capability_stage)
        selected_plan.append(planned)
    workflow_id = row.workflow_id
    workflow_version = row.version
    workflow_ref = f"{workflow_id}@{workflow_version}"
    plan = {
        **plan,
        "workflow_id": workflow_id,
        "workflow_version": workflow_version,
        "workflow_ref": workflow_ref,
        "stages": selected_plan,
    }
    plan_sha256 = compute_workflow_plan_hash(plan)
    runtime_authority = _workflow_runtime_authority_checksum(
        project_id=str(project_id),
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        workflow_ref=workflow_ref,
        definition=runtime_definition,
        plan_sha256=plan_sha256,
        agent_configs=workflow_configs,
        resolved_agent_configs=configs,
    )
    prompts = _prompt_registry_versions()
    runtime = _runtime_version_snapshot()
    planning_context = {
        "cluster_child_settings": cluster_settings,
        "async_decision_report_supersession_enabled": bool(async_supersession),
        "contract_agent_settings": {"enabled": specialist_flags["contract_validation"]},
        "log_intelligence_settings": {"enabled": specialist_flags["log_intelligence"]},
        "regression_watchman_settings": {"enabled": specialist_flags["regression_watchman"]},
        "change_ownership_settings": {"enabled": specialist_flags["change_ownership"]},
        "defect_commander_settings": {"enabled": specialist_flags["defect_commander"]},
        "run_budget": run_budget,
    }
    return {
        str(step.get("id") or ""): step_execution_authority(
            definition=runtime_definition,
            step_id=str(step.get("id") or ""),
            resolved_agent_configs=configs,
            endpoint_authority_fingerprints=endpoint_fingerprints,
            prompt_versions=prompts,
            runtime_versions=runtime,
            workflow_plan_sha256=plan_sha256,
            workflow_runtime_authority_sha256=runtime_authority,
            planning_context=planning_context,
        )
        for step in steps
    }


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
    from app.services.agent_config_service import lock_agent_config_authority

    await lock_agent_config_authority(db, project_id)
    lock_key = replay_corpus_lock_key(project_id, row.base)
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": lock_key},
    )
    candidate_authority = await _candidate_step_authority(
        db, project_id=project_id, row=row
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
        candidate_step_authority=candidate_authority,
        sample_limit=sample_limit,
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
    "PUBLISH_REPLAY_RUNS",
    "prompt_version_for",
    "replay_input_hash",
    "replay_corpus_lock_key",
    "step_execution_authority",
]
