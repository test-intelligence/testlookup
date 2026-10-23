"""
Tool: Reconstruct distributed request traces from Splunk/ELK across multiple services.
Follows correlation IDs across microservices to build a causal chain.

W10 INT-01 / INT-03: the Splunk base URL, token and index come from the bound
runtime integration config (``investigation_context.runtime_integrations()``,
bound by the log stage), else THIS module's ``settings``. The tool checks the
index and the egress host once before its search; every service name and the
correlation id are sanitized before they enter SPL.
"""
import logging
from typing import Optional

from langchain_core.tools import tool

from app.core.config import settings
from app.core.http_client import get_http_client
from app.core.integration_values import TARGET_NOT_ALLOWED, valid_splunk_index
from app.services.input_sanitizer import sanitize_query_param, sanitize_service_name
from app.services.integration_config_service import IntegrationRuntimeConfig
from app.services.url_safety import egress_block_reason
from app.tools.investigation_context import runtime_integrations

logger = logging.getLogger("tools.reconstruct_trace")

INVALID_INDEX_SUMMARY = "Splunk index is not valid — cannot reconstruct the distributed trace."
NOT_ALLOWED_SUMMARY = f"Splunk lookup refused: {TARGET_NOT_ALLOWED}."


async def _query_splunk(
    spl: str,
    earliest: str = "-10m",
    latest: str = "now",
    cfg: Optional[IntegrationRuntimeConfig] = None,
) -> list[dict]:
    """One export search. ``cfg`` is the config the tool already gated (index,
    egress host); without one (a direct caller) the environment of this
    module's settings is used and the host is checked here."""
    gated = cfg is not None
    if cfg is None:
        cfg = IntegrationRuntimeConfig.from_settings(settings)
    if settings.AI_OFFLINE_MODE or not cfg.splunk_enabled or not cfg.splunk_base_url:
        return []
    if not gated and await egress_block_reason(cfg.splunk_base_url) is not None:
        return []
    try:
        client = get_http_client()
        search_url = f"{cfg.splunk_base_url.rstrip('/')}/services/search/jobs/export"
        headers = {"Authorization": f"Bearer {cfg.splunk_api_token}"} if cfg.splunk_api_token else {}
        resp = await client.post(
            search_url,
            headers=headers,
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
        logger.debug("Splunk query failed: %s", type(exc).__name__)
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

    cfg = runtime_integrations() or IntegrationRuntimeConfig.from_settings(settings)
    if settings.AI_OFFLINE_MODE or not cfg.splunk_enabled:
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

    # W10: gate once before the search: an index that is not SPL-safe, or a
    # host the egress guard refuses, sends nothing.
    if not valid_splunk_index(cfg.splunk_index):
        return json.dumps({"trace_steps": [], "causal_summary": INVALID_INDEX_SUMMARY, "services_queried": []})
    if cfg.splunk_base_url:
        block = await egress_block_reason(cfg.splunk_base_url)
        if block is not None:
            logger.info("Splunk trace lookup refused before sending the token: %s", block)
            return json.dumps({"trace_steps": [], "causal_summary": NOT_ALLOWED_SUMMARY, "services_queried": []})

    earliest = (ts - timedelta(seconds=window)).strftime("%Y-%m-%dT%H:%M:%S")
    latest = (ts + timedelta(seconds=window)).strftime("%Y-%m-%dT%H:%M:%S")

    # Build SPL query. Every value that enters SPL is sanitized first (the
    # services and the correlation id reached it raw before W10).
    safe_services = [
        name for name in (sanitize_service_name(str(item)) for item in services if item is not None) if name
    ] if isinstance(services, list) else []
    safe_correlation = sanitize_query_param(str(correlation_id or ""))
    service_filter = ""
    if safe_services:
        service_filter = " OR ".join(f'service="{s}"' for s in safe_services)
        service_filter = f"({service_filter}) "

    if safe_correlation:
        spl = f'index={cfg.splunk_index} {service_filter}("{safe_correlation}") earliest="{earliest}" latest="{latest}" | sort _time | fields _time, service, level, message, host'
    else:
        spl = f'index={cfg.splunk_index} {service_filter}(ERROR OR WARN OR Exception OR Timeout) earliest="{earliest}" latest="{latest}" | sort _time | fields _time, service, level, message, host'

    raw_events = await _query_splunk(spl, earliest=earliest, latest=latest, cfg=cfg)

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
