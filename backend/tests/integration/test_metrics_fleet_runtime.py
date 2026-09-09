"""Live acceptance test for M08 fleet metrics.

Set ``TESTLOOKUP_PROMETHEUS_URL`` and ``TESTLOOKUP_API_URL`` against a running
monitoring stack. The test is skipped in ordinary unit runs because it requires
Prometheus, the API, Redis, and both Celery worker queues.
"""
from __future__ import annotations

import os
import time

import httpx
import pytest
from celery import Celery

PROMETHEUS_URL = os.getenv("TESTLOOKUP_PROMETHEUS_URL")
API_URL = os.getenv("TESTLOOKUP_API_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not PROMETHEUS_URL or not API_URL,
        reason="set TESTLOOKUP_PROMETHEUS_URL and TESTLOOKUP_API_URL",
    ),
]


def _prometheus(path: str, **params):
    response = httpx.get(f"{PROMETHEUS_URL}{path}", params=params, timeout=10)
    response.raise_for_status()
    payload = response.json()
    assert payload["status"] == "success", payload
    return payload["data"]


def _scalar(query: str) -> float:
    result = _prometheus("/api/v1/query", query=query)["result"]
    return 0.0 if not result else float(result[0]["value"][1])


def _wait_for_delta(query: str, baseline: float, delta: int) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        value = _scalar(query)
        if value >= baseline + delta:
            assert value == baseline + delta
            return
        time.sleep(2)
    pytest.fail(f"metric did not reach exact delta {delta}: {query}")


def test_every_target_is_up_and_known_work_has_exact_fleet_counts():
    targets = _prometheus("/api/v1/targets")["activeTargets"]
    expected = {
        "testlookup-backend": int(os.getenv("M08_EXPECTED_BACKEND_TARGETS", "1")),
        "testlookup-worker": int(os.getenv("M08_EXPECTED_WORKER_TARGETS", "2")),
    }
    for scrape_pool, count in expected.items():
        matching = [target for target in targets if target["scrapePool"] == scrape_pool]
        assert len(matching) == count, (scrape_pool, matching)
        assert all(target["health"] == "up" for target in matching), matching

    http_query = (
        'sum(http_requests_total{handler="/health/version",method="GET",status="2xx"})'
    )
    http_baseline = _scalar(http_query)
    request_count = 5
    for _ in range(request_count):
        response = httpx.get(f"{API_URL}/health/version", timeout=10)
        response.raise_for_status()
    _wait_for_delta(http_query, http_baseline, request_count)

    broker = os.environ["CELERY_BROKER_URL"]
    result_backend = os.environ["CELERY_RESULT_BACKEND"]
    celery = Celery("m08-acceptance", broker=broker, backend=result_backend)
    queues = ("default", "agent_children")
    task_count = len(queues)
    task_query = 'sum(testlookup_celery_tasks_total{task_name="celery.ping",status="success"})'
    runtime_query = 'sum(celery_task_runtime_seconds_count{task_name="celery.ping"})'
    task_baseline = _scalar(task_query)
    runtime_baseline = _scalar(runtime_query)
    results = [celery.send_task("celery.ping", queue=queue) for queue in queues]
    assert [result.get(timeout=30) for result in results] == ["pong"] * task_count
    _wait_for_delta(task_query, task_baseline, task_count)
    _wait_for_delta(runtime_query, runtime_baseline, task_count)
