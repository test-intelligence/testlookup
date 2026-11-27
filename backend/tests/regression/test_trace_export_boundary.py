"""Every configured trace sink receives sanitized copies, including exception data."""
from __future__ import annotations

import io
import json

import pytest

from app.core import tracing

pytest.importorskip("opentelemetry.sdk.trace")

from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans  # noqa: E402
from opentelemetry.sdk.resources import Resource  # noqa: E402
from opentelemetry.sdk.trace import SpanLimits, TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import (  # noqa: E402
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402
from opentelemetry.trace import Link, SpanContext, SpanKind, TraceFlags, TraceState  # noqa: E402

pytestmark = pytest.mark.regression


@pytest.fixture
def sinks():
    provider = TracerProvider(resource=Resource({"service.name": "testlookup", "server.address": "10.42.0.7"}))
    safe, original = InMemorySpanExporter(), InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(tracing.redacting_span_exporter(safe)))
    # This test-only sink proves sanitization did not mutate the original SDK
    # span or the copies other processors observe. It never sends data anywhere.
    provider.add_span_processor(SimpleSpanProcessor(original))
    yield provider, safe, original
    provider.shutdown()


def serialized(spans):
    return "\n".join(span.to_json() for span in spans).encode() + encode_spans(spans).SerializeToString()


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("failed https://partner.test/items?unusual=fake-query-secret#fragment", "fake-query-secret"),
        ("failed https://partner.test/items?x=%66%61%6b%65%2dencoded-secret", "encoded-secret"),
        ("failed https://hooks.slack.com/services/Tfake/Bfake/fake-hook-path", "fake-hook-path"),
        ("failed https://fake-user:fake-pass@db.test/items", "fake-pass"),
        ("Authorization: Basic ZmFrZS11c2VyOmZha2UtcGFzcw==", "ZmFrZS11c2VyOmZha2UtcGFzcw=="),
        ('decision {"access_token": "tiny"}', "tiny"),
        ('decision {"private_key": "fake-opaque-private-key"}', "fake-opaque-private-key"),
        ("Bearer tiny", "tiny"),
        ("rejected eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.ZmFrZQ", "eyJhbGciOiJIUzI1NiJ9"),
        ("rejected tl_fake_key_abcdefghijklmnop", "tl_fake_key_abcdefghijklmnop"),
        ("failed https://[invalid/fake-malformed-secret?x=y", "fake-malformed-secret"),
        ("failed //fake-user:fake-relative-pass@partner.test/path", "fake-relative-pass"),
    ],
)
def test_exception_status_events_and_custom_text_are_scrubbed_without_mutation(sinks, text, secret):
    provider, safe, original = sinks
    with pytest.raises(RuntimeError):
        with provider.get_tracer("test.workflow").start_as_current_span("agent.step") as span:
            span.set_attribute("route.rationale", text)
            span.set_attribute("decisions.notes", ["legitimate decision", text])
            span.add_event("decision", {"custom.text": text})
            raise RuntimeError(text)

    emitted = safe.get_finished_spans()
    raw = original.get_finished_spans()
    assert secret.encode() not in serialized(emitted)
    assert secret in raw[0].to_json(), "the trigger must really reach the original finished span"
    assert emitted[0] is not raw[0]
    assert emitted[0].events[-1].attributes["exception.type"] == "RuntimeError"
    assert emitted[0].status.status_code == raw[0].status.status_code
    assert emitted[0].context == raw[0].context
    assert emitted[0].events[-1].timestamp == raw[0].events[-1].timestamp


@pytest.mark.parametrize("depth", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("key", ["access_token", "private-key", "vendor.client_secret"])
def test_nested_json_credentials_preserve_surrounding_diagnostic_text(sinks, depth, key):
    provider, safe, original = sinks
    text = json.dumps({key: 'fake-opaque-secret with "quotes" and a trailing slash\\', "legitimate": "keep-me"})
    for _ in range(depth):
        text = json.dumps(text)
    text = "operation-before " + text + " operation-after"
    with pytest.raises(ValueError):
        with provider.get_tracer("test").start_as_current_span("known-operation") as span:
            span.set_attribute("route.rationale", text)
            span.add_event("decision", {"custom.text": text})
            raise ValueError(text)
    payload = serialized(safe.get_finished_spans())
    assert b"fake-opaque-secret" not in payload
    for legitimate in (b"operation-before", b"operation-after", b"keep-me"):
        assert legitimate in payload
    assert b"fake-opaque-secret" in serialized(original.get_finished_spans())


def test_structured_secrets_and_payloads_are_redacted_but_operational_values_survive(sinks):
    provider, safe, original = sinks
    with provider.get_tracer("test.workflow").start_as_current_span("agent.step") as span:
        span.set_attribute("http.request.header.authorization", "opaque-short-secret")
        span.set_attribute("testlookup.vendor.api_key", "another-opaque-secret")
        span.set_attribute("gen_ai.input.messages", "private prompt with no secret marker")
        span.set_attribute("token_count", 1234567890)
        span.set_attribute("password_changed", True)
        span.set_attribute("testlookup.pipeline_run_id", "05982379-5519-4289-a773-123456789012")
        span.set_attribute("safe.text", "10.42.0.7:6379 processed 1234567890 bytes")
    snapshot = safe.get_finished_spans()[0]
    assert snapshot.attributes["http.request.header.authorization"] == tracing.REDACTED
    assert snapshot.attributes["testlookup.vendor.api_key"] == tracing.REDACTED
    assert snapshot.attributes["gen_ai.input.messages"] == tracing.REDACTED
    for key in ("token_count", "password_changed", "testlookup.pipeline_run_id", "safe.text"):
        assert snapshot.attributes[key] == original.get_finished_spans()[0].attributes[key]
    assert snapshot.resource.attributes["server.address"] == "10.42.0.7"


def test_url_query_and_full_url_sequence_forms_cannot_bypass_sanitization(sinks):
    provider, safe, _ = sinks
    with provider.get_tracer("test").start_as_current_span("operation") as span:
        span.set_attribute("url.query", ["unusual=fake-array-secret", "x=1"])
        span.set_attribute("url.full", ["//fake-user:fake-password@partner.test/path?x=fake-array-secret"])
    payload = serialized(safe.get_finished_spans())
    assert b"fake-array-secret" not in payload and b"fake-password" not in payload
    assert b"partner.test/path" in payload


def test_oversized_text_is_explicitly_omitted_without_exporting_a_partial_secret(sinks):
    provider, safe, _ = sinks
    with provider.get_tracer("test").start_as_current_span("operation") as span:
        span.set_attribute("custom.text", "x" * 100_000 + "fake-trailing-secret")
    payload = serialized(safe.get_finished_spans())
    assert b"fake-trailing-secret" not in payload
    assert b"REDACTED_OVERSIZED_TRACE_TEXT" in payload


def test_loss_accounting_is_preserved_for_span_event_and_link():
    from opentelemetry.attributes import BoundedAttributes

    provider = TracerProvider(span_limits=SpanLimits(max_attributes=1, max_events=1, max_links=1))
    safe, original = InMemorySpanExporter(), InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(tracing.redacting_span_exporter(safe)))
    provider.add_span_processor(SimpleSpanProcessor(original))
    attrs = BoundedAttributes(maxlen=1, attributes={"first": "safe", "second": "safe"})
    try:
        with provider.get_tracer("test").start_as_current_span("operation", links=[Link(SpanContext(123, 456, True), attrs)]) as span:
            span.set_attribute("first", "safe")
            span.set_attribute("second", "safe")
            span.add_event("first")
            span.add_event("second", attrs)
        snapshot, raw = safe.get_finished_spans()[0], original.get_finished_spans()[0]
        assert snapshot.dropped_attributes == raw.dropped_attributes == 1
        assert snapshot.dropped_events == raw.dropped_events == 1
        assert snapshot.dropped_links == raw.dropped_links
        assert snapshot.events[0].dropped_attributes == raw.events[0].dropped_attributes
        assert snapshot.links[0].dropped_attributes == raw.links[0].dropped_attributes
    finally:
        provider.shutdown()


def test_resource_scope_link_and_span_name_copies_are_sanitized():
    secret = "fake-metadata-secret"
    address = f"https://partner.test/path?ticket={secret}"
    provider = TracerProvider(resource=Resource({"service.name": "testlookup", "custom.reference": address}, address))
    safe, original = InMemorySpanExporter(), InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(tracing.redacting_span_exporter(safe)))
    provider.add_span_processor(SimpleSpanProcessor(original))
    linked_context = SpanContext(
        123, 456, is_remote=True, trace_flags=TraceFlags(1),
        trace_state=TraceState([("vendor", "legitimate"), ("token", secret)]),
    )
    try:
        tracer = provider.get_tracer(address, "1.2.3", schema_url=address, attributes={"custom.text": address})
        with tracer.start_as_current_span(address, kind=SpanKind.CLIENT, links=[Link(linked_context, {"custom.text": address})]) as span:
            span.add_event(address, {"custom.text": address})
        snapshot, raw = safe.get_finished_spans()[0], original.get_finished_spans()[0]
        assert secret.encode() not in serialized([snapshot])
        assert secret.encode() in serialized([raw])
        assert snapshot.kind is SpanKind.CLIENT
        assert snapshot.context == raw.context
        assert snapshot.start_time == raw.start_time and snapshot.end_time == raw.end_time
        assert snapshot.instrumentation_scope.version == "1.2.3"
        assert snapshot.links[0].context.trace_id == 123
        assert snapshot.links[0].context.trace_state["vendor"] == "legitimate"
        assert snapshot.links[0].context.trace_state["token"] == tracing.REDACTED
    finally:
        provider.shutdown()


@pytest.mark.parametrize("environment", ["development", "production"])
@pytest.mark.parametrize("encoded_json", [False, True])
def test_configured_console_and_otlp_paths_serialize_only_sanitized_spans(monkeypatch, environment, encoded_json):
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http import trace_exporter
    from opentelemetry.sdk.trace import export

    stream = io.StringIO()
    otlp_payloads = []
    providers = []

    class RecordingOTLP(SpanExporter):
        def export(self, spans):
            otlp_payloads.append(encode_spans(spans).SerializeToString())
            return SpanExportResult.SUCCESS

    monkeypatch.setattr(trace, "set_tracer_provider", providers.append)
    monkeypatch.setattr(tracing, "_initialised", False)
    monkeypatch.setattr(tracing, "_apply_auto_instrumentors", lambda: None)
    monkeypatch.setattr(export, "BatchSpanProcessor", SimpleSpanProcessor)
    monkeypatch.setattr(export, "ConsoleSpanExporter", lambda: ConsoleSpanExporter(out=stream))
    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", lambda **kwargs: RecordingOTLP())
    tracing.setup_tracing("testlookup", "test", environment, "http://collector.test:4318" if environment == "production" else None)
    try:
        message = "failed https://partner.test/items?token=fake-sink-secret"
        if encoded_json:
            message = "operation-before " + json.dumps(json.dumps({"access_token": "fake-sink-secret", "legitimate": "keep-me"})) + " operation-after"
        with pytest.raises(ValueError):
            with providers[0].get_tracer("test.export").start_as_current_span("known-operation"):
                raise ValueError(message)
        payload = b"".join(otlp_payloads) + stream.getvalue().encode()
        assert payload and b"fake-sink-secret" not in payload
        assert b"known-operation" in payload and b"ValueError" in payload
        if encoded_json:
            assert b"operation-before" in payload and b"operation-after" in payload and b"keep-me" in payload
    finally:
        providers[0].shutdown()


def test_sanitization_failure_drops_only_unsafe_span_without_raw_fallback(monkeypatch, caplog, sinks):
    provider, safe, original = sinks
    sanitizer = tracing._sanitized_span

    def fail_one(span):
        if span.name == "unsafe":
            raise ValueError("fake-failure-secret")
        return sanitizer(span)

    monkeypatch.setattr(tracing, "_sanitized_span", fail_one)
    with provider.get_tracer("test").start_as_current_span("unsafe") as span:
        span.set_attribute("custom.text", "fake-failure-secret")
    with provider.get_tracer("test").start_as_current_span("ordinary"):
        pass
    assert [span.name for span in safe.get_finished_spans()] == ["ordinary"]
    assert len(original.get_finished_spans()) == 2
    assert "sanitization failed" in caplog.text and "fake-failure-secret" not in caplog.text


def test_exporter_lifecycle_is_forwarded():
    calls = []

    class Exporter(SpanExporter):
        def shutdown(self):
            calls.append("shutdown")

        def force_flush(self, timeout_millis=30000):
            calls.append(timeout_millis)
            return True

    wrapped = tracing.redacting_span_exporter(Exporter())
    assert wrapped.force_flush(timeout_millis=123)
    wrapped.shutdown()
    assert calls == [123, "shutdown"]
