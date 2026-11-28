"""Bounded central MCP operation projection for the pinned SDK's middleware API."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
import uuid
from collections.abc import Mapping
from typing import Any
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import CallToolResult
from opentelemetry import trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.trace import SpanLimits, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import SpanKind, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from pydantic import ValidationError

from telemetry_context import operation_context, validated_request_id, validated_traceparent
from telemetry_resources import exporter_session, resource, shutdown_bounded, validated_endpoint

logger = logging.getLogger("testlookup.mcp.telemetry")
OUTCOMES = frozenset({"success", "tool_error", "request_error", "cancelled", "internal_error"})
MAX_KNOWN_TOOLS = 256
METRIC_ATTRIBUTES = {"tool", "outcome", "transport"}
DURATION_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)
_TOOL_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")


class ToolTelemetry:
    def __init__(self, settings: Any, *, tracer_provider: Any = None, meter_provider: Any = None, log_sink: Any = None):
        self.settings = settings
        self.tracer_provider = tracer_provider
        self.meter_provider = meter_provider
        self.log_sink = log_sink
        self.log_provider: Any = None
        self.server: Any = None
        self.known_tools: frozenset[str] | None = None
        self._started = False
        self._closed = False
        self._owned: list[Any] = []
        self._tracer: Any = trace.NoOpTracer()
        self._calls: Any = None
        self._duration: Any = None

    def install(self, server: Any) -> None:
        """Replace only the SDK's unsafe projection through its public chain."""
        defaults = [item for item in server.middleware if
                    type(item).__module__ == "mcp.server._otel" and type(item).__name__ == "OpenTelemetryMiddleware"]
        if len(defaults) != 1:
            raise RuntimeError("MCP telemetry requires the verified MCP2.3 middleware chain")
        self.server = server
        server.middleware[:] = [self if item is defaults[0] else item for item in server.middleware]

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        identity = resource(self.settings.telemetry_environment)
        endpoint = self.settings.otlp_traces_endpoint
        if self.tracer_provider is None and endpoint:
            try:
                exporter = OTLPSpanExporter(endpoint=validated_endpoint(endpoint, "traces"), timeout=2,
                    max_request_size=65_536, headers={}, session=exporter_session())
                provider = TracerProvider(resource=identity, sampler=ParentBased(TraceIdRatioBased(self.settings.telemetry_trace_sample_ratio)),
                    span_limits=SpanLimits(max_attributes=8, max_events=0, max_links=0), shutdown_on_exit=False)
                provider.add_span_processor(BatchSpanProcessor(exporter, max_queue_size=512,
                    max_export_batch_size=64, schedule_delay_millis=1000, export_timeout_millis=2000))
                self.tracer_provider = provider
                self._owned.append(provider)
            except Exception:  # noqa: BLE001 — telemetry cannot prevent tool execution
                logger.warning("MCP trace exporter initialization failed", extra={"event_code": "exporter_init_failed"})
        if self.tracer_provider is not None:
            self._tracer = self.tracer_provider.get_tracer("testlookup.mcp")
        endpoint = self.settings.otlp_metrics_endpoint
        if self.meter_provider is None and endpoint:
            try:
                metric_exporter = OTLPMetricExporter(endpoint=validated_endpoint(endpoint, "metrics"), timeout=2,
                    max_request_size=65_536, max_export_batch_size=128, headers={}, session=exporter_session())
                reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=30_000, export_timeout_millis=2000)
                capacity = (MAX_KNOWN_TOOLS + 1) * len(OUTCOMES) * 2
                self.meter_provider = MeterProvider(resource=identity, metric_readers=[reader], shutdown_on_exit=False, views=[
                    View(instrument_name="testlookup.mcp.tool.calls", attribute_keys=METRIC_ATTRIBUTES, aggregation_cardinality_limit=capacity),
                    View(instrument_name="testlookup.mcp.tool.duration", attribute_keys=METRIC_ATTRIBUTES,
                         aggregation=ExplicitBucketHistogramAggregation(DURATION_BUCKETS), aggregation_cardinality_limit=capacity),
                ])
                self._owned.append(self.meter_provider)
            except Exception:  # noqa: BLE001
                logger.warning("MCP metric exporter initialization failed", extra={"event_code": "exporter_init_failed"})
        if self.meter_provider is not None:
            meter = self.meter_provider.get_meter("testlookup.mcp")
            self._calls = meter.create_counter("testlookup.mcp.tool.calls", unit="1", description="Completed MCP tool operations")
            self._duration = meter.create_histogram("testlookup.mcp.tool.duration", unit="s", description="MCP tool completion duration")
        if self.log_sink is None and self.settings.otlp_logs_endpoint:
            from operational_logs import create_operational_logger

            try:
                self.log_provider, self.log_sink = create_operational_logger(self.settings.otlp_logs_endpoint, self.settings.telemetry_environment)
                self._owned.append(self.log_provider)
            except Exception:  # noqa: BLE001
                logger.warning("MCP log exporter initialization failed", extra={"event_code": "exporter_init_failed"})

    def shutdown(self) -> None:
        if self._closed or not self._owned:
            return
        self._closed = True
        shutdown_bounded(self._owned)

    def own_provider(self, provider: Any) -> None:
        self._owned.append(provider)

    def wrap_lifecycle(self, app: ASGIApp) -> ASGIApp:
        async def observed(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "lifespan":
                await app(scope, receive, send)
                return

            async def sent(message: Message) -> None:
                if message["type"] == "lifespan.startup.complete":
                    self.start()
                elif message["type"] == "lifespan.shutdown.complete":
                    self.shutdown()
                await send(message)

            await app(scope, receive, sent)
        return observed

    async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
        if ctx.method != "tools/call":
            return await call_next(ctx)
        self.start()
        if self.known_tools is None:
            try:
                tools = await self.server.list_tools()
                names = sorted({item.name for item in tools if isinstance(item.name, str) and _TOOL_NAME.fullmatch(item.name)})
            except Exception:  # noqa: BLE001 — inventory collection cannot change tool authorization or execution
                names = []
                logger.warning("MCP tool projection inventory failed", extra={"event_code": "tool_inventory_failed"})
            self.known_tools = frozenset(names[:MAX_KNOWN_TOOLS])
            if len(names) > MAX_KNOWN_TOOLS:
                logger.warning("MCP tool inventory exceeds projection limit", extra={"event_code": "tool_inventory_truncated"})
        raw_name = ctx.params.get("name") if isinstance(ctx.params, Mapping) else None
        tool = raw_name if isinstance(raw_name, str) and raw_name in self.known_tools else "other"
        transport = "stdio" if ctx.request is None else "sse"
        headers = getattr(ctx.request, "headers", {}) if ctx.request is not None else {}
        request_id = validated_request_id(headers.get("X-Request-ID")) or str(uuid.uuid4())
        meta = ctx.meta if isinstance(ctx.meta, Mapping) else {}
        parent = validated_traceparent(meta.get("traceparent")) or validated_traceparent(headers.get("traceparent"))
        context = TraceContextTextMapPropagator().extract({"traceparent": parent}, context=Context()) if parent else Context()
        attributes = {"rpc.system": "mcp", "rpc.method": "tools/call", "mcp.tool.name": tool, "mcp.transport": transport}
        outcome = "internal_error"
        started = time.monotonic()
        with self._tracer.start_as_current_span("mcp.tool", context=context, kind=SpanKind.SERVER,
                attributes=attributes, record_exception=False, set_status_on_exception=False) as span:
            span_context = span.get_span_context()
            outgoing_parent = (f"00-{span_context.trace_id:032x}-{span_context.span_id:016x}-{int(span_context.trace_flags):02x}"
                               if span_context.is_valid else None)
            with operation_context(request_id, outgoing_parent) as operation:
                try:
                    result = await call_next(ctx)
                    tool_error = (isinstance(result, CallToolResult) and result.is_error) or (isinstance(result, dict) and result.get("isError") is True)
                    outcome = ("request_error" if operation.backend_failed or (tool_error and operation.backend_error_seen)
                               else "tool_error" if tool_error else "success")
                    return result
                except asyncio.CancelledError:
                    outcome = "cancelled"
                    raise
                except (MCPError, ValidationError):
                    outcome = "request_error"
                    raise
                finally:
                    span.set_attribute("mcp.tool.outcome", outcome)
                    if outcome != "success":
                        span.set_status(StatusCode.ERROR)
                    duration = max(0.0, time.monotonic() - started)
                    if not math.isfinite(duration):
                        duration = 0.0
                    labels = {"tool": tool, "outcome": outcome, "transport": transport}
                    try:
                        if self._calls is not None:
                            self._calls.add(1, labels)
                            self._duration.record(duration, labels)
                    except Exception:  # noqa: BLE001
                        logger.warning("MCP metric projection failed", extra={"event_code": "metric_record_failed"})
                    values = {**labels, "duration_ms": min(round(duration * 1000, 2), 600_000),
                              "request_id": hashlib.sha256(request_id.encode("ascii")).hexdigest()}
                    diagnostic_values = dict(values)
                    if span_context.is_valid:
                        diagnostic_values.update(trace_id=f"{span_context.trace_id:032x}", span_id=f"{span_context.span_id:016x}")
                    logger.info("MCP tool completed", extra={"event_code": "tool_execution", **diagnostic_values})
                    if self.log_sink is not None:
                        try:
                            self.log_sink.emit(timestamp=time.time_ns(), event_name="mcp_tool_execution",
                                severity_number=SeverityNumber.INFO if outcome == "success" else SeverityNumber.WARN,
                                severity_text="INFO" if outcome == "success" else "WARN",
                                body=json.dumps({"event": "mcp_tool_execution", **values}, separators=(",", ":")), attributes=values)
                        except Exception:  # noqa: BLE001
                            logger.warning("MCP log projection failed", extra={"event_code": "log_record_failed"})
