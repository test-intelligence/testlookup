"""A Celery retry must not be silenced by the task's own dedup lock.

The trap: a task takes a Redis ``SET NX`` lock to suppress *concurrent
duplicates*, then fails and calls ``self.retry()``. Celery keeps the task id
stable across retries but the lock outlives the failed attempt, so the retry
walks into its own lock, decides it is a duplicate, logs "skipping duplicate"
and returns **success** having done nothing. The retry policy is inert and the
work is dropped silently — the worst shape available, because every counter
says the task succeeded.

``run_agent_pipeline`` was fixed for exactly this in Phase J (see
``tests/test_pipeline_dedup_retry.py``): pass ``owner=`` so the same task id
can reacquire, and release the lock on the error path. That fix was applied to
one call site; the siblings in this file kept the bug.

These are behavioural tests — they drive the real task through
``Task.apply()`` twice with the **same task id**, which is what Celery does
across a retry, and assert the work actually happened. A source-text
assertion cannot see "the payload was never ingested".
"""
from __future__ import annotations

import pytest

from app.worker import tasks as worker_tasks


class _DedupRedis:
    """Minimal async Redis fake: the ``SET NX``/``EX`` subset the helpers use."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def set(self, key, value, *, ex=None, nx=False):
        if nx and key in self._data:
            return False
        self._data[key] = value
        return True

    async def get(self, key):
        return self._data.get(key)

    async def expire(self, key, seconds):
        return key in self._data

    async def delete(self, *keys):
        removed = 0
        for k in keys:
            if k in self._data:
                del self._data[k]
                removed += 1
        return removed


@pytest.fixture
def dedup_redis(monkeypatch):
    """Point the dedup helpers at an in-process Redis stand-in.

    The helpers do ``from app.db.redis_client import get_redis`` at call time,
    so patching the attribute on that module is what takes effect.
    """
    fake = _DedupRedis()
    import app.db.redis_client as redis_client

    monkeypatch.setattr(redis_client, "get_redis", lambda: fake)
    return fake


SENTINEL = {"build_number": "42", "project_id": "proj-1"}
PREFIX = "runs/proj-1/42/"
TASK_ID = "stable-celery-task-id"


def test_ingest_test_run_retry_actually_ingests(monkeypatch, dedup_redis):
    """A transient failure then a retry must leave the run ingested.

    Under the bug the retry short-circuits on the surviving dedup key, so
    ``ingested`` stays empty while Celery records a successful task.
    """
    attempts: list[str] = []
    ingested: list[str] = []

    async def fake_process_sentinel(sentinel, minio_prefix):
        attempts.append(minio_prefix)
        if len(attempts) == 1:
            raise RuntimeError("transient MinIO read failure")
        ingested.append(minio_prefix)

    import app.services.ingestion as ingestion_mod

    monkeypatch.setattr(ingestion_mod, "process_sentinel", fake_process_sentinel)
    monkeypatch.setattr(
        worker_tasks.reindex_search, "apply_async", lambda *a, **k: None
    )

    # One apply(): Celery runs the retry inline in eager mode, keeping the
    # task id stable exactly as a real worker does.
    worker_tasks.ingest_test_run.apply(
        args=[SENTINEL, PREFIX], task_id=TASK_ID, throw=False
    )

    assert attempts == [PREFIX, PREFIX], (
        f"expected a first attempt and one retry, got {attempts}"
    )
    assert ingested == [PREFIX], (
        "the retry was silenced by the task's own dedup lock: "
        f"attempts={attempts} ingested={ingested} — the results were dropped"
    )


def test_ingest_test_run_still_suppresses_a_concurrent_duplicate(
    monkeypatch, dedup_redis
):
    """The dedup lock must still do its job: a *different* task id is a duplicate.

    Without this the obvious "fix" (drop the lock) would pass the test above
    while double-ingesting every webhook fan-out.
    """
    attempts: list[str] = []

    async def fake_process_sentinel(sentinel, minio_prefix):
        attempts.append(minio_prefix)

    import app.services.ingestion as ingestion_mod

    monkeypatch.setattr(ingestion_mod, "process_sentinel", fake_process_sentinel)
    monkeypatch.setattr(
        worker_tasks.reindex_search, "apply_async", lambda *a, **k: None
    )

    worker_tasks.ingest_test_run.apply(
        args=[SENTINEL, PREFIX], task_id="webhook-a", throw=False
    )
    worker_tasks.ingest_test_run.apply(
        args=[SENTINEL, PREFIX], task_id="webhook-b", throw=False
    )

    assert attempts == [PREFIX], (
        f"concurrent duplicate was not suppressed: attempts={attempts}"
    )


def test_ingest_test_run_lock_is_released_once_retries_are_exhausted(
    monkeypatch, dedup_redis
):
    """After the retry train dies, a fresh trigger must not meet a stale lock.

    ``owner=`` alone rescues the retries (same task id). It does nothing for the
    hour after they are exhausted: the lock's TTL is 3600s, so without an
    explicit release a re-delivered webhook — a *new* task id — is refused for
    an hour on the strength of an attempt that ingested nothing. This is the
    half of the fix the retry test above cannot see.
    """
    ingested: list[str] = []
    fail = {"always": True}

    async def fake_process_sentinel(sentinel, minio_prefix):
        if fail["always"]:
            raise RuntimeError("MinIO down for the whole retry train")
        ingested.append(minio_prefix)

    import app.services.ingestion as ingestion_mod

    monkeypatch.setattr(ingestion_mod, "process_sentinel", fake_process_sentinel)
    monkeypatch.setattr(
        worker_tasks.reindex_search, "apply_async", lambda *a, **k: None
    )

    worker_tasks.ingest_test_run.apply(
        args=[SENTINEL, PREFIX], task_id="doomed-attempt", throw=False
    )

    # MinIO comes back; CI re-delivers the webhook under a new task id.
    fail["always"] = False
    worker_tasks.ingest_test_run.apply(
        args=[SENTINEL, PREFIX], task_id="redelivered", throw=False
    )

    assert ingested == [PREFIX], (
        "a new delivery was refused by the dead attempt's stale dedup lock"
    )
