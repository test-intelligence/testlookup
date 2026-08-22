"""
Pydantic v2 schemas for validating LLM outputs across all pipeline agents.

These models enforce structure on non-deterministic LLM responses. Every agent
that parses JSON from an LLM call should validate through one of these schemas.
On validation failure, agents fall back to deterministic alternatives — never crash.

Usage:
    from app.models.llm_schemas import IncidentView, validate_llm_output

    raw_dict, parse_error = parse_llm_json(raw_text, ...)
    validated = validate_llm_output(IncidentView, raw_dict, context="summary_layer2")
"""
from __future__ import annotations

import structlog
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = structlog.get_logger("models.llm_schemas")


# ── Summary Agent Layer Schemas ──────────────────────────────────────────────


class IncidentView(BaseModel):
    """Layer 2: Structured incident view from summary agent."""
    what_failed: str = ""
    likely_cause: str = ""
    scope: str = ""
    criticality: str = Field(default="MEDIUM")
    release_impact: str = Field(default="CONDITIONAL_GO")
    failure_breakdown: dict[str, int] = Field(default_factory=dict)
    # F-3: ids the model cites, resolved server-side against the evidence
    # catalogue and replaced by ``citations`` before the layer is stored.
    # Declared here because a field absent from the schema is STRIPPED by
    # validation -- the contract would be asked for and silently discarded.
    evidence_ids: list[str] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("criticality", mode="before")
    @classmethod
    def normalize_criticality(cls, v: Any) -> str:
        allowed = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
        val = str(v).strip().upper()
        return val if val in allowed else "MEDIUM"

    @field_validator("release_impact", mode="before")
    @classmethod
    def normalize_release_impact(cls, v: Any) -> str:
        allowed = {"GO", "CONDITIONAL_GO", "NO_GO"}
        val = str(v).strip().upper().replace(" ", "_")
        return val if val in allowed else "CONDITIONAL_GO"


class EvidencePack(BaseModel):
    """Layer 3: Evidence pack from summary agent."""
    top_stack_traces: list[str] = Field(default_factory=list)
    log_anomalies: list[str] = Field(default_factory=list)
    flaky_test_ids: list[str] = Field(default_factory=list)
    similar_historical_failures: list[str] = Field(default_factory=list)
    data_sources_used: list[str] = Field(default_factory=list)
    # F-3: ids the model cites, resolved server-side against the evidence
    # catalogue and replaced by ``citations`` before the layer is stored.
    # Declared here because a field absent from the schema is STRIPPED by
    # validation -- the contract would be asked for and silently discarded.
    evidence_ids: list[str] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)


class ActionPlan(BaseModel):
    """Layer 4: Action plan from summary agent."""
    immediate_mitigation: str = ""
    fix_recommendations: list[str] = Field(default_factory=list)
    validation_steps: list[str] = Field(default_factory=list)
    rollback_guidance: str = ""
    owner_hints: dict[str, str] = Field(default_factory=dict)
    # F-3: ids the model cites, resolved server-side against the evidence
    # catalogue and replaced by ``citations`` before the layer is stored.
    # Declared here because a field absent from the schema is STRIPPED by
    # validation -- the contract would be asked for and silently discarded.
    evidence_ids: list[str] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)


# ── Root-Cause Analysis Schema ──────────────────────────────────────────────


class RootCauseAnalysis(BaseModel):
    """Structured output from the ReAct root-cause triage agent."""
    root_cause_summary: str = ""
    failure_category: str = "UNKNOWN"
    backend_error_found: bool = False
    pod_issue_found: bool = False
    is_flaky: bool = False
    confidence_score: int = Field(default=0, ge=0, le=100)
    recommended_actions: list[str] = Field(default_factory=list)
    role_actions: dict[str, str] = Field(default_factory=dict)
    evidence_references: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("failure_category", mode="before")
    @classmethod
    def normalize_failure_category(cls, v: Any) -> str:
        allowed = {
            "PRODUCT_BUG",
            "INFRASTRUCTURE",
            "TEST_DATA",
            "AUTOMATION_DEFECT",
            "FLAKY",
            "UNKNOWN",
        }
        val = str(v or "UNKNOWN").strip().upper().replace(" ", "_")
        return val if val in allowed else "UNKNOWN"

    @field_validator("recommended_actions", mode="before")
    @classmethod
    def normalize_actions(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        return [str(item).strip() for item in v if str(item).strip()][:10]


# ── Executive Panel Schemas ────────────────────────────────────────────────


class ExecutivePanelMetrics(BaseModel):
    """Core run metrics for the executive summary panel."""
    build_number: str = ""
    branch: str = ""
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    pass_rate: float = 0.0
    duration_seconds: int | None = None
    failure_clusters: int = 0
    anomaly_count: int = 0


class DominantFailure(BaseModel):
    """Dominant failure category in the executive panel."""
    category: str = "UNKNOWN"
    count: int = 0
    percentage: float = 0.0


class BaselineComparison(BaseModel):
    """Baseline comparison metrics for the executive panel."""
    pass_rate_delta: float = 0.0
    new_failures: int = 0
    resolved: int = 0
    classification: str = "unclassified"


class ExecutivePanel(BaseModel):
    """Structured executive summary panel — deterministically generated."""
    headline: str = ""
    status_signal: str = Field(default="CONDITIONAL_GO")
    risk_score: int | None = None
    metrics: ExecutivePanelMetrics = Field(default_factory=ExecutivePanelMetrics)
    dominant_failure: DominantFailure | None = None
    key_takeaways: list[str] = Field(default_factory=list)
    baseline_comparison: BaselineComparison | None = None
    next_actions: list[str] = Field(default_factory=list)

    @field_validator("status_signal", mode="before")
    @classmethod
    def normalize_status_signal(cls, v: Any) -> str:
        allowed = {"GO", "CONDITIONAL_GO", "NO_GO"}
        val = str(v).strip().upper().replace(" ", "_")
        return val if val in allowed else "CONDITIONAL_GO"


# ── Regression Watchman Schemas ──────────────────────────────────────────────


class ClusterClassification(BaseModel):
    """Single cluster classification from regression watchman."""
    classification: str = "new_regression"
    confidence: int = Field(default=0, ge=0, le=100)
    evidence: str = ""

    @field_validator("classification", mode="before")
    @classmethod
    def normalize_classification(cls, v: Any) -> str:
        allowed = {"new_regression", "known_flaky_recurrence", "environmental_anomaly"}
        val = str(v).strip().lower()
        return val if val in allowed else "new_regression"


# ── Release Risk Agent Schemas ───────────────────────────────────────────────


class ReleaseReasoning(BaseModel):
    """LLM reasoning output from release risk agent."""
    reasoning: str = ""
    blocking_issues: list[str] = Field(default_factory=list)
    conditions_for_go: list[str] = Field(default_factory=list)


# ── Validation Utility ───────────────────────────────────────────────────────


def validate_llm_output(
    schema: type[BaseModel],
    raw_dict: dict[str, Any],
    context: str = "",
) -> dict[str, Any]:
    """Validate a parsed LLM dict against a Pydantic schema.

    On success: returns the validated (and normalized) dict.
    On failure: logs a warning and returns ``raw_dict`` with defaults filled in
    via ``schema(**{}).model_dump()`` merged over it. Never raises.
    """
    try:
        validated = schema.model_validate(raw_dict)
        return validated.model_dump()
    except Exception as exc:
        logger.warning(
            "LLM output validation failed — using defaults for missing fields",
            context=context,
            error=str(exc),
        )
        # Merge: raw dict takes precedence, but missing keys get defaults
        defaults = schema.model_construct().model_dump()
        merged = {**defaults, **{k: v for k, v in raw_dict.items() if v is not None}}
        return merged


def validate_llm_output_with_error(
    schema: type[BaseModel],
    raw_dict: dict[str, Any],
    context: str = "",
) -> tuple[dict[str, Any], str | None]:
    """Validate parsed LLM output and return a structured failure reason."""
    try:
        validated = schema.model_validate(raw_dict)
        return validated.model_dump(), None
    except Exception as exc:
        logger.warning(
            "LLM output validation failed — using defaults for missing fields",
            context=context,
            error=str(exc),
        )
        defaults = schema.model_construct().model_dump()
        merged = {**defaults, **{k: v for k, v in raw_dict.items() if v is not None}}
        return merged, str(exc)
