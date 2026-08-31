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
