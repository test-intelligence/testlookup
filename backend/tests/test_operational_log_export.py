"""The private OTLP hop carries only the approved operational projection."""

import hashlib
import json
import logging
import queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import structlog
from opentelemetry import trace
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest

from app.core.operational_logs import OperationalLogExport
from app.core.config import settings
from app.core.logging_config import configure_logging


def test_operational_events_are_exported_without_free_text() -> None:
    uploads: queue.Queue[ExportLogsServiceRequest] = queue.Queue()

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            assert self.path == "/v1/logs"
            request = ExportLogsServiceRequest.FromString(self.rfile.read(int(self.headers["Content-Length"])))
            uploads.put(request)
            self.send_response(200)
            self.end_headers()

    with ThreadingHTTPServer(("127.0.0.1", 0), Receiver) as receiver:
        server = Thread(target=receiver.serve_forever)
        server.start()
        try:
            sink = OperationalLogExport(
                endpoint=f"http://127.0.0.1:{receiver.server_port}/v1/logs",
                service="testlookup", environment="production", role="api",
            )
            secret = "synthetic-opaque-input"
            approved = {
                "logger": "http.access", "event": "http_request", "duration_ms": 4.2,
                "status_code": 201, "request_id": secret, "path": "/" + secret,
                "trace_id": "a" * 32, "error": secret,
            }
            ignored = {"logger": "app.other", "event": "http_request", "request_id": secret}
            assert sink(None, "info", ignored) is ignored
            assert uploads.empty()

            span = trace.NonRecordingSpan(trace.SpanContext(
                trace_id=0x101, span_id=0x102, is_remote=False, trace_flags=trace.TraceFlags.SAMPLED,
            ))
            with trace.use_span(span):
                assert sink(None, "info", approved) is approved
            serialized = uploads.get(timeout=5)
            assert secret.encode() not in serialized.SerializeToString()
            resource = serialized.resource_logs[0]
            assert len(resource.scope_logs[0].log_records) == 1
            record = resource.scope_logs[0].log_records[0]
            assert record.event_name == "http_request"
            assert record.trace_id.hex() == f"{0x101:032x}"
            assert record.span_id.hex() == f"{0x102:016x}"
            digest = hashlib.sha256(secret.encode()).hexdigest()
            assert json.loads(record.body.string_value) == {
                "event": "http_request", "duration_ms": 4.2, "status_code": 201,
                "request_id": digest, "trace_id": f"{0x101:032x}", "span_id": f"{0x102:016x}",
            }
            assert {value.key: value.value.string_value for value in record.attributes}["request_id"] == digest
        finally:
            receiver.shutdown()
            server.join()


def test_cleartext_remote_endpoint_leaves_normal_logging_available() -> None:
    sink = OperationalLogExport(
        endpoint="http://other.example/v1/logs",
        service="testlookup", environment="production", role="api",
    )
    event = {"logger": "http.access", "event": "http_request"}
    assert sink(None, "info", event) is event
    assert sink._start_failed


def test_configured_operational_export_preserves_stdout_logging(monkeypatch) -> None:
    root = logging.getLogger()
    previous_settings = structlog.get_config()
    previous_handlers, previous_level = root.handlers[:], root.level
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "http://otel-collector:4318/v1/logs")
    try:
        configure_logging()
        assert len(root.handlers) == 1
        assert any(isinstance(processor, OperationalLogExport)
                   for processor in structlog.get_config()["processors"])
    finally:
        structlog.configure(**previous_settings)
        root.handlers[:] = previous_handlers
        root.setLevel(previous_level)
