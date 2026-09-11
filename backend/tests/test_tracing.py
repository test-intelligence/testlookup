"""Unit coverage for the optional OpenTelemetry startup boundary."""
import logging

import pytest

from app.core import tracing


@pytest.fixture(autouse=True)
def _reset_tracing_guard(monkeypatch):
    monkeypatch.setattr(tracing, "_initialised", False)


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("http://jaeger:4318", "http://jaeger:4318/v1/traces"),
        ("http://jaeger:4318/", "http://jaeger:4318/v1/traces"),
        ("http://collector:4318/v1/traces", "http://collector:4318/v1/traces"),
        ("http://collector:4318/v1/traces/", "http://collector:4318/v1/traces"),
    ],
)
def test_otlp_trace_endpoint_is_signal_qualified_once(configured, expected):
    assert tracing._otlp_trace_endpoint(configured) == expected


def test_exporter_configuration_failure_does_not_abort_startup(monkeypatch, caplog):
    from opentelemetry.exporter.otlp.proto.http import trace_exporter

    exporter_kwargs = {}

    def reject_endpoint(**kwargs):
        exporter_kwargs.update(kwargs)
        raise ValueError("collector URL rejected")

    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", reject_endpoint)
    monkeypatch.setattr(
        tracing,
        "_apply_auto_instrumentors",
        lambda: pytest.fail("instrumentors must not run after setup failure"),
    )

    with caplog.at_level(logging.WARNING, logger=tracing.__name__):
        tracing.setup_tracing(
            service_name="testlookup",
            service_version="test",
            environment="test",
            otlp_endpoint="http://collector:4318/v1/traces/",
        )

    assert tracing._initialised is False
    assert exporter_kwargs == {
        "endpoint": "http://collector:4318/v1/traces",
        "timeout": 10,
    }
    assert "OTEL tracing initialization failed; tracing disabled" in caplog.text
    assert "collector URL rejected" in caplog.text


# ── N22: no endpoint in production means no exporter, not stdout ───────────
#
# k8s/base/configmap.yaml shipped OTEL_ENABLED=true with the endpoint
# commented out, so every span was printed to stdout by ConsoleSpanExporter:
# ~90k backend log lines per 10 minutes on the homelab.


def _spy(monkeypatch):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import export

    consoles = []

    class SpyConsole(export.ConsoleSpanExporter):
        def __init__(self, *args, **kwargs):
            consoles.append(self)
            super().__init__(*args, **kwargs)

    providers = []
    monkeypatch.setattr(export, "ConsoleSpanExporter", SpyConsole)
    monkeypatch.setattr(trace, "set_tracer_provider", providers.append)
    monkeypatch.setattr(tracing, "_apply_auto_instrumentors", lambda: None)
    return consoles, providers


def _processors(provider):
    return provider._active_span_processor._span_processors


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_no_endpoint_outside_development_exports_nothing_and_warns_once(
    monkeypatch, caplog, environment
):
    consoles, providers = _spy(monkeypatch)
    with caplog.at_level(logging.WARNING, logger=tracing.__name__):
        for _ in range(2):
            tracing.setup_tracing("testlookup", "test", environment, otlp_endpoint=None)

    assert consoles == [], "spans must not be printed to stdout outside development"
    assert len(providers) == 1
    assert _processors(providers[0]) == ()
    warnings = [r for r in caplog.records if "OTEL_EXPORTER_OTLP_ENDPOINT" in r.getMessage()]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    assert tracing._initialised is True


def test_development_keeps_the_stdout_fallback(monkeypatch):
    consoles, providers = _spy(monkeypatch)
    tracing.setup_tracing("testlookup", "test", "development", otlp_endpoint=None)
    assert len(consoles) == 1
    assert len(_processors(providers[0])) == 1


def test_missing_otlp_package_in_production_does_not_fall_back_to_stdout(monkeypatch, caplog):
    import sys

    consoles, providers = _spy(monkeypatch)
    monkeypatch.setitem(sys.modules, "opentelemetry.exporter.otlp.proto.http.trace_exporter", None)
    with caplog.at_level(logging.WARNING, logger=tracing.__name__):
        tracing.setup_tracing("testlookup", "test", "production", otlp_endpoint="http://c:4318")
    assert consoles == []
    assert _processors(providers[0]) == ()
    assert "opentelemetry-exporter-otlp-proto-http not installed" in caplog.text


def test_an_endpoint_gets_the_otlp_exporter_and_no_console(monkeypatch):
    from opentelemetry.exporter.otlp.proto.http import trace_exporter

    consoles, providers = _spy(monkeypatch)
    made = []
    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", lambda **kw: made.append(kw) or object())
    from opentelemetry.sdk.trace import export

    monkeypatch.setattr(export, "BatchSpanProcessor", lambda exporter: _NullProcessor())
    tracing.setup_tracing("testlookup", "test", "production", otlp_endpoint="http://c:4318")
    assert made == [{"endpoint": "http://c:4318/v1/traces", "timeout": 10}]
    assert consoles == []
    assert len(_processors(providers[0])) == 1


class _NullProcessor:
    def on_start(self, span, parent_context=None):
        pass

    def on_end(self, span):
        pass

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        return True


def test_the_base_configmap_never_enables_tracing_without_an_endpoint():
    """The effective value in k8s/base/configmap.yaml, parsed — not grepped."""
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[2] / "k8s" / "base" / "configmap.yaml"
    if not path.exists():
        pytest.skip("k8s manifests not present (backend-only checkout)")
    docs = [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]
    data = next(d for d in docs if d["metadata"]["name"] == "testlookup-config")["data"]
    enabled = str(data.get("OTEL_ENABLED", "true")).lower() == "true"
    assert not (enabled and not data.get("OTEL_EXPORTER_OTLP_ENDPOINT")), (
        "OTEL_ENABLED=true with no OTEL_EXPORTER_OTLP_ENDPOINT"
    )
