"""Real Redis/Celery late-ack redelivery regression.

The test is opt-in when a Redis broker is available (CI supplies REDIS_URL).
It deliberately uses a disposable queue, marker, and four-second visibility
timeout; no application task or database row is touched.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

pytest.importorskip("celery")
pytest.importorskip("redis")


def _redis():
    from redis import Redis

    if os.environ.get("TESTLOOKUP_RUN_CELERY_VISIBILITY") != "1":
        pytest.skip("explicit broker visibility regression opt-in is not set")
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        pytest.skip("REDIS_URL is not configured")
    client = Redis.from_url(url, decode_responses=True)
    try:
        client.ping()
    except Exception as exc:  # pragma: no cover - environment gate
        pytest.skip(f"Redis broker unavailable: {type(exc).__name__}")
    return client


def _worker_process(redis, marker: str) -> tuple[subprocess.Popen, str, str]:
    module = "tests.regression.celery_visibility_worker"
    env = os.environ.copy()
    # Keep the checkout on the path while retaining the deployed image's
    # application root.  The latter matters when this test is copied into a
    # temporary directory inside a backend pod for the homelab smoke run.
    repo_root = str(Path(__file__).parents[2])
    env["PYTHONPATH"] = os.pathsep.join(
        path for path in (repo_root, env.get("PYTHONPATH"), "/app") if path
    )
    worker_name = f"ci-visibility-{uuid.uuid4().hex}"
    env["VISIBILITY_WORKER_NAME"] = worker_name
    ready_key = f"testlookup:ci:visibility:ready:{marker}:{worker_name}"
    redis.delete(ready_key)
    env["VISIBILITY_READY_KEY"] = ready_key
    return subprocess.Popen(
        [sys.executable, "-m", module],
        cwd=Path(__file__).parents[2],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ), worker_name, ready_key


def _wait_until(predicate, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError("timed out waiting for disposable Celery visibility probe")


def _wait_ready(process, redis, ready_key: str, timeout: float = 30.0) -> None:
    """Wait for the worker's readiness marker, failing FAST if it died first.

    The worker imports ``app.worker.celery_app``, and ``app/db/postgres.py``
    builds its engine at IMPORT time — so a missing DATABASE_URL kills the
    subprocess instantly. Polling only for the marker turns that into an opaque
    "timed out" after the full timeout; surfacing the subprocess's own output
    the moment it exits turns a 30-second mystery into a one-line diagnosis.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            try:
                output = process.stdout.read() or ""
            except Exception:  # pragma: no cover - stream already closed
                output = "<no captured output>"
            raise AssertionError(
                "disposable Celery worker exited with code "
                f"{process.returncode} before signalling readiness:\n"
                f"{output[-4000:]}"
            )
        if redis.get(ready_key) == "1":
            return
        time.sleep(0.1)
    raise AssertionError(
        "timed out waiting for the disposable Celery worker to signal readiness"
    )


def test_late_ack_task_redelivers_with_same_id_after_worker_loss():
    redis = _redis()
    marker = uuid.uuid4().hex
    key = f"testlookup:ci:visibility:{marker}"
    queue = "ci_visibility_drill"
    redis.delete(f"{key}:events", f"{key}:done")
    redis.delete(queue)
    first = second = None
    try:
        first, _first_name, first_ready = _worker_process(redis, marker)
        from app.worker.celery_app import celery_app
        # Celery's pidbox is disabled by the disposable worker.  The worker
        # writes a short-lived Redis readiness marker after importing the app,
        # avoiding a brittle fixed startup sleep in CI and homelab images.
        _wait_ready(first, redis, first_ready)

        task = celery_app.send_task(
            "testlookup.regression.visibility_task",
            args=[marker, 8],
            queue=queue,
            exchange="default",
            routing_key=queue,
        )
        task_id = task.id
        # Generous: this is a real publish -> broker -> consume round trip on a
        # cold CI runner, not an in-process call.
        _wait_until(lambda: redis.llen(f"{key}:events") == 1, timeout=30)
        print("visibility first delivery", task_id)
        first.kill()
        first.wait(timeout=10)
        # Kombu's Redis transport restores visibility leases on its periodic
        # restore pass.  Give that pass a bounded interval before starting the
        # replacement consumer; this models broker recovery rather than a
        # competing consumer racing the visibility timer.
        time.sleep(8)

        second, _second_name, second_ready = _worker_process(redis, marker)
        _wait_ready(second, redis, second_ready)
        print(
            "visibility second worker ready",
            redis.llen(queue),
            redis.lrange(f"{key}:events", 0, -1),
        )
        _wait_until(
            lambda: redis.llen(f"{key}:events") == 2,
            timeout=20,
        )
        events = redis.lrange(f"{key}:events", 0, -1)
        assert events == [f"{task_id}:0", f"{task_id}:1"]
        _wait_until(lambda: redis.get(f"{key}:done") == "1", timeout=15)
    finally:
        for process in (first, second):
            if process is not None and process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
        redis.delete(f"{key}:events", f"{key}:done", queue)
