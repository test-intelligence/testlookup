"""The "Total executions" KPI must count executions, not runs.

``metrics_service`` built the dashboard KPI from the run count::

    total_exec = cur["total_runs"]          # func.count(TestRun.id)
    ...
    "total_executions_7d": {"value": total_exec, ...}

while ``analytics_service.coverage_stats`` computes a genuine execution total.
On the seeded project — 5 runs x 12 tests — the two surfaces reported the same
quantity as::

    coverage.summary.total_executions   60      <- executions
    summary.total_executions_7d          5      <- runs, labelled "Total executions"
    recomputed from run rows            60      <- ground truth

A 12x understatement on a headline KPI, and the field name
(``total_executions_7d``) and the UI label (``label="Total executions"`` in
``OverviewPage``) both say executions. The *value* was the outlier, so the
value is what changed.

**What this deliberately does NOT change.** ``total_exec`` remains a run count
where it feeds release readiness:

* ``if total_exec <= 0`` — the "no evidence in the window" gate;
* ``_compute_readiness(total_runs, ...)``, whose first parameter is named
  ``total_runs`` and which uses it only as ``total_runs <= 0``.

Neither has a threshold that scales with the number, so swapping in a 12x
larger value would not have changed today's verdicts — but it would have made
the readiness contract silently wrong, and a future threshold would inherit
the confusion. Runs and executions are now separate values.

The unfiltered branch of ``_period_stats`` was also missing ``sum_total``
entirely; the suite-filtered branch already selected it. That asymmetry is why
the run count was reachable as a substitute in the first place.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.services import metrics_service  # noqa: E402

pytestmark = pytest.mark.regression


def _summary_source() -> str:
    return inspect.getsource(metrics_service.get_dashboard_summary)


def _period_stats_source() -> str:
    return inspect.getsource(metrics_service._period_stats)


class TestTheKpiIsFedByExecutions:
    def test_kpi_value_is_not_the_run_count(self):
        src = _summary_source()
        match = re.search(
            r'"total_executions_7d":\s*\{\s*\n?\s*"value":\s*(\w+)', src
        )
        assert match, "could not find the total_executions_7d mapping"
        fed_by = match.group(1)
        assert fed_by != "total_exec", (
            "the KPI is fed the run count; on 5 runs x 12 tests it reads 5 "
            "while Coverage reports 60 executions for the same data"
        )

    def test_the_executions_value_comes_from_period_stats(self):
        src = _summary_source()
        assert 'cur["total_executions"]' in src, (
            "the KPI no longer reads total_executions from _period_stats"
        )

    def test_the_trend_uses_the_same_quantity_as_the_value(self):
        """A value from one series and a delta from another is worse than
        either alone."""
        src = _summary_source()
        assert 'prev["total_executions"]' in src, (
            "the trend still compares run counts while the value is executions"
        )


class TestPeriodStatsExposesBoth:
    def test_it_returns_executions(self):
        assert '"total_executions"' in _period_stats_source()

    def test_it_still_returns_runs(self):
        assert '"total_runs"' in _period_stats_source(), (
            "the run count was removed; readiness depends on it"
        )

    def test_both_select_branches_provide_the_executions_sum(self):
        """The unfiltered branch was missing ``sum_total`` while the
        suite-filtered branch had it — the asymmetry that made the run count
        an available substitute."""
        src = _period_stats_source()
        assert src.count('label("sum_total")') >= 2, (
            "only one branch of _period_stats selects sum_total, so the "
            "suite-filtered and unfiltered paths disagree on what is available"
        )


class TestReadinessSemanticsAreUntouched:
    """Correcting the KPI must not quietly redefine release readiness."""

    def test_readiness_is_still_called_with_the_run_count(self):
        src = _summary_source()
        assert re.search(r"_compute_readiness\(\s*total_exec\s*,", src), (
            "_compute_readiness is no longer receiving the run count; its "
            "first parameter is named total_runs"
        )

    def test_the_evidence_gate_still_uses_the_run_count(self):
        src = _summary_source()
        assert "if total_exec <= 0" in src, (
            "the 'no evidence in the window' gate changed quantity"
        )

    def test_readiness_only_uses_it_as_a_presence_check(self):
        """Documents *why* the split was safe: no scaling threshold."""
        src = inspect.getsource(metrics_service._compute_readiness)
        numeric_uses = re.findall(r"total_runs\s*(<=|<|>=|>|==)\s*(\d+)", src)
        assert numeric_uses == [("<=", "0")], (
            f"_compute_readiness now compares total_runs against {numeric_uses} — "
            "it has acquired a scaling threshold, so runs-vs-executions is no "
            "longer a free choice and this split needs revisiting"
        )
