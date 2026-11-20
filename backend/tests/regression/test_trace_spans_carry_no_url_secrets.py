"""Trace spans carry no query string and no webhook path.

Spans do not pass through the log redaction: in development they are printed
to stdout, elsewhere they go to whatever collector is configured. A request URL
can carry a credential in its query string (the live-stream route takes the
access token there; download routes take a one-time ticket), and an incoming
webhook URL (Slack, Teams, Discord, Google Chat) is a secret in its path.

So a span processor, registered on the provider BEFORE any exporter, replaces
the query string in ``http.url``, ``url.full``, ``url.query`` and
``http.target`` and the path of a known webhook host. The path of every other
URL is kept: a trace still says which route was called.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI

from app.core import tracing

pytest.importorskip("opentelemetry.sdk.trace")

from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

pytestmark = pytest.mark.regression

TOKEN = "fake-access-token-abc123"
HOOK_PATH = "/services/T0FAKE/B0FAKE/fakewebhooksecret"


@pytest.fixture
def traced():
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    tracing.add_url_redaction(provider)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield provider, exporter
    provider.shutdown()


def _exported(exporter) -> str:
    """Everything an exporter would write: the JSON the console exporter prints."""
    spans = exporter.get_finished_spans()
    assert spans, "no span was produced, so this test proves nothing"
    return "\n".join(span.to_json() for span in spans)


def _span_with(provider, exporter, attributes: dict) -> dict:
    with provider.get_tracer("test").start_as_current_span("unit") as span:
        for key, value in attributes.items():
            span.set_attribute(key, value)
    return dict(exporter.get_finished_spans()[-1].attributes)


# ── The four attributes ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("http.url", f"http://api.test/api/v1/stream/sse/p1?token={TOKEN}", "http://api.test/api/v1/stream/sse/p1?[REDACTED]"),
        ("url.full", f"https://api.test:8443/a/b?ticket={TOKEN}#frag", "https://api.test:8443/a/b?[REDACTED]"),
        ("url.query", f"token={TOKEN}&x=1", "[REDACTED]"),
        ("http.target", f"/api/v1/stream/sse/p1?token={TOKEN}", "/api/v1/stream/sse/p1?[REDACTED]"),
    ],
)
def test_the_query_string_is_replaced_and_the_path_kept(traced, key, value, expected):
    provider, exporter = traced
    attributes = _span_with(provider, exporter, {key: value, "http.method": "GET"})
    assert attributes[key] == expected
    assert attributes["http.method"] == "GET"
    assert TOKEN not in _exported(exporter)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("http.url", "http://api.test/api/v1/projects"),
        ("url.full", "https://api.test/health/ready"),
        ("url.query", ""),
        ("http.target", "/api/v1/projects"),
    ],
)
def test_a_url_without_a_query_is_left_alone(traced, key, value):
    provider, exporter = traced
    assert _span_with(provider, exporter, {key: value})[key] == value


def test_credentials_in_the_address_are_dropped(traced):
    provider, exporter = traced
    address = "https://" + "fakeuser" + ":" + "fakepass" + "@" + "db.test/path?x=1"
    attributes = _span_with(provider, exporter, {"url.full": address})
    assert attributes["url.full"] == "https://db.test/path?[REDACTED]"
    assert "fakepass" not in _exported(exporter)


# ── Webhook hosts: the path is the secret ────────────────────────────────────


@pytest.mark.parametrize(
    "host",
    ["hooks.slack.com", "tenant.webhook.office.com", "outlook.office.com", "discord.com", "chat.googleapis.com"],
)
def test_a_webhook_url_keeps_only_its_host(traced, host):
    provider, exporter = traced
    attributes = _span_with(provider, exporter, {"url.full": f"https://{host}{HOOK_PATH}?key=k"})
    assert attributes["url.full"] == f"https://{host}/[REDACTED]"
    assert "fakewebhooksecret" not in _exported(exporter)


def test_a_webhook_span_hides_the_path_attributes_too(traced):
    provider, exporter = traced
    attributes = _span_with(
        provider,
        exporter,
        {"server.address": "hooks.slack.com", "url.path": HOOK_PATH, "http.target": HOOK_PATH + "?a=1"},
    )
    assert attributes["url.path"] == "/[REDACTED]"
    assert attributes["http.target"] == "/[REDACTED]"
    assert "fakewebhooksecret" not in _exported(exporter)


def test_a_host_that_only_looks_like_a_webhook_host_keeps_its_path(traced):
    provider, exporter = traced
    attributes = _span_with(provider, exporter, {"url.full": "https://nothooks.slack.com.example.test/a/b"})
    assert attributes["url.full"] == "https://nothooks.slack.com.example.test/a/b"


def test_the_webhook_hosts_cover_the_export_scrubber_list():
    """One list must not fall behind the other."""
    from app.services.project_export_scrub import SECRET_PATH_HOSTS

    assert set(SECRET_PATH_HOSTS) <= set(tracing.WEBHOOK_HOSTS)


# ── Through the real instrumentors ───────────────────────────────────────────


def test_a_server_span_and_a_client_span_carry_no_query(traced):
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from starlette.testclient import TestClient

    provider, exporter = traced
    app = FastAPI()

    @app.get("/api/v1/stream/sse/{project_id}")
    def sse(project_id: str, token: str):
        return {"project": project_id}

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    try:
        with TestClient(app) as client:
            response = client.get(f"/api/v1/stream/sse/p1?token={TOKEN}")
        assert response.status_code == 200
    finally:
        FastAPIInstrumentor.uninstrument_app(app)

    outbound = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    HTTPXClientInstrumentor.instrument_client(outbound, tracer_provider=provider)
    try:
        outbound.get(f"https://partner.test/v1/items?api_key={TOKEN}")
        outbound.post(f"https://hooks.slack.com{HOOK_PATH}")
    finally:
        HTTPXClientInstrumentor.uninstrument_client(outbound)
        outbound.close()

    written = _exported(exporter)
    assert TOKEN not in written and "fakewebhooksecret" not in written
    # Positive: the spans are still produced and still say which route was called.
    assert "/api/v1/stream/sse/" in written
    assert "partner.test/v1/items" in written
    assert "hooks.slack.com" in written
    kinds = {json.loads(span.to_json())["kind"] for span in exporter.get_finished_spans()}
    assert {"SpanKind.SERVER", "SpanKind.CLIENT"} <= kinds


# ── Registered before the exporters ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("environment", "endpoint"),
    [("development", None), ("production", "http://collector.test:4318")],
)
def test_setup_registers_the_redaction_before_any_exporter(monkeypatch, environment, endpoint):
    from opentelemetry import trace

    providers: list = []
    monkeypatch.setattr(tracing, "_initialised", False)
    monkeypatch.setattr(trace, "set_tracer_provider", providers.append)
    monkeypatch.setattr(tracing, "_apply_auto_instrumentors", lambda: None)

    tracing.setup_tracing("testlookup", "test", environment, otlp_endpoint=endpoint)
    try:
        processors = providers[0]._active_span_processor._span_processors
        assert tracing.is_url_redaction(processors[0]), "the redaction must run before every exporter"
        assert sum(1 for p in processors if tracing.is_url_redaction(p)) == 1
        assert len(processors) == 2, "one redaction, then one exporter"
    finally:
        providers[0].shutdown()
        monkeypatch.setattr(tracing, "_initialised", False)
