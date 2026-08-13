"""
Log Intelligence Agent.
Runs distributed trace reconstruction and log rate anomaly detection for a failure cluster.
Operates as a specialist sub-agent called by DeepRootCauseAgent.
"""
import json

import structlog

from app.agents.evidence import EvidenceRef
from app.models.agent_contracts import (
    LogIntelligenceAgentOutput,
    validate_agent_contract,
)
from app.tools.detect_log_anomaly import detect_log_rate_anomaly
from app.tools.reconstruct_trace import reconstruct_distributed_trace
from app.services.evidence_sanitizer import (
    sanitize_persistence_payload,
    sanitize_reference_text,
)

logger = structlog.get_logger("agents.log_intelligence")


def _grade_anomaly_evidence(anomaly_data: dict) -> tuple[str, int]:
    """Map a log-rate-anomaly result to (strength, contribution) from the ACTUAL
    spike magnitude, not a fixed ``medium``/80.

    A *detected* spike is graded by its ratio (current/baseline); a "no anomaly"
    result is WEAK evidence — the absence of a log-rate spike rules a cause out,
    it is not medium-strength support for one. Grading these identically was
    cosmetic: it made the evidence-weighting machinery inert. Degrades to weak
    when the fields are absent (e.g. Splunk disabled)."""
    if not anomaly_data.get("anomaly_detected"):
        return "weak", 15
    levels = anomaly_data.get("levels") or {}
    max_ratio = max(
        (float(v.get("ratio", 0) or 0) for v in levels.values() if isinstance(v, dict)),
        default=0.0,
    )
    if max_ratio >= 6.0:
        return "strong", 85
    return "medium", 60


def _grade_trace_evidence(trace_data: dict) -> tuple[str, int]:
    """Map a reconstructed trace to (strength, contribution) by whether it
    surfaced an actual error chain.

    An error-bearing trace (ERROR/FATAL steps) is real causal evidence and
    scales with the number of error events; a trace with only context steps (no
    errors) is weak, and an empty trace weaker still. Previously every trace was
    emitted at a fixed ``medium``/80 regardless of whether it found anything."""
    steps = trace_data.get("trace_steps") or []
    error_steps = [
        s for s in steps
        if isinstance(s, dict) and s.get("level") in ("ERROR", "FATAL")
    ]
    if error_steps:
        return ("strong" if len(error_steps) >= 3 else "medium"), min(90, 60 + len(error_steps) * 10)
    if steps:
        return "weak", 20  # context only — no error chain
    return "weak", 10      # empty trace


class LogIntelligenceAgent:
    """
    Stateless specialist for log-based evidence gathering.
    Called per cluster with context about the failing service and timestamp.
    """

    async def investigate(
        self,
        service_name: str,
        timestamp_utc: str,
        correlation_id: str = "",
        related_services: list[str] | None = None,
    ) -> dict:
        """
        Run trace reconstruction + log anomaly detection for a service/timestamp pair.
        Returns aggregated log evidence dict.
        """
        evidence = {}

        safe_service, _, _ = sanitize_reference_text(service_name, limit=200)
        safe_timestamp, _, _ = sanitize_reference_text(timestamp_utc, limit=80)
        safe_correlation, _, _ = sanitize_reference_text(correlation_id, limit=200)
        safe_related = []
        for item in related_services or [service_name]:
            safe_item, _, _ = sanitize_reference_text(item, limit=200)
            safe_related.append(safe_item)

        # 1. Reconstruct distributed trace
        try:
            trace_json = await reconstruct_distributed_trace.ainvoke({
                "params_json": json.dumps({
                    "correlation_id": safe_correlation,
                    "timestamp_utc": safe_timestamp,
                    "services": safe_related,
                    "window_seconds": 45,
                })
            })
            trace_data = json.loads(trace_json)
            evidence["distributed_trace"] = sanitize_persistence_payload(trace_data)[0]
        except Exception as exc:
            logger.debug("trace_reconstruction_failed", error_type=type(exc).__name__)
            evidence["distributed_trace"] = {"error": type(exc).__name__}

        # 2. Detect log rate anomaly for primary service
        try:
            anomaly_json = await detect_log_rate_anomaly.ainvoke({
                "params_json": json.dumps({
                    "service_name": safe_service,
                    "timestamp_utc": safe_timestamp,
                    "window_minutes": 10,
                    "baseline_days": 7,
                })
            })
            anomaly_data = json.loads(anomaly_json)
            evidence["log_anomaly"] = sanitize_persistence_payload(anomaly_data)[0]
        except Exception as exc:
            logger.debug("log_anomaly_detection_failed", error_type=type(exc).__name__)
            evidence["log_anomaly"] = {"error": type(exc).__name__}

        # Build a summary for the calling agent
        trace_summary = evidence.get("distributed_trace", {}).get("causal_summary", "Trace unavailable.")
        anomaly_assessment = evidence.get("log_anomaly", {}).get("assessment", "Anomaly check unavailable.")
        safe_trace_summary, _, _ = sanitize_reference_text(trace_summary, limit=500)
        safe_anomaly_assessment, _, _ = sanitize_reference_text(anomaly_assessment, limit=500)
        evidence["log_summary"] = (
            f"Trace: {safe_trace_summary} | Anomaly: {safe_anomaly_assessment}"
        )

        trace_ok = "error" not in evidence.get("distributed_trace", {})
        anomaly_ok = "error" not in evidence.get("log_anomaly", {})
        structured_evidence: list[EvidenceRef] = []
        if trace_ok:
            trace_strength, trace_contribution = _grade_trace_evidence(
                evidence.get("distributed_trace", {})
            )
            structured_evidence.append(EvidenceRef(
                source="distributed_trace",
                ref_id=safe_service,
                excerpt=safe_trace_summary,
                strength=trace_strength,
                contribution=trace_contribution,
            ))
        if anomaly_ok:
            anomaly_strength, anomaly_contribution = _grade_anomaly_evidence(
                evidence.get("log_anomaly", {})
            )
            structured_evidence.append(EvidenceRef(
                source="log_anomaly",
                ref_id=safe_service,
                excerpt=safe_anomaly_assessment,
                strength=anomaly_strength,
                contribution=anomaly_contribution,
            ))
        fallback_used = not (trace_ok and anomaly_ok)
        evidence["status"] = "complete" if not fallback_used else "failed"
        return validate_agent_contract(
            LogIntelligenceAgentOutput, evidence, agent_name="log_intelligence",
            agent_version="v1", fallback_used=fallback_used,
            structured_evidence=structured_evidence,
            decision_reason="log_evidence_gathered" if not fallback_used else "partial_log_evidence",
        )
