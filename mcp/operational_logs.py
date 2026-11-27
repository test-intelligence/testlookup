"""Bounded, payload-free operational logs for the deployed SSE transport."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.util.types import AttributeValue
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from telemetry_resources import exporter_session, resource, shutdown_bounded, validated_endpoint

logger = logging.getLogger(__name__)
_CORRELATION_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_QUIET_PATHS = frozenset({"/health/live", "/health/ready"})


def create_operational_logger(endpoint: str, environment: str):
    exporter = OTLPLogExporter(endpoint=validated_endpoint(endpoint, "logs"), timeout=2,
        max_request_size=65_536, headers={}, session=exporter_session())
    provider = LoggerProvider(resource=resource(environment), shutdown_on_exit=False)
    provider.add_log_record_processor(BatchLogRecordProcessor(
        exporter, max_queue_size=512, max_export_batch_size=64, schedule_delay_millis=1000,
        export_timeout_millis=2000,
    ))
    return provider, provider.get_logger("testlookup.operational")


def add_operational_logs(app: ASGIApp, endpoint: str, environment: str, *, own_provider=None) -> ASGIApp:
    try:
        _provider, sink = create_operational_logger(endpoint, environment)
        if own_provider is not None:
            own_provider(_provider)
    except Exception:  # noqa: BLE001 - the service runs if telemetry configuration is invalid
        logger.warning("Operational log exporter could not initialize")
        return app

    async def observed(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan" and own_provider is None:
            async def send_lifecycle(message: Message) -> None:
                if message["type"] == "lifespan.shutdown.complete":
                    shutdown_bounded((_provider,))
                await send(message)
            await app(scope, receive, send_lifecycle)
            return
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
