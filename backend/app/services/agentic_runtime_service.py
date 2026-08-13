"""Adapters from current pipeline persistence into AgenticRunV1."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Sequence

from app.models.agentic_runtime import (
    AgentEvidenceV1,
    AgentFindingV1,
    AgentTaskV1,
    AgenticRunV1,
    TaskBudgetV1,
    TaskUsageV1,
)
from app.services.agent_capability_registry import get_capability
from app.services.agent_planner import workflow_plan_integrity_status
from app.services.evidence_sanitizer import sanitize_reference_text

MAX_RUNTIME_TASKS = 200
MAX_RUNTIME_CHILDREN = 20
MAX_RECURSIVE_TASKS = 1_000
MAX_RUNTIME_FINDINGS = 100
MAX_RUNTIME_EVIDENCE = 200
_STAGE_ALIASES = {"analysis": "root_cause_analysis", "anomaly": "anomaly_detection"}
_RUN_STATUSES = {"pending", "running", "completed", "failed", "partial", "cancelled"}
_TASK_STATUSES = _RUN_STATUSES | {"skipped"}


def _status(value: Any, *, task: bool = False) -> str:
    normalized = str(value or "").lower()
    allowed = _TASK_STATUSES if task else _RUN_STATUSES
    return normalized if normalized in allowed else "failed"


def _duration_ms(started_at: Any, completed_at: Any) -> int:
    if not started_at or not completed_at:
        return 0
    try:
        return max(0, int((completed_at - started_at).total_seconds() * 1000))
    except (TypeError, ValueError, OverflowError):
        return 0


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _nonnegative_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _optional_nonnegative_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_error(value: Any) -> str | None:
    if not value:
        return None
    return sanitize_reference_text(str(value), limit=2_000)[0]


def _safe_text(value: Any, *, limit: int, fallback: str = "") -> str:
    if value is None:
        return fallback
    sanitized = sanitize_reference_text(str(value), limit=limit)[0].strip()
    return sanitized or fallback


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = json.dumps([str(part) for part in parts], separators=(",", ":"), ensure_ascii=True)
    return f"{prefix}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}"


def _scoped_task_key(root_task_id: str, value: Any) -> str:
    key = _safe_text(value, limit=180)
    if not key:
        return ""
    if key == root_task_id or key.startswith("pipeline:"):
        return key
    return f"{root_task_id}:task:{key}"


def _stop_reason(status: str, *, error: str | None, skipped_reason: str | None) -> str | None:
    if status == "completed":
        return "completed"
    if status == "skipped":
        return "policy_skip" if skipped_reason else "not_selected"
    if status == "failed":
        lowered = (error or "").lower()
        if "budget" in lowered:
            return "budget_exhausted"
        if "timeout" in lowered:
            return "timeout"
        return "capability_error"
    if status == "cancelled":
        return "cancelled"
    if status == "partial":
        return "partial"
    return None


def _task_budget(value: Any, *, fallback_retries: int | None = None) -> TaskBudgetV1:
    data = value if isinstance(value, dict) else {}
    return TaskBudgetV1(
        max_llm_calls=_optional_nonnegative_int(data.get("max_llm_calls")),
        max_tokens=_optional_nonnegative_int(data.get("max_tokens")),
        max_cost_usd=_optional_nonnegative_float(data.get("max_cost_usd")),
        max_seconds=_optional_nonnegative_int(data.get("max_seconds")),
        max_retries=_optional_nonnegative_int(
            data.get("max_retries") if "max_retries" in data else fallback_retries
        ),
    )


def _capability_for_stage(stage_name: Any):
    normalized = _STAGE_ALIASES.get(str(stage_name), str(stage_name))
    try:
        return get_capability(normalized), normalized
    except ValueError:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", normalized)[:80] or "unknown"
        return None, safe


def _load_plan(pipeline: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    stored = metadata.get("initial_workflow_plan") or metadata.get("workflow_plan")
    if isinstance(stored, dict) and isinstance(stored.get("stages"), list):
        return stored
    if stored is None:
        return {"schema_version": 1, "planner_version": "legacy", "stages": []}
    return {
        "schema_version": 2,
        "planner_version": "malformed",
        "stages": [],
        "malformed_source": True,
    }


def build_agentic_run_projection(pipeline: Any, stages: Iterable[Any]) -> AgenticRunV1:
    metadata = dict(pipeline.execution_metadata or {})
    plan = _load_plan(pipeline, metadata)
    integrity_status, actual_plan_hash = workflow_plan_integrity_status(plan)
    planned = {
        item.get("stage"): item
        for item in plan.get("stages", [])
        if isinstance(item, dict) and item.get("stage")
    }
    root_task_id = f"pipeline:{pipeline.id}"
    pipeline_error = _safe_error(pipeline.error)
    pipeline_status = _status(pipeline.status)
    tasks: list[AgentTaskV1] = [AgentTaskV1(
        task_id=root_task_id,
        source_pipeline_run_id=str(pipeline.id),
        capability_id="runtime.pipeline.v1",
        stage_name="pipeline",
        status=pipeline_status,
        selected=True,
        required=True,
        selection_reason="root task for the persisted pipeline execution",
        usage=TaskUsageV1(duration_ms=_duration_ms(pipeline.started_at, pipeline.completed_at)),
        stop_reason=_stop_reason(pipeline_status, error=pipeline_error, skipped_reason=None),
        started_at=pipeline.started_at,
        completed_at=pipeline.completed_at,
        error=pipeline_error,
    )]

    attempts: dict[str, int] = {}
    seen_task_ids = {root_task_id}
    stage_rows = list(stages)
    for stage in stage_rows[:MAX_RUNTIME_TASKS]:
        spec, normalized_stage = _capability_for_stage(stage.stage_name)
        plan_item = planned.get(normalized_stage, {})
        stage_status = _status(stage.status, task=True)
        attempts[normalized_stage] = attempts.get(normalized_stage, 0) + 1
        durable_attempt = _optional_nonnegative_int(getattr(stage, "attempt", None))
        attempt = durable_attempt if durable_attempt and durable_attempt >= 1 else attempts[normalized_stage]
        legacy_task_id = f"{root_task_id}:stage:{normalized_stage}:attempt:{attempt}"
        task_id = _scoped_task_key(root_task_id, getattr(stage, "task_key", None)) or legacy_task_id
        if task_id in seen_task_ids:
            task_id = f"{legacy_task_id}:duplicate:{attempts[normalized_stage]}"
        seen_task_ids.add(task_id)
        durable_parent = _scoped_task_key(
            root_task_id, getattr(stage, "parent_task_key", None)
        )
        parent_task_id = durable_parent or root_task_id
        if not durable_parent and normalized_stage.startswith("hypothesis_"):
            parent_task_id = f"{root_task_id}:stage:investigator_plan:attempt:1"
        durable_capability = _safe_text(getattr(stage, "capability_id", None), limit=160)
        capability_id = durable_capability or (spec.capability_id if spec else f"legacy.{normalized_stage}.v1")
        durable_dependencies = getattr(stage, "dependencies", None)
        dependencies = tuple(
            str(value)[:160] for value in (
                durable_dependencies
                if isinstance(durable_dependencies, (list, tuple))
                else plan_item.get("dependencies") or (spec.dependencies if spec else ())
            )
        )
        durable_selected = getattr(stage, "selected", None)
        selected = bool(
            durable_selected
            if isinstance(durable_selected, bool)
            else plan_item.get("planned", stage_status != "skipped")
        )
        durable_required = getattr(stage, "required", None)
        required = bool(
            durable_required
            if isinstance(durable_required, bool)
            else plan_item.get("required", False)
        )
        allocated_budget = getattr(stage, "allocated_budget", None)
        budget_data = (
            allocated_budget
            if isinstance(allocated_budget, dict)
            else plan_item.get("budget") if isinstance(plan_item.get("budget"), dict) else {}
        )
        stage_error = _safe_error(stage.error)
        raw_result_data = getattr(stage, "result_data", None)
        result_data = raw_result_data if isinstance(raw_result_data, dict) else {}
        durable_stop_reason = getattr(stage, "stop_reason", None)
        enrichment_stop_reason = durable_stop_reason or result_data.get("stop_reason")
        if enrichment_stop_reason not in {
            "cancelled",
            "llm_call_budget_exhausted",
            "token_budget_exhausted",
            "wall_clock_budget_exhausted",
            "investigation_not_found",
            "budget_reservation_failed",
            "duplicate_reservation",
            "budget_ledger_invalid",
            "budget_reservation_identity_mismatch",
            "budget_settlement_failed",
        "budget_settlement_pending",
            "budget_overrun",
            "reservation_lease_expired",
            "cost_budget_exhausted",
            "cluster_child_join_timeout",
            "cluster_child_cancelled",
            "cluster_child_failed",
            "cluster_child_dispatch_exhausted",
            "project_child_capacity_exhausted",
        }:
            enrichment_stop_reason = None
        input_tokens = _nonnegative_int(stage.input_tokens)
        output_tokens = _nonnegative_int(stage.output_tokens)
        tasks.append(AgentTaskV1(
            task_id=task_id,
            parent_task_id=parent_task_id,
            source_pipeline_run_id=str(pipeline.id),
            failure_cluster_id=_safe_text(
                getattr(stage, "failure_cluster_id", None), limit=200
            ) or None,
            capability_id=capability_id,
            stage_name=normalized_stage,
            status=stage_status,
            selected=selected,
            required=required,
            selection_reason=str(plan_item.get("rationale") or stage.route_rationale or "legacy pipeline stage"),
            dependencies=dependencies,
            attempt=attempt,
            budget=_task_budget(
                budget_data, fallback_retries=spec.max_retries if spec else None
            ),
            usage=TaskUsageV1(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                llm_calls=_nonnegative_int(stage.llm_calls_count),
                cost_usd=_nonnegative_float(stage.cost_usd),
                duration_ms=_duration_ms(stage.started_at, stage.completed_at),
            ),
            stop_reason=_stop_reason(stage_status, error=stage_error, skipped_reason=stage.skipped_reason),
            enrichment_stop_reason=enrichment_stop_reason,
            started_at=stage.started_at,
            completed_at=stage.completed_at,
            error=stage_error,
        ))

    materialized_ids = {task.task_id for task in tasks}
    tasks = [
        task if not task.parent_task_id or task.parent_task_id in materialized_ids
        else task.model_copy(update={"parent_task_id": root_task_id})
        for task in tasks
    ]

    selected_ids = tuple(dict.fromkeys(task.capability_id for task in tasks[1:] if task.selected))
    skipped_ids = tuple(dict.fromkeys(task.capability_id for task in tasks[1:] if not task.selected))
    budget_spend = metadata.get("budget_spend")
    if not isinstance(budget_spend, dict):
        budget_spend = {}
    budget_stop_reasons = tuple(
        str(item)[:100] for item in budget_spend.get("budget_stop_reasons") or []
    )[:20]
    return AgenticRunV1(
        agentic_run_id=f"agentic:{pipeline.id}",
        source_pipeline_run_id=str(pipeline.id),
        test_run_id=str(pipeline.test_run_id),
        workflow_type=pipeline.workflow_type,
        status=pipeline_status,
        root_task_id=root_task_id,
        plan_id=str(plan.get("plan_id") or f"legacy:{pipeline.id}"),
        plan_sha256=actual_plan_hash,
        plan_integrity_status=integrity_status,
        planner_version=str(plan.get("planner_version") or "legacy"),
        run_budget=_task_budget(metadata.get("run_budget")),
        tasks=tuple(tasks),
        selected_capability_ids=selected_ids,
        skipped_capability_ids=skipped_ids,
        observed_task_rows=len(stage_rows),
        tasks_truncated=len(stage_rows) > MAX_RUNTIME_TASKS,
        terminal_outcome={
            "status": pipeline_status,
            "error": pipeline_error,
            "workflow_verification": metadata.get("workflow_verification"),
            "decision_report_verification": metadata.get("decision_report_verification"),
            "budget_exhausted": bool(budget_spend.get("budget_exhausted"))
            or "cost_budget_exhausted" in budget_stop_reasons,
            "budget_stop_reasons": budget_stop_reasons,
        },
        started_at=pipeline.started_at,
        completed_at=pipeline.completed_at,
    )


def _finding_confidence(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed > 1:
        parsed /= 100
    return min(1.0, max(0.0, parsed))


def _child_findings_and_evidence(
    investigation: Any,
    tasks: Sequence[AgentTaskV1],
) -> tuple[list[AgentFindingV1], list[AgentEvidenceV1], dict[str, tuple[list[str], list[str]]]]:
    by_stage = {task.stage_name: task for task in tasks}
    root = tasks[0]
    links: dict[str, tuple[list[str], list[str]]] = {}
    findings: list[AgentFindingV1] = []
    evidence_items: list[AgentEvidenceV1] = []
    investigation_id = str(getattr(investigation, "id", "unknown"))

    for index, raw in enumerate(getattr(investigation, "hypotheses", None) or []):
        if not isinstance(raw, dict):
            continue
        hypothesis_id = _safe_text(raw.get("id"), limit=80, fallback=f"unknown-{index}")
        task = by_stage.get(f"hypothesis_{hypothesis_id}", root)
        finding_id = _stable_id("finding", investigation_id, "hypothesis", hypothesis_id)
        evidence_ids: list[str] = []
        for evidence_index, raw_evidence in enumerate(raw.get("evidence") or []):
            if len(evidence_items) >= MAX_RUNTIME_EVIDENCE:
                break
            if isinstance(raw_evidence, dict):
                kind = _safe_text(raw_evidence.get("type") or raw_evidence.get("kind"), limit=80, fallback="observation")
                label = _safe_text(raw_evidence.get("label") or raw_evidence.get("id"), limit=200, fallback=f"Evidence {evidence_index + 1}")
                description_raw = raw_evidence.get("description") or raw_evidence.get("statement") or raw_evidence.get("value") or raw_evidence
            else:
                kind = "observation"
                label = f"Evidence {evidence_index + 1}"
                description_raw = raw_evidence
            description, changed, _ = sanitize_reference_text(str(description_raw), limit=2_000)
            evidence_id = _stable_id(
                "evidence", investigation_id, hypothesis_id, evidence_index, kind, label, description
            )
            evidence_items.append(AgentEvidenceV1(
                evidence_id=evidence_id,
                task_id=task.task_id,
                kind=kind,
                label=label,
                description=description,
                classification="restricted" if changed else "internal",
            ))
            evidence_ids.append(evidence_id)
        statement = _safe_text(
            raw.get("summary") or raw.get("title"),
            limit=2_000,
            fallback=f"Hypothesis {hypothesis_id} produced no summary.",
        )
        findings.append(AgentFindingV1(
            finding_id=finding_id,
            task_id=task.task_id,
            kind=f"hypothesis.{hypothesis_id}",
            statement=statement,
            classification="inference",
            confidence=_finding_confidence(raw.get("confidence")),
            confidence_basis=_safe_text(raw.get("confidence_basis"), limit=200) or None,
            evidence_ids=tuple(evidence_ids),
        ))
        task_finding_ids, task_evidence_ids = links.setdefault(task.task_id, ([], []))
        task_finding_ids.append(finding_id)
        task_evidence_ids.extend(evidence_ids)

    verdict = getattr(investigation, "verdict", None)
    if isinstance(verdict, dict):
        task = by_stage.get("investigator_synthesis", root)
        finding_id = _stable_id("finding", investigation_id, "verdict")
        primary = _safe_text(verdict.get("primary_cause"), limit=200, fallback="unknown")
        narrative = _safe_text(
            verdict.get("narrative"), limit=2_000, fallback=f"Primary cause: {primary}."
        )
        findings.append(AgentFindingV1(
            finding_id=finding_id,
            task_id=task.task_id,
            kind="investigation.verdict",
            statement=narrative,
            classification="inference",
            confidence=_finding_confidence(verdict.get("confidence")),
            confidence_basis="investigator_synthesis",
        ))
        links.setdefault(task.task_id, ([], []))[0].append(finding_id)
        for action_index, action in enumerate(verdict.get("recommended_actions") or []):
            if len(findings) >= MAX_RUNTIME_FINDINGS:
                break
            action_id = _stable_id("finding", investigation_id, "action", action_index, action)
            findings.append(AgentFindingV1(
                finding_id=action_id,
                task_id=task.task_id,
                kind="recommended_action",
                statement=_safe_text(action, limit=2_000, fallback="Review investigation output."),
                classification="recommendation",
                confidence=_finding_confidence(verdict.get("confidence")),
                confidence_basis="investigator_synthesis",
            ))
            links[task.task_id][0].append(action_id)

    return findings[:MAX_RUNTIME_FINDINGS], evidence_items[:MAX_RUNTIME_EVIDENCE], links


def build_recursive_agentic_run_projection(
    parent_pipeline: Any,
    parent_stages: Iterable[Any],
    children: Iterable[tuple[Any, Iterable[Any], Any]],
) -> AgenticRunV1:
    """Project one authorized parent pipeline and bounded Investigator children."""
    parent = build_agentic_run_projection(parent_pipeline, parent_stages)
    supplied_children = [
        (pipeline, list(stages), investigation)
        for pipeline, stages, investigation in children
    ]
    eligible = [
        child for child in supplied_children
        if str(getattr(child[0], "test_run_id", "")) == str(parent.test_run_id)
        and str(getattr(child[2], "run_id", parent.test_run_id)) == str(parent.test_run_id)
    ]
    selected_children = eligible[:MAX_RUNTIME_CHILDREN]
    tasks = list(parent.tasks)
    findings = list(parent.findings)
    evidence_items = list(parent.evidence)
    task_ids = {task.task_id for task in tasks}
    child_run_ids: list[str] = []
    tasks_truncated = parent.tasks_truncated

    for child_pipeline, child_stages, investigation in selected_children:
        child = build_agentic_run_projection(child_pipeline, child_stages)
        desired_parent = _safe_text(
            getattr(investigation, "parent_task_id", None), limit=240
        ) or parent.root_task_id
        if desired_parent not in task_ids:
            desired_parent = parent.root_task_id
        cluster_id = _safe_text(
            getattr(investigation, "failure_cluster_id", None)
            or getattr(investigation, "cluster_id", None),
            limit=200,
        ) or None
        child_tasks: list[AgentTaskV1] = []
        for child_task in child.tasks:
            if child_task.task_id in task_ids:
                continue
            parent_task_id = desired_parent if child_task.task_id == child.root_task_id else child_task.parent_task_id
            child_tasks.append(child_task.model_copy(update={
                "parent_task_id": parent_task_id,
                "source_pipeline_run_id": str(child_pipeline.id),
                "failure_cluster_id": child_task.failure_cluster_id or cluster_id,
            }))
        remaining = max(0, MAX_RECURSIVE_TASKS - len(tasks))
        included_child_tasks = child_tasks[:remaining]
        if len(included_child_tasks) < len(child_tasks):
            tasks_truncated = True
        included_ids = {task.task_id for task in included_child_tasks}
        included_child_tasks = [
            task if not task.parent_task_id or task.parent_task_id in task_ids | included_ids
            else task.model_copy(update={"parent_task_id": desired_parent})
            for task in included_child_tasks
        ]
        if child.root_task_id not in included_ids:
            tasks_truncated = True
            continue
        child_run_ids.append(child.agentic_run_id)
        child_findings, child_evidence, links = _child_findings_and_evidence(
            investigation, included_child_tasks
        )
        included_child_tasks = [
            task.model_copy(update={
                "finding_ids": tuple(links.get(task.task_id, ([], []))[0]),
                "evidence_ids": tuple(links.get(task.task_id, ([], []))[1]),
            })
            for task in included_child_tasks
        ]
        tasks.extend(included_child_tasks)
        task_ids.update(task.task_id for task in included_child_tasks)
        finding_room = max(0, MAX_RUNTIME_FINDINGS - len(findings))
        evidence_room = max(0, MAX_RUNTIME_EVIDENCE - len(evidence_items))
        findings.extend(child_findings[:finding_room])
        evidence_items.extend(child_evidence[:evidence_room])

    materialized_finding_ids = {finding.finding_id for finding in findings}
    materialized_evidence_ids = {item.evidence_id for item in evidence_items}
    tasks = [task.model_copy(update={
        "finding_ids": tuple(
            value for value in task.finding_ids if value in materialized_finding_ids
        ),
        "evidence_ids": tuple(
            value for value in task.evidence_ids if value in materialized_evidence_ids
        ),
    }) for task in tasks]
    return parent.model_copy(update={
        "tasks": tuple(tasks),
        "findings": tuple(findings),
        "evidence": tuple(evidence_items),
        "child_agentic_run_ids": tuple(child_run_ids),
        "observed_child_runs": len(supplied_children),
        "children_truncated": len(eligible) > MAX_RUNTIME_CHILDREN,
        "observed_task_rows": parent.observed_task_rows + sum(
            len(child_stages) for _, child_stages, _ in selected_children
        ),
        "tasks_truncated": tasks_truncated,
    })
