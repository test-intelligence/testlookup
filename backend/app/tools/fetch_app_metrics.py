"""
Tool: application metrics of the system under test, from its Prometheus (W23d AGT-15, template-only).

The caller names ADMIN-defined metric templates by id; the server substitutes
the (regex-checked, escaped) service name. Raw PromQL is REFUSED with no HTTP
call. The configuration is the bound runtime config (the log stage binds it)
else the instance block (``runtime_integrations_for(None)``), resolved per
call -- never the process ``PROMETHEUS_URL`` (that is TestLookup's own
Performance-page Prometheus). ``AI_OFFLINE_MODE`` refuses before any DNS or
HTTP (D19). A timeout is ``timed_out``, never "no data".
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from langchain_core.tools import tool

from app.core.config import settings
from app.services import prometheus_vocab as pv
from app.services.app_metrics_service import (
    TemplateRequestError,
    outcome_as_dict,
    query_templates,
    unavailable,
)

#: Any of these keys means the caller tried to send PromQL: refused, no call.
RAW_PROMQL_KEYS = ("metrics", "query", "promql", "expr")


def _tool_error(code: str, message: Optional[str] = None) -> str:
    return json.dumps({"error": code, "message": message or pv.TOOL_ERRORS.get(code, code)})


def _parse_timestamp(raw: Any) -> Optional[datetime]:
    """An ISO-8601 time (naive = UTC; ``Z`` accepted) as an aware UTC datetime, else None."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


async def _runtime_config():
    """The bound runtime config (the log stage's), else the instance block (None when it cannot be read)."""
    from app.services.integration_config_service import runtime_integrations_for
    from app.tools.investigation_context import runtime_integrations

    bound = runtime_integrations()
    if bound is not None:
        return bound
    return await runtime_integrations_for(None)


def _answer(outcome) -> str:
    result = outcome_as_dict(outcome)
    result["anomalies"] = [item["sentence"] for item in result["series"] if item.get("rose")]
    # Kept for any reader of the old key: the outcome / finding sentences, joined.
    result["infrastructure_summary"] = outcome.sentence
    return json.dumps(result)


@tool
async def fetch_app_metrics(params_json: str) -> str:
    """
    Fetch application metrics (latency, error rate, memory, CPU) of a service around a failure time.

    Input JSON keys:
      - service_name: the service under test (letters, digits, "_", ".", ":", "-")
      - timestamp_utc: ISO-8601 time of the failure (naive = UTC)
      - window_minutes: the failure window (1..1440, default 15); the window before it is the baseline
      - template_ids: up to 4 ids of the configured metric templates (omitted: the first 4)

    Raw PromQL (metrics / query / promql / expr) is not accepted.

    Returns: JSON with outcome, sentence, service, time_window, series (≤ 120 points each), anomalies and
    infrastructure_summary.
    """
    try:
        params = json.loads(params_json)
    except (json.JSONDecodeError, TypeError, AttributeError):
        return _tool_error("invalid_input", "Input must be a JSON object.")
    if not isinstance(params, dict):
        return _tool_error("invalid_input", "Input JSON must be an object.")
    if any(key in params for key in RAW_PROMQL_KEYS):
        return _tool_error("raw_promql_refused")

    service_raw = params.get("service_name", "")
    window_raw = params.get("window_minutes", pv.WINDOW_MINUTES_DEFAULT)
    ids_raw = params.get("template_ids", [])
    if not isinstance(service_raw, str):
        return _tool_error("invalid_input", "service_name must be a string.")
    at = _parse_timestamp(params.get("timestamp_utc"))
    if at is None:
        return _tool_error("invalid_timestamp")
    if isinstance(window_raw, bool) or not isinstance(window_raw, (int, str)):
        return _tool_error("invalid_input", "window_minutes must be an integer.")
    try:
        window_minutes = int(window_raw)
    except ValueError:
        return _tool_error("invalid_input", "window_minutes must be an integer.")
    if not 1 <= window_minutes <= pv.WINDOW_MINUTES_MAX:
        return _tool_error("invalid_input", f"window_minutes must be between 1 and {pv.WINDOW_MINUTES_MAX}.")
    if not isinstance(ids_raw, list) or not all(isinstance(item, str) for item in ids_raw):
        return _tool_error("invalid_input", "template_ids must be a list of template id strings.")
    if len(ids_raw) > pv.TEMPLATES_PER_CALL_MAX:
        return _tool_error("too_many_templates")

    service = service_raw.strip()
    if settings.AI_OFFLINE_MODE:
        # D19: refused before the configuration is even resolved -- no session, no DNS, no HTTP.
        return _answer(unavailable("offline_mode", service=service, at=at, window_minutes=window_minutes))
    cfg = await _runtime_config()
    if cfg is None:
        return _answer(unavailable("config_unavailable", service=service, at=at, window_minutes=window_minutes))
    try:
        outcome = await query_templates(
            cfg, service=service, at=at, window_minutes=window_minutes, template_ids=list(ids_raw),
        )
    except TemplateRequestError as exc:
        return _tool_error(exc.code, exc.message)
    return _answer(outcome)
