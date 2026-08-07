"""Coverage's pass rate must exclude SKIPPED, like every other surface.

Closes F-014 — the last surface dividing by ``COUNT(*)``.

Measured live on the seeded project, same window, same moment::

    /metrics/summary     avg_pass_rate_7d = 81.0    (47 / 58, evaluated)
    /analytics/coverage  avg_pass_rate    = 78.3    (47 / 60, incl. 2 skips)

The rest of the codebase already states the rule outright, in four places:

  * ``analysis_report_service`` — "evaluated = passed + failed + broken;
    skips don't count"
  * ``ingestion._update_run_aggregates`` — "EXCLUDES skipped from the
    denominator: a skipped test wasn't executed"
  * ``metrics_service._evaluated`` — "Skips are excluded on purpose"
  * ``metrics_service.get_trend_data`` — "Skips stay out: never evaluated"

Coverage was the outlier, so it moved.

``total_executions`` deliberately keeps ``COUNT(*)``: that is a count of
executions, and a skip genuinely is one. Only the pass-rate *denominator*
changes — pinned below, because collapsing the two would silently change a
different, correct number.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.services import analytics_service  # noqa: E402
from app.services.metrics_service import _evaluated  # noqa: E402


def _coverage_sql() -> str:
    """Coverage SQL with Python and SQL comments stripped.

    Stripping matters. The fix's own comment explains *why* SKIPPED is excluded,
    so it contains the word "SKIPPED"; an earlier version of this helper kept
    comments and the assertions were satisfied by that prose rather than by the
    query. That is the third time in this session a structural assertion was
    fooled by a comment, so it is handled explicitly here.
    """
    src = inspect.getsource(analytics_service.coverage_stats)
    kept = []
    for line in src.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("--"):
            continue
        kept.append(re.sub(r"--\s.*$", "", line))
    return re.sub(r"\s+", " ", " ".join(kept))


def _pass_rate_denominator() -> str:
    """The NULLIF denominator of ``avg_pass_rate`` only.

    Scoped deliberately: ``COUNT(*)`` legitimately appears elsewhere in the same
    SELECT (as ``total_executions``), so a whole-query search would match the
    wrong expression.
    """
    sql = _coverage_sql()
    marker = sql.find("AS avg_pass_rate")
    assert marker != -1, "could not find avg_pass_rate — update this test"
    head = sql[:marker]
    hits = list(re.finditer(r"NULLIF\(", head))
    assert hits, "no NULLIF denominator found before avg_pass_rate"
    depth, out = 1, []
    for ch in head[hits[-1].end():]:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        out.append(ch)
    return "".join(out).strip()


class TestSkipsAreOutOfTheDenominator:
    def test_denominator_names_the_evaluated_statuses(self):
        denom = _pass_rate_denominator()
        for status in ("PASSED", "FAILED", "BROKEN"):
            assert status in denom, f"denominator omits {status}: {denom}"

    def test_skipped_is_not_in_the_denominator(self):
        denom = _pass_rate_denominator()
        assert "SKIPPED" not in denom, (
            f"a skipped test was never evaluated and must not dilute the rate; "
            f"denominator was: {denom}"
        )

    # NOTE: a `denominator != "COUNT(*)"` assertion was written here and then
    # REMOVED. It passed against the buggy source, because the extractor returns
    # the innermost NULLIF argument and that string never matched exactly. A
    # test that cannot fail is worse than no test — it reports confidence it has
    # not earned. `test_denominator_names_the_evaluated_statuses` above is the
    # assertion that genuinely moves between the broken and fixed versions
    # (verified 1 failed -> 11 passed).


class TestTotalExecutionsIsUnchanged:
    """A skip IS an execution. Only the pass-rate denominator moved."""

    def test_total_executions_still_counts_everything(self):
        assert re.search(r"COUNT\(\*\) AS total_executions", _coverage_sql()), (
            "total_executions must remain a plain COUNT(*) — excluding skips "
            "there would silently change a different, correct number"
        )


class TestAgreesWithTheCanonicalHelper:
    """Arithmetic parity with ``metrics_service._evaluated``."""

    @pytest.mark.parametrize(
        "passed,failed,broken,expected",
        [
            (47, 9, 2, 81.0),   # the live seeded window: Coverage read 78.3
            (10, 0, 2, 83.3),
            (9, 1, 0, 90.0),    # skips must not drag this below 90
            (12, 0, 0, 100.0),
            (0, 0, 5, 0.0),
        ],
    )
    def test_expected_rate(self, passed, failed, broken, expected):
        denom = _evaluated(passed, failed, broken)
        rate = round(passed / denom * 100.0, 1) if denom else 0.0
        assert rate == expected

    def test_skips_cannot_enter_the_helper_at_all(self):
        """``_evaluated`` takes no skip argument — excluded by construction."""
        assert _evaluated(9, 1, 0) == 10

    def test_an_all_skipped_window_does_not_divide_by_zero(self):
        assert _evaluated(0, 0, 0) == 0
