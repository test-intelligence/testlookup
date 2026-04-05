"""LangChain tool: query Splunk for correlated backend errors with retry."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import httpx
from langchain_core.tools import tool

from app.core.config import settings
from app.services.input_sanitizer import sanitize_query_param, sanitize_service_name
from app.services.resilience import async_retry

logger = logging.getLogger(__name__)


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
    # Phase 4: Sanitise inputs to prevent query injection
    service_name = sanitize_service_name(service_name)
    timestamp_utc = sanitize_query_param(timestamp_utc)

    if not service_name:
        return "Invalid service name after sanitization. Cannot query Splunk."

    if not settings.SPLUNK_ENABLED:
        return (
            "Splunk integration is not configured (SPLUNK_ENABLED=false). "
            "Cannot query backend logs. Consider this when determining root cause."
        )

    try:
        fail_time = datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00"))
    except ValueError:
        fail_time = datetime.now(timezone.utc)

    window_start = (fail_time - timedelta(minutes=5)).strftime("%m/%d/%Y:%H:%M:%S")
    window_end = (fail_time + timedelta(minutes=5)).strftime("%m/%d/%Y:%H:%M:%S")

    spl_query = (
        f'search index={settings.SPLUNK_INDEX} '
        f'service="{service_name}" '
        f'(status>=500 OR level=ERROR OR "Exception" OR "Timeout" OR "Connection refused") '
        f'earliest="{window_start}" latest="{window_end}" '
        f'| head 20 '
        f'| table _time, level, message, exception, status_code'
    )

    headers = {"Authorization": f"Bearer {settings.SPLUNK_API_TOKEN}"}

    async def _do_splunk_query() -> list:
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:  # noqa: S501
            create_resp = await client.post(
                f"{settings.SPLUNK_BASE_URL}/services/search/jobs",
                headers=headers,
                data={"search": spl_query, "output_mode": "json", "exec_mode": "oneshot"},
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

        lines = [f"=== Splunk Log Correlation ({len(results)} entries) ==="]
        for entry in results[:10]:
            ts = entry.get("_time", "?")
            msg = entry.get("message") or entry.get("_raw", "")
            level = entry.get("level", "?")
            lines.append(f"[{ts}] [{level}] {msg[:300]}")

        return "\n".join(lines)

    except httpx.HTTPStatusError as e:
        logger.warning("Splunk API error: %s", e)
        return f"Splunk query failed: HTTP {e.response.status_code}. Cannot correlate backend logs."
    except Exception as e:
        logger.warning("Splunk query exception: %s", e)
        return f"Splunk query failed: {str(e)}. Cannot correlate backend logs."
