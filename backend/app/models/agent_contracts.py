"""Versioned contracts for agent workflow outputs.

R1 introduces these contracts as a validation layer around the existing
LangGraph state updates. Agents still return workflow-compatible dicts, with
contract metadata added under ``agent_contracts`` for audit/replay consumers.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional, TypeVar

import structlog
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

logger = structlog.get_logger("models.agent_contracts")

AGENT_CONTRACT_SCHEMA_VERSION = 1

# Max detail length carried on a gap/contradiction item (chars). Structural
# tokens only — never raw error/log text.
_ITEM_DETAIL_MAX = 240


class AgentContractMetadata(BaseModel):
    schema_version: int = AGENT_CONTRACT_SCHEMA_VERSION
    agent_name: str
    agent_version: str = "v1"
    fallback_used: bool = False
    confidence: Optional[int] = None
    confidence_score: int = 0
    evidence_count: int = 0
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    decision_reason: str = ""
    confidence_breakdown: Optional[dict[str, Any]] = None
    output_keys: list[str] = Field(default_factory=list)
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @field_validator("confidence_score")
    @classmethod
    def _clamp_confidence_score(cls, v: int) -> int:
        return max(0, min(100, int(v)))



class DecisionClaimV1(BaseModel):
    """Typed, evidence-aware claim surfaced by the terminal decision report."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=160)
    kind: Literal["fact", "inference", "unknown", "recommendation"]
    text: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_basis: str = Field(min_length=1, max_length=500)
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    counter_evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    source_stage: str = Field(min_length=1, max_length=80)
    freshness: str | None = Field(default=None, max_length=80)
    hypothesis: bool = False


class ProposedActionV1(BaseModel):
    """Approval-aware action proposal; never an execution receipt."""

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    owner: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=1, max_length=500)
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    risk: Literal["low", "medium", "high", "unknown"] = "unknown"
    required_permission: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=1, max_length=160)
    status: Literal["proposed"] = "proposed"
class ContractedAgentOutput(BaseModel):
    contract: AgentContractMetadata


class ReviewReferenceSetV1(BaseModel):
    """Identifiers the reviewed output is allowed to cite."""

    model_config = ConfigDict(extra="forbid")

    test_case_ids: list[str] = Field(default_factory=list)
    cluster_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)


class ReviewedStepV1(BaseModel):
    """One completed step and the policy context needed to review it."""

    model_config = ConfigDict(extra="forbid")

    step_name: str = Field(min_length=1, max_length=80)
    output: dict[str, Any]
    mode: Literal["shadow", "suggest", "act"] = "shadow"
    tools_used: list[str] = Field(default_factory=list)
    tool_permissions: dict[str, Literal["read_only", "propose_action", "mutating"]] = Field(
        default_factory=dict
    )
    model_tier: Optional[Literal["deterministic", "slm", "llm"]] = None
    model_provider: Optional[str] = Field(default=None, min_length=1, max_length=80)
    model_name: Optional[str] = Field(default=None, min_length=1, max_length=160)


class ReviewerInputV1(BaseModel):
    """Deterministic evidence supplied to the generic reviewer."""

    model_config = ConfigDict(extra="forbid")

    reviewed_steps: list[ReviewedStepV1] = Field(min_length=1, max_length=20)
    references: ReviewReferenceSetV1 = Field(default_factory=ReviewReferenceSetV1)
    numeric_facts: dict[str, float] = Field(default_factory=dict)
    numeric_tolerance: float = Field(default=0.01, ge=0.0, le=1.0)
    run_data: dict[str, Any] = Field(default_factory=dict)
    analyses: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _step_names_are_unique(self) -> "ReviewerInputV1":
        names = [step.step_name for step in self.reviewed_steps]
        if len(names) != len(set(names)):
            raise ValueError("reviewed step names must be unique")
        if any(not math.isfinite(value) for value in self.numeric_facts.values()):
            raise ValueError("numeric facts must be finite")
        return self


class ReviewCheckV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: Literal[1, 2, 3, 4, 5]
    name: str = Field(min_length=1, max_length=120)
    passed: bool
    severity: Literal["info", "warning", "blocking"]
    detail: str = Field(default="", max_length=1_000)
    step_name: Optional[str] = Field(default=None, max_length=80)
    offending_refs: list[str] = Field(default_factory=list, max_length=100)


class ReviewDisagreementV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=120)
    agents: list[str] = Field(min_length=1, max_length=20)
    resolution: str = Field(min_length=1, max_length=500)
    severity: Literal["warning", "blocking"] = "warning"


class SecondModelCheckV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)
    agreement_score: float = Field(ge=0.0, le=1.0)


class ReviewVerdictV1(BaseModel):
    """Fail-closed result consumed by the workflow supervisor."""

    model_config = ConfigDict(extra="forbid")

    reviewed_steps: list[str] = Field(min_length=1, max_length=20)
    checks: list[ReviewCheckV1]
    verdict: Literal["pass", "pass_with_flags", "retry", "reject"]
    disagreements: list[ReviewDisagreementV1] = Field(default_factory=list)
    hallucination_risk: Literal["low", "medium", "high"]
    requires_human_review: bool
    second_model: Optional[SecondModelCheckV1] = None

    @model_validator(mode="after")
    def _verdict_is_consistent(self) -> "ReviewVerdictV1":
        if len(self.reviewed_steps) != len(set(self.reviewed_steps)):
            raise ValueError("reviewed step names must be unique")
        if self.verdict in {"pass", "pass_with_flags"}:
            for step_name in self.reviewed_steps:
                families = {
                    check.family for check in self.checks if check.step_name == step_name
                }
                if not {1, 2, 5}.issubset(families):
                    raise ValueError(
                        "a continuing verdict requires check families 1, 2, and 5 "
                        "for every reviewed step"
                    )
        if self.verdict == "pass":
            if any(not check.passed for check in self.checks):
                raise ValueError("pass requires every deterministic check to pass")
        if self.verdict in {"pass", "pass_with_flags"} and any(
            not check.passed
            and check.severity == "blocking"
            and check.family in {1, 2, 5}
            for check in self.checks
        ):
            raise ValueError(
                "pass_with_flags cannot continue after a blocking deterministic check fails"
            )
        if self.verdict in {"pass", "pass_with_flags"} and any(
            item.severity == "blocking" for item in self.disagreements
        ):
            raise ValueError("a continuing verdict is incompatible with a blocking disagreement")
        if self.verdict in {"pass", "pass_with_flags"} and self.hallucination_risk == "high":
            raise ValueError("a continuing verdict is incompatible with high hallucination risk")
        if (
            self.verdict == "pass"
            and self.second_model is not None
            and self.second_model.agreement_score < 0.7
        ):
            raise ValueError("pass requires second-model agreement of at least 0.7")
        if self.verdict != "pass" and not self.requires_human_review:
            raise ValueError("a non-pass verdict requires human review")
        return self


class ReviewerSupervisorDecisionV1(BaseModel):
    """Deterministic routing instruction emitted for the workflow compiler."""

    model_config = ConfigDict(extra="forbid")

    route: Literal["continue", "retry", "finalize"]
    reviewed_steps: list[str] = Field(min_length=1, max_length=20)
    tier_override: Optional[Literal["llm"]] = None
    status: Optional[Literal["failed"]] = None
    error_code: Optional[Literal["validation_failed"]] = None
    requires_human_review: bool = False
    retry_count: int = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def _route_fields_are_consistent(self) -> "ReviewerSupervisorDecisionV1":
        if self.route == "retry":
            if self.tier_override != "llm" or self.retry_count != 1:
                raise ValueError("retry requires tier_override=llm and retry_count=1")
            if self.status is not None or self.error_code is not None:
                raise ValueError("retry cannot carry a terminal status")
        elif self.route == "finalize":
            if self.status != "failed" or self.error_code != "validation_failed":
                raise ValueError("finalize requires failed/validation_failed")
            if self.tier_override is not None:
                raise ValueError("finalize cannot carry a tier override")
        elif any(value is not None for value in (self.tier_override, self.status, self.error_code)):
            raise ValueError("continue cannot carry retry or terminal fields")
        return self


class ReviewerAgentOutput(ContractedAgentOutput):
    """Workflow-state envelope for a contracted reviewer verdict."""

    review_verdict: ReviewVerdictV1
    supervisor: ReviewerSupervisorDecisionV1
    step_llm_budget: dict[str, Any]


# ── AIQ-P4 shared item/enum models ────────────────────────────────────────────
# Defined here (import-light module) and imported into the gap_detection /
# report_refinement agents to avoid a models -> agents import cycle. All
# coercion lives in ``field_validator(mode="before")`` so a malformed field
# degrades to a default rather than crashing.


class GapReason(str, Enum):
    UNANALYZED = "unanalyzed"
    ERRORED = "errored"
    INCONCLUSIVE = "inconclusive"
    NO_EVIDENCE = "no_evidence"
    LOW_CONFIDENCE = "low_confidence"


class GapItem(BaseModel):
    """One coverage/quality gap for a single failed test. Structural only."""

    model_config = ConfigDict(extra="ignore")

    test_id: str = ""
    reason: GapReason = GapReason.UNANALYZED
    detail: str = ""
    bucket: Literal["analyzed", "skipped", "errored"] = "skipped"

    @field_validator("test_id", mode="before")
    @classmethod
    def _coerce_test_id(cls, value) -> str:
        if value is None:
            return ""
        return str(value)

    @field_validator("reason", mode="before")
    @classmethod
    def _coerce_reason(cls, value):
        if isinstance(value, GapReason):
            return value
        raw = getattr(value, "value", value)
        try:
            lowered = str(raw).lower()
        except Exception:
            return GapReason.UNANALYZED
        try:
            return GapReason(lowered)
        except ValueError:
            return GapReason.UNANALYZED

    @field_validator("detail", mode="before")
    @classmethod
    def _truncate_detail(cls, value) -> str:
        if value is None:
            return ""
        text = str(value)
        return text[:_ITEM_DETAIL_MAX]


class ContradictionType(str, Enum):
    FLAKY_VS_REGRESSION = "flaky_vs_regression"
    CATEGORY_DISAGREEMENT = "category_disagreement"
    CONFIDENCE_SPLIT = "confidence_split"


class ResolutionStrategy(str, Enum):
    PREFER_ANALYSIS = "prefer_analysis"
    PREFER_ANOMALY = "prefer_anomaly"
    MERGE = "merge"
    FLAG_FOR_REVIEW = "flag_for_review"


class Contradiction(BaseModel):
    """A cross-route disagreement for a single multi-route test. Structural only."""

    model_config = ConfigDict(extra="ignore")

    test_id: str = ""
    type: ContradictionType = ContradictionType.CATEGORY_DISAGREEMENT
    routes: list[str] = Field(default_factory=list)
    resolution: ResolutionStrategy = ResolutionStrategy.FLAG_FOR_REVIEW
    detail: str = ""
    #: Whether the reconciliation actually ACTED on ``resolution``, rather than
    #: merely labelling the contradiction with it.
    #:
    #: ``contradictions_resolved`` used to be derived from ``resolution !=
    #: FLAG_FOR_REVIEW``, i.e. from the label. The agent only ever assigns
    #: PREFER_ANOMALY or MERGE, so that count was structurally equal to the
    #: number of contradictions found: it could never report an unresolved one,
    #: and ``unresolved_count`` was always 0. Counting the label also could not
    #: notice that the label and the behaviour disagreed -- and they did.
    #: Deriving the counts from this flag means a strategy added without an
    #: application branch shows up as unresolved instead of silently claiming
    #: success.
    applied: bool = False

    @field_validator("test_id", mode="before")
    @classmethod
    def _coerce_test_id(cls, value) -> str:
        if value is None:
            return ""
        return str(value)

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type(cls, value):
        if isinstance(value, ContradictionType):
            return value
        raw = getattr(value, "value", value)
        try:
            lowered = str(raw).lower()
        except Exception:
            return ContradictionType.CATEGORY_DISAGREEMENT
        try:
            return ContradictionType(lowered)
        except ValueError:
            return ContradictionType.CATEGORY_DISAGREEMENT

    @field_validator("resolution", mode="before")
    @classmethod
    def _coerce_resolution(cls, value):
        if isinstance(value, ResolutionStrategy):
            return value
        raw = getattr(value, "value", value)
        try:
            lowered = str(raw).lower()
        except Exception:
            return ResolutionStrategy.FLAG_FOR_REVIEW
        try:
            return ResolutionStrategy(lowered)
        except ValueError:
            return ResolutionStrategy.FLAG_FOR_REVIEW

    @field_validator("routes", mode="before")
    @classmethod
    def _coerce_routes(cls, value) -> list[str]:
        if not isinstance(value, (list, tuple, set)):
            return []
        allowed = {"analysis", "anomaly", "cluster"}
        return [str(r) for r in value if str(r) in allowed]

    @field_validator("detail", mode="before")
    @classmethod
    def _truncate_detail(cls, value) -> str:
        if value is None:
            return ""
        text = str(value)
        return text[:_ITEM_DETAIL_MAX]


class IngestionAgentOutput(ContractedAgentOutput):
    test_run_data: Optional[dict[str, Any]] = None
    branch: Optional[str] = None
    failed_test_ids: list[str] = Field(default_factory=list)
    # F-8: hash of the authoritative counts + failed-test set as ingestion saw
    # them. MUST be declared here -- an undeclared key is silently stripped by
    # the contract validator, which is how a previous field reached no consumer
    # at all while every test stayed green.
    run_input_fingerprint: Optional[str] = None
    total_tests: int = 0
    pass_rate: float = 0.0
    ingestion_enriched: bool = False
    completed_stages: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    current_stage: str = "anomaly_detection"


class ClusterAgentOutput(ContractedAgentOutput):
    failure_clusters: list[dict[str, Any]] = Field(default_factory=list)
    cluster_map: dict[str, str] = Field(default_factory=dict)


class ContractAgentOutput(ContractedAgentOutput):
    """Bounded API-contract specialist result (AIQ-P4)."""

    status: Literal["complete", "not_enough_evidence", "failed"] = "not_enough_evidence"
    violations: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    violation_count: int = Field(default=0, ge=0)
    critical_count: int = Field(default=0, ge=0)
    drift_count: int = Field(default=0, ge=0)
    endpoints_checked: list[str] = Field(default_factory=list, max_length=200)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    summary: str = Field(default="", max_length=2000)
    suggests_product_bug: bool = False


class AnomalyDetectionAgentOutput(ContractedAgentOutput):
    anomalies: list[dict[str, Any]] = Field(default_factory=list)
    is_regression: bool = False
    regression_tests: list[str] = Field(default_factory=list)
    anomaly_summary: Optional[str] = None
    completed_stages: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    current_stage: str = "root_cause_analysis"


class AnalysisAgentOutput(ContractedAgentOutput):
    analyses: dict[str, dict[str, Any]] = Field(default_factory=dict)
    completed_stages: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    stage_errors: dict[str, list[str]] = Field(default_factory=dict)
    stage_quality: Optional[str] = None
    low_confidence_count: int = 0
    current_stage: str = "summary"


class SummaryAgentOutput(ContractedAgentOutput):
    executive_summary: Optional[str] = None
    summary_markdown: Optional[str] = None
    structured_summary: Optional[dict[str, Any]] = None
    summary_provenance: Optional[dict[str, Any]] = None
    completed_stages: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    current_stage: str = "triage"


class DecisionReportAgentOutput(ContractedAgentOutput):
    structured_summary: Optional[dict[str, Any]] = None
    summary_markdown: Optional[str] = None
    decision_intelligence: Optional[dict[str, Any]] = None
    decision_evidence_snapshot: Optional[dict[str, Any]] = None
    completed_stages: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    current_stage: str = "decision_report_critic"


class DecisionReportCriticAgentOutput(ContractedAgentOutput):
    structured_summary: Optional[dict[str, Any]] = None
    summary_markdown: Optional[str] = None
    decision_intelligence: Optional[dict[str, Any]] = None
    decision_report_verification: Optional[dict[str, Any]] = None
    completed_stages: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    current_stage: str = "done"


class DefectTriageAgentOutput(ContractedAgentOutput):
    triage_results: list[dict[str, Any]] = Field(default_factory=list)
    completed_stages: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    current_stage: str = "done"


class FlakySentinelAgentOutput(ContractedAgentOutput):
    flaky_findings: list[dict[str, Any]] = Field(default_factory=list)


class TestHealthAgentOutput(ContractedAgentOutput):
    test_health_findings: list[dict[str, Any]] = Field(default_factory=list)


class ReleaseRiskAgentOutput(ContractedAgentOutput):
    release_decision: Optional[dict[str, Any]] = None


class RunCompareAgentOutput(ContractedAgentOutput):
    model_config = ConfigDict(extra="allow")
    status: str = "ready"
    executive_summary: Optional[str] = None
    risk_level: Optional[str] = None
    key_differences: list[str] = Field(default_factory=list)
    new_risks: list[str] = Field(default_factory=list)
    resolved_risks: list[str] = Field(default_factory=list)
    duration_concerns: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    confidence: int = 0
    confidence_reason: str = ""
    fallback_used: bool = False
    markdown_report: Optional[str] = None


class LogIntelligenceAgentOutput(ContractedAgentOutput):
    # extra='allow' for parity with RunCompareAgentOutput so an undeclared
    # nested payload key is preserved rather than silently dropped on validate.
    model_config = ConfigDict(extra="allow")

    distributed_trace: dict[str, Any] = Field(default_factory=dict)
    log_anomaly: dict[str, Any] = Field(default_factory=dict)
    log_summary: str = ""


class RegressionWatchmanAgentOutput(ContractedAgentOutput):
    # extra='allow' for parity with RunCompareAgentOutput so an undeclared
    # nested payload key is preserved rather than silently dropped on validate.
    model_config = ConfigDict(extra="allow")

    regression_classification: dict[str, Any] = Field(default_factory=dict)
    completed_stages: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    current_stage: str = "defect_commander"


class ChangeOwnershipAgentOutput(ContractedAgentOutput):
    """Deterministic baseline/change and ownership specialist result."""

    status: Literal["complete", "not_enough_evidence", "failed"] = "not_enough_evidence"
    baseline_diff: dict[str, Any] = Field(default_factory=dict)
    ownership_resolutions: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    summary: str = Field(default="", max_length=240)


class GapReport(BaseModel):
    """Nested coverage/integrity report matching the gap_detection payload."""

    model_config = ConfigDict(extra="ignore")

    failed_count: int = Field(default=0, ge=0)
    analyzed_count: int = Field(default=0, ge=0)
    skipped_count: int = Field(default=0, ge=0)
    errored_count: int = Field(default=0, ge=0)
    coverage_ratio: float = 0.0
    integrity_ok: bool = True
    gaps: list[GapItem] = Field(default_factory=list)
    inconclusive_count: int = Field(default=0, ge=0)
    no_evidence_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _recompute_invariants(self) -> "GapReport":
        # Defensive: recompute integrity + clamp coverage. MUST NOT raise.
        try:
            self.integrity_ok = (
                self.analyzed_count + self.skipped_count + self.errored_count
                == self.failed_count
            )
            ratio = float(self.coverage_ratio)
            self.coverage_ratio = max(0.0, min(1.0, ratio))
            if self.failed_count == 0:
                self.coverage_ratio = 1.0
        except Exception:  # pragma: no cover — invariant backstop
            pass
        return self


class GapDetectionAgentOutput(ContractedAgentOutput):
    gap_report: GapReport = Field(default_factory=GapReport)
    completed_stages: list[str] = Field(default_factory=list)
    current_stage: str = "report_refinement"


class RefinedReport(BaseModel):
    """Nested dedup/contradiction report matching the report_refinement payload."""

    model_config = ConfigDict(extra="ignore")

    dedup_count: int = Field(default=0, ge=0)
    contradictions_resolved: int = Field(default=0, ge=0)
    contradictions: list[Contradiction] = Field(default_factory=list)
    reconciled_tests: dict[str, dict[str, Any]] = Field(default_factory=dict)
    multi_route_test_ids: list[str] = Field(default_factory=list)
    unresolved_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _recompute_resolution_counts(self) -> "RefinedReport":
        # Defensive: derive resolved/unresolved from the contradiction list.
        # MUST NOT raise.
        try:
            resolved = sum(
                1
                for c in self.contradictions
                if c.applied and c.resolution != ResolutionStrategy.FLAG_FOR_REVIEW
            )
            unresolved = sum(
                1
                for c in self.contradictions
                if not (c.applied and c.resolution != ResolutionStrategy.FLAG_FOR_REVIEW)
            )
            self.contradictions_resolved = resolved
            self.unresolved_count = unresolved
        except Exception:  # pragma: no cover — invariant backstop
            pass
        return self


class ReportRefinementAgentOutput(ContractedAgentOutput):
    refined_report: RefinedReport = Field(default_factory=RefinedReport)
    completed_stages: list[str] = Field(default_factory=list)
    current_stage: str = "flaky_sentinel"


InvestigatorStopReason = Literal[
    "cancelled",
    "llm_call_budget_exhausted",
    "token_budget_exhausted",
    "cost_budget_exhausted",
    "wall_clock_budget_exhausted",
    "investigation_not_found",
    "duplicate_reservation",
    "budget_ledger_invalid",
    "budget_reservation_identity_mismatch",
    "budget_reservation_failed",
    "budget_settlement_failed",
    "budget_overrun",
    "reservation_lease_expired",
]


class InvestigatorHypothesisV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Literal["infra", "commit", "environment", "known_flaky", "regression"]
    title: str = Field(min_length=1, max_length=300)
    status: Literal["validated", "invalidated", "inconclusive", "pending"]
    confidence: int = Field(ge=0, le=100)
    confidence_basis: Literal["heuristic_estimate", "llm_weighted"]
    summary: str = Field(max_length=1000)
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    signals: dict[str, Any] = Field(default_factory=dict)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    llm_enrichment_stop_reason: Optional[InvestigatorStopReason] = None


class InvestigatorDegradationV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    degraded: bool = False
    budget_exhausted: bool = False
    hypotheses_with_stops: list[str] = Field(default_factory=list, max_length=5)
    synthesis_stop_reason: Optional[InvestigatorStopReason] = None


class InvestigatorVerdictV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_cause: Literal[
        "infra", "commit", "environment", "known_flaky", "regression", "unknown"
    ]
    narrative: str = Field(min_length=1, max_length=4000)
    confidence: int = Field(ge=0, le=100)
    recommended_actions: list[str] = Field(default_factory=list, max_length=10)
    degradation: InvestigatorDegradationV1 = Field(default_factory=InvestigatorDegradationV1)


class InvestigatorHypothesisOutput(ContractedAgentOutput):
    """One hypothesis sub-agent's verdict (Agentic plan AI-1).

    ``hypothesis`` carries the pinned wire shape persisted onto
    ``AgentInvestigation.hypotheses`` (id/title/status/confidence/
    confidence_basis/summary/evidence/started_at/completed_at).
    """

    model_config = ConfigDict(extra="ignore")

    hypothesis: InvestigatorHypothesisV1


class InvestigatorSynthesisOutput(ContractedAgentOutput):
    """The Investigator's synthesized verdict (Agentic plan AI-1)."""

    model_config = ConfigDict(extra="ignore")

    verdict: InvestigatorVerdictV1


TContract = TypeVar("TContract", bound=ContractedAgentOutput)


def validate_agent_contract(
    schema: type[TContract],
    payload: dict[str, Any],
    *,
    agent_name: str,
    agent_version: str = "v1",
    fallback_used: bool = False,
    confidence: Optional[int] = None,
    evidence_refs: Optional[list[dict[str, Any]]] = None,
    decision_reason: str = "",
    structured_evidence: Optional[list] = None,
) -> dict[str, Any]:
    """Validate an agent payload and append audit-friendly contract metadata.

    When ``structured_evidence`` (a list of ``EvidenceRef``) is supplied, the
    confidence is derived from ``aggregate_confidence`` and a
    ``confidence_breakdown`` is stamped onto the metadata. An explicit
    ``confidence`` kwarg still wins; if the caller passed no ``evidence_refs``,
    they are auto-populated from the structured evidence. Kept entirely inside
    the existing try/except so it never raises.
    """
    confidence_breakdown: Optional[dict[str, Any]] = None
    if structured_evidence:
        # Defense-in-depth: aggregate_confidence is contracted never-raise, but
        # keep the derivation guarded so this helper cannot raise even if a
        # future evidence change regresses that invariant.
        try:
            # Lazy import to avoid a models -> agents import cycle (evidence.py
            # lives under app/agents/ and imports from app/models/).
            from app.agents.evidence import aggregate_confidence

            final_confidence, confidence_breakdown = aggregate_confidence(structured_evidence)
            if confidence is None:
                confidence = final_confidence
            if not evidence_refs:
                evidence_refs = [
                    ref.as_legacy_dict()
                    for ref in structured_evidence
                    if hasattr(ref, "as_legacy_dict")
                ]
        except Exception as exc:  # pragma: no cover - invariant backstop
            logger.warning(
                "structured evidence aggregation failed; continuing without breakdown",
                agent_name=agent_name,
                error=str(exc),
            )
            confidence_breakdown = None

    metadata = AgentContractMetadata(
        agent_name=agent_name,
        agent_version=agent_version,
        fallback_used=fallback_used,
        confidence=confidence,
        confidence_score=max(0, min(100, int(confidence if confidence is not None else 0))),
        evidence_count=len(evidence_refs or []),
        evidence_refs=evidence_refs or [],
        decision_reason=decision_reason,
        confidence_breakdown=confidence_breakdown,
        output_keys=sorted(k for k in payload.keys() if k != "agent_contracts"),
    )
    try:
        validated = schema.model_validate({**payload, "contract": metadata})
    except Exception as exc:
        logger.warning(
            "agent contract validation failed; returning original payload",
            agent_name=agent_name,
            schema=schema.__name__,
            error=str(exc),
        )
        contracted = dict(payload)
        contracted.setdefault("agent_contracts", {})
        contracted["agent_contracts"][agent_name] = {
            **metadata.model_dump(mode="json"),
            "contract_validation_error": str(exc),
        }
        return contracted

    model_dump = validated.model_dump(mode="json")
    contract = model_dump.pop("contract")
    model_dump["agent_contracts"] = {
        **payload.get("agent_contracts", {}),
        agent_name: contract,
    }
    return model_dump


def degraded_contract(
    schema: type[TContract],
    payload: dict[str, Any],
    *,
    agent_name: str,
    decision_reason: str,
) -> dict[str, Any]:
    """Contract for a specialist that produced findings but had no evidence.

    Every early-return path in a specialist still writes its output key, and
    ``_check_contract_evidence_support`` flags any populated output carrying no
    contract -- so a stage that legitimately had nothing to look at has to SAY
    so rather than stay silent. An absent contract reads as a lost record, not
    as a negative result, and the decision critic fails closed on it.

    ``fallback_used`` and a ``no_*`` reason are what make the emptiness legible
    to the verifier's ``no_evidence_ok`` branch.

    Lives here rather than in ``app/agents/workflow.py`` because both the
    workflow nodes (flag-disabled branches) and the specialist agents
    themselves (no-evidence branches) need it, and the agents are imported *by*
    workflow -- putting it in workflow would be an import cycle.
    """
    return validate_agent_contract(
        schema,
        payload,
        agent_name=agent_name,
        fallback_used=True,
        confidence=0,
        evidence_refs=[],
        decision_reason=decision_reason,
    )
