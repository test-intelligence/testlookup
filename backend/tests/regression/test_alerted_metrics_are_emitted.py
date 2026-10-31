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
* ``TestLookupCompliancePackGenerationErrors`` -> testlookup_compliance_pack_generated_total{result="error"}
  (W25b HYG-09: the service counted disabled / not_available / success and
  nothing else, so the alert's label value was never written)

W25b also drives the two label values the Grafana "Tier 0-2 jobs" row filters
on -- ``celery_tasks_total{status="failure"}`` under the six Tier 0-2 task names
and ``feature_flag_evaluations_total{flag_key="perf_regression_detection"}`` --
so no panel of that row charts a series nothing emits.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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
        self.hashes: dict[str, dict[str, str]] = {}
        self.values: dict[str, str] = {}

    async def hset(self, key, mapping=None, **_kw):
        self.hashes.setdefault(key, {}).update(
            {str(k): str(v) for k, v in (mapping or {}).items()}
        )
        return 1

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hincrby(self, key, field, amount):
        values = self.hashes.setdefault(key, {})
        value = int(values.get(field, "0")) + amount
        values[field] = str(value)
        return value

    async def set(self, key, value, *, ex=None, nx=False):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = str(value)
        return True

    async def expire(self, *args, **kwargs):
        del args, kwargs
        return True

    async def delete(self, *keys):
        for key in keys:
            self.hashes.pop(key, None)
            self.values.pop(key, None)
        return len(keys)


class TestCircuitBreakerTrips:
    def _breaker(self, monkeypatch):
        import app.services.llm_circuit_breaker as cb

        fake = _FakeRedis()
        monkeypatch.setattr(cb, "get_redis", lambda: fake)
        cb.LLMCircuitBreaker._known_open_until.clear()
        return cb, fake

    def test_opening_the_circuit_is_counted(self, monkeypatch):
        cb, _ = self._breaker(monkeypatch)
        before = metrics.llm_circuit_breaker_trips_total._value.get()

        for _ in range(cb.FAILURE_THRESHOLD):
            asyncio.run(
                cb.LLMCircuitBreaker.record_failure(
                    "ollama", "http://model-a:11434", TimeoutError("down")
                )
            )

        assert metrics.llm_circuit_breaker_trips_total._value.get() == before + 1

    def test_failures_while_already_open_do_not_recount(self, monkeypatch):
        """The metric counts *transitions to* OPEN. Counting every failure past
        the threshold would report one trip as many, and the alert fires on any
        increase — so the noise would land straight on the on-call."""
        cb, _ = self._breaker(monkeypatch)
        before = metrics.llm_circuit_breaker_trips_total._value.get()

        for _ in range(cb.FAILURE_THRESHOLD * 3):
            asyncio.run(
                cb.LLMCircuitBreaker.record_failure(
                    "ollama", "http://model-b:11434", TimeoutError("down")
                )
            )

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


# ── Compliance pack generation errors (W25b HYG-09, D-W25b-9) ────────────────

class TestCompliancePackErrors:
    """``TestLookupCompliancePackGenerationErrors`` fires on
    ``compliance_pack_generated_total{result="error"}``. ``generate_pack``
    counted ``disabled``, ``not_available`` and ``success`` only, so the alert
    could never fire however often packs failed. The error branch now counts,
    and the exception still reaches the caller."""

    def _arm(self, monkeypatch):
        from app.services import compliance_pack_service as svc

        monkeypatch.setattr(svc, "_feature_enabled", AsyncMock(return_value=True))
        release = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
        return svc, release

    def _counts(self) -> tuple[float, float]:
        return (
            _counter(metrics.compliance_pack_generated_total, result="error"),
            _counter(metrics.compliance_pack_generated_total, result="not_available"),
        )

    def test_a_payload_failure_is_counted_as_error_and_raised(self, monkeypatch):
        svc, release = self._arm(monkeypatch)
        monkeypatch.setattr(
            svc, "_build_pack_payload", AsyncMock(side_effect=RuntimeError("collector broke")),
        )
        error_before, not_available_before = self._counts()

        with pytest.raises(RuntimeError, match="collector broke"):
            asyncio.run(svc.generate_pack(MagicMock(), release, actor=None))

        assert self._counts() == (error_before + 1, not_available_before)

    def test_a_failure_after_the_payload_is_counted_too(self, monkeypatch):
        """The alert's own description names a MinIO write failure: the try
        covers the whole body after the flag gate, not only the payload build."""
        svc, release = self._arm(monkeypatch)
        payload = (b"zip", {"manifest_sha256": "0" * 64, "run_id": str(uuid.uuid4())})
        monkeypatch.setattr(svc, "_build_pack_payload", AsyncMock(return_value=payload))
        monkeypatch.setattr(svc, "_sign_pack", AsyncMock(side_effect=OSError("store unreachable")))
        error_before, not_available_before = self._counts()

        with pytest.raises(OSError, match="store unreachable"):
            asyncio.run(svc.generate_pack(MagicMock(), release, actor=None))

        assert self._counts() == (error_before + 1, not_available_before)

    def test_no_linked_run_stays_not_available_and_is_not_an_error(self, monkeypatch):
        """``not_available`` is the expected "release has no linked run yet"
        answer; counting it as ``error`` too would page on a normal state."""
        svc, release = self._arm(monkeypatch)
        monkeypatch.setattr(
            svc,
            "_build_pack_payload",
            AsyncMock(side_effect=svc.CompliancePackNotAvailableError("no linked run")),
        )
        error_before, not_available_before = self._counts()

        with pytest.raises(svc.CompliancePackNotAvailableError):
            asyncio.run(svc.generate_pack(MagicMock(), release, actor=None))

        assert self._counts() == (error_before, not_available_before + 1)


# ── The Tier 0-2 row's filtered series (W25b HYG-09) ─────────────────────────

#: Panel 88 of infra/monitoring/grafana/dashboards/testlookup-overview.json
#: charts ``celery_tasks_total{status="failure"}`` for exactly these names
#: (pinned there by test_grafana_dashboard_metrics_are_declared.py).
TIER_0_2_TASKS = (
    "app.worker.tasks.run_flaky_quarantine_maintenance",
    "app.worker.tasks.refresh_perf_baselines",
    "app.worker.tasks.dispatch_scheduled_digests",
    "app.worker.tasks.deliver_webhook",
    "app.worker.tasks.flag_orphan_test_suites",
    "app.worker.tasks.run_suite_membership_parity",
)


class TestTierJobFailuresAreCounted:
    """A renamed task would leave panel 88's regex matching nothing -- and an
    empty failure panel reads as "no failures"."""

    @pytest.mark.parametrize("name", TIER_0_2_TASKS)
    def test_the_task_exists_and_its_failure_is_counted_under_its_name(self, name):
        from celery.signals import task_postrun

        import app.worker.tasks  # noqa: F401  (registers the tasks)
        from app.worker.celery_app import celery_app

        assert name in celery_app.tasks, f"{name} is not a registered Celery task"
        before = _counter(metrics.celery_tasks_total, task_name=name, status="failure")
        task_postrun.send(
            sender=None, task_id="t-w25b", task=celery_app.tasks[name], state="FAILURE",
        )
        after = _counter(metrics.celery_tasks_total, task_name=name, status="failure")

        assert after == before + 1


class TestPerfFlagEvaluationsAreCounted:
    """Panel 84's target B charts the ``perf_regression_detection`` flag's
    evaluations by result: the nightly refresh's own gate must be one of them."""

    @pytest.mark.parametrize("enabled, result", [(True, "enabled"), (False, "disabled")])
    def test_the_refresh_gate_counts_the_flag_evaluation(self, monkeypatch, enabled, result):
        from app.services import feature_flags as ff
        from app.services import perf_regression_service as perf

        stored = {
            "key": "perf_regression_detection",
            "enabled_global": enabled,
            "enabled_projects": [],
            "enabled_roles": [],
            "rollout_percent": 100,
        }
        monkeypatch.setattr(ff, "_load_from_redis", AsyncMock(return_value=stored))
        before = _counter(
            metrics.feature_flag_evaluations_total,
            flag_key="perf_regression_detection", result=result,
        )

        assert asyncio.run(perf._feature_enabled(any_scope=True)) is enabled

        assert _counter(
            metrics.feature_flag_evaluations_total,
            flag_key="perf_regression_detection", result=result,
        ) == before + 1
