"""A skipped test is *not evaluated* — it must not dilute any pass rate.

Product decision (2026-08-08): a skip means the test never ran, so it is
neither a pass nor a failure and belongs in **no** pass-rate denominator. Most
of the codebase already worked this way — ``analysis_report_service``
("evaluated = passed + failed + broken; skipped from the denominator"),
``metrics_service._evaluated``, and the ``coverage_stats`` **summary** block.

The **per-suite rows** were missed. Measured live on the seeded project::

    summary.avg_pass_rate = 81.0          <- matches the dashboard
    suites[api]: passed=20 failed=3 skipped=2  pass_rate=80.0

``20 / (20 + 3 + 2) = 80.0`` — skips in the denominator. Excluding them gives
``20 / (20 + 3) = 87.0``. So one response carried a correct summary rate and
an inconsistent per-suite rate, and a user comparing a suite against the
headline saw a gap that no longer had a cause.

Note the ``failed`` column already counts ``FAILED`` *and* ``BROKEN``
(``FILTER (WHERE tc.status IN ('FAILED','BROKEN'))``), so "evaluated" here is
``passed + failed``.

Worth recording: the summary block's own comment claimed it was *"the last
surface still dividing by COUNT(*)"*. It was not — the suite rows in the same
function still were. A comment asserting completeness is not evidence of it.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.services import analytics_service  # noqa: E402

pytestmark = pytest.mark.regression


def _strip_sql_comments(sql: str) -> str:
    """Drop ``--`` comments before structural parsing.

    Prose about a denominator contains the words it describes. An earlier test
    in this suite matched a table name out of a comment; assertions about code
    must not read the commentary about that code.
    """
    return re.sub(r"--[^\n]*", "", sql)


def _source() -> str:
    return _strip_sql_comments(inspect.getsource(analytics_service.coverage_stats))


def _pass_rate_denominators() -> list[str]:
    """The divisor of every ``pass_rate`` expression in coverage_stats.

    Each looks like ``COUNT(*) FILTER (...) * 100.0 / NULLIF(<denominator>, 0)``.
    """
    return re.findall(r"/\s*NULLIF\(\s*(.*?)\s*,\s*0\s*\)", _source(), re.S)


class TestNoPassRateDividesByEveryRow:
    def test_at_least_two_pass_rates_exist(self):
        """Guards the test itself: summary + per-suite. If the shape changes,
        fail loudly rather than silently asserting over an empty list."""
        assert len(_pass_rate_denominators()) >= 2, (
            f"expected >=2 pass_rate denominators in coverage_stats, found "
            f"{_pass_rate_denominators()!r} — has the query been restructured?"
        )

    def test_no_denominator_is_a_bare_count_star(self):
        """The defect: ``NULLIF(COUNT(*), 0)`` counts SKIPPED rows."""
        bare = [d for d in _pass_rate_denominators() if re.fullmatch(r"COUNT\(\s*\*\s*\)", d.strip())]
        assert not bare, (
            "a pass_rate divides by COUNT(*), so SKIPPED executions dilute it — "
            "measured live as suites[api] 20/(20+3+2)=80.0 where evaluated-only "
            "gives 20/23=87.0"
        )

    def test_every_denominator_filters_to_evaluated_statuses(self):
        for denominator in _pass_rate_denominators():
            assert "FILTER" in denominator.upper(), (
                f"pass_rate denominator {denominator!r} is unfiltered; it must "
                "count only evaluated executions (PASSED/FAILED/BROKEN)"
            )
            assert "SKIPPED" not in denominator.upper(), (
                f"pass_rate denominator {denominator!r} mentions SKIPPED"
            )


class TestSkipsAreStillReported:
    """Excluding skips from the denominator must not hide them."""

    def test_skipped_is_still_selected(self):
        assert re.search(r"FILTER\s*\(\s*WHERE\s+tc\.status\s*=\s*'SKIPPED'\s*\)", _source(), re.I), (
            "the skipped column was removed; skips must stay visible even though "
            "they no longer affect the rate"
        )

    def test_total_executions_still_counts_everything(self):
        """``total_executions`` is a volume figure, not a rate — it should keep
        counting skips, and the two must not be conflated."""
        assert re.search(r"COUNT\(\s*\*\s*\)\s+AS\s+total_executions", _source(), re.I), (
            "total_executions no longer counts every execution; it is deliberately "
            "COUNT(*) while the RATE excludes skips"
        )


def test_the_evaluated_convention_is_shared_not_reinvented():
    """Anchors the decision to the surfaces that already implement it, so a
    future change has to break something visible to diverge again."""
    from app.services import metrics_service

    assert hasattr(metrics_service, "_evaluated"), (
        "metrics_service._evaluated is gone — the shared definition of "
        "'evaluated = passed + failed + broken' has moved or been lost"
    )
    assert metrics_service._evaluated(passed=8, failed=1, broken=1) == 10
