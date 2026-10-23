"""LangChain tool: query Splunk for correlated backend errors with retry.

W10 INT-01 / INT-03: the Splunk base URL, token and index come from the
runtime integration config the server resolved before the investigation
(``investigation_context.runtime_integrations()``: the settings saved at
``/settings/integrations`` and the project's Splunk index), falling back to
THIS module's ``settings`` when nothing is bound. The host is checked with
``url_safety.egress_block_reason`` before the token is sent. The search
itself lives in ``splunk_correlation`` so the server-side lookup
(``services/integration_evidence.py``) and the tool share one code path.
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Optional, cast

import httpx
from langchain_core.tools import tool

from app.core.config import settings
from app.core.http_client import get_http_client
from app.core.integration_values import TARGET_NOT_ALLOWED, valid_splunk_index
from app.services.input_sanitizer import sanitize_query_param, sanitize_service_name
from app.services.integration_config_service import IntegrationRuntimeConfig
from app.services.resilience import async_retry
from app.services.url_safety import egress_block_reason
from app.tools.investigation_context import (
    get_investigation_context,
    prefetched_observation,
    runtime_integrations,
)
from app.services.evidence_sanitizer import sanitize_reference_text

logger = logging.getLogger(__name__)

TOOL_NAME = "query_splunk_logs"

SPLUNK_DISABLED_TEXT = (
    "Splunk integration is not configured (SPLUNK_ENABLED=false). "
    "Cannot query backend logs. Consider this when determining root cause."
)
SPLUNK_OFFLINE_TEXT = (
    "Splunk lookup disabled: AI_OFFLINE_MODE=true forbids outbound "
    "integration calls. Backend logs were not consulted."
)
SPLUNK_NOT_CONFIGURED_TEXT = "Splunk lookup unavailable: no Splunk base URL is configured."
SPLUNK_INVALID_INDEX_TEXT = "Splunk lookup refused: the Splunk index is not valid."
SPLUNK_NOT_ALLOWED_TEXT = f"Splunk lookup refused: {TARGET_NOT_ALLOWED}."
#: The prefix of an answer that found log entries (the server-side lookup
#: keeps only those).
SPLUNK_FOUND_PREFIX = "=== Splunk Log Correlation"


def runtime_config(project_id: Optional[str] = None) -> IntegrationRuntimeConfig:
    """The bound runtime config, else the environment of THIS module's settings."""
    return runtime_integrations() or IntegrationRuntimeConfig.from_settings(settings, project_id)


async def splunk_correlation(cfg: IntegrationRuntimeConfig, service_name: str, timestamp_utc: str) -> str:
    """Search ``cfg``'s Splunk for backend errors within +/-5 minutes of the failure.

    ``service_name`` / ``timestamp_utc`` are server-bound values (the caller
    sanitizes them). Refuses with a sentence and NO HTTP when the integration
    is disabled, offline mode is on, no base URL is configured, the index is
    not SPL-safe, or the host is not allowed.
    """
    if not cfg.splunk_enabled:
        return SPLUNK_DISABLED_TEXT
    # Re-audit N19: architecture/SECURITY.md s5 says every outbound
    # integration short-circuits on AI_OFFLINE_MODE. Hard gate, read live.
    if settings.AI_OFFLINE_MODE:
        return SPLUNK_OFFLINE_TEXT
    if not cfg.splunk_base_url:
        return SPLUNK_NOT_CONFIGURED_TEXT
    if not valid_splunk_index(cfg.splunk_index):
        return SPLUNK_INVALID_INDEX_TEXT
    block = await egress_block_reason(cfg.splunk_base_url)
    if block is not None:
        logger.info("Splunk lookup refused before sending the token: %s", block)
        return SPLUNK_NOT_ALLOWED_TEXT
    if not service_name:
        return "Invalid service name after sanitization. Cannot query Splunk."

    try:
        fail_time = datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00"))
    except ValueError:
        return "Splunk lookup unavailable: invalid authorized timestamp."

    window_start = (fail_time - timedelta(minutes=5)).strftime("%m/%d/%Y:%H:%M:%S")
    window_end = (fail_time + timedelta(minutes=5)).strftime("%m/%d/%Y:%H:%M:%S")

    spl_query = (
        f'search index={cfg.splunk_index} '
        f'service="{service_name}" '
        f'(status>=500 OR level=ERROR OR "Exception" OR "Timeout" OR "Connection refused") '
        f'earliest="{window_start}" latest="{window_end}" '
        f'| head 20 '
        f'| table _time, level, message, exception, status_code'
    )

    headers = {"Authorization": f"Bearer {cfg.splunk_api_token}"} if cfg.splunk_api_token else {}
    search_url = f"{cfg.splunk_base_url.rstrip('/')}/services/search/jobs"

    async def _do_splunk_query() -> list:
        client = get_http_client()
        create_resp = await client.post(
            search_url,
            headers=headers,
            data={"search": spl_query, "output_mode": "json", "exec_mode": "oneshot"},
            timeout=30.0,
        )
        create_resp.raise_for_status()
        return cast(list[Any], create_resp.json().get("results", []))

    try:
        results = await async_retry(
            _do_splunk_query,
            max_retries=2,
            base_delay=2.0,
            max_delay=15.0,
            operation_name=f"splunk_query({service_name})",
        )

        if not results:
            return (
                f"No backend errors found in Splunk for service '{service_name}' "
                f"within ±5 minutes of {timestamp_utc}."
            )

        lines = [f"{SPLUNK_FOUND_PREFIX} ({len(results)} entries) ==="]
        for entry in results[:10]:
            ts = entry.get("_time", "?")
            msg = entry.get("message") or entry.get("_raw", "")
            level = entry.get("level", "?")
            lines.append(f"[{ts}] [{level}] {msg[:300]}")

        rendered, _, _ = sanitize_reference_text("\n".join(lines), limit=6000)
        return rendered

    except httpx.HTTPStatusError as e:
        logger.warning("Splunk API error: HTTP %s", e.response.status_code)
        return f"Splunk query failed: HTTP {e.response.status_code}. Cannot correlate backend logs."
    except Exception as e:
        logger.warning("Splunk query exception: %s", type(e).__name__)
        return f"Splunk query failed: {str(e)}. Cannot correlate backend logs."


@tool
async def query_splunk_logs(service_name: str, timestamp_utc: str) -> str:
    """
    Search Splunk for backend application errors that occurred within a
    5-minute window around the time the test failed. Look for HTTP 5xx errors,
    database connection timeouts, and exception stack traces.

    Args:
        service_name: The backend service name (e.g. 'payment-gateway', 'user-service').
        timestamp_utc: ISO 8601 timestamp of the test failure (e.g. '2026-03-10T14:30:00Z').

    Returns:
        Matching log entries as a formatted string, or a message if none found.
    """
    context = get_investigation_context()
    if context is None or not context.service_name or not context.timestamp:
        return "Splunk lookup unavailable: authorized service/time context is missing."
    if service_name != context.service_name or timestamp_utc != context.timestamp:
        return "Splunk lookup denied: requested scope differs from the investigation context."
    # Sanitize server-bound values, not model-selected scope.
    service_name = sanitize_service_name(context.service_name)
    timestamp_utc = sanitize_query_param(context.timestamp)

    if not service_name:
        return "Invalid service name after sanitization. Cannot query Splunk."

    cfg = runtime_config(context.project_id)
    if not cfg.splunk_enabled:
        return SPLUNK_DISABLED_TEXT
    if settings.AI_OFFLINE_MODE:
        return SPLUNK_OFFLINE_TEXT

    # W10 D-4: the server already ran this lookup for this investigation's
    # bound service and time; answer from the memo, not a second search.
    memo = prefetched_observation(TOOL_NAME)
    if memo is not None:
        return memo

    return await splunk_correlation(cfg, service_name, timestamp_utc)
