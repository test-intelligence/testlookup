"""Versioned, adapter-backed contracts for the unified agent runtime.

These contracts do not replace the existing pipeline tables.  They provide a
stable read model over the current LangGraph pipeline while later Phase 3
slices move scheduling and cluster investigations behind the same contract.
"""
from __future__ import annotations

from datetime import datetime
from types import MappingProxyType
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


class RuntimeContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw(item) for item in value]
    return value


class CapabilitySpecV1(RuntimeContract):
    schema_version: Literal[1] = 1
    capability_id: str
    stage_name: str
    input_schema: str
    output_schema: str
    required_evidence: tuple[str, ...] = ()
    permission: Literal["read_only", "propose_action", "mutating"] = "read_only"
    dependencies: tuple[str, ...] = ()
    expected_latency_ms: int = Field(ge=0)
    expected_cost_usd: float = Field(ge=0)
    timeout_seconds: int = Field(gt=0)
    max_retries: int = Field(ge=0)
    fallback: str
    concurrency_class: str = "default"
    # How this capability actually gets executed. "planned" means a workflow
    # type lists it in its stage order; the other three are real executors that
    # a stage list will never contain. Declaring it is what stops a capability
    # from *reading* like a pipeline stage that nothing runs -- defect_commander
    # sat here with permission="mutating" and dependencies=("root_cause_analysis",)
    # and had never produced a single agent_stage_results row.
    execution: Literal["planned", "child_spawned", "on_demand", "runtime"] = "planned"


class TaskBudgetV1(RuntimeContract):
    max_llm_calls: int | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, ge=0)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_seconds: int | None = Field(default=None, ge=0)
    max_retries: int | None = Field(default=None, ge=0)


class TaskUsageV1(RuntimeContract):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _token_total_matches(self) -> "TaskUsageV1":
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
        return self


class AgentFindingV1(RuntimeContract):
    schema_version: Literal[1] = 1
    finding_id: str
    task_id: str
    kind: str
    statement: str
    classification: Literal["fact", "inference", "unknown", "recommendation"]
    confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_basis: str | None = None
    evidence_ids: tuple[str, ...] = ()
    counter_evidence_ids: tuple[str, ...] = ()


class AgentEvidenceV1(RuntimeContract):
    schema_version: Literal[1] = 1
    evidence_id: str = Field(min_length=1, max_length=160)
    task_id: str = Field(min_length=1, max_length=240)
    kind: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2_000)
    classification: Literal["internal", "restricted"] = "internal"

    @field_validator("kind", "label", "description", mode="before")
    @classmethod
    def _sanitize_text(cls, value: Any, info: Any) -> str:
        from app.services.evidence_sanitizer import sanitize_reference_text

        limits = {"kind": 80, "label": 200, "description": 2_000}
        return sanitize_reference_text(str(value or ""), limit=limits[info.field_name])[0]


class AgentTaskV1(RuntimeContract):
    schema_version: Literal[1] = 1
    task_id: str
    parent_task_id: str | None = None
    source_pipeline_run_id: str | None = None
    failure_cluster_id: str | None = Field(default=None, max_length=200)
    capability_id: str
    stage_name: str
    status: Literal["pending", "running", "completed", "failed", "skipped", "cancelled", "partial"]
    selected: bool
    required: bool = False
    selection_reason: str
    dependencies: tuple[str, ...] = ()
    attempt: int = Field(default=1, ge=1)
    budget: TaskBudgetV1 = Field(default_factory=TaskBudgetV1)
    usage: TaskUsageV1 = Field(default_factory=TaskUsageV1)
    stop_reason: Literal[
        "completed", "policy_skip", "not_selected", "budget_exhausted",
        "timeout", "capability_error", "cancelled", "partial",
    ] | None = None
    enrichment_stop_reason: Literal[
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
    ] | None = None
    evidence_ids: tuple[str, ...] = ()
    finding_ids: tuple[str, ...] = ()
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _validate_lifecycle(self) -> "AgentTaskV1":
        if self.started_at and self.completed_at and self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if self.status in {"completed", "failed", "skipped", "cancelled", "partial"} and not self.stop_reason:
            raise ValueError("terminal task status requires stop_reason")
        return self


class TerminalOutcomeV1(RuntimeContract):
    # E7.5: the closed internal vocabulary (migration 0173). ``partial`` and
    # ``cancelled`` are retired; ``retry_wait`` and ``passed`` were missing, so
    # the projection had been reporting both as ``failed``.
    status: Literal["pending", "running", "retry_wait", "completed", "passed", "failed"]
    error: str | None = None
    workflow_verification: Mapping[str, Any] | None = None
    decision_report_verification: Mapping[str, Any] | None = None
    budget_exhausted: bool = False
    budget_stop_reasons: tuple[str, ...] = ()

    @field_validator("workflow_verification", "decision_report_verification")
    @classmethod
    def _freeze_mapping(cls, value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        return _deep_freeze(value) if value is not None else None

    @field_serializer("workflow_verification", "decision_report_verification")
    def _serialize_mapping(self, value: Mapping[str, Any] | None) -> dict | None:
        return _deep_thaw(value) if value is not None else None


class AgenticRunV1(RuntimeContract):
    schema_version: Literal[1] = 1
    agentic_run_id: str
    source_pipeline_run_id: str
    test_run_id: str
    workflow_type: str
    status: Literal["pending", "running", "retry_wait", "completed", "passed", "failed"]
    # E7.5: the four-value projection clients should branch on.
    public_status: Literal["in_progress", "completed", "failed", "passed"]
    root_task_id: str
    plan_id: str
    plan_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    plan_integrity_status: Literal["verified", "failed", "legacy_unhashed"]
    planner_version: str
    run_budget: TaskBudgetV1 = Field(default_factory=TaskBudgetV1)
    tasks: tuple[AgentTaskV1, ...] = ()
    findings: tuple[AgentFindingV1, ...] = ()
    evidence: tuple[AgentEvidenceV1, ...] = ()
    child_agentic_run_ids: tuple[str, ...] = ()
    observed_child_runs: int = Field(default=0, ge=0)
    children_truncated: bool = False
    selected_capability_ids: tuple[str, ...] = ()
    skipped_capability_ids: tuple[str, ...] = ()
    observed_task_rows: int = Field(default=0, ge=0)
    tasks_truncated: bool = False
    terminal_outcome: TerminalOutcomeV1
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_run(self) -> "AgenticRunV1":
        task_ids = {task.task_id for task in self.tasks}
        if len(task_ids) != len(self.tasks):
            raise ValueError("task_id values must be unique")
        if self.root_task_id not in task_ids:
            raise ValueError("root_task_id must identify a materialized task")
        dangling = [task.task_id for task in self.tasks if task.parent_task_id and task.parent_task_id not in task_ids]
        if dangling:
            raise ValueError(f"task parent links must resolve: {dangling}")
        dangling_findings = [finding.finding_id for finding in self.findings if finding.task_id not in task_ids]
        if dangling_findings:
            raise ValueError(f"finding task links must resolve: {dangling_findings}")
        dangling_evidence = [item.evidence_id for item in self.evidence if item.task_id not in task_ids]
        if dangling_evidence:
            raise ValueError(f"evidence task links must resolve: {dangling_evidence}")
        if self.started_at and self.completed_at and self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        return self
