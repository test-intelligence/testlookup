"""Twelve declared Prometheus metrics that nothing ever incremented.

A declared-but-dead metric is worse than a missing one. An unlabelled counter
is exported as a confident ``0.0`` -- which reads as a *measured* zero ("no
ingestion failures", "no release blocked") -- and a labelled one exports no
series at all, so a Grafana panel reading it renders "No data", which looks
like a quiet system rather than a broken instrument.

Three of the twelve backed live panels on ``infra/monitoring/grafana/dashboards
/testlookup-overview.json``:

* ``testlookup_ingestion_test_cases_total``  -> "test cases ingested by framework"
* ``testlookup_release_decisions_total``     -> "release decisions by recommendation"
* ``testlookup_llm_request_duration_seconds``-> LLM latency p50 / p95 by provider

``scripts/quality_gate.py``'s ``backend.metrics-are-emitted`` guard pins the
*static* invariant (every declared metric has a production call site). It
cannot tell a real ``.inc()`` from one that never runs, so these tests drive
each emitter and read the value back -- the same split as
``test_alerted_metrics_are_emitted.py`` and ``test_celery_queue_depth_metric.py``
before it.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("app.core.metrics")

from app.core import metrics  # noqa: E402

pytestmark = pytest.mark.regression


def _counter(metric, **labels) -> float:
    return metric.labels(**labels)._value.get()


def _hist_count(metric, **labels) -> float:
    """Number of observations, not their sum: a 0.0-second call still counts.

    prometheus_client's Histogram exposes no ``_count``, and ``_buckets`` holds
    PER-bucket counts (they are only accumulated at collect time), so the last
    bucket is the >600s range rather than the total. Summing them gives the
    observation count, since every observation increments exactly one.
    """
    child = metric.labels(**labels) if labels else metric
    return sum(b.get() for b in child._buckets)


# ── auth_failures_total ──────────────────────────────────────────────────────

class TestAuthFailureReasons:
    """The counter documented four reasons and emitted none of them.

    Its only call site was ``token_revocation._count``, which emits
    ``revocation_unavailable`` / ``revocation_write_failed`` -- both outside the
    declared vocabulary. So an alert on ``reason="expired_token"`` matched zero
    series for ever, while the rejections an operator most wants to see went
    uncounted. Same class as the ``backend.status-enum-vocab`` gate.
    """

    @pytest.mark.parametrize(
        "reason",
        ["expired_token", "invalid_token", "insufficient_role", "inactive_user"],
    )
    def test_each_declared_reason_can_be_emitted(self, reason):
        from app.core.deps import _count_auth_failure

        before = _counter(metrics.auth_failures_total, reason=reason)
        _count_auth_failure(reason)
        assert _counter(metrics.auth_failures_total, reason=reason) == before + 1

    def test_the_declared_vocabulary_is_the_one_the_code_uses(self):
        """Guards the pair against drifting apart again. Every reason string
        passed to ``_count_auth_failure`` in deps.py must appear in the comment
        documenting the label, and vice versa."""
        import inspect
        import re

        from app.core import deps

        emitted = set(
            re.findall(r'_count_auth_failure\(\s*"([a-z_]+)"', inspect.getsource(deps))
        )
        assert emitted == {
            "expired_token",
            "invalid_token",
            "insufficient_role",
            "inactive_user",
        }, f"deps.py emits {sorted(emitted)}"

        declared = inspect.getsource(metrics)
        for reason in emitted | {"revocation_unavailable", "revocation_write_failed"}:
            assert reason in declared, (
                f"{reason} is emitted but not named in metrics.py's documented "
                "vocabulary -- the drift this counter already suffered once"
            )

    def test_instrumentation_never_raises(self, monkeypatch):
        """Auth must not 500 because telemetry is unhappy."""
        from app.core import deps

        class _Boom:
            def labels(self, **_kw):
                raise RuntimeError("registry exploded")

        monkeypatch.setattr(metrics, "auth_failures_total", _Boom())
        deps._count_auth_failure("expired_token")  # must not raise


# ── llm_requests_total / llm_request_duration_seconds ────────────────────────

class _Inner:
    """Stand-in for the wrapped provider model."""

    def __init__(self, exc: BaseException | None = None) -> None:
        self._exc = exc

    def invoke(self, *_a, **_kw):
        if self._exc:
            raise self._exc
        return "ok"

    async def ainvoke(self, *_a, **_kw):
        if self._exc:
            raise self._exc
        return "ok"


class TestLlmRequestMetrics:
    """``BudgetedLLM`` is the one point every provider and every graph call site
    passes through, and it already knows the provider -- so it is where the
    emission belongs. The latency histogram backs two live Grafana panels."""

    def _wrap(self, exc=None):
        from app.services.llm_factory import BudgetedLLM

        return BudgetedLLM(_Inner(exc), provider="ollama", model="llama3")

    def test_a_successful_call_is_counted_and_timed(self):
        llm = self._wrap()
        before = _counter(metrics.llm_requests_total, provider="ollama", status="success")
        timed = _hist_count(metrics.llm_request_duration_seconds, provider="ollama")

        assert llm.invoke("hi") == "ok"

        assert _counter(metrics.llm_requests_total, provider="ollama", status="success") == before + 1
        assert _hist_count(metrics.llm_request_duration_seconds, provider="ollama") == timed + 1

    def test_the_async_path_is_counted_too(self):
        llm = self._wrap()
        before = _counter(metrics.llm_requests_total, provider="ollama", status="success")

        assert asyncio.run(llm.ainvoke("hi")) == "ok"

        assert _counter(metrics.llm_requests_total, provider="ollama", status="success") == before + 1

    def test_a_timeout_is_not_folded_into_failure(self):
        """The declared vocabulary is success|failure|timeout. A provider timing
        out at the 120s bucket is the signal these panels exist to show."""
        llm = self._wrap(TimeoutError("too slow"))
        before = _counter(metrics.llm_requests_total, provider="ollama", status="timeout")

        with pytest.raises(TimeoutError):
            llm.invoke("hi")

        assert _counter(metrics.llm_requests_total, provider="ollama", status="timeout") == before + 1

    def test_a_failure_is_still_timed(self):
        """Dropping latency for failures would make an outage look like reduced
        traffic rather than a slow provider."""
        llm = self._wrap(ValueError("boom"))
        before = _counter(metrics.llm_requests_total, provider="ollama", status="failure")
        timed = _hist_count(metrics.llm_request_duration_seconds, provider="ollama")

        with pytest.raises(ValueError):
            llm.invoke("hi")

        assert _counter(metrics.llm_requests_total, provider="ollama", status="failure") == before + 1
        assert _hist_count(metrics.llm_request_duration_seconds, provider="ollama") == timed + 1

    def test_a_budget_refusal_is_not_counted_as_a_provider_call(self, monkeypatch):
        """``_check`` raises before any request is made. Counting it would blame
        the provider for a decision taken here."""
        from app.services import llm_factory

        llm = self._wrap()
        monkeypatch.setattr(
            llm, "_check",
            lambda: (_ for _ in ()).throw(llm_factory.PipelineBudgetExceeded("no budget")),
        )
        before = sum(
            _counter(metrics.llm_requests_total, provider="ollama", status=s)
            for s in ("success", "failure", "timeout")
        )

        with pytest.raises(llm_factory.PipelineBudgetExceeded):
            llm.invoke("hi")

        after = sum(
            _counter(metrics.llm_requests_total, provider="ollama", status=s)
            for s in ("success", "failure", "timeout")
        )
        assert after == before


# ── release_decisions_total ──────────────────────────────────────────────────

class TestReleaseDecisions:
    def test_a_real_decision_is_counted(self):
        from app.services.policy_evaluator_service import _count_release_decision

        before = _counter(metrics.release_decisions_total, recommendation="NO_GO")
        _count_release_decision("NO_GO", "project")
        assert _counter(metrics.release_decisions_total, recommendation="NO_GO") == before + 1

    def test_a_simulator_run_is_not_counted(self):
        """``policy_override`` is how the policy editor answers "what WOULD this
        decide?". Counting those would let a user inflate the NO_GO series by
        dragging a slider."""
        from app.services.policy_evaluator_service import _count_release_decision

        before = _counter(metrics.release_decisions_total, recommendation="NO_GO")
        _count_release_decision("NO_GO", "simulated")
        assert _counter(metrics.release_decisions_total, recommendation="NO_GO") == before


# ── ingestion_test_cases_total / ingestion_runs_total ────────────────────────

class TestIngestionMetrics:
    def test_cases_are_counted_by_framework_and_outcome(self):
        from app.services.ingestion_pipeline import _count_ingested_cases

        before_pass = _counter(metrics.ingestion_test_cases_total, framework="junit", status="passed")
        before_fail = _counter(metrics.ingestion_test_cases_total, framework="junit", status="failed")

        _count_ingested_cases("JUnit", {"PASSED": 2, "FAILED": 1})

        assert _counter(metrics.ingestion_test_cases_total, framework="junit", status="passed") == before_pass + 2
        assert _counter(metrics.ingestion_test_cases_total, framework="junit", status="failed") == before_fail + 1

    def test_a_missing_framework_reports_unknown_rather_than_empty(self):
        from app.services.ingestion_pipeline import _count_ingested_cases

        before = _counter(metrics.ingestion_test_cases_total, framework="unknown", status="passed")
        _count_ingested_cases(None, {"PASSED": 1})
        assert _counter(metrics.ingestion_test_cases_total, framework="unknown", status="passed") == before + 1

    def test_a_run_records_its_outcome_and_duration(self):
        from app.worker.tasks import _count_ingestion_run

        before = _counter(metrics.ingestion_runs_total, status="failure")
        timed = _hist_count(metrics.ingestion_duration_seconds)

        _count_ingestion_run("failure", 12.5)

        assert _counter(metrics.ingestion_runs_total, status="failure") == before + 1
        assert _hist_count(metrics.ingestion_duration_seconds) == timed + 1


# ── ai_analysis_duration_seconds ─────────────────────────────────────────────

def test_pipeline_duration_is_observed():
    from app.worker.tasks import _observe_pipeline_duration

    before = _hist_count(metrics.ai_analysis_duration_seconds, workflow_type="deep")
    _observe_pipeline_duration("deep", 42.0)
    assert _hist_count(metrics.ai_analysis_duration_seconds, workflow_type="deep") == before + 1


# ── feature_flag_evaluations_total ───────────────────────────────────────────

class TestFeatureFlagEvaluations:
    def test_the_value_passes_through_unchanged(self):
        """The counter sits on the return path of a gate. If it ever altered the
        value it would turn telemetry into a feature switch."""
        from app.services.feature_flags import _counted

        assert _counted("k", True) is True
        assert _counted("k", False) is False

    @pytest.mark.parametrize(
        "value,override,expected",
        [(True, None, "enabled"), (False, None, "disabled"), (False, "default", "default")],
    )
    def test_the_result_label_distinguishes_off_from_absent(self, value, override, expected):
        """"disabled" implies somebody turned it off; "default" means no flag row
        existed and the answer came from the env fallback or a hardcoded False."""
        from app.services.feature_flags import _counted

        before = _counter(metrics.feature_flag_evaluations_total, flag_key="k", result=expected)
        _counted("k", value, override)
        assert _counter(metrics.feature_flag_evaluations_total, flag_key="k", result=expected) == before + 1


# ── secret_read_failures_total ───────────────────────────────────────────────

def test_a_secret_that_will_not_decrypt_is_counted():
    """``read_secret`` returns None for "no such secret" and for "the ciphertext
    will not decrypt" alike, so an integration silently stops authenticating
    after a key rotation with only an ERROR line as evidence."""
    from app.services.secret_service import _count_secret_read_failure

    before = _counter(metrics.secret_read_failures_total, scope="smtp_config")
    _count_secret_read_failure("smtp_config")
    assert _counter(metrics.secret_read_failures_total, scope="smtp_config") == before + 1


# ── websocket_connections_active ─────────────────────────────────────────────

def test_the_socket_gauge_is_set_from_the_channels_not_incremented():
    """Set from the authoritative structure rather than ++/--: a dropped socket
    that skips ``disconnect`` would otherwise leak the gauge upward for the life
    of the process, and a gauge that only climbs is worse than no gauge."""
    from app.routers.live import ConnectionManager

    mgr = ConnectionManager()
    a, b = object(), object()

    mgr.register("p1", a)
    mgr.register("p1", b)
    assert metrics.websocket_connections_active._value.get() == 2

    mgr.disconnect("p1", a)
    assert metrics.websocket_connections_active._value.get() == 1

    mgr.disconnect("p1", b)
    assert metrics.websocket_connections_active._value.get() == 0
