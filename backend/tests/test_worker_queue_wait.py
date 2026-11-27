"""Dispatch-ready wait, legacy coverage and real Celery retry attempt accounting."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

from celery import Celery
from celery.app.trace import build_tracer
from prometheus_client import CollectorRegistry, Counter, Histogram
import pytest

from app.core import metrics
from app.core.config import settings
from app.worker import celery_app as worker  # noqa: F401 — register actual producer/worker signals
from app.worker import queue_wait

NOW = 1_795_737_600_000


@pytest.fixture
def instruments(monkeypatch):
    registry = CollectorRegistry()
    wait = Histogram("fixture_wait_seconds", "Fixture", ["queue_name"], registry=registry)
    unavailable = Counter("fixture_wait_unavailable_total", "Fixture", ["queue_name", "reason"], registry=registry)
    runtime = Histogram("fixture_runtime_seconds", "Fixture", ["task_name", "queue_name"], registry=registry)
    outcomes = Counter("fixture_outcomes_total", "Fixture", ["task_name", "status"], registry=registry)
    monkeypatch.setattr(metrics, "celery_task_queue_wait_seconds", wait)
    monkeypatch.setattr(metrics, "celery_task_queue_wait_unavailable_total", unavailable)
    monkeypatch.setattr(metrics, "celery_task_runtime_seconds", runtime)
    monkeypatch.setattr(metrics, "celery_tasks_total", outcomes)
    monkeypatch.setattr(settings, "OTEL_ENABLED", False)
    monkeypatch.setattr(queue_wait.time, "time_ns", lambda: NOW * 1_000_000)
    return registry


def request(*, headers=None, eta=None, eager=False, queue="agent_children"):
    return SimpleNamespace(request=SimpleNamespace(headers=headers, eta=eta, is_eager=eager,
        delivery_info={"routing_key": queue}, args=("private-payload-canary",), kwargs={"secret": "private-canary"}))


@pytest.mark.parametrize("eta,expected", [
    (None, 10),
    (datetime.fromtimestamp((NOW - 2_000) / 1000, timezone.utc), 2),
    (datetime.fromtimestamp((NOW - 2_000) / 1000, timezone.utc).isoformat(), 2),
    (datetime.fromtimestamp((NOW - 2_000) / 1000, timezone.utc).isoformat().replace("+00:00", "Z"), 2),
    (datetime.fromtimestamp((NOW - 2_000) / 1000, timezone(timedelta(hours=2))).isoformat(), 2),
    (datetime.fromtimestamp((NOW - 20_000) / 1000, timezone.utc), 10),
])
def test_wait_excludes_only_intentional_aware_eta_delay(eta, expected):
    assert queue_wait.wait_observation(NOW - 10_000, eta, now_ms=NOW) == (expected, None)


@pytest.mark.parametrize("stamp", [None, True, False, -1, 0, 2**60, 1.5, float("nan"), float("inf"), "1795737600000"])
def test_invalid_stamps_are_unknown_instead_of_zero(stamp):
    assert queue_wait.wait_observation(stamp, now_ms=NOW) == (None, "invalid_stamp")


@pytest.mark.parametrize("eta", ["", "not-an-eta", 5, False, datetime(2026, 1, 1), "2026-01-01T00:00:00"])
def test_invalid_or_naive_eta_is_unknown(eta):
    assert queue_wait.wait_observation(NOW - 1_000, eta, now_ms=NOW) == (None, "invalid_eta")


def test_legacy_clock_skew_and_exact_ready_time_are_distinct():
    assert queue_wait.wait_observation(now_ms=NOW) == (None, "missing_stamp")
    assert queue_wait.wait_observation(NOW + 1, now_ms=NOW) == (None, "clock_skew")
    future_eta = datetime.fromtimestamp((NOW + 1_000) / 1000, timezone.utc)
    assert queue_wait.wait_observation(NOW - 1_000, future_eta, now_ms=NOW) == (None, "clock_skew")
    assert queue_wait.wait_observation(NOW, now_ms=NOW) == (0, None)


def test_publication_overwrites_only_reserved_scalar_even_when_tracing_is_disabled(instruments, monkeypatch):
    headers = {queue_wait.PUBLISHED_AT_HEADER: "stale-private-value", "business": {"keep": "private-canary"}}
    queue_wait.stamp_publish_time(headers=headers, body=object(), sender="private-task-name")
    assert headers == {queue_wait.PUBLISHED_AT_HEADER: NOW, "business": {"keep": "private-canary"}}
    monkeypatch.setattr(queue_wait.time, "time_ns", Mock(side_effect=OSError("synthetic clock refusal")))
    queue_wait.stamp_publish_time(headers=headers)
    assert headers == {"business": {"keep": "private-canary"}}
    queue_wait.stamp_publish_time(headers=None)  # Legacy caller metadata cannot fail publication.


def test_start_is_observed_once_and_legacy_retry_context_can_be_reused(instruments):
    task = request(headers={queue_wait.PUBLISHED_AT_HEADER: NOW - 7_000})
    queue_wait.observe_queue_wait(task=task)
    queue_wait.observe_queue_wait(task=task)
    assert instruments.get_sample_value("fixture_wait_seconds_count", {"queue_name": "agent_children"}) == 1
    assert instruments.get_sample_value("fixture_wait_seconds_sum", {"queue_name": "agent_children"}) == 7
    queue_wait.finish_queue_wait(task=task, state="RETRY")
    task.request.headers = None
    queue_wait.observe_queue_wait(task=task)
    assert instruments.get_sample_value("fixture_wait_unavailable_total", {"queue_name": "agent_children", "reason": "missing_stamp"}) == 1
    queue_wait.finish_queue_wait(task=task, state="FAILURE")
    assert not hasattr(task.request, queue_wait._ACTIVE)


def test_queue_dimension_is_closed_and_failure_cannot_affect_a_task(instruments, monkeypatch):
    for index in range(1000):
        task = request(headers={queue_wait.PUBLISHED_AT_HEADER: NOW - 1_000}, queue=f"ingestion.shard.{index}")
        queue_wait.observe_queue_wait(task=task)
        queue_wait.finish_queue_wait(task=task)
    assert instruments.get_sample_value("fixture_wait_seconds_count", {"queue_name": "ingestion_shard"}) == 1000
    task = request(headers={queue_wait.PUBLISHED_AT_HEADER: "private-invalid-stamp"}, queue="unregistered-customer-queue")
    queue_wait.observe_queue_wait(task=task)
    assert instruments.get_sample_value("fixture_wait_unavailable_total", {"queue_name": "other", "reason": "invalid_stamp"}) == 1
    broken = Mock()
    broken.labels.side_effect = RuntimeError("synthetic metric refusal")
    monkeypatch.setattr(metrics, "celery_task_queue_wait_seconds", broken)
    queue_wait.observe_queue_wait(task=request(headers={queue_wait.PUBLISHED_AT_HEADER: NOW - 1_000}))
    queue_wait.observe_queue_wait(task=None)


def test_real_eager_execution_never_claims_broker_wait(instruments):
    app = Celery("queue-wait-eager-fixture", broker="memory://", backend="cache+memory://", set_as_current=False)

    @app.task(name="fixture.queue_wait.eager", shared=False)
    def work():
        return "unchanged-business-result"
    try:
        assert work.apply(headers={queue_wait.PUBLISHED_AT_HEADER: NOW - 10_000}).get() == "unchanged-business-result"
        assert not any(sample.name.startswith("fixture_wait_") for metric in instruments.collect() for sample in metric.samples)
    finally:
        app.close()


def test_real_legacy_execution_without_stamp_keeps_result_and_marks_wait_unknown(instruments):
    app = Celery("queue-wait-legacy-fixture", broker="memory://", backend="cache+memory://", set_as_current=False)

    @app.task(name="fixture.queue_wait.legacy", shared=False)
    def work(value):
        return value
    try:
        task_id = str(uuid.uuid4())
        tracer = build_tracer(work.name, work, app=app)
        result = tracer(task_id, (), {"value": "unchanged-legacy-result"}, request={
            "id": task_id, "task": work.name, "is_eager": False, "ignore_result": True,
            "delivery_info": {"routing_key": "agent_children"},
        })
        assert result.info is None and result.retval == "unchanged-legacy-result"
        assert instruments.get_sample_value("fixture_wait_seconds_count", {"queue_name": "agent_children"}) is None
        assert instruments.get_sample_value("fixture_wait_unavailable_total", {"queue_name": "agent_children", "reason": "missing_stamp"}) == 1
        assert instruments.get_sample_value("fixture_runtime_seconds_count", {"task_name": "other", "queue_name": "agent_children"}) == 1
    finally:
        app.close()


def test_real_retry_republishes_same_task_with_fresh_stamp_and_eta_and_keeps_body(instruments, monkeypatch):
    clock = [NOW]
    monkeypatch.setattr(queue_wait.time, "time_ns", lambda: clock[0] * 1_000_000)
    app = Celery("queue-wait-retry-fixture", broker="memory://", backend="cache+memory://", set_as_current=False)
    app.conf.update(task_default_queue="agent_children", enable_utc=True, timezone="UTC")
    app.now = lambda: datetime.fromtimestamp(clock[0] / 1000, timezone.utc)

    @app.task(bind=True, name="fixture.queue_wait.retry", max_retries=1, shared=False)
    def work(self, private_value):
        assert private_value == "synthetic-private-payload"
        if self.request.retries == 0:
            clock[0] += 3_000
            raise self.retry(countdown=5)
        return "durable-work-is-a-separate-contract"

    task_id = str(uuid.uuid4())
    try:
        with app.connection_for_write() as connection:
            inbox = connection.SimpleQueue("agent_children")
            try:
                work.apply_async(kwargs={"private_value": "synthetic-private-payload"}, task_id=task_id,
                    headers={"business": "retained", queue_wait.PUBLISHED_AT_HEADER: NOW - 30_000}, ignore_result=True)
                first = inbox.get(block=False)
                assert first.headers[queue_wait.PUBLISHED_AT_HEADER] == NOW
                args, kwargs, _embed = first.payload
                clock[0] += 2_000
                tracer = build_tracer(work.name, work, app=app)
                initial = tracer(task_id, args, kwargs, request={**first.headers,
                    "delivery_info": {"routing_key": "agent_children"}, "is_eager": False})
                assert initial.info.state == "RETRY"
                first.ack()
                second = inbox.get(block=False)
                assert second.headers["id"] == task_id and second.headers["retries"] == 1
                assert second.headers[queue_wait.PUBLISHED_AT_HEADER] == NOW + 5_000
                assert second.headers["business"] == "retained" and second.payload[:2] == first.payload[:2]
                eta = datetime.fromisoformat(second.headers["eta"])
                assert eta.utcoffset() == timedelta(0)
                clock[0] = int(eta.timestamp() * 1000) + 2_000
                result = tracer(task_id, *second.payload[:2], request={**second.headers,
                    "delivery_info": {"routing_key": "agent_children"}, "is_eager": False})
                assert result.info is None and result.retval == "durable-work-is-a-separate-contract"
                second.ack()
                assert instruments.get_sample_value("fixture_wait_seconds_count", {"queue_name": "agent_children"}) == 2
                assert instruments.get_sample_value("fixture_wait_seconds_sum", {"queue_name": "agent_children"}) == 4
                assert instruments.get_sample_value("fixture_runtime_seconds_count", {"task_name": "other", "queue_name": "agent_children"}) == 2
            finally:
                inbox.close()
    finally:
        app.close()
