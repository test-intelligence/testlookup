"""Live acceptance test for M08 fleet metrics.

Set ``TESTLOOKUP_PROMETHEUS_URL`` and ``TESTLOOKUP_API_URL`` against a running
monitoring stack. The test is skipped in ordinary unit runs because it requires
Prometheus, the API, Redis, and both Celery worker queues.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
import subprocess
import sys
import json

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
    task_query = 'sum(testlookup_celery_tasks_total{task_name="app.worker.celery_app.queue_delivery_probe",status="success"})'
    runtime_query = 'sum(celery_task_runtime_seconds_count{task_name="app.worker.celery_app.queue_delivery_probe"})'
    task_baseline = _scalar(task_query)
    runtime_baseline = _scalar(runtime_query)
    correlation = "fleet-" + str(time.time_ns())
    results = [celery.send_task("app.worker.celery_app.queue_delivery_probe", queue=queue,
                                kwargs={"correlation_id": correlation}) for queue in queues]
    for queue, result in zip(queues, results):
        receipt = result.get(timeout=30)
        assert receipt["correlation_id"] == correlation and receipt["routing_key"] == queue and receipt["worker"]
    _wait_for_delta(task_query, task_baseline, task_count)
    _wait_for_delta(runtime_query, runtime_baseline, task_count)


@pytest.mark.skipif(os.getenv("TESTLOOKUP_FULL_SIGNAL_GATE") != "1", reason="full disposable signal profile is opt-in")
def test_candidate_bound_durable_client_log_trace_signal_path(tmp_path):
    """Reuse the release checker against the same real monitored fleet."""
    root = Path(__file__).resolve().parents[3]
    output = tmp_path / "release-signal-proof.json"
    result = subprocess.run([
        sys.executable, str(root / "scripts/monitoring/release_signal_gate.py"),
        "--manifest", os.environ["TESTLOOKUP_SIGNAL_MANIFEST"],
        "--runtime-proof", os.environ["TESTLOOKUP_SIGNAL_RUNTIME_PROOF"], "--out", str(output),
    ], cwd=root, capture_output=True, text=True, timeout=900)
    proof = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {"status": "missing"}
    assert result.returncode == 0 and proof["status"] == "passed", proof
