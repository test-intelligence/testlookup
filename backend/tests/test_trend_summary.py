"""W08 TRND-06: ``trend_summary_service.summarize`` is the Trends page's derivation, literally.

The page computed the verdict, the confidence score, the variance and the run
gap in the browser (``computeConfidenceModel`` + ``pickVerdict``,
``TrendsPage.tsx`` at 3c6d4511). The nine rows of :data:`CASES` are that
derivation's answers: the expected values were produced by running the ORIGINAL
TypeScript lines (types stripped, a fixed today) under node on these exact
inputs, not by this port -- so a port that drifts from the page fails here.

Two of them pin the port's traps: case 9's weighted sum is exactly 92.5, which
JS ``Math.round`` (:func:`half_up`) makes 93 and Python's banker's ``round()``
makes 92; case 7 holds two gaps of equal length, and only a STRICT ``>`` in
the scan keeps the earlier one, as the page does.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services import trend_summary_service as svc

TODAY = date(2026, 9, 18)  # the window is 2026-09-12 .. 2026-09-18
DAYS = 7


def day(d: int, passed: int, failed: int = 0, skipped: int = 0, broken: int = 0) -> dict:
    """One ``get_trend_data`` row of 2026-09-``d``."""
    evaluated = passed + failed + broken
    return {
        "date": f"2026-09-{d:02d}", "passed": passed, "failed": failed, "skipped": skipped, "broken": broken,
        "total": passed + failed + skipped + broken,
        "pass_rate": round(passed / evaluated * 100, 1) if evaluated else 0.0,
    }


def suite(name: str, passed: int, failed: int = 0, skipped: int = 0) -> dict:
    """One ``coverage_stats`` suite row (``failed`` includes broken there)."""
    return {"suite_name": name, "passed": passed, "failed": failed, "skipped": skipped}


def expect(score, verdict, band, dims, days, executions, pass_rate, variance, gap, runs, untagged) -> dict:
    return dict(score=score, verdict=verdict, band=band, dims=dims, days=days, executions=executions,
                pass_rate=pass_rate, variance=variance, gap=gap, runs=runs, untagged=untagged)


#: (label, trend rows, suites, expected). ``dims`` = (data coverage, sample size,
#: variance stability, tag quality); ``days`` = (with runs, empty, active share);
#: ``executions`` = (total, passed, failed, broken, skipped, evaluated);
#: ``variance`` = (stddev pp, stability); ``gap`` = (silent days, start, end,
#: ongoing, expected runs missed); ``runs`` = (last run day, previous run day).
CASES = [
    ("no trend rows", [], [],
     expect(15, "PENDING", "Low", (0.0, 0.0, 0.0, 100.0), (0, 7, 0.0), (0, 0, 0, 0, 0, 0), None,
            (None, 0.0), None, (None, None), 0.0)),
    ("one active day, 10 passed", [day(18, 10)], [suite("api", 10)],
     expect(32, "INSUFFICIENT", "Low", (14.29, 10.0, 45.0, 100.0), (1, 6, 0.1429), (10, 10, 0, 0, 0, 10), 100.0,
            (None, 45.0), (6, "2026-09-12", "2026-09-17", False, 6), ("2026-09-18", None), 0.0)),
    ("seven active days, all 100 %", [day(d, 20) for d in range(12, 19)], [suite("api", 140)],
     expect(100, "HEALTHY", "High", (100.0, 100.0, 100.0, 100.0), (7, 0, 1.0), (140, 140, 0, 0, 0, 140), 100.0,
            (0.0, 100.0), None, ("2026-09-18", "2026-09-17"), 0.0)),
    ("100/100/100/50/50/50/50 declines",
     [day(d, 10) for d in (12, 13, 14)] + [day(d, 5, 5) for d in (15, 16, 17, 18)], [suite("api", 50, 20)],
     expect(76, "DECLINING", "High", (100.0, 70.0, 17.52, 100.0), (7, 0, 1.0), (70, 50, 20, 0, 0, 70), 71.4,
            (24.74, 17.52), None, ("2026-09-18", "2026-09-17"), 0.0)),
    ("a trailing 4-day gap is ongoing", [day(d, 10) for d in (12, 13, 14)], [suite("api", 30)],
     expect(60, "MIXED", "Moderate", (42.86, 30.0, 100.0, 100.0), (3, 4, 0.4286), (30, 30, 0, 0, 0, 30), 100.0,
            (0.0, 100.0), (4, "2026-09-15", "2026-09-18", True, 4), ("2026-09-14", "2026-09-13"), 0.0)),
    ("a middle 3-day gap beats a trailing 1-day gap; broken counts against its day",
     [day(12, 10), day(16, 8, broken=2), day(17, 10)], [suite("api", 28, 2)],
     expect(53, "MIXED", "Moderate", (42.86, 30.0, 68.57, 100.0), (3, 4, 0.4286), (30, 28, 0, 2, 0, 30), 93.3,
            (9.43, 68.57), (3, "2026-09-13", "2026-09-15", False, 3), ("2026-09-17", "2026-09-16"), 0.0)),
    ("of two equal gaps the earlier wins", [day(12, 10), day(15, 10), day(18, 10)], [suite("api", 30)],
     expect(60, "MIXED", "Moderate", (42.86, 30.0, 100.0, 100.0), (3, 4, 0.4286), (30, 30, 0, 0, 0, 30), 100.0,
            (0.0, 100.0), (2, "2026-09-13", "2026-09-14", False, 2), ("2026-09-18", "2026-09-15"), 0.0)),
    ("a day of only skipped tests is 0 % that day",
     [day(d, 10) for d in (12, 13, 14, 16, 17, 18)] + [day(15, 0, skipped=5)], [suite("api", 60, 0, 5)],
     expect(71, "HEALTHY", "High", (100.0, 65.0, 0.0, 100.0), (7, 0, 1.0), (65, 60, 0, 0, 5, 60), 100.0,
            (34.99, 0.0), None, ("2026-09-18", "2026-09-17"), 0.0)),
    ("half untagged, 120 executions: sample capped, 92.5 rounds up",
     [day(d, 17) for d in range(12, 18)] + [day(18, 18)], [suite("Unknown Suite", 60), suite("api", 60)],
     expect(93, "HEALTHY", "High", (100.0, 100.0, 100.0, 50.0), (7, 0, 1.0), (120, 120, 0, 0, 0, 120), 100.0,
            (0.0, 100.0), None, ("2026-09-18", "2026-09-17"), 0.5)),
]


def _compact(body: dict) -> dict:
    confidence, executions, gap = body["confidence"], body["executions"], body["gap"]
    return dict(
        score=confidence["score"], verdict=confidence["verdict"], band=confidence["band"],
        dims=tuple(d["score"] for d in confidence["dimensions"]),
        days=(body["days_with_runs"], body["empty_days"], body["active_day_share"]),
        executions=tuple(executions[k] for k in ("total", "passed", "failed", "broken", "skipped", "evaluated")),
        pass_rate=body["pass_rate"],
        variance=(body["variance"]["stddev_pp"], body["variance"]["stability"]),
        gap=None if gap is None else (
            gap["silent_days"], gap["start"], gap["end"], gap["ongoing"], gap["expected_runs_missed"],
        ),
        runs=(body["last_run_day"], body["previous_run_day"]),
        untagged=body["untagged_share"],
    )


def test_the_case_table_has_its_nine_rows():
    assert len(CASES) == 9


@pytest.mark.parametrize("label,trend,suites,expected", CASES, ids=[c[0] for c in CASES])
def test_the_summary_equals_the_pages_derivation(label, trend, suites, expected):
    body = svc.summarize(trend, suites, days=DAYS, today=TODAY)
    assert _compact(body) == expected
    assert body["confidence"]["verdict"] in svc.TREND_VERDICTS
    assert body["measured"] is (expected["executions"][0] > 0)
    assert body["reason"] == (None if body["measured"] else "No executions in this window.")


def test_half_up_is_javascripts_math_round():
    assert [svc.half_up(x) for x in (0.5, 1.5, 2.5, 92.5, 92.49999, 0.0, 99.5)] == [1, 2, 3, 93, 92, 0, 100]
    assert round(92.5) == 92, "Python's own round() is banker's: the reason half_up exists"


def test_the_body_has_exactly_the_contract_keys():
    body = svc.summarize([day(12, 10), day(18, 5, 5)], [], days=DAYS, today=TODAY)
    assert set(body) == {
        "window_days", "today", "measured", "reason", "days_with_runs", "empty_days", "active_day_share",
        "executions", "pass_rate", "variance", "gap", "last_run_day", "previous_run_day", "untagged_share",
        "confidence",
    }
    assert (body["window_days"], body["today"]) == (7, "2026-09-18")
    assert set(body["executions"]) == {"total", "passed", "failed", "broken", "skipped", "evaluated"}
    assert set(body["variance"]) == {"stddev_pp", "stability"}
    assert set(body["gap"]) == {"silent_days", "start", "end", "ongoing", "expected_runs_missed"}
    assert set(body["confidence"]) == {"score", "dimensions", "verdict", "band"}
    assert [(d["id"], d["label"], d["weight"]) for d in body["confidence"]["dimensions"]] == [
        (i, label, w) for i, label, w in svc.TREND_DIMENSIONS
    ]
    assert all(set(d) == {"id", "label", "score", "weight"} for d in body["confidence"]["dimensions"])


def test_nothing_evaluated_is_null_never_zero():
    body = svc.summarize([day(15, 0, skipped=4)], [], days=DAYS, today=TODAY)
    assert body["pass_rate"] is None
    assert body["executions"]["evaluated"] == 0 and body["executions"]["total"] == 4
    assert body["variance"]["stddev_pp"] is None, "one active day has no spread"


@pytest.mark.parametrize("name", ["", "  ", "Unknown Suite", " unknown suite ", "UNKNOWN", "Untagged", None])
def test_every_spelling_of_no_suite_counts_as_untagged(name):
    assert svc.untagged_share([suite(name, 3), suite("api", 1)]) == 0.75


def test_a_named_suite_is_not_untagged_and_an_empty_list_shares_nothing():
    assert svc.untagged_share([suite("unknown-suite", 3), suite("api", 1)]) == 0.0
    assert svc.untagged_share([]) == 0.0


def test_rows_outside_the_window_count_in_the_totals_only():
    """As the page: the totals sum every trend row, the cells are the window's days."""
    body = svc.summarize([day(1, 10), day(18, 10)], [], days=DAYS, today=TODAY)
    assert body["executions"]["total"] == 20
    assert body["days_with_runs"] == 1


def test_a_malformed_row_is_read_as_nothing_rather_than_raising():
    body = svc.summarize([{"date": None, "passed": "x"}, {"passed": None}], [{"suite_name": 3}], days=DAYS, today=TODAY)
    assert body["executions"]["total"] == 0 and body["confidence"]["verdict"] == "PENDING"
