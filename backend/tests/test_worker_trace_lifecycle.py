"""Publication, standalone Beat and bounded process-exit trace boundaries."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import Mock

import pytest
from celery import Celery
from celery.beat import Service
from opentelemetry import context, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from app.core import tracing as core
from app.core.config import settings
from app.worker import tracing


@pytest.fixture
def spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(core.redacting_span_exporter(exporter)))
    monkeypatch.setattr(tracing, "_tracer", lambda: provider.get_tracer("synthetic.worker"))
    monkeypatch.setattr(settings, "OTEL_ENABLED", True)
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    token = context.attach(context.Context())
    yield exporter
    context.detach(token)
    provider.shutdown()


@pytest.mark.parametrize("failed", [False, True])
def test_send_task_ends_producer_and_restores_context_on_broker_result(spans, monkeypatch, failed):
    published = []

    def send(_app, *args, **kwargs):
        headers = kwargs["headers"]
        tracing.propagate_traceparent(headers=headers)
        published.append(dict(headers))
        assert trace.get_current_span().get_span_context().is_valid
        if failed:
            raise OSError("synthetic-private-broker-message")
        return "accepted"

    monkeypatch.setattr(Celery, "send_task", send)
    app = tracing.TracingCelery("synthetic", set_as_current=False)
    header = "00-" + "1" * 32 + "-" + "2" * 16 + "-01"
    with tracing._tracer().start_as_current_span("api-parent") as parent:
        def operation():
            return app.send_task("synthetic-private-task", args=("private-payload",),
                                 queue="agent_children", headers={"traceparent": header,
                                 "baggage": "private", "tracestate": "private"})
        if failed:
            with pytest.raises(OSError, match="synthetic-private-broker-message"):
                operation()
        else:
            assert operation() == "accepted"
        assert trace.get_current_span() is parent
    producer, parent_span = spans.get_finished_spans()
    assert producer.name == "celery.publish" and producer.kind == SpanKind.PRODUCER
    assert producer.parent.span_id == parent_span.context.span_id
    assert published == [{"traceparent": f"00-{producer.context.trace_id:032x}-{producer.context.span_id:016x}-01"}]
    assert producer.status.status_code == (StatusCode.ERROR if failed else StatusCode.UNSET)
    assert not producer.events and producer.status.description is None
    assert dict(producer.attributes) == {"messaging.system": "celery", "messaging.operation.type": "send",
                                         "messaging.destination.name": "agent_children"}
    assert "private" not in str(producer.to_json())


def test_unrelated_root_publications_and_explicit_parent_do_not_leak(spans, monkeypatch):
    monkeypatch.setattr(Celery, "send_task", lambda *args, **kwargs: None)
    app = tracing.TracingCelery("synthetic", set_as_current=False)
    app.send_task("private", headers={"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"})
    app.send_task("private")
    first, second = spans.get_finished_spans()
    assert first.parent.is_remote and first.context.trace_id == int("1" * 32, 16)
    assert second.parent is None and second.context.trace_id != first.context.trace_id
    assert not trace.get_current_span().get_span_context().is_valid


@pytest.mark.parametrize("failed", [False, True])
def test_standalone_beat_initializes_before_schedule_and_drains_even_on_error(monkeypatch, failed):
    events = []
    monkeypatch.setattr(tracing, "initialize_beat_tracing", lambda: events.append("initialize"))
    monkeypatch.setattr(tracing, "shutdown_worker_tracing", lambda: events.append("drain"))

    def start(_service, **kwargs):
        events.append("schedule")
        if failed:
            raise RuntimeError("synthetic")

    monkeypatch.setattr(Service, "start", start)
    app = tracing.TracingCelery("synthetic", set_as_current=False)
    service = app.Beat.Service(app)
    if failed:
        with pytest.raises(RuntimeError):
            service.start()
    else:
        service.start()
    assert events == ["initialize", "schedule", "drain"]


def test_beat_initialization_requires_opt_in_and_uses_beat_resource(monkeypatch):
    setup = Mock(return_value=True)
    monkeypatch.setattr(core, "setup_tracing", setup)
    monkeypatch.setattr(settings, "OTEL_ENABLED", False)
    tracing.initialize_beat_tracing()
    setup.assert_not_called()
    monkeypatch.setattr(settings, "OTEL_ENABLED", True)
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    tracing.initialize_beat_tracing()
    assert setup.call_args.kwargs == {"service_role": "beat"}


def test_beat_setup_and_drain_failures_preserve_scheduler_behavior(monkeypatch):
    monkeypatch.setattr(settings, "OTEL_ENABLED", True)
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setattr(core, "setup_tracing", Mock(side_effect=ValueError("synthetic-private")))
    monkeypatch.setattr(core, "shutdown_tracing", Mock(side_effect=RuntimeError("synthetic-private")))
    scheduled = Mock(return_value="scheduled")
    monkeypatch.setattr(Service, "start", scheduled)
    app = tracing.TracingCelery("synthetic", set_as_current=False)
    assert app.Beat.Service(app).start() == "scheduled"
    scheduled.assert_called_once()


def test_production_app_owns_beat_service_and_child_drain_signal():
    from celery.signals import worker_process_shutdown
    from app.worker.celery_app import celery_app

    assert isinstance(celery_app, tracing.TracingCelery)
    assert celery_app.Beat.Service is tracing.TracingBeatService
    assert tracing.shutdown_worker_tracing in worker_process_shutdown._live_receivers(None)


def test_shutdown_has_one_deadline_and_does_not_shutdown_inherited_provider(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    provider = Mock()
    provider.shutdown.side_effect = lambda: (entered.set(), release.wait(5))
    monkeypatch.setattr(core, "_provider", provider)
    monkeypatch.setattr(core, "_provider_pid", os.getpid() + 1)
    monkeypatch.setattr(core, "_shutdown_thread", None)
    monkeypatch.setattr(core, "_shutdown_requested", None)
    monkeypatch.setattr(core, "_shutdown_deadline", 0.0)
    assert core.shutdown_tracing(50)
    provider.shutdown.assert_not_called()
    monkeypatch.setattr(core, "_provider_pid", os.getpid())
    core._prepare_provider_shutdown(provider)
    started = time.monotonic()
    try:
        assert core.shutdown_tracing(50) is False
        assert entered.is_set()
        assert core.shutdown_tracing(5000) is False
        assert time.monotonic() - started < 0.3
        assert core._shutdown_thread.daemon
        assert provider.shutdown.call_count == 1
    finally:
        release.set()
        core._shutdown_thread.join(1)


def test_normal_interpreter_exit_drains_without_starting_atexit_thread(tmp_path):
    # Python >=3.12 rejects Thread.start inside atexit. A real SDK batch and
    # process exit must export successfully through the prestarted waiter.
    receipt = tmp_path / "exported"
    code = '''
import sys
from pathlib import Path
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from app.core import tracing
class Sink(SpanExporter):
    def export(self, spans):
        Path(sys.argv[1]).write_text(str(len(spans)))
        return SpanExportResult.SUCCESS
    def shutdown(self):
        pass
provider = TracerProvider(shutdown_on_exit=False)
provider.add_span_processor(BatchSpanProcessor(tracing.redacting_span_exporter(Sink()), schedule_delay_millis=60000))
tracing._prepare_provider_shutdown(provider)
import atexit
atexit.register(tracing.shutdown_tracing)
with provider.get_tracer("synthetic").start_as_current_span("celery.publish"):
    pass
'''
    result = subprocess.run([sys.executable, "-c", code, str(receipt)], cwd=Path(core.__file__).resolve().parents[2],
                            capture_output=True, text=True, timeout=6)
    assert result.returncode == 0 and not result.stderr
    assert receipt.read_text() == "1"


def test_failed_child_initialization_can_retry_without_false_ready_pid(monkeypatch):
    monkeypatch.setattr(core, "setup_tracing", Mock(side_effect=[False, True]))
    monkeypatch.setattr(tracing, "_READY_PID", None)
    monkeypatch.setattr(settings, "OTEL_ENABLED", True)
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    tracing.initialize_worker_tracing()
    assert tracing._READY_PID is None
    tracing.initialize_worker_tracing()
    assert tracing._READY_PID == os.getpid()
