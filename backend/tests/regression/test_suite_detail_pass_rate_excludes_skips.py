"""The suite drill-down's pass rates must exclude SKIPPED, like ``/coverage``.

Product decision (2026-08-08): a skip means the test never ran, so it is
neither a pass nor a failure and belongs in **no** pass-rate denominator.
``coverage_stats`` (summary + per-suite rows) already divides by *evaluated* =
``PASSED + FAILED + BROKEN``; see
``test_coverage_suite_pass_rate_excludes_skips.py``.

``suite_detail`` is the drill-down for that **same** suite view — the router
hands it the suite a user clicked from ``/coverage``, and it shares
``_effective_suite_sql()`` with ``coverage_stats``. But every one of its pass
rates still counted skips in the denominator:

* the three ``test_cases`` queries (summary, per-case, per-run) divided by a
  bare ``COUNT(*)``, so a suite reading ``20/(20+3)=87.0`` on ``/coverage``
  opened to a detail summary of ``20/(20+3+2)=80.0``; and
* the run-aggregate **fallback** (used when the per-test rows didn't land)
  divided ``passed_tests`` by ``total_tests`` — and ``total_tests`` includes
  ``skipped_tests`` — so it diverged from the primary path the same way.

The canonical denominator is ``passed + failed + broken`` — never
``total_tests`` and never a bare ``COUNT(*)`` — as
``ingestion._update_run_aggregates`` and ``metrics_service`` spell out. Note
the ``failed`` columns here already fold ``FAILED`` *and* ``BROKEN`` together.

The per-case ``is_flaky`` expression *also* divides by ``COUNT(*)`` —
deliberately, as a failure *ratio* over all executions — so these assertions
anchor on the ``* 100.0 / NULLIF(...)`` shape unique to a pass rate and leave
that ratio alone.
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

    Prose about a denominator contains the words it describes, so assertions
    about code must not read the commentary about that code.
    """
    return re.sub(r"--[^\n]*", "", sql)


def _source() -> str:
    return _strip_sql_comments(inspect.getsource(analytics_service.suite_detail))


def _pass_rate_denominators(source: str | None = None) -> list[str]:
    """The divisor of every ``pass_rate`` expression.

    Each looks like ``<passed> * 100.0 / NULLIF(<denominator>, 0)``. Anchoring
    on ``* 100.0`` picks out the pass rates and skips the ``is_flaky`` failure
    *ratio*, which divides by ``COUNT(*)`` on purpose. The denominator can itself
    contain nested ``COALESCE(col, 0)`` calls, so this walks balanced parens
    rather than using a (paren-blind) regex to read out the divisor.
    """
    src = _source() if source is None else source
    denoms: list[str] = []
    for match in re.finditer(r"\*\s*100\.0\s*/\s*NULLIF\(", src):
        depth, i = 1, match.end()
        while i < len(src) and depth:
            depth += {"(": 1, ")": -1}.get(src[i], 0)
            i += 1
        inner = src[match.end():i - 1]  # everything inside NULLIF( ... )
        denoms.append(re.sub(r",\s*0\s*$", "", inner.strip()))
    return denoms


def _counts_skips(denominator: str) -> bool:
    """True if this denominator would put a skipped execution back in the rate.

    Two shapes count everything: a bare ``COUNT(*)`` (over ``test_cases``, one
    row per execution including SKIPPED), and ``total_tests`` (the ``test_runs``
    aggregate column that already folds ``skipped_tests`` in).
    """
    normalised = re.sub(r"\s+", " ", denominator).strip()
    if re.fullmatch(r"COUNT\(\s*\*\s*\)", normalised, re.I):
        return True
    if "total_tests" in normalised.lower():
        return True
    if "skipped" in normalised.lower():
        return True
    return False


class TestNoPassRateCountsSkips:
    def test_all_pass_rates_are_found(self):
        """Guards the test itself: 3 test_cases queries + 2 fallback queries. If
        the shape changes, fail loudly rather than assert over an empty list."""
        assert len(_pass_rate_denominators()) >= 5, (
            f"expected >=5 pass_rate denominators in suite_detail (summary, "
            f"cases, runs, + fallback summary & runs), found "
            f"{_pass_rate_denominators()!r} — has the query been restructured?"
        )

    def test_no_denominator_counts_skipped(self):
        """The defect: dividing by COUNT(*) or total_tests lets SKIPPED dilute
        the rate, so the detail disagreed with /coverage for the same suite."""
        offenders = [d for d in _pass_rate_denominators() if _counts_skips(d)]
        assert not offenders, (
            f"these pass_rate denominators count SKIPPED executions: "
            f"{offenders!r} — a suite reads 20/23=87.0 on /coverage but "
            f"20/(20+3+2)=80.0 here"
        )

    def test_every_denominator_is_evaluated_only(self):
        """Positive check: each denominator counts exactly PASSED+FAILED+BROKEN,
        either as a status FILTER (test_cases) or as the run-aggregate columns."""
        for denominator in _pass_rate_denominators():
            up = denominator.upper()
            filter_form = (
                "FILTER" in up and "PASSED" in up and "FAILED" in up and "BROKEN" in up
            )
            aggregate_form = (
                "PASSED_TESTS" in up and "FAILED_TESTS" in up and "BROKEN_TESTS" in up
            )
            assert filter_form or aggregate_form, (
                f"pass_rate denominator {denominator!r} does not clearly count "
                f"evaluated (PASSED+FAILED+BROKEN) executions"
            )


class TestSkipsAreStillReported:
    """Excluding skips from the denominator must not hide them."""

    def test_skipped_is_still_selected(self):
        assert re.search(
            r"FILTER\s*\(\s*WHERE\s+tc\.status\s*=\s*'SKIPPED'\s*\)", _source(), re.I
        ) and re.search(r"skipped_tests", _source(), re.I), (
            "a skipped column was removed; skips must stay visible even though "
            "they no longer affect the rate"
        )

    def test_total_executions_still_counts_everything(self):
        """``total_executions`` is a volume figure, not a rate — it should keep
        counting skips, and the two must not be conflated."""
        assert re.search(
            r"COUNT\(\s*\*\s*\)\s+AS\s+total_executions", _source(), re.I
        ), (
            "total_executions no longer counts every execution; it is "
            "deliberately COUNT(*) while the RATE excludes skips"
        )


def test_suite_detail_matches_coverage_stats_convention():
    """Both functions describe the same suite view; anchor the decision to the
    sibling that already implements it so a future edit can't diverge silently."""
    coverage_src = _strip_sql_comments(
        inspect.getsource(analytics_service.coverage_stats)
    )
    coverage_denoms = _pass_rate_denominators(coverage_src)
    assert coverage_denoms, "coverage_stats has no pass_rate divisions — has it moved?"
    for denominator in coverage_denoms:
        assert not _counts_skips(denominator), (
            "coverage_stats no longer excludes skips; suite_detail is pinned to "
            "match it, so this test's premise is stale"
        )
