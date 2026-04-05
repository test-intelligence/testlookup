"""
Tool: Reconstruct distributed request traces from Splunk/ELK across multiple services.
Follows correlation IDs across microservices to build a causal chain.
"""
import logging

import httpx
from langchain_core.tools import tool

from app.core.config import settings

logger = logging.getLogger("tools.reconstruct_trace")


async def _query_splunk(spl: str, earliest: str = "-10m", latest: str = "now") -> list[dict]:
    if not settings.SPLUNK_ENABLED or not settings.SPLUNK_BASE_URL:
        return []
    try:
        async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
            search_url = f"{settings.SPLUNK_BASE_URL}/services/search/jobs/export"
            resp = await client.post(
                search_url,
                headers={"Authorization": f"Bearer {settings.SPLUNK_API_TOKEN}"},
                data={
                    "search": f"search {spl}",
                    "output_mode": "json",
                    "earliest_time": earliest,
                    "latest_time": latest,
                    "count": 50,
                },
            )
            if resp.status_code != 200:
                return []
            lines = [ln for ln in resp.text.strip().split("\n") if ln.strip()]
            import json
            return [json.loads(ln).get("result", {}) for ln in lines if ln]
    except Exception as exc:
        logger.debug("Splunk query failed: %s", exc)
        return []


@tool
async def reconstruct_distributed_trace(params_json: str) -> str:
    """
    Reconstruct a distributed request trace from logs across multiple services.

    Input JSON keys:
      - correlation_id: request/trace ID to follow (optional)
      - test_case_id: test case ID for timestamp lookup
      - timestamp_utc: ISO timestamp of test failure
      - services: list of service names to query (optional, queries all if omitted)
      - window_seconds: time window around failure (default 30)

    Returns: JSON with trace_steps (service, timestamp, level, message) and causal_summary.
    """
    import json
    from datetime import datetime, timedelta, timezone

    try:
        params = json.loads(params_json)
    except (json.JSONDecodeError, AttributeError) as exc:
        return json.dumps({"error": f"Invalid JSON: {exc}"})

    correlation_id: str = params.get("correlation_id", "")
    timestamp_str: str = params.get("timestamp_utc", "")
    services: list[str] = params.get("services", [])
    window: int = int(params.get("window_seconds", 30))

    if not settings.SPLUNK_ENABLED:
        return json.dumps({
            "trace_steps": [],
            "causal_summary": "Splunk integration is disabled. Enable SPLUNK_ENABLED to use distributed trace reconstruction.",
            "services_queried": [],
        })

    # Build time window
    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        ts = datetime.now(timezone.utc)

    earliest = (ts - timedelta(seconds=window)).strftime("%Y-%m-%dT%H:%M:%S")
    latest = (ts + timedelta(seconds=window)).strftime("%Y-%m-%dT%H:%M:%S")

    # Build SPL query
    service_filter = ""
    if services:
        service_filter = " OR ".join(f'service="{s}"' for s in services)
        service_filter = f"({service_filter}) "

    if correlation_id:
        spl = f'index={settings.SPLUNK_INDEX} {service_filter}("{correlation_id}") earliest="{earliest}" latest="{latest}" | sort _time | fields _time, service, level, message, host'
    else:
        spl = f'index={settings.SPLUNK_INDEX} {service_filter}(ERROR OR WARN OR Exception OR Timeout) earliest="{earliest}" latest="{latest}" | sort _time | fields _time, service, level, message, host'

    raw_events = await _query_splunk(spl, earliest=earliest, latest=latest)

    trace_steps = []
    for event in raw_events[:40]:
        trace_steps.append({
            "service": event.get("service", "unknown"),
            "timestamp": event.get("_time", ""),
            "level": event.get("level", "INFO"),
            "message": str(event.get("message", ""))[:300],
            "host": event.get("host", ""),
        })

    # Build a causal summary
    services_seen = list({s["service"] for s in trace_steps})
    error_steps = [s for s in trace_steps if s["level"] in ("ERROR", "FATAL")]
    if not trace_steps:
        causal_summary = "No log events found in the specified window. Check service names and time range."
    elif error_steps:
        first_error = error_steps[0]
        causal_summary = (
            f"First error appeared in service '{first_error['service']}' at {first_error['timestamp']}: "
            f"{first_error['message'][:200]}. "
            f"Total {len(error_steps)} error events across {len(services_seen)} services."
        )
    else:
        causal_summary = f"No ERROR-level events found. {len(trace_steps)} WARN events across {len(services_seen)} services."

    return json.dumps({
        "trace_steps": trace_steps,
        "causal_summary": causal_summary,
        "services_queried": services_seen,
        "correlation_id": correlation_id,
        "time_window": {"earliest": earliest, "latest": latest},
    })
