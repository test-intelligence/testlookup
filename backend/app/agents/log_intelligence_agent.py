"""
Log Intelligence Agent.
Runs distributed trace reconstruction and log rate anomaly detection for a failure cluster.
Operates as a specialist sub-agent called by DeepRootCauseAgent.
"""
import json

import structlog

from app.tools.detect_log_anomaly import detect_log_rate_anomaly
from app.tools.reconstruct_trace import reconstruct_distributed_trace

logger = structlog.get_logger("agents.log_intelligence")


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

        # 1. Reconstruct distributed trace
        try:
            trace_json = await reconstruct_distributed_trace.ainvoke({
                "params_json": json.dumps({
                    "correlation_id": correlation_id,
                    "timestamp_utc": timestamp_utc,
                    "services": related_services or [service_name],
                    "window_seconds": 45,
                })
            })
            trace_data = json.loads(trace_json)
            evidence["distributed_trace"] = trace_data
        except Exception as exc:
            logger.debug("trace_reconstruction_failed", error=str(exc))
            evidence["distributed_trace"] = {"error": str(exc)}

        # 2. Detect log rate anomaly for primary service
        try:
            anomaly_json = await detect_log_rate_anomaly.ainvoke({
                "params_json": json.dumps({
                    "service_name": service_name,
                    "timestamp_utc": timestamp_utc,
                    "window_minutes": 10,
                    "baseline_days": 7,
                })
            })
            anomaly_data = json.loads(anomaly_json)
            evidence["log_anomaly"] = anomaly_data
        except Exception as exc:
            logger.debug("log_anomaly_detection_failed", error=str(exc))
            evidence["log_anomaly"] = {"error": str(exc)}

        # Build a summary for the calling agent
        trace_summary = evidence.get("distributed_trace", {}).get("causal_summary", "Trace unavailable.")
        anomaly_assessment = evidence.get("log_anomaly", {}).get("assessment", "Anomaly check unavailable.")
        evidence["log_summary"] = f"Trace: {trace_summary} | Anomaly: {anomaly_assessment}"

        return evidence
