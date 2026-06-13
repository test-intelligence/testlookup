"""Versioned contracts for agent workflow outputs.

R1 introduces these contracts as a validation layer around the existing
LangGraph state updates. Agents still return workflow-compatible dicts, with
contract metadata added under ``agent_contracts`` for audit/replay consumers.
"""
from __future__ import annotations

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


class ContractedAgentOutput(BaseModel):
    contract: AgentContractMetadata


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
    total_tests: int = 0
    pass_rate: float = 0.0
    ingestion_enriched: bool = False
    completed_stages: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    current_stage: str = "anomaly_detection"


class ClusterAgentOutput(ContractedAgentOutput):
    failure_clusters: list[dict[str, Any]] = Field(default_factory=list)
    cluster_map: dict[str, str] = Field(default_factory=dict)


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
                if c.resolution != ResolutionStrategy.FLAG_FOR_REVIEW
            )
            unresolved = sum(
                1
                for c in self.contradictions
                if c.resolution == ResolutionStrategy.FLAG_FOR_REVIEW
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
