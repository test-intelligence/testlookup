"""Export an allowlisted projection of application logs to a private OTLP sink."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from functools import lru_cache
from urllib.parse import urlsplit

import requests
from opentelemetry import trace
from opentelemetry._logs import Logger, SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.util.types import AttributeValue

from app.core.telemetry_identity import telemetry_identity
from structlog.types import EventDict, WrappedLogger

logger = logging.getLogger(__name__)

_HTTP_EVENTS = frozenset({
    ("http.access", "http_request"),
    ("app.core.analytics_errors", "analytics_unhandled_error"),
    ("app.core.analytics_errors", "http_unhandled_error"),
})
_WORKER_EVENT = ("app.worker.celery_app", "celery_task_execution")
_STATE = frozenset({"success", "failure", "retry", "other"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_SEVERITY = {"warning": SeverityNumber.WARN, "error": SeverityNumber.ERROR}


def _validate_endpoint(url: str) -> None:
    endpoint = urlsplit(url)
    private_http = endpoint.scheme == "http" and endpoint.hostname in {
        "otel-collector", "localhost", "127.0.0.1", "::1",
    }
    if (
        not endpoint.hostname or (endpoint.scheme != "https" and not private_http)
        or endpoint.path != "/v1/logs" or endpoint.username is not None
        or endpoint.query or endpoint.fragment
    ):
        raise ValueError("expected an HTTPS OTLP/HTTP logs URL")


class OperationalLogExport:
    """Feed a bounded SDK processor without replacing the normal stdout log."""

    def __init__(
        self, *, endpoint: str, service: str, environment: str, role: str, min_level: int = logging.INFO,
        service_version: str = "", build_revision: str = "", installation: str = "",
    ) -> None:
        self._endpoint = endpoint
        self._attributes = {
            "service.name": service,
            "service.namespace": "testlookup",
            "service.version": service_version,
            "deployment.environment.name": environment,
            "testlookup.service.role": role,
            **telemetry_identity(build_revision, installation),
        }
        self._role = role
        self._min_level = min_level
        self._pid = os.getpid()
        self._sdk_logger: Logger | None = None
        self._start_failed = False
        self._lock = threading.Lock()

    def _get_logger(self) -> Logger | None:
        if self._start_failed:
            return None
        if self._sdk_logger is not None:
            return self._sdk_logger
        with self._lock:
            if self._sdk_logger is not None or self._start_failed:
                return self._sdk_logger
            try:
                _validate_endpoint(self._endpoint)
                session = requests.Session()
                session.trust_env = False
                session.max_redirects = 0
                exporter = OTLPLogExporter(
                    endpoint=self._endpoint, timeout=2, max_request_size=65_536, session=session,
                )
                provider = LoggerProvider(resource=Resource(self._attributes))
                provider.add_log_record_processor(BatchLogRecordProcessor(
                    exporter, max_queue_size=512, max_export_batch_size=64, schedule_delay_millis=1000,
                ))
                self._sdk_logger = provider.get_logger("testlookup.operational")
            except Exception:  # noqa: BLE001 - telemetry must not interrupt logging
                self._start_failed = True
                logger.warning("Operational log exporter could not initialize; using stdout")
        return self._sdk_logger

    def __call__(self, _logger: WrappedLogger, method: str, event: EventDict) -> EventDict:
        event_name = event.get("event")
        source = event.get("logger")
        if not isinstance(event_name, str) or not isinstance(source, str):
            return event
        key = (source, event_name)
        api_event = key in _HTTP_EVENTS and self._role == "api"
        worker_event = key == _WORKER_EVENT and self._role in {"worker", "worker-children"}
        if not api_event and not worker_event:
            return event
        if getattr(logging, method.upper(), logging.INFO) < self._min_level:
            return event
        pid = os.getpid()
        if pid != self._pid:
            # The inherited SDK session and lock may belong to a dead thread.
            self._sdk_logger = None
            self._lock = threading.Lock()
            self._start_failed = False
            self._pid = pid
        sdk_logger = self._get_logger()
        if sdk_logger is None:
            return event
        try:
            body: dict[str, str | int | float] = {"event": event_name}
            attributes: dict[str, AttributeValue] = {}
            for field in ("request_id", "celery_task_id"):
                value = event.get(field)
                if isinstance(value, str) and _IDENTIFIER.fullmatch(value):
                    # IDs come from callers/brokers. Hash even well-formed
                    # values to keep arbitrary opaque input out of the store.
                    digest = hashlib.sha256(value.encode("ascii")).hexdigest()
                    body[field] = digest
                    attributes[field] = digest
            status = event.get("status_code")
            if type(status) is int and 100 <= status <= 599:
                body["status_code"] = status
                attributes["status_code"] = status
            duration = event.get("duration_ms")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool) and 0 <= duration <= 600_000:
                body["duration_ms"] = duration
                attributes["duration_ms"] = duration
            state = event.get("state")
            if isinstance(state, str) and state in _STATE:
                body["state"] = state
                attributes["state"] = state
            span = trace.get_current_span().get_span_context()
            if span.is_valid:
                body.update(trace_id=f"{span.trace_id:032x}", span_id=f"{span.span_id:016x}")
            severity = _SEVERITY.get(method, SeverityNumber.INFO)
            sdk_logger.emit(
                timestamp=time.time_ns(),
                severity_number=severity,
                severity_text=severity.name,
                event_name=event_name,
                body=json.dumps(body, separators=(",", ":")),
                attributes=attributes,
            )
        except Exception:  # noqa: BLE001 - preserve stdout if projection or submission fails
            logger.warning("Operational log submission failed")
        return event


@lru_cache(maxsize=1)
def operational_log_export(
    *, endpoint: str, service: str, environment: str, role: str, min_level: int,
    service_version: str = "", build_revision: str = "", installation: str = "",
) -> OperationalLogExport:
    return OperationalLogExport(
        endpoint=endpoint, service=service, environment=environment, role=role, min_level=min_level,
        service_version=service_version, build_revision=build_revision, installation=installation,
    )
