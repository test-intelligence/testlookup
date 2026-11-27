"""Bounded, payload-free operational logs for the deployed SSE transport."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from urllib.parse import urlsplit

import requests
from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.util.types import AttributeValue
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)
_CORRELATION_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_QUIET_PATHS = frozenset({"/health/live", "/health/ready"})


def add_operational_logs(app: ASGIApp, endpoint: str, environment: str) -> ASGIApp:
    try:
        url = urlsplit(endpoint)
        private_http = url.scheme == "http" and url.hostname in {
            "otel-collector", "localhost", "127.0.0.1", "::1",
        }
        if (
            not url.hostname or (url.scheme != "https" and not private_http)
            or url.path != "/v1/logs" or url.username is not None
            or url.query or url.fragment
        ):
            raise ValueError("expected an HTTPS OTLP/HTTP logs URL")
        resource = Resource({
            "service.name": "testlookup",
            "service.namespace": "testlookup",
            "deployment.environment.name": environment
            if environment in {"development", "staging", "production"} else "unknown",
            "testlookup.service.role": "mcp",
        })
        session = requests.Session()
        session.trust_env = False
        session.max_redirects = 0
        exporter = OTLPLogExporter(
            endpoint=endpoint, timeout=2, max_request_size=65_536, session=session,
        )
        provider = LoggerProvider(resource=resource)
        provider.add_log_record_processor(BatchLogRecordProcessor(
            exporter, max_queue_size=512, max_export_batch_size=64, schedule_delay_millis=1000,
        ))
        sink = provider.get_logger("testlookup.operational")
    except Exception:  # noqa: BLE001 - the service runs if telemetry configuration is invalid
        logger.warning("Operational log exporter could not initialize")
        return app

    async def observed(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in _QUIET_PATHS:
            await app(scope, receive, send)
            return
        supplied = Headers(scope=scope).get("X-Request-ID", "")
        request_id = supplied if _CORRELATION_ID.fullmatch(supplied) else str(uuid.uuid4())
        started = time.monotonic()

        async def send_observed(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
                status = message["status"]
                severity = SeverityNumber.WARN if status >= 500 else SeverityNumber.INFO
                attributes: dict[str, AttributeValue] = {
                    "request_id": hashlib.sha256(request_id.encode("ascii")).hexdigest(),
                    "status_code": status,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                }
                try:
                    sink.emit(
                        timestamp=time.time_ns(),
                        severity_number=severity,
                        severity_text=severity.name,
                        event_name="mcp_request",
                        body=json.dumps({"event": "mcp_request", **attributes}, separators=(",", ":")),
                        attributes=attributes,
                    )
                except Exception:  # noqa: BLE001 - preserve the response if telemetry fails
                    logger.warning("Operational log submission failed")
            await send(message)

        await app(scope, receive, send_observed)

    return observed
