"""Execution spans preserve caller context and export no task/header payload."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from opentelemetry import context, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app.core.config import settings
from app.worker import tracing

TRACE = "123456789abcdef0" * 2
PARENT = "123456789abcdef0"
HEADER = f"00-{TRACE}-{PARENT}-01"


@pytest.fixture
def proof(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("synthetic.worker-proof")
    monkeypatch.setattr(settings, "OTEL_ENABLED", True)
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    initialize = Mock()
    monkeypatch.setattr(tracing, "initialize_worker_tracing", initialize)
    monkeypatch.setattr(tracing, "_tracer", lambda: tracer)
    token = context.attach(context.Context())
    yield SimpleNamespace(exporter=exporter, tracer=tracer, initialize=initialize)
    context.detach(token)
    provider.shutdown()


def task(*, headers=None, eager=False, queue="agent_children"):
    return SimpleNamespace(name="arbitrary-task-secret", request=SimpleNamespace(
        headers=headers, is_eager=eager, delivery_info={"routing_key": queue},
        args=("synthetic-sensitive-payload",), kwargs={"password": "synthetic-sensitive-password"},
    ))


@pytest.mark.parametrize("state", ["SUCCESS", "FAILURE", "RETRY", "untrusted-state"])
def test_consumer_uses_only_w3c_parent_bounded_attributes_and_always_restores(proof, state):
    value = task(headers={"traceparent": HEADER, "baggage": "secret=synthetic-private", "tracestate": "vendor=private"})
    tracing.start_task_trace(task=value)
    span = trace.get_current_span()
    assert span.get_span_context().trace_id == int(TRACE, 16)
    tracing.finish_task_trace(task=value, state=state)
    assert not trace.get_current_span().get_span_context().is_valid
    assert not hasattr(value.request, tracing._ACTIVE)
    finished, = proof.exporter.get_finished_spans()
    assert finished.name == "celery.consume" and finished.parent.span_id == int(PARENT, 16)
    assert finished.parent.is_remote is True
    assert dict(finished.attributes) == {"messaging.system": "celery", "messaging.operation.type": "process",
                                         "messaging.destination.name": "agent_children",
                                         "status": state.lower() if state in {"SUCCESS", "FAILURE", "RETRY"} else "other"}
    assert finished.status.status_code == (StatusCode.ERROR if state == "FAILURE" else StatusCode.UNSET)
    assert finished.events == ()
    assert "synthetic" not in str(finished.attributes)


@pytest.mark.parametrize("header", [None, 17, HEADER.upper(), "00-" + "0" * 32 + f"-{PARENT}-01", HEADER + "\n", HEADER.replace("-01", "-gg"), "x" * 10000])
def test_invalid_parent_is_ignored_without_reusing_ambient_execution(proof, header):
    with proof.tracer.start_as_current_span("ambient") as ambient:
        value = task(headers={"traceparent": header})
        tracing.start_task_trace(task=value)
        assert trace.get_current_span().get_span_context().trace_id != ambient.get_span_context().trace_id
        tracing.finish_task_trace(task=value, state="SUCCESS")
        assert trace.get_current_span() is ambient


def test_publish_propagates_only_traceparent_and_preserves_business_headers(proof):
    headers = {"baggage": "private=secret", "tracestate": "vendor=private", "downstream_outbox_id": "synthetic-business-id"}
    with proof.tracer.start_as_current_span("client") as span:
        tracing.propagate_traceparent(headers=headers, body={"password": "synthetic-private"})
        assert headers == {"traceparent": f"00-{span.get_span_context().trace_id:032x}-{span.get_span_context().span_id:016x}-01",
                           "downstream_outbox_id": "synthetic-business-id"}


def test_explicit_valid_client_parent_survives_no_active_producer_span(proof):
    headers = {"traceparent": HEADER, "baggage": "private=secret"}
    tracing.propagate_traceparent(headers=headers)
    assert headers == {"traceparent": HEADER}


def test_eager_task_restores_in_process_parent_and_does_not_initialize_worker(proof):
    with proof.tracer.start_as_current_span("api-parent") as parent:
        value = task(eager=True)
        tracing.start_task_trace(task=value)
        assert trace.get_current_span().get_span_context().trace_id == parent.get_span_context().trace_id
        tracing.finish_task_trace(task=value, state="SUCCESS")
        assert trace.get_current_span() is parent
    proof.initialize.assert_not_called()


def test_disabled_endpoint_is_inert(proof, monkeypatch):
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", None)
    value = task(headers={"traceparent": HEADER})
    tracing.start_task_trace(task=value)
    tracing.finish_task_trace(task=value, state="SUCCESS")
    proof.initialize.assert_not_called()
    assert proof.exporter.get_finished_spans() == ()


def test_child_initialization_is_endpoint_gated_and_pid_local(monkeypatch):
    from app.core import tracing as core
    initialize = Mock()
    monkeypatch.setattr(core, "setup_tracing", initialize)
    monkeypatch.setattr(tracing, "_READY_PID", None)
    monkeypatch.setattr(settings, "OTEL_ENABLED", True)
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")
    monkeypatch.setattr(settings, "OTEL_LOGS_ROLE", "worker-children")
    pid = [10]
    monkeypatch.setattr(tracing.os, "getpid", lambda: pid[0])
    tracing.initialize_worker_tracing(sender="synthetic-worker")
    tracing.initialize_worker_tracing()
    assert initialize.call_count == 1
    assert initialize.call_args.kwargs == {"service_role": "worker-children"}
    pid[0] = 11
    tracing.initialize_worker_tracing()
    assert initialize.call_count == 2


def test_real_eager_celery_signal_execution_has_one_span(proof):
    from app.worker.celery_app import celery_app

    @celery_app.task(name="testlookup.synthetic.trace-proof")
    def synthetic():
        return trace.get_current_span().get_span_context().is_valid

    try:
        with proof.tracer.start_as_current_span("api-parent") as parent:
            assert synthetic.apply(throw=True).get() is True
            assert trace.get_current_span() is parent
        consumers = [span for span in proof.exporter.get_finished_spans() if span.name == "celery.consume"]
        assert len(consumers) == 1
    finally:
        celery_app.tasks.pop("testlookup.synthetic.trace-proof", None)


def test_release_probe_is_a_real_registered_read_only_transport_receipt():
    from app.worker.celery_app import celery_app
    registered = celery_app.tasks["app.worker.celery_app.queue_delivery_probe"]
    registered.push_request(delivery_info={"routing_key": "agent_children"}, hostname="synthetic-worker")
    try:
        assert registered.run("synthetic-client") == {
            "correlation_id": "synthetic-client", "routing_key": "agent_children", "worker": "synthetic-worker",
        }
    finally:
        registered.pop_request()
