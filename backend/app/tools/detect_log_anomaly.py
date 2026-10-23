"""
Tool: Detect log rate anomalies by comparing error/warn rates against historical baseline.

W10 INT-01 / INT-03: the Splunk base URL, token and index come from the bound
runtime integration config (``investigation_context.runtime_integrations()``,
bound by the log stage), else THIS module's ``settings``. The tool gates once
per invocation (index validity, ``egress_block_reason``) before its
level x day loop of count searches.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from langchain_core.tools import tool

from app.core.config import settings
from app.core.http_client import get_http_client
from app.core.integration_values import TARGET_NOT_ALLOWED, valid_splunk_index
from app.services.input_sanitizer import sanitize_query_param, sanitize_service_name
from app.services.integration_config_service import IntegrationRuntimeConfig
from app.services.url_safety import egress_block_reason
from app.tools.investigation_context import runtime_integrations

logger = logging.getLogger("tools.detect_log_anomaly")

INVALID_INDEX_ASSESSMENT = "Splunk index is not valid — cannot measure log rate anomaly."
NOT_ALLOWED_ASSESSMENT = f"Splunk lookup refused: {TARGET_NOT_ALLOWED}."


async def _count_splunk_events(
    service: str,
    level: str,
    start: str,
    end: str,
    cfg: Optional[IntegrationRuntimeConfig] = None,
) -> int:
    """One ``| stats count`` search. ``cfg`` is the config the tool already
    gated (index, egress host); without one (a direct caller) the environment
    of this module's settings is used and the host is checked here."""
    gated = cfg is not None
    if cfg is None:
        cfg = IntegrationRuntimeConfig.from_settings(settings)
    if settings.AI_OFFLINE_MODE or not cfg.splunk_enabled or not cfg.splunk_base_url:
        return 0
    if not valid_splunk_index(cfg.splunk_index):
        return 0
    if not gated and await egress_block_reason(cfg.splunk_base_url) is not None:
        return 0
    # Phase 4: Sanitise query components
    service = sanitize_service_name(service)
    level = sanitize_query_param(level)
    try:
        spl = f'index={cfg.splunk_index} service="{service}" level="{level}" earliest="{start}" latest="{end}" | stats count'
        client = get_http_client()
        headers = {"Authorization": f"Bearer {cfg.splunk_api_token}"} if cfg.splunk_api_token else {}
        resp = await client.post(
            f"{cfg.splunk_base_url.rstrip('/')}/services/search/jobs/export",
            headers=headers,
            data={"search": f"search {spl}", "output_mode": "json", "count": 1},
            timeout=15.0,
        )
        if resp.status_code != 200:
            return 0
        import json
        for line in resp.text.strip().split("\n"):
            if line:
                result = json.loads(line).get("result", {})
                return int(result.get("count", 0))
    except Exception as exc:
        logger.debug("Splunk count query failed: %s", type(exc).__name__)
    return 0


@tool
async def detect_log_rate_anomaly(params_json: str) -> str:
    """
    Compare current error/warn rates for a service against its historical baseline.

    Input JSON keys:
      - service_name: name of the microservice
      - timestamp_utc: ISO timestamp of the test failure
      - window_minutes: measurement window (default 10)
      - baseline_days: days of history for baseline (default 7)
      - levels: list of log levels to check (default ["ERROR", "WARN"])

    Returns: JSON with current_count, baseline_avg, ratio, anomaly_detected, assessment.
    """
    import json

    try:
        params = json.loads(params_json)
    except (json.JSONDecodeError, AttributeError):
        return json.dumps({"error": "invalid_json"})

    service: str = sanitize_service_name(params.get("service_name", ""))
    timestamp_str: str = sanitize_query_param(params.get("timestamp_utc", ""))
    window_min: int = int(params.get("window_minutes", 10))
    baseline_days: int = int(params.get("baseline_days", 7))
    levels: list[str] = params.get("levels", ["ERROR", "WARN"])

    cfg = runtime_integrations() or IntegrationRuntimeConfig.from_settings(settings)
    if settings.AI_OFFLINE_MODE or not cfg.splunk_enabled:
        return json.dumps({
            "anomaly_detected": False,
            "assessment": "Splunk integration disabled — cannot measure log rate anomaly.",
        })

    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return json.dumps({"error": "invalid_timestamp"})

    # W10: gate ONCE per invocation, before the level x day loop (up to 16
    # searches): an index that is not SPL-safe, or a host the egress guard
    # refuses, sends nothing.
    if not valid_splunk_index(cfg.splunk_index):
        return json.dumps({"anomaly_detected": False, "assessment": INVALID_INDEX_ASSESSMENT})
    if cfg.splunk_base_url:
        block = await egress_block_reason(cfg.splunk_base_url)
        if block is not None:
            logger.info("Splunk anomaly lookup refused before sending the token: %s", block)
            return json.dumps({"anomaly_detected": False, "assessment": NOT_ALLOWED_ASSESSMENT})

    window_start = (ts - timedelta(minutes=window_min)).strftime("%Y-%m-%dT%H:%M:%S")
    window_end = ts.strftime("%Y-%m-%dT%H:%M:%S")

    results = {}
    for level in levels:
        current = await _count_splunk_events(service, level, window_start, window_end, cfg=cfg)
        # Baseline: average per window across last N days
        baseline_total = 0
        for day_offset in range(1, baseline_days + 1):
            base_end = (ts - timedelta(days=day_offset)).strftime("%Y-%m-%dT%H:%M:%S")
            base_start = (ts - timedelta(days=day_offset, minutes=window_min)).strftime("%Y-%m-%dT%H:%M:%S")
            baseline_total += await _count_splunk_events(service, level, base_start, base_end, cfg=cfg)
        baseline_avg = baseline_total / baseline_days if baseline_days > 0 else 0
        ratio = (current / baseline_avg) if baseline_avg > 0 else (float("inf") if current > 0 else 1.0)
        results[level] = {
            "current_count": current,
            "baseline_avg": round(baseline_avg, 2),
            "ratio": round(ratio, 2),
            "anomaly_detected": ratio > 3.0 and current > 5,
        }

    anomalies = [lvl for lvl, r in results.items() if r["anomaly_detected"]]
    if anomalies:
        assessment = (
            f"ANOMALY DETECTED: {', '.join(anomalies)} rate spiked in service '{service}'. "
            + " | ".join(
                f"{lvl}: {results[lvl]['current_count']} vs baseline {results[lvl]['baseline_avg']} "
                f"({results[lvl]['ratio']}×)"
                for lvl in anomalies
            )
        )
    else:
        assessment = f"No anomaly detected in service '{service}'. Log rates are within normal range."

    return json.dumps({
        "service": service,
        "levels": results,
        "anomaly_detected": bool(anomalies),
        "anomalous_levels": anomalies,
        "assessment": assessment,
    })
