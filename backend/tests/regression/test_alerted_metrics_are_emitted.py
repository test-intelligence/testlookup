"""The three metrics whose alerts could never fire must actually be recorded.

``tests/regression/test_celery_queue_depth_metric.py`` pins the *static*
invariant — every metric an alert names has a code path that records it. That
check cannot tell a real ``.inc()`` from a mention in a comment, so these tests
drive the emitters and read the counter back.

The alerts that were structurally unable to fire until this landed:

* ``TestLookupAIPipelineFailures``            -> testlookup_analyses_total
* ``TestLookupLLMCircuitBreakerOpen``         -> testlookup_llm_circuit_breaker_trips_total
* ``TestLookupFlakyQuarantineMaintenanceFailing`` -> testlookup_celery_tasks_total
* ``TestLookupPerfBaselineRefreshFailing``    -> testlookup_celery_tasks_total
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("app.core.metrics")

from app.core import metrics  # noqa: E402

pytestmark = pytest.mark.regression


def _counter(metric, **labels) -> float:
    return metric.labels(**labels)._value.get()


class _Task:
    def __init__(self, name: str) -> None:
        self.name = name


# ── Celery task outcome ──────────────────────────────────────────────────────

class TestCeleryTaskOutcome:
    """``celery_tasks_total`` is the only signal separating a task that is
    retrying from one that has failed terminally. Queue depth cannot do it: a
    task waiting on a retry countdown is not on the queue at all, which is why
    an incident once showed every queue at zero while 38 tasks were stuck."""

    @pytest.mark.parametrize(
        "state, expected",
        [("SUCCESS", "success"), ("FAILURE", "failure"), ("RETRY", "retry")],
    )
    def test_each_terminal_state_is_counted(self, state, expected):
        """Driven through ``task_postrun.send`` rather than by calling the
        handler, so the test fails if the handler is ever disconnected from the
        signal. Calling it directly passed happily with ``@task_postrun.connect``
        deleted — a metric wired to nothing is the exact failure being fixed."""
        from celery.signals import task_postrun

        import app.worker.celery_app  # noqa: F401  (registers the receivers)

        task = _Task(f"app.worker.tasks.probe_{expected}")
        before = _counter(metrics.celery_tasks_total, task_name=task.name, status=expected)
        task_postrun.send(sender=None, task_id="t1", task=task, state=state)
        after = _counter(metrics.celery_tasks_total, task_name=task.name, status=expected)

        assert after == before + 1, (
            f"state={state} was not counted as {expected} — is the receiver "
            f"still connected to task_postrun?"
        )

    def test_an_unexpected_state_reports_itself(self):
        """REVOKED must appear as ``revoked``, not be folded into a bucket that
        tells the reader nothing."""
        from celery.signals import task_postrun

        import app.worker.celery_app  # noqa: F401

        task = _Task("app.worker.tasks.probe_revoked")
        task_postrun.send(sender=None, task_id="t2", task=task, state="REVOKED")

        assert _counter(
            metrics.celery_tasks_total, task_name=task.name, status="revoked"
        ) == 1.0

    def test_instrumentation_never_raises(self):
        """A metrics failure must not fail the task it is measuring."""
        from app.worker.celery_app import _record_task_outcome

        _record_task_outcome(task_id=None, task=None, state=None)


# ── LLM circuit breaker ──────────────────────────────────────────────────────

class _FakeRedis:
    def __init__(self) -> None:
        self.hash: dict[str, str] = {}
        self.zset: dict[str, float] = {}

    async def hset(self, key, mapping=None, **_kw):
        self.hash.update(mapping or {})
        return 1

    async def hgetall(self, key):
        return dict(self.hash)

    async def hget(self, key, field):
        return self.hash.get(field)

    async def expire(self, *a, **k):
        return True

    async def zadd(self, key, mapping):
        self.zset.update(mapping)
        return 1

    async def zremrangebyscore(self, *a, **k):
        return 0

    async def zcard(self, key):
        return len(self.zset)

    async def delete(self, *keys):
        self.hash.clear()
        self.zset.clear()
        return 1


class TestCircuitBreakerTrips:
    def _breaker(self, monkeypatch):
        import app.streams.circuit_breaker as cb

        fake = _FakeRedis()
        monkeypatch.setattr(cb, "get_redis", lambda: fake)
        return cb, fake

    def test_opening_the_circuit_is_counted(self, monkeypatch):
        cb, _ = self._breaker(monkeypatch)
        before = metrics.llm_circuit_breaker_trips_total._value.get()

        for _ in range(cb.FAILURE_THRESHOLD):
            asyncio.run(cb.LLMCircuitBreaker.record_failure())

        assert metrics.llm_circuit_breaker_trips_total._value.get() == before + 1

    def test_failures_while_already_open_do_not_recount(self, monkeypatch):
        """The metric counts *transitions to* OPEN. Counting every failure past
        the threshold would report one trip as many, and the alert fires on any
        increase — so the noise would land straight on the on-call."""
        cb, _ = self._breaker(monkeypatch)
        before = metrics.llm_circuit_breaker_trips_total._value.get()

        for _ in range(cb.FAILURE_THRESHOLD * 3):
            asyncio.run(cb.LLMCircuitBreaker.record_failure())

        assert metrics.llm_circuit_breaker_trips_total._value.get() == before + 1


# ── AI pipeline outcome ──────────────────────────────────────────────────────

class TestPipelineOutcome:
    def test_success_and_failure_are_counted_separately(self):
        from app.worker.tasks import _count_pipeline

        before_ok = _counter(metrics.ai_analyses_total, workflow_type="deep", status="success")
        before_bad = _counter(metrics.ai_analyses_total, workflow_type="deep", status="failure")

        _count_pipeline("deep", "success")
        _count_pipeline("deep", "failure")

        assert _counter(metrics.ai_analyses_total, workflow_type="deep", status="success") == before_ok + 1
        assert _counter(metrics.ai_analyses_total, workflow_type="deep", status="failure") == before_bad + 1

    def test_a_deduplicated_run_is_not_counted_as_a_completion(self):
        """``run_agent_pipeline`` returns early with ``duplicate: True`` when
        another trigger already owns the run. That completed no pipeline;
        counting it would inflate the success rate with runs that never ran."""
        import inspect

        from app.worker import tasks

        source = inspect.getsource(tasks.run_agent_pipeline)

        # Both call sites must exist: a counter that only ever records
        # successes is worse than none, because the failure rate it implies
        # is zero by construction.
        assert '_count_pipeline(workflow_type, "success")' in source
        assert '_count_pipeline(workflow_type, "failure")' in source

        assert 'if not final_state.get("duplicate"):' in source
        assert source.index('if not final_state.get("duplicate"):') < source.index(
            '_count_pipeline(workflow_type, "success")'
        )
