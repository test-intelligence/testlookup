"""Versioned contracts for agent workflow outputs.

R1 introduces these contracts as a validation layer around the existing
LangGraph state updates. Agents still return workflow-compatible dicts, with
contract metadata added under ``agent_contracts`` for audit/replay consumers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, TypeVar

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger("models.agent_contracts")

AGENT_CONTRACT_SCHEMA_VERSION = 1


class AgentContractMetadata(BaseModel):
    schema_version: int = AGENT_CONTRACT_SCHEMA_VERSION
    agent_name: str
    agent_version: str = "v1"
    fallback_used: bool = False
    confidence: Optional[int] = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    decision_reason: str = ""
    output_keys: list[str] = Field(default_factory=list)
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ContractedAgentOutput(BaseModel):
    contract: AgentContractMetadata


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
) -> dict[str, Any]:
    """Validate an agent payload and append audit-friendly contract metadata."""
    metadata = AgentContractMetadata(
        agent_name=agent_name,
        agent_version=agent_version,
        fallback_used=fallback_used,
        confidence=confidence,
        evidence_refs=evidence_refs or [],
        decision_reason=decision_reason,
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
