"""Payload-free stdlib diagnostics on stderr, leaving stdio protocol output intact."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sys

from telemetry_context import outgoing_operation_headers

_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_CODES = {"protocol_diagnostic", "exporter_init_failed", "metric_record_failed", "log_record_failed",
          "provider_shutdown_failed", "tool_inventory_truncated", "tool_inventory_failed", "tool_execution"}


class DiagnosticFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Never format message/args, exception text or stack information. SDK
        # records can contain tool arguments, backend URLs and caller strings.
        name = record.name
        category = ("sdk" if name.startswith("mcp") else "http" if name.startswith(("httpx", "httpcore", "urllib3", "requests"))
                    else "telemetry" if name.startswith("opentelemetry") else "application")
        code = getattr(record, "event_code", "protocol_diagnostic")
        if code == "tool_execution":
            values: dict[str, str | int | float] = {"event": "mcp_tool_execution", "level": "INFO"}
            for key, pattern in (("tool", r"[a-z][a-z0-9_]{0,63}"), ("request_id", r"[0-9a-f]{64}"),
                                 ("trace_id", r"[0-9a-f]{32}"), ("span_id", r"[0-9a-f]{16}")):
                item = getattr(record, key, None)
                if isinstance(item, str) and re.fullmatch(pattern, item):
                    values[key] = item
            outcome, transport = getattr(record, "outcome", None), getattr(record, "transport", None)
            if outcome in {"success", "tool_error", "request_error", "cancelled", "internal_error"}:
                values["outcome"] = outcome
                values["level"] = "INFO" if outcome == "success" else "WARNING"
            if transport in {"stdio", "sse"}:
                values["transport"] = transport
            duration = getattr(record, "duration_ms", None)
            if isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and 0 <= duration <= 600_000:
                values["duration_ms"] = duration
            return json.dumps(values, separators=(",", ":"))
        safe = {"event": "mcp_diagnostic", "level": record.levelname if record.levelname in _LEVELS else "WARNING",
                "category": category, "code": code if isinstance(code, str) and code in _CODES else "protocol_diagnostic"}
        request_id = outgoing_operation_headers().get("X-Request-ID")
        if request_id is not None:
            safe["request_id"] = hashlib.sha256(request_id.encode("ascii")).hexdigest()
        return json.dumps(safe, separators=(",", ":"))


class StderrHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stderr
        super().emit(record)


def configure_diagnostic_logging() -> None:
    handler = StderrHandler()
    handler.setFormatter(DiagnosticFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.captureWarnings(True)
    prefixes = ("mcp", "testlookup.mcp", "httpx", "httpcore", "opentelemetry", "urllib3", "requests", "uvicorn", "asyncio")
    for name, candidate in logging.Logger.manager.loggerDict.items():
        if isinstance(candidate, logging.Logger) and name.startswith(prefixes):
            candidate.handlers.clear()
            candidate.propagate = True
            candidate.setLevel(logging.INFO if name.startswith("testlookup.mcp") else logging.WARNING)
