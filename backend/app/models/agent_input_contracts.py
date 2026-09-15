"""Concrete, versioned input contracts published by the agent catalog.

The workflow still assembles these values from stored, tenant-scoped subjects.
These models make every registry label machine-readable without weakening that
authorization boundary.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.agent_contracts import AgentContractMetadata
from app.models.evidence_contracts import (
    EvidenceReferenceV2,
    RunEvidenceBundleV1,
    RunEvidenceBundleV2,
    RunMetricSnapshotV1,
    SHA256_PATTERN,
)


class CatalogInputContract(BaseModel):
    """Immutable, closed base for public catalog input schemas."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class TestRun(CatalogInputContract):
    """Stored test-run identity and lifecycle data consumed by ingestion."""

    schema_version: Literal[1] = 1
    id: uuid.UUID
    project_id: uuid.UUID
    build_number: str = Field(min_length=1, max_length=255)
    status: str = Field(min_length=1, max_length=50)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def _completion_follows_start(self) -> "TestRun":
        if (
            self.started_at
            and self.completed_at
            and self.completed_at < self.started_at
        ):
            raise ValueError("completed_at must not precede started_at")
        return self


class MetricSnapshotV1(RunMetricSnapshotV1):
    """Release-risk input, sharing the canonical run-metric contract."""


class AuthoritativeFailureClusterV1(CatalogInputContract):
    """Minimal database-authoritative cluster identity accepted by the planner."""

    schema_version: Literal[1] = 1
    failure_cluster_id: uuid.UUID
    cluster_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,20}$")
    member_test_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def _members_are_unique(self) -> "AuthoritativeFailureClusterV1":
        if len(set(self.member_test_ids)) != len(self.member_test_ids):
            raise ValueError("member_test_ids must be unique")
        return self


class ClusterBudgetV1(CatalogInputContract):
    max_llm_calls: int = Field(ge=0)
    max_tokens: int = Field(ge=0)
    max_cost_usd: float = Field(ge=0)
    max_seconds: int = Field(ge=0)
    max_retries: int = Field(default=0, ge=0)


class ClusterPlanLimitsV1(CatalogInputContract):
    max_children: int = Field(ge=0)
    max_members: int = Field(gt=0)


class ClusterScopeV1(CatalogInputContract):
    project_id: uuid.UUID
    run_id: uuid.UUID
    failure_cluster_id: uuid.UUID
    cluster_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,20}$")
    member_test_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=10_000)


class ClusterSlaV1(CatalogInputContract):
    expected_latency_ms: int = Field(ge=0)
    timeout_seconds: int = Field(ge=0)


class ClusterInvestigationTaskV1(CatalogInputContract):
    failure_cluster_id: uuid.UUID | None = None
    cluster_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,20}$")
    member_test_ids: tuple[uuid.UUID, ...] = Field(default=(), max_length=10_000)
    member_count: int = Field(ge=0)
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    cluster_scope: ClusterScopeV1 | None = None
    cluster_scope_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    spawn_key: str | None = Field(default=None, pattern=SHA256_PATTERN)
    task_key: str | None = Field(default=None, min_length=1)
    capability_id: Literal["agent.cluster_investigation.v1"]
    selected: bool
    skip_reason: str | None = None
    rationale: str = Field(min_length=1, max_length=500)
    dependencies: tuple[
        Literal["failure_clustering", "cluster_investigation_dispatch"], ...
    ]
    permission: Literal["read_only"]
    sla: ClusterSlaV1 | None = None
    budget: ClusterBudgetV1
    fallback: str = Field(min_length=1)
    concurrency_class: str = Field(min_length=1)

    @model_validator(mode="after")
    def _selection_fields_are_consistent(self) -> "ClusterInvestigationTaskV1":
        if self.member_count != len(self.member_test_ids):
            raise ValueError("member_count must match member_test_ids")
        selected_fields = (
            self.failure_cluster_id,
            self.cluster_id,
            self.cluster_scope,
            self.cluster_scope_sha256,
            self.spawn_key,
            self.task_key,
            self.sla,
        )
        if self.selected and (
            self.skip_reason is not None or any(v is None for v in selected_fields)
        ):
            raise ValueError(
                "selected tasks require scope, execution fields, and no skip_reason"
            )
        if not self.selected and self.skip_reason is None:
            raise ValueError("skipped tasks require skip_reason")
        return self


class ClusterInvestigationExpansionPlanV1(CatalogInputContract):
    schema_version: Literal[1] = 1
    planner_version: Literal["cluster-investigation-planner:v1"]
    parent_pipeline_run_id: uuid.UUID
    project_id: uuid.UUID
    run_id: uuid.UUID
    aggregate_budget: ClusterBudgetV1
    limits: ClusterPlanLimitsV1
    candidate_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    selected: tuple[ClusterInvestigationTaskV1, ...] = ()
    skipped: tuple[ClusterInvestigationTaskV1, ...] = ()
    tasks: tuple[ClusterInvestigationTaskV1, ...] = ()
    dependencies: tuple[
        Literal["failure_clustering", "cluster_investigation_dispatch"], ...
    ]
    fallback: Literal["continue_without_cluster_children"]
    expansion_id: str = Field(pattern=r"^cluster-plan:[0-9a-f]{20}$")
    expansion_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _counts_and_task_projection_match(
        self,
    ) -> "ClusterInvestigationExpansionPlanV1":
        if self.selected_count != len(self.selected) or self.skipped_count != len(
            self.skipped
        ):
            raise ValueError("plan counts must match selected and skipped tasks")
        if self.candidate_count != self.selected_count + self.skipped_count:
            raise ValueError(
                "candidate_count must equal selected_count plus skipped_count"
            )
        if self.tasks != self.selected + self.skipped:
            raise ValueError("tasks must be selected followed by skipped")
        if any(not task.selected for task in self.selected) or any(
            task.selected for task in self.skipped
        ):
            raise ValueError("task selection flags must match their plan collection")
        return self


class ClusterScopedEvidenceBundleV1(CatalogInputContract):
    """Verified evidence authority for one failure-cluster child."""

    schema_version: Literal[1] = 1
    project_id: uuid.UUID
    test_run_id: uuid.UUID
    pipeline_run_id: uuid.UUID
    parent_pipeline_run_id: uuid.UUID
    failure_cluster_id: uuid.UUID
    cluster_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,20}$")
    member_test_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=10_000)
    cluster_scope_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_refs: tuple[EvidenceReferenceV2, ...] = Field(default=(), max_length=500)

    @model_validator(mode="after")
    def _cluster_members_are_unique(self) -> "ClusterScopedEvidenceBundleV1":
        if len(set(self.member_test_ids)) != len(self.member_test_ids):
            raise ValueError("member_test_ids must be unique")
        return self


class PreliminarySummary(CatalogInputContract):
    """Summary-stage result consumed by deterministic gap detection."""

    contract: AgentContractMetadata
    executive_summary: str | None = None
    summary_markdown: str | None = None
    structured_summary: Mapping[str, Any] | None = None
    summary_provenance: Mapping[str, Any] | None = None
    completed_stages: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    current_stage: str = "triage"


class VerificationContextV3(CatalogInputContract):
    known_test_ids: tuple[str, ...] = ()
    completed_stages: tuple[str, ...] = ()
    skipped_stages: tuple[str, ...] = ()
    agent_contracts: Mapping[str, Any] = Field(default_factory=dict)


class DecisionEvidenceSnapshotV3(CatalogInputContract):
    """Signed immutable authority used to generate a decision report."""

    schema_version: Literal[3] = 3
    snapshot_id: str = Field(alias="_id", pattern=SHA256_PATTERN)
    test_run_id: uuid.UUID
    pipeline_run_id: uuid.UUID
    project_id: uuid.UUID
    canonical_decision: Mapping[str, Any]
    run_evidence_bundle: RunEvidenceBundleV1 | RunEvidenceBundleV2
    verification_context: VerificationContextV3
    release_policy_inputs: Mapping[str, Any]
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    signature_version: Literal[1]
    signature_key_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    signature_hmac_sha256: str = Field(pattern=SHA256_PATTERN)
    created_at: datetime


class InvestigationRequestV1(CatalogInputContract):
    schema_version: Literal[1] = 1
    investigation_id: uuid.UUID
    pipeline_run_id: uuid.UUID
    run_id: uuid.UUID
    project_id: uuid.UUID
    build_number: str = Field(min_length=1, max_length=255)
    mode: Literal["shadow", "suggest", "act"]
    triggered_by: str = Field(min_length=1, max_length=255)
    scope_type: Literal["run", "failure_cluster"] = "run"
    failure_cluster_id: uuid.UUID | None = None
    cluster_member_test_ids: tuple[uuid.UUID, ...] = Field(
        default=(), max_length=10_000
    )

    @model_validator(mode="after")
    def _scope_authority_is_complete(self) -> "InvestigationRequestV1":
        has_cluster = self.failure_cluster_id is not None
        has_members = bool(self.cluster_member_test_ids)
        if self.scope_type == "failure_cluster" and not (has_cluster and has_members):
            raise ValueError("failure-cluster scope requires a cluster and member IDs")
        if self.scope_type == "run" and (has_cluster or has_members):
            raise ValueError("run scope cannot carry failure-cluster authority")
        if len(set(self.cluster_member_test_ids)) != len(self.cluster_member_test_ids):
            raise ValueError("cluster_member_test_ids must be unique")
        return self


class InvestigationPlanV1(InvestigationRequestV1):
    """Bounded plan shared by the five investigator hypothesis agents."""

    budget: ClusterBudgetV1
    deadline_ts: float = Field(gt=0)
    bundle: Mapping[str, Any]
    cancelled: bool = False
