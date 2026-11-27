"""Actual candidate stdio JSON-RPC, loopback backend and OTLP projection proof."""
# The optional SDK guard precedes its protobuf imports.
# ruff: noqa: E402
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("mcp.server.mcpserver")
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

MCP_DIR = Path(__file__).resolve().parents[1]


def test_candidate_stdio_stdout_is_only_jsonrpc_and_stderr_is_sanitized(tmp_path):
    upstream, exports = [], []

    class Backend(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/api/v1/projects":
                self.send_error(404)
                return
            upstream.append(dict(self.headers))
            self.send_response(200 if len(upstream) == 1 else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Request-ID", "synthetic-backend-error-id")
            self.end_headers()
            self.wfile.write(json.dumps([{"id": "project-fixture", "name": "result-canary", "slug": "fixture",
                                          "created_at": "2026-11-27T00:00:00Z"}] if len(upstream) == 1 else
                                        {"detail": "backend-body-canary"}).encode())

        def do_POST(self):
            assert self.path == "/v1/traces"
            length = int(self.headers["Content-Length"])
            assert 0 < length <= 65_536
            exports.append(ExportTraceServiceRequest.FromString(self.rfile.read(length)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    port = backend.server_address[1]
    env = dict(os.environ)
    env.update(TESTLOOKUP_API_URL=f"http://127.0.0.1:{port}", TESTLOOKUP_USERNAME="", TESTLOOKUP_PASSWORD="",
        TESTLOOKUP_ENV_FILE=str(tmp_path / "absent.env"), TESTLOOKUP_OTLP_LOGS_ENDPOINT="",
        TESTLOOKUP_OTLP_METRICS_ENDPOINT="", TESTLOOKUP_OTLP_TRACES_ENDPOINT=f"http://127.0.0.1:{port}/v1/traces",
        TESTLOOKUP_TELEMETRY_TRACE_SAMPLE_RATIO="1", TESTLOOKUP_TELEMETRY_ENVIRONMENT="development",
        TESTLOOKUP_TELEMETRY_INSTALLATION="synthetic-stdio-site", APP_VERSION="1.2.3", BUILD_REVISION="a" * 40,
        PYTHONUTF8="1")
    process = subprocess.Popen([sys.executable, str(MCP_DIR / "server.py"), "--transport", "stdio"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)
    output, errors, received = [], [], queue.Queue()

    def read_output():
        for line in process.stdout:
            output.append(line)
            try:
                received.put(json.loads(line))
            except ValueError:
                received.put({"invalid_stdout": True})

    def read_errors():
        errors.extend(process.stderr)

    readers = [threading.Thread(target=read_output, daemon=True), threading.Thread(target=read_errors, daemon=True)]
    for reader in readers:
        reader.start()

    def send(message, identity):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            response = received.get(timeout=max(0.01, deadline - time.monotonic()))
            assert "invalid_stdout" not in response
            if response.get("id") == identity:
                return response
        raise AssertionError("Synthetic stdio response deadline expired")

    try:
        initialized = send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "synthetic-fixture", "version": "1"}}}, 1)
        assert initialized["result"]["serverInfo"]["name"] == "TestLookup"
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        process.stdin.flush()
        success = send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_projects", "arguments": {},
            "_meta": {"traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01", "private_extension": "metadata-canary"}}}, 2)
        failure = send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_projects", "arguments": {}}}, 3)
        assert not success["result"]["isError"] and "result-canary" in repr(success)
        assert failure["result"]["isError"] and "synthetic-backend-error-id" in repr(failure)
        assert "backend-body-canary" not in repr(failure)
        process.stdin.close()
        started = time.monotonic()
        assert process.wait(timeout=5) == 0
        assert time.monotonic() - started < 4
        for reader in readers:
            reader.join(timeout=1)
        assert len(output) == 3 and all(json.loads(line)["jsonrpc"] == "2.0" for line in output)
        events = [json.loads(line) for line in errors if line.strip().startswith("{")]
        tools = [event for event in events if event.get("event") == "mcp_tool_execution"]
        assert len(tools) == 2
        assert {event["outcome"] for event in tools} == {"success", "request_error"}
        assert {event["transport"] for event in tools} == {"stdio"}
        assert all("request_id" in event and "trace_id" in event and "span_id" in event for event in tools)
        assert not any(marker in "".join(errors) for marker in ("result-canary", "metadata-canary", "backend-body-canary"))
        assert len(upstream) == 2
        assert len({headers["X-Request-ID"] for headers in upstream}) == 2
        assert all("traceparent" in {key.lower() for key in headers} for headers in upstream)
        spans = [span for request in exports for rm in request.resource_spans for sm in rm.scope_spans for span in sm.spans]
        assert len(spans) == 2 and all(span.name == "mcp.tool" for span in spans)
        assert all(not span.events and not span.status.message for span in spans)
        assert "canary" not in str(exports)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        backend.shutdown()
        backend.server_close()
        thread.join(timeout=1)
