"""Payload-free MCP observation through real SDK dispatch and local signal readers."""
# The optional SDK guard and flat-module path bootstrap precede imports.
# ruff: noqa: E402
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp.server.mcpserver")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import Client
from mcp.server._otel import OpenTelemetryMiddleware
from mcp.server.context import ServerRequestContext
from mcp.server.mcpserver import Context as ToolContext, MCPServer
from mcp.shared.exceptions import MCPError
from mcp_types import CallToolResult, TextContent
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from diagnostics import DiagnosticFormatter
from telemetry_context import mark_backend_failure, note_backend_error, outgoing_operation_headers
from telemetry_resources import resource, shutdown_bounded, validated_endpoint
from tool_telemetry import MAX_KNOWN_TOOLS, ToolTelemetry


def settings(**changes):
    return SimpleNamespace(telemetry_environment="development", telemetry_trace_sample_ratio=1.0,
        otlp_traces_endpoint="", otlp_metrics_endpoint="", otlp_logs_endpoint="", **changes)


@pytest.fixture
def observed():
    spans, reader, logs = InMemorySpanExporter(), InMemoryMetricReader(), InMemoryLogRecordExporter()
    tracer = TracerProvider(resource=resource("development"), shutdown_on_exit=False)
    tracer.add_span_processor(SimpleSpanProcessor(spans))
    meter = MeterProvider(resource=resource("development"), metric_readers=[reader], shutdown_on_exit=False)
    log_provider = LoggerProvider(resource=resource("development"), shutdown_on_exit=False)
    log_provider.add_log_record_processor(SimpleLogRecordProcessor(logs))
    telemetry = ToolTelemetry(settings(), tracer_provider=tracer, meter_provider=meter,
                              log_sink=log_provider.get_logger("testlookup.mcp"))
    telemetry.server = SimpleNamespace(list_tools=_inventory)
    try:
        yield telemetry, spans, reader, logs
    finally:
        log_provider.shutdown()
        meter.shutdown()
        tracer.shutdown()


async def _inventory():
    return [SimpleNamespace(name="fixture_tool")]


def ctx(*, name="fixture_tool", meta=None, headers=None):
    return ServerRequestContext(session=object(), lifespan_context={}, protocol_version="2025-11-25",
        method="tools/call", params={"name": name, "arguments": {"secret": "argument-canary"}},
        request_id="private-jsonrpc-id", meta=meta,
        request=None if headers is None else SimpleNamespace(headers=headers))


def metric_points(reader):
    return {metric.name: metric.data.data_points for rm in reader.get_metrics_data().resource_metrics
            for sm in rm.scope_metrics for metric in sm.metrics}


@pytest.mark.parametrize("kind,expected", [("success", "success"), ("typed_error", "tool_error"),
    ("http_error", "request_error"), ("rendered_error", "request_error"),
    ("handled_fallback", "success"), ("protocol_error", "request_error"),
    ("internal_error", "internal_error"), ("cancelled", "cancelled")])
def test_each_completion_counts_once_with_closed_outcomes_and_no_payload(observed, kind, expected):
    runtime, spans, reader, logs = observed
    meta = {"traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
            "baggage": "credential=private-canary", "tracestate": "private-canary",
            "private_extension": {"unchanged": "metadata-canary"}}
    request = ctx(meta=meta, headers={"X-Request-ID": "fixture-request"})
    before = copy.deepcopy((request.params, request.meta))
    output = CallToolResult(content=[TextContent(type="text", text="response-canary")], is_error=kind in {"typed_error", "http_error"})

    async def handler(actual):
        assert actual is request
        headers = outgoing_operation_headers()
        assert set(headers) == {"X-Request-ID", "traceparent"}
        assert headers["X-Request-ID"] == "fixture-request"
        if kind in {"http_error", "handled_fallback"}:
            note_backend_error()
        if kind == "rendered_error":
            mark_backend_failure()
        if kind == "protocol_error":
            raise MCPError(-32602, "private-error-canary", {"secret": "private-canary"})
        if kind == "internal_error":
            raise RuntimeError("private-error-canary")
        if kind == "cancelled":
            raise asyncio.CancelledError()
        return output

    async def run():
        try:
            result = await runtime(request, handler)
            assert result is output
        except BaseException as error:
            assert kind in {"protocol_error", "internal_error", "cancelled"}
            assert type(error) in {MCPError, RuntimeError, asyncio.CancelledError}
        assert outgoing_operation_headers() == {}
    asyncio.run(run())
    assert (request.params, request.meta) == before
    span, = spans.get_finished_spans()
    assert span.name == "mcp.tool" and span.parent.span_id == int("0123456789abcdef", 16)
    assert span.attributes == {"rpc.system": "mcp", "rpc.method": "tools/call", "mcp.tool.name": "fixture_tool",
                               "mcp.transport": "sse", "mcp.tool.outcome": expected}
    assert span.events == () and span.status.description is None
    points = metric_points(reader)
    call, = points["testlookup.mcp.tool.calls"]
    duration, = points["testlookup.mcp.tool.duration"]
    assert call.value == duration.count == 1
    assert call.attributes == duration.attributes == {"tool": "fixture_tool", "outcome": expected, "transport": "sse"}
    entry, = logs.get_finished_logs()
    assert entry.log_record.trace_id == span.context.trace_id
    assert entry.log_record.span_id == span.context.span_id
    body = json.loads(entry.log_record.body)
    assert set(body) == {"event", "tool", "outcome", "transport", "duration_ms", "request_id"}
    assert body["request_id"] == hashlib.sha256(b"fixture-request").hexdigest()
    assert "canary" not in repr((span.attributes, span.events, body, call.attributes))


def test_concurrent_operations_have_distinct_correlation_and_reset(observed):
    runtime, spans, reader, _logs = observed
    ready, snapshots = asyncio.Event(), []

    async def handler(context):
        before = outgoing_operation_headers()
        snapshots.append(before)
        if len(snapshots) == 2:
            ready.set()
        await ready.wait()
        assert outgoing_operation_headers() == before
        return CallToolResult(content=[])

    async def run():
        await asyncio.gather(runtime(ctx(headers={"X-Request-ID": "alice"}), handler),
                             runtime(ctx(headers={"X-Request-ID": "bob"}), handler))
        assert outgoing_operation_headers() == {}
    asyncio.run(run())
    assert {item["X-Request-ID"] for item in snapshots} == {"alice", "bob"}
    assert len({item["traceparent"] for item in snapshots}) == 2
    assert len(spans.get_finished_spans()) == 2
    assert sum(point.value for point in metric_points(reader)["testlookup.mcp.tool.calls"]) == 2


def test_inventory_is_frozen_and_unknown_peer_names_never_become_labels(observed):
    runtime, spans, reader, _logs = observed
    calls = []

    async def inventory():
        calls.append(True)
        return [SimpleNamespace(name=f"tool_{index}") for index in range(MAX_KNOWN_TOOLS + 50)]
    runtime.server.list_tools = inventory

    async def handler(_context):
        return CallToolResult(content=[])
    async def run():
        await runtime(ctx(name="user-controlled-private-name"), handler)
        await runtime(ctx(name="second-private-name"), handler)
    asyncio.run(run())
    assert len(calls) == 1 and len(runtime.known_tools) == MAX_KNOWN_TOOLS
    assert all(span.attributes["mcp.tool.name"] == "other" for span in spans.get_finished_spans())
    point, = metric_points(reader)["testlookup.mcp.tool.calls"]
    assert point.attributes == {"tool": "other", "outcome": "success", "transport": "stdio"} and point.value == 2


def test_real_sdk_chain_observes_calls_without_exporting_sdk_raw_spans(observed, monkeypatch):
    runtime, spans, reader, _logs = observed
    import mcp.shared._otel as sdk_otel
    unsafe = InMemorySpanExporter()
    unsafe_provider = TracerProvider(shutdown_on_exit=False)
    unsafe_provider.add_span_processor(SimpleSpanProcessor(unsafe))
    monkeypatch.setattr(sdk_otel, "_tracer", unsafe_provider.get_tracer("sdk-global-fixture"))
    app = MCPServer("telemetry-fixture")
    original_boundaries = [type(item) for item in app.middleware if not isinstance(item, OpenTelemetryMiddleware)]
    seen = []

    @app.tool()
    async def fixture_tool(secret: str, context: ToolContext) -> str:
        seen.append(copy.deepcopy(context.request_context.meta))
        return secret

    runtime.install(app)
    assert [type(item) for item in app.middleware if item is not runtime] == original_boundaries
    meta = {"private_extension": {"value": "opaque-canary"}, "baggage": "private-canary"}

    async def run():
        async with Client(app, mode="legacy") as caller:
            result = await caller.call_tool("fixture_tool", {"secret": "caller-result-canary"}, meta=meta)
            assert result.content[0].text == "caller-result-canary"
    try:
        asyncio.run(run())
        assert seen[0]["private_extension"] == meta["private_extension"]
        assert meta == {"private_extension": {"value": "opaque-canary"}, "baggage": "private-canary"}
        assert len(spans.get_finished_spans()) == 1
        assert all(span.kind.name != "SERVER" for span in unsafe.get_finished_spans())
        assert sum(point.value for point in metric_points(reader)["testlookup.mcp.tool.calls"]) == 1
    finally:
        unsafe_provider.shutdown()


def test_real_sse_clients_keep_tool_context_and_principals_separate(observed):
    import uvicorn
    import client as api
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.server.auth.provider import AccessToken
    from mcp.server.auth.settings import AuthSettings
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.server.transport_security import TransportSecuritySettings

    runtime, spans, reader, logs = observed
    identities = {"synthetic-alice-credential", "synthetic-bob-credential"}

    class Verifier:
        async def verify_token(self, token):
            return AccessToken(token=token, client_id=token, subject=token, expires_at=int(time.time()) + 120,
                               scopes=[]) if token in identities else None

    app = MCPServer("telemetry-sse-fixture", token_verifier=Verifier(),
                    auth=AuthSettings(issuer_url="http://127.0.0.1/auth", resource_server_url=None))
    seen = []

    @app.tool()
    async def fixture_tool(secret: str, fail: bool = False) -> str:
        snapshot = (api._request_access_token(), dict(outgoing_operation_headers()))
        seen.append(snapshot)
        await asyncio.sleep(0.01)
        assert snapshot == (api._request_access_token(), outgoing_operation_headers())
        if fail:
            note_backend_error()
            raise ToolError("Synthetic backend refusal")
        return secret

    runtime.install(app)

    async def run():
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        asgi = runtime.wrap_lifecycle(app.sse_app(host="127.0.0.1", transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=["127.0.0.1:*"], allowed_origins=[])))
        server = uvicorn.Server(uvicorn.Config(asgi, log_level="error", log_config=None))
        serve = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            for _ in range(200):
                if server.started:
                    break
                await asyncio.sleep(0.01)
            assert server.started
            async with sse_client(f"http://127.0.0.1:{port}/sse", headers={"Authorization": "Bearer synthetic-alice-credential"}) as a, \
                       sse_client(f"http://127.0.0.1:{port}/sse", headers={"Authorization": "Bearer synthetic-bob-credential"}) as b:
                async with ClientSession(*a) as alice, ClientSession(*b) as bob:
                    await asyncio.gather(alice.initialize(), bob.initialize())
                    good, bad = await asyncio.gather(alice.call_tool("fixture_tool", {"secret": "alice-result-canary"}),
                        bob.call_tool("fixture_tool", {"secret": "bob-argument-canary", "fail": True}))
                    assert good.content[0].text == "alice-result-canary" and not good.is_error
                    assert bad.is_error
            assert outgoing_operation_headers() == {}
        finally:
            server.should_exit = True
            await serve
            listener.close()
    asyncio.run(run())
    assert {identity for identity, _headers in seen} == identities
    assert len({headers["X-Request-ID"] for _identity, headers in seen}) == 2
    assert len({headers["traceparent"] for _identity, headers in seen}) == 2
    assert {span.attributes["mcp.tool.outcome"] for span in spans.get_finished_spans()} == {"success", "request_error"}
    assert {point.attributes["transport"] for point in metric_points(reader)["testlookup.mcp.tool.calls"]} == {"sse"}
    assert "credential" not in repr([(entry.log_record.body, entry.log_record.attributes) for entry in logs.get_finished_logs()])


def test_export_failures_preserve_result_and_stderr_projection_drops_arbitrary_messages(observed):
    runtime, _spans, _reader, _logs = observed

    class Broken:
        def add(self, *_args):
            raise RuntimeError("private-exporter-canary")
        def emit(self, **_kwargs):
            raise RuntimeError("private-exporter-canary")
    runtime.start()
    runtime._calls, runtime.log_sink = Broken(), Broken()
    output = CallToolResult(content=[TextContent(type="text", text="caller-canary")])
    async def handler(_context):
        return output
    assert asyncio.run(runtime(ctx(), handler)) is output
    record = logging.LogRecord("mcp.private-canary", logging.ERROR, "", 1, "private %s", ("argument-canary",),
                               (RuntimeError, RuntimeError("exception-canary"), None))
    body = json.loads(DiagnosticFormatter().format(record))
    assert body == {"event": "mcp_diagnostic", "level": "ERROR", "category": "sdk", "code": "protocol_diagnostic"}


def test_explicit_lifespan_shutdown_has_one_total_budget_and_never_needs_atexit_thread_start():
    released = threading.Event()
    slow = SimpleNamespace(force_flush=lambda **_kwargs: released.wait(10), shutdown=lambda: None)
    runtime = ToolTelemetry(settings())
    runtime.own_provider(slow)
    sent = []

    async def app(_scope, receive, send):
        assert (await receive())["type"] == "lifespan.startup"
        await send({"type": "lifespan.startup.complete"})
        assert (await receive())["type"] == "lifespan.shutdown"
        await send({"type": "lifespan.shutdown.complete"})

    async def run():
        incoming = iter(({"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}))
        async def receive():
            return next(incoming)
        async def send(message):
            sent.append(message["type"])
        await runtime.wrap_lifecycle(app)({"type": "lifespan"}, receive, send)
    started = time.monotonic()
    try:
        asyncio.run(run())
        assert time.monotonic() - started < 2.5
        assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
        assert runtime._closed
    finally:
        released.set()


@pytest.mark.parametrize("endpoint", ["http://remote.example/v1/traces", "https://secret@collector/v1/traces",
    "https://collector/v1/logs", "https://collector/v1/traces?token=canary", "https://collector/v1/traces#canary"])
def test_invalid_trace_endpoints_are_rejected_without_egress(endpoint):
    with pytest.raises(ValueError):
        validated_endpoint(endpoint, "traces")


def test_owned_resource_is_stable_per_process_and_has_no_ambient_identity(monkeypatch):
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "private=resource-canary")
    first, second = resource("development"), resource("development")
    assert first.attributes["service.instance.id"] == second.attributes["service.instance.id"]
    assert first.attributes["testlookup.service.role"] == "mcp"
    assert "private" not in first.attributes
    # Public shutdown utility is safe for an unstarted or empty projection.
    shutdown_bounded(())
