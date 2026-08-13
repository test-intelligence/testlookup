"""
Tool: Reconstruct distributed request traces from Splunk/ELK across multiple services.
Follows correlation IDs across microservices to build a causal chain.
"""
import logging

from langchain_core.tools import tool

from app.core.config import settings
from app.core.http_client import get_http_client

logger = logging.getLogger("tools.reconstruct_trace")


async def _query_splunk(spl: str, earliest: str = "-10m", latest: str = "now") -> list[dict]:
    if settings.AI_OFFLINE_MODE or not settings.SPLUNK_ENABLED or not settings.SPLUNK_BASE_URL:
        return []
    try:
        client = get_http_client()
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
            timeout=20.0,
        )
        if resp.status_code != 200:
            return []
        lines = [ln for ln in resp.text.strip().split("\n") if ln.strip()]
        import json
        return [json.loads(ln).get("result", {}) for ln in lines if ln]
    except Exception as exc:
        logger.debug("splunk_query_failed", error_type=type(exc).__name__)
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
    from datetime import datetime, timedelta

    try:
        params = json.loads(params_json)
    except (json.JSONDecodeError, AttributeError):
        return json.dumps({"error": "invalid_json"})

    correlation_id: str = params.get("correlation_id", "")
    timestamp_str: str = params.get("timestamp_utc", "")
    services: list[str] = params.get("services", [])
    window: int = int(params.get("window_seconds", 30))

    if settings.AI_OFFLINE_MODE or not settings.SPLUNK_ENABLED:
        return json.dumps({
            "trace_steps": [],
            "causal_summary": "Splunk integration is disabled. Enable SPLUNK_ENABLED to use distributed trace reconstruction.",
            "services_queried": [],
        })

    # Build time window
    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return json.dumps({"error": "invalid_timestamp"})

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
