"""EXEC-03: ``execution_metrics_service`` -- the pure assembly and the refusals.

What only the SQL can show (the plans, the window percentiles, the tenant
filter against real rows, the HTTP guards) is in
``tests/integration/test_execution_metrics_postgres.py``. Here: the parallelism
math, every "not measured" path (null with a reason, never 0), lane and
timeline assembly, the suite regression rule on fixtures (5 slow runs listed,
too little history not), the test regression rule, the histogram buckets, the
per-day zero-fill, and that every refusal happens before the database.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.analytics_errors import AnalyticsQueryError
from app.services import execution_metrics_service as svc
from app.services.analytics_scope import AnalyticsScope

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
RUN = uuid.UUID("33333333-3333-4333-8333-333333333333")
T0 = datetime(2026, 10, 14, 10, 0, 0, tzinfo=timezone.utc)


def _scope(*, suites=(), days=30, denied=False, releases=()) -> AnalyticsScope:
    return AnalyticsScope(
        project_id=PROJECT, allowed_project_ids=None, release_ids=tuple(releases),
        suite_names=tuple(suites), days=days, denied=denied,
    )


def _agg(**over):
    base = dict(
        test_count=12, timed_count=12, duration_count=12, summed_ms=17400, phased_count=0,
        setup_ms=None, call_ms=None, teardown_ms=None,
        first_started_at=T0, last_finished_at=T0 + timedelta(seconds=9), worker_rows=12,
    )
    base.update(over)
    return SimpleNamespace(**base)


# ── Request validation (before any query) ────────────────────────────────────


@pytest.mark.parametrize("value,expected", [(None, None), ("", None), ("main", "main"), ("x" * 255, "x" * 255)])
def test_branch_is_optional_and_exact(value, expected):
    assert svc.parse_branch(value) == expected


@pytest.mark.parametrize("value", ["x" * 256, 7, ["main"]])
def test_a_bad_branch_is_refused_without_echo(value):
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_branch(value)
    assert exc.value.code == "branch_length" and exc.value.param == "branch"
    assert "xxxx" not in exc.value.message


def test_suite_history_needs_exactly_one_suite():
    with pytest.raises(AnalyticsQueryError) as none:
        svc.single_suite_key(_scope())
    assert none.value.code == "missing_parameter" and none.value.param == "suite_name"
    with pytest.raises(AnalyticsQueryError) as two:
        svc.single_suite_key(_scope(suites=("Checkout", "payments")))
    assert two.value.code == "suite_single_valued"
    # Trimmed and lower-cased: the suite_key EXEC-02 wrote.
    assert svc.single_suite_key(_scope(suites=("  Checkout ",))) == "checkout"
    # Two spellings of one suite are one suite.
    assert svc.single_suite_key(_scope(suites=("Checkout", "checkout"))) == "checkout"


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs,code", [
    ({"limit": 0}, "limit_range"),
    ({"limit": 501}, "limit_range"),
    ({"limit": True}, "limit_range"),
    ({"branch": "b" * 300}, "branch_length"),
])
async def test_suite_history_refuses_before_the_database(kwargs, code):
    db = AsyncMock()
    with pytest.raises(AnalyticsQueryError) as exc:
        await svc.suite_history(db, _scope(suites=("checkout",)), **kwargs)
    assert exc.value.code == code
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs,code", [
    ({"top": 0}, "top_range"),
    ({"top": 101}, "top_range"),
    ({"suite_limit": 201}, "suite_limit_range"),
    ({"branch": "b" * 256}, "branch_length"),
])
async def test_execution_metrics_refuses_before_the_database(kwargs, code):
    db = AsyncMock()
    with pytest.raises(AnalyticsQueryError) as exc:
        await svc.project_execution_metrics(db, _scope(), **kwargs)
    assert exc.value.code == code
    db.execute.assert_not_awaited()


# ── The denied scope: the empty shape, no data query ─────────────────────────


@pytest.mark.asyncio
async def test_a_denied_suite_history_is_the_empty_shape_and_reads_nothing():
    db = AsyncMock()
    out = await svc.suite_history(db, _scope(suites=("checkout",), denied=True, days=90))
    db.execute.assert_not_awaited()
    assert out["measured"] is False and out["reason"] == svc.REASON_DENIED
    assert out["points"] == [] and out["total"] == 0
    assert out["project_id"] is None and out["suite_key"] is None
    assert out["stats"]["wall"] == {"n": 0, "p50_ms": None, "p95_ms": None, "measured": False,
                                    "reason": svc.REASON_DENIED}


@pytest.mark.asyncio
async def test_a_denied_execution_metrics_is_the_empty_shape_and_reads_nothing():
    db = AsyncMock()
    now = T0
    out = await svc.project_execution_metrics(db, _scope(denied=True, days=7), now=now)
    db.execute.assert_not_awaited()
    assert out["project_id"] is None and out["measured"] is False
    assert out["suites"]["items"] == [] and out["regressions"]["suites"]["items"] == []
    assert out["slowest_tests"]["items"] == [] and out["duration_histogram"]["n"] == 0
    # Seven UTC days, every one present, every figure null.
    assert [p["day"] for p in out["per_day"]["points"]] == [
        (T0.date() - timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)
    ]
    assert all(p["wall_clock_ms"] is None and p["runs"] == 0 for p in out["per_day"]["points"])
    from app.models.schemas import ExecutionMetricsResponse

    ExecutionMetricsResponse(**out)  # the shape validates


# ── Run timing assembly ──────────────────────────────────────────────────────


def test_parallelism_is_summed_over_a_known_wall_clock():
    run = SimpleNamespace(wall_clock_ms=9000, duration_ms=17400, timing_source="derived",
                          exec_started_at=T0, exec_finished_at=T0 + timedelta(seconds=9))
    figures = svc.run_figures(run, _agg())
    assert figures["wall_clock_ms"] == 9000 and figures["summed_ms"] == 17400
    assert figures["parallelism"] == 1.93


def test_an_unknown_wall_clock_is_null_never_the_sum():
    run = SimpleNamespace(wall_clock_ms=None, duration_ms=12500, timing_source="derived_sum",
                          exec_started_at=None, exec_finished_at=None)
    figures = svc.run_figures(run, _agg(timed_count=0))
    assert figures["wall_clock_ms"] is None
    assert figures["parallelism"] is None
    assert figures["summed_ms"] == 12500 and figures["timing_source"] == "derived_sum"


def test_summed_falls_back_to_the_rows_when_the_run_column_is_empty():
    run = SimpleNamespace(wall_clock_ms=None, duration_ms=None, timing_source=None,
                          exec_started_at=None, exec_finished_at=None)
    assert svc.run_figures(run, _agg(summed_ms=300))["summed_ms"] == 300
    assert svc.run_figures(run, _agg(summed_ms=None))["summed_ms"] is None


def test_phases_not_reported_are_null_with_a_reason():
    block = svc.phase_block(_agg())
    assert block["measured"] is False and block["reason"] == svc.REASON_NO_PHASES
    assert block["setup_ms"] is None and block["setup_share"] is None
    empty = svc.phase_block(_agg(test_count=0, timed_count=0))
    assert empty["reason"] == svc.REASON_NO_RESULTS


def test_phase_shares_are_over_the_reported_parts():
    block = svc.phase_block(_agg(phased_count=10, setup_ms=120, call_ms=1600, teardown_ms=80))
    assert block["measured"] is True and block["tests_with_phases"] == 10
    assert block["setup_share"] == round(120 / 1800, 4)
    assert block["teardown_share"] == round(80 / 1800, 4)


def test_suites_not_recorded_say_so():
    block = svc.suites_block([])
    assert block == {"measured": False, "reason": svc.REASON_NO_SUITE_ROWS, "total": 0,
                     "truncated": False, "items": []}


def test_suites_are_capped():
    rows = [SimpleNamespace(suite_name=f"s{i}", suite_key=f"s{i}", source="derived_sum", wall_ms=None,
                            summed_ms=i, started_at=None, finished_at=None, setup_ms=None, teardown_ms=None,
                            test_count=1, timed_count=0, passed=1, failed=0, broken=0, skipped=0,
                            unknown=0, parallelism=None)
            for i in range(svc.MAX_RUN_SUITES + 1)]
    block = svc.suites_block(rows)
    assert block["truncated"] is True and len(block["items"]) == svc.MAX_RUN_SUITES
    assert block["items"][0]["wall_ms"] is None  # derived_sum: not the sum


def _slow_row(fp, duration, **over):
    base = dict(test_id=uuid.uuid4(), test_name=f"test_{fp}", test_fingerprint=fp, suite_name="checkout",
                status="PASSED", duration_ms=duration, started_at=None, finished_at=None, setup_ms=None,
                call_ms=None, teardown_ms=None, worker_id=None, timing_source=None)
    base.update(over)
    return SimpleNamespace(**base)


def test_slowest_carries_its_baseline_and_the_ratio():
    baselines = {"fp-pay": {"mean_ms": 900.0, "stddev_ms": 40.0, "p95_ms": 980.0, "sample_count": 14,
                            "last_observed_ms": 1800, "last_observed_at": T0}}
    block = svc.slowest_block([_slow_row("fp-pay", 1800), _slow_row("fp-new", 900)], baselines,
                              limit=20, agg=_agg())
    first, second = block["items"]
    assert first["baseline"]["mean_ms"] == 900.0 and first["baseline_ratio"] == 2.0
    assert second["baseline"] is None and second["baseline_ratio"] is None


def test_slowest_without_durations_is_not_measured():
    block = svc.slowest_block([], {}, limit=20, agg=_agg(duration_count=0))
    assert block["measured"] is False and block["reason"] == svc.REASON_NO_DURATIONS


def test_a_zero_sample_baseline_is_no_baseline():
    assert svc._baseline_block(SimpleNamespace(sample_count=0, mean_ms=0.0, stddev_ms=0.0, p95_ms=None,
                                               last_observed_ms=None, last_observed_at=None)) is None


def _lane(worker, count, start_s, end_s, total=2):
    return SimpleNamespace(worker_id=worker, test_count=count, busy_ms=count * 1000,
                           started_at=T0 + timedelta(seconds=start_s), finished_at=T0 + timedelta(seconds=end_s),
                           lane_total=total)


def test_lanes_without_timestamps_say_the_report_carried_none():
    block = svc.workers_block([], _agg(timed_count=0, worker_rows=0))
    assert block["measured"] is False and block["reason"] == svc.REASON_NO_TIMESTAMPS
    assert block["lanes"] == []


def test_lanes_are_ordered_by_worker_number_and_span_their_tests():
    block = svc.workers_block(
        [_lane("10", 2, 0, 3, 3), _lane("1", 5, 0, 9, 3), _lane("0", 6, 1, 8, 3)], _agg(),
    )
    assert [lane["worker_id"] for lane in block["lanes"]] == ["0", "1", "10"]
    assert block["lanes"][1]["span_ms"] == 9000 and block["measured"] is True
    assert block["total"] == 3 and block["truncated"] is False


def test_timed_tests_without_a_worker_are_one_unnamed_lane():
    block = svc.workers_block([_lane(None, 12, 0, 9, 1)], _agg(worker_rows=0))
    assert block["measured"] is False and block["reason"] == svc.REASON_NO_WORKERS
    assert block["lanes"][0]["worker_id"] is None


def test_lanes_report_the_cap():
    block = svc.workers_block([_lane(str(i), 1, 0, 1, svc.MAX_LANES + 5) for i in range(svc.MAX_LANES)], _agg())
    assert block["total"] == svc.MAX_LANES + 5 and block["truncated"] is True


def _bar(start_s, end_s, worker="0"):
    return SimpleNamespace(test_id=uuid.uuid4(), test_name="t", suite_name="s", status="PASSED",
                           worker_id=worker, started_at=T0 + timedelta(seconds=start_s),
                           finished_at=T0 + timedelta(seconds=end_s), duration_ms=int((end_s - start_s) * 1000))


def test_the_timeline_is_ordered_by_start_with_offsets_from_the_origin():
    agg = _agg(timed_count=3, first_started_at=T0, last_finished_at=T0 + timedelta(seconds=9))
    block = svc.timeline_block([_bar(5, 9), _bar(0, 4.5), _bar(1.25, 3.05, worker="1")], agg, limit=2000)
    assert [item["offset_ms"] for item in block["items"]] == [0, 1250, 5000]
    assert block["origin"] == T0 and block["end"] == T0 + timedelta(seconds=9)
    assert block["truncated"] is False and block["total"] == 3


def test_the_timeline_says_it_was_cut():
    agg = _agg(timed_count=5000)
    block = svc.timeline_block([_bar(0, 1)] * 2, agg, limit=2)
    assert block["truncated"] is True and block["total"] == 5000 and len(block["items"]) == 2


def test_an_old_run_has_no_timeline_and_says_why():
    block = svc.timeline_block([], _agg(timed_count=0), limit=2000)
    assert block["measured"] is False and block["reason"] == "report carried no timestamps"
    assert block["origin"] is None and block["items"] == []


class _RunDB:
    """Answers the run-timing statements in order; records what was asked."""

    def __init__(self, run, agg):
        self.run, self.agg, self.sql = run, agg, []

    async def execute(self, stmt, params=None):
        text = str(stmt)
        self.sql.append(text)
        result = SimpleNamespace()
        if "FROM test_runs" in text and "test_cases" not in text:
            result.one_or_none = lambda: self.run
        elif "COUNT(*) AS test_count" in text:
            result.one = lambda: self.agg
        else:
            rows: list = []
            result.all = lambda: rows
            result.scalars = lambda: SimpleNamespace(all=lambda: rows)
            result.one_or_none = lambda: None
            result.scalar_one_or_none = lambda: None
        return result


@pytest.mark.asyncio
async def test_run_timing_of_an_unknown_run_is_none():
    db = _RunDB(None, None)
    assert await svc.run_timing(db, RUN) is None


@pytest.mark.asyncio
async def test_an_old_run_skips_the_timeline_and_lane_reads(monkeypatch):
    run = SimpleNamespace(id=RUN, project_id=PROJECT, status="PASSED", build_number="42", branch="main",
                          created_at=T0, duration_ms=12500, wall_clock_ms=None, timing_source="derived_sum",
                          exec_started_at=None, exec_finished_at=None)
    db = _RunDB(run, _agg(timed_count=0, worker_rows=0, first_started_at=None, last_finished_at=None))
    findings = AsyncMock(return_value={"measured": False, "reason": "not enough history (4 of 10)"})
    monkeypatch.setattr(svc.regressions, "get_duration_regressions", findings)
    out = await svc.run_timing(db, RUN)
    assert out["run"]["timing_source"] == "derived_sum" and out["run"]["wall_clock_ms"] is None
    assert out["timeline"]["measured"] is False
    assert out["timeline"]["reason"] == "report carried no timestamps"
    assert not any("tc.started_at IS NOT NULL AND tc.finished_at IS NOT NULL\nGROUP" in s for s in db.sql)
    assert not any("worker_id, COUNT(*)" in s for s in db.sql)
    assert out["duration_findings"]["run_id"] == RUN
    from app.models.schemas import RunTimingResponse

    RunTimingResponse(**out)


@pytest.mark.asyncio
async def test_a_failing_findings_read_costs_only_its_own_block(monkeypatch):
    run = SimpleNamespace(id=RUN, project_id=PROJECT, status="PASSED", build_number="42", branch=None,
                          created_at=T0, duration_ms=10, wall_clock_ms=None, timing_source=None,
                          exec_started_at=None, exec_finished_at=None)
    db = _RunDB(run, _agg(test_count=0, timed_count=0, duration_count=0, summed_ms=None))
    monkeypatch.setattr(svc.regressions, "get_duration_regressions", AsyncMock(side_effect=RuntimeError("x")))
    out = await svc.run_timing(db, RUN)
    assert out["duration_findings"] is None
    assert out["timeline"]["reason"] == svc.REASON_NO_RESULTS


def test_the_slowest_read_follows_the_run_duration_index():
    """``ix_test_cases_run_duration (test_run_id, duration_ms DESC NULLS LAST)``:
    the statement is keyed by the run and ordered the index's way, with a LIMIT."""
    sql = str(svc._SLOWEST_SQL)
    assert "WHERE tc.test_run_id = :run_id" in sql
    assert "ORDER BY tc.duration_ms DESC NULLS LAST" in sql and "LIMIT :limit" in sql
    timeline = str(svc._TIMELINE_SQL)
    assert "ORDER BY tc.duration_ms DESC NULLS LAST" in timeline and "LIMIT :limit" in timeline


# ── Suite history assembly ───────────────────────────────────────────────────


def _hist_row(i, wall, summed=None):
    return SimpleNamespace(run_id=uuid.uuid4(), build_number=str(i), run_created_at=T0 - timedelta(hours=i),
                           branch="main", run_status="PASSED", source="derived" if wall else "derived_sum",
                           wall_ms=wall, summed_ms=summed if summed is not None else (wall or 1000) * 2,
                           parallelism=2.0 if wall else None, started_at=None, finished_at=None,
                           test_count=3, timed_count=3, passed=3, failed=0, broken=0, skipped=0, unknown=0,
                           suite_name="Checkout")


def test_suite_history_is_oldest_first_and_says_it_was_cut():
    rows = [_hist_row(i, 1000 + i) for i in range(3)]  # newest first, as SQL returns them
    stats = SimpleNamespace(total=7, wall_n=7, wall_p50=1001.0, wall_p95=1002.0, summed_n=7, summed_p50=2000.0,
                            summed_p95=2004.0, parallelism_p50=2.0)
    out = svc.assemble_suite_history(_scope(suites=("checkout",), days=90), "checkout", rows, stats,
                                     branch="main", limit=3)
    assert [p["build_number"] for p in out["points"]] == ["2", "1", "0"]
    assert out["truncated"] is True and out["total"] == 7
    assert out["suite_name"] == "Checkout" and out["stats"]["wall"]["p95_ms"] == 1002.0


def test_a_suite_without_a_wall_clock_has_null_wall_percentiles():
    rows = [_hist_row(0, None, summed=500)]
    stats = SimpleNamespace(total=1, wall_n=0, wall_p50=None, wall_p95=None, summed_n=1, summed_p50=500.0,
                            summed_p95=500.0, parallelism_p50=None)
    out = svc.assemble_suite_history(_scope(suites=("checkout",)), "checkout", rows, stats, branch=None, limit=10)
    assert out["points"][0]["wall_ms"] is None
    assert out["stats"]["wall"]["measured"] is False and out["stats"]["wall"]["p50_ms"] is None
    assert out["stats"]["summed"]["p50_ms"] == 500.0


def test_a_suite_that_did_not_run_in_the_window_says_so():
    stats = SimpleNamespace(total=0)
    out = svc.assemble_suite_history(_scope(suites=("Checkout",)), "checkout", [], stats, branch=None, limit=10)
    assert out["measured"] is False and out["reason"] == svc.REASON_SUITE_NOT_IN_WINDOW
    assert out["suite_key"] == "checkout" and out["project_id"] == PROJECT


# ── Regressions ──────────────────────────────────────────────────────────────


def _history(key, branch, walls):
    """``walls`` newest first."""
    return [
        SimpleNamespace(suite_key=key, branch=branch, suite_name=key.title(), wall_ms=w, summed_ms=w,
                        test_run_id=uuid.uuid4(), run_created_at=T0 - timedelta(hours=i))
        for i, w in enumerate(walls)
    ]


def test_five_slow_runs_over_twenty_normal_ones_are_a_regression():
    rows = _history("checkout", "main", [2000] * 5 + [1000] * 20)
    out = svc.judge_suite_regressions(rows, top=20)
    assert out["measured"] is True and out["evaluated"] == 1 and out["total"] == 1
    item = out["items"][0]
    assert item["suite_key"] == "checkout" and item["branch"] == "main" and item["ratio"] == 2.0
    assert item["metric"] == "wall" and item["recent_median_ms"] == 2000.0 and item["baseline_median_ms"] == 1000.0
    assert item["was_slow_before"] is True  # the run before was already in the slow window
    assert item["latest_run_id"] == rows[0].test_run_id


def test_four_runs_are_not_enough_history():
    out = svc.judge_suite_regressions(_history("payments", "main", [2000] * 4), top=20)
    assert out["items"] == [] and out["measured"] is False
    assert out["reason"] == "not enough history (4 of 15)" and out["considered"] == 1


def test_two_slow_runs_do_not_move_the_median():
    out = svc.judge_suite_regressions(_history("checkout", "main", [2000] * 2 + [1000] * 23), top=20)
    assert out["measured"] is True and out["items"] == []


def test_each_branch_is_judged_on_its_own_history():
    rows = _history("checkout", "main", [2000] * 5 + [1000] * 20) + _history("checkout", "dev", [1000] * 25)
    out = svc.judge_suite_regressions(rows, top=20)
    assert out["considered"] == 2 and out["evaluated"] == 2
    assert [(i["suite_key"], i["branch"]) for i in out["items"]] == [("checkout", "main")]


def test_no_suite_in_the_window_is_not_a_clean_bill():
    out = svc.judge_suite_regressions([], top=20)
    assert out["measured"] is False and out["reason"] == svc.REASON_NO_SUITES_IN_WINDOW


def test_regressions_are_cut_to_top_after_counting():
    rows = []
    for index in range(3):
        rows += _history(f"s{index}", "main", [1000 * (2 + index)] * 5 + [1000] * 20)
    out = svc.judge_suite_regressions(rows, top=2)
    assert out["total"] == 3 and [i["suite_key"] for i in out["items"]] == ["s2", "s1"]


def _candidate(fp, mean):
    return SimpleNamespace(test_fingerprint=fp, test_name=f"t_{fp}", suite_name="s", mean_ms=mean, p95_ms=mean * 1.2,
                           sample_count=14)


def test_a_test_whose_window_median_doubled_is_listed():
    out = svc.judge_test_regressions(
        [_candidate("fp1", 100.0), _candidate("fp2", 100.0), _candidate("fp3", 100.0)],
        [SimpleNamespace(test_fingerprint="fp1", n=6, p50=200.0), SimpleNamespace(test_fingerprint="fp2", n=6,
                                                                                  p50=120.0)],
        top=20, flag_on=True,
    )
    assert out["measured"] is True and out["considered"] == 2
    assert [(i["test_fingerprint"], i["ratio"]) for i in out["items"]] == [("fp1", 2.0)]


def test_no_baseline_names_the_flag_when_it_is_off():
    off = svc.judge_test_regressions([], [], top=20, flag_on=False)
    assert off["measured"] is False and "perf_regression_detection" in off["reason"]
    on = svc.judge_test_regressions([], [], top=20, flag_on=True)
    assert "perf_regression_detection" not in on["reason"]


# ── Histogram, per day, suites table ─────────────────────────────────────────


def test_the_histogram_always_has_every_bucket():
    row = SimpleNamespace(n=3, p50=40.0, p95=400000.0, **{f"b{i}": 0 for i in range(14)})
    row.b0, row.b3, row.b13 = 1, 1, 1
    block = svc.histogram_block(row)
    assert len(block["buckets"]) == len(svc.HISTOGRAM_EDGES_MS) == 14
    assert block["buckets"][0] == {"lower_ms": 0, "upper_ms": 10, "count": 1}
    assert block["buckets"][-1] == {"lower_ms": 300000, "upper_ms": None, "count": 1}
    assert sum(b["count"] for b in block["buckets"]) == block["n"] == 3
    empty = svc.histogram_block(SimpleNamespace(n=0, p50=None, p95=None))
    assert empty["measured"] is False and empty["p50_ms"] is None and len(empty["buckets"]) == 14
    assert all(b["count"] == 0 for b in empty["buckets"])


def test_the_histogram_buckets_cover_every_duration_once():
    columns = svc._histogram_bucket_columns()
    assert "COUNT(*) FILTER (WHERE tc.duration_ms < 10) AS b0" in columns
    assert "COUNT(*) FILTER (WHERE tc.duration_ms >= 10 AND tc.duration_ms < 25) AS b1" in columns
    assert "COUNT(*) FILTER (WHERE tc.duration_ms >= 300000) AS b13" in columns


def test_per_day_has_every_day_and_null_for_nothing_measured():
    start = T0.replace(hour=0, minute=0) - timedelta(days=2)
    rows = [
        SimpleNamespace(day=(T0.date() - timedelta(days=2)).isoformat(), runs=2, wall_ms=18000, wall_runs=2,
                        summed_ms=34000, summed_runs=2, both_summed_ms=34000, both_wall_ms=18000),
        SimpleNamespace(day=T0.date().isoformat(), runs=1, wall_ms=None, wall_runs=0, summed_ms=12500,
                        summed_runs=1, both_summed_ms=None, both_wall_ms=None),
    ]
    per_day, totals = svc.per_day_block(rows, grain="run", start=start, now=T0)
    assert [p["runs"] for p in per_day["points"]] == [2, 0, 1]
    assert per_day["points"][1]["wall_clock_ms"] is None and per_day["points"][1]["summed_ms"] is None
    assert per_day["points"][2]["wall_clock_ms"] is None  # ran, but no wall clock: not 0
    assert totals == {"runs": 3, "wall_clock_ms": 18000, "wall_runs": 2, "summed_ms": 46500,
                      "summed_runs": 3, "parallelism": 1.89}


def test_suite_change_is_against_the_previous_window():
    row = SimpleNamespace(suite_key="checkout", suite_name="Checkout", suite_total=1,
                          cur_runs=5, cur_wall_n=5, cur_wall_p50=1100.0, cur_wall_p95=1500.0,
                          cur_summed_n=5, cur_summed_p50=2200.0, cur_summed_p95=3000.0,
                          prev_runs=0, prev_wall_n=0, prev_wall_p50=None, prev_wall_p95=None,
                          prev_summed_n=0, prev_summed_p50=None, prev_summed_p95=None)
    table = svc.suites_table([row], limit=50)
    item = table["items"][0]
    assert item["current"]["wall"]["p50_ms"] == 1100.0 and item["previous"]["wall"]["measured"] is False
    assert item["wall_p50_change_pct"] is None  # nothing to compare with: not 0
    row.prev_runs, row.prev_wall_n, row.prev_wall_p50 = 4, 4, 1000.0
    assert svc.suites_table([row], limit=50)["items"][0]["wall_p50_change_pct"] == 10.0


# ── Every analytics statement carries the tenant rule ────────────────────────


@pytest.mark.parametrize("builder", ["suite_history", "per_day_run", "per_day_suite", "suites", "pairs", "window",
                                     "slowest", "histogram"])
def test_every_statement_is_tenant_scoped(builder):
    params: dict = {}
    scope = _scope(suites=("checkout",), releases=(str(uuid.uuid4()),))
    sql = {
        "suite_history": lambda: svc._suite_history_where(params, scope, "checkout", "main"),
        "per_day_run": lambda: svc._per_day_run_sql(params, scope, "main"),
        "per_day_suite": lambda: svc._per_day_suite_sql(params, scope, "main", ("checkout",)),
        "suites": lambda: svc._suites_sql(params, scope, "main", ("checkout",)),
        "pairs": lambda: svc._regression_pairs_sql(params, scope, "main", ("checkout",)),
        "window": lambda: svc._window_p50_sql(params, scope, "main"),
        "slowest": lambda: svc._slowest_tests_sql(params, scope, "main"),
        "histogram": lambda: svc._histogram_sql(params, scope, "main"),
    }[builder]()
    assert "tr.project_id = :project_id" in sql
    assert "IN (SELECT id FROM projects WHERE is_active)" in sql
    assert "tr.primary_release_id = :release_id" in sql
    assert ":branch" in sql and params["branch"] == "main"
    assert params["project_id"] == str(PROJECT)
    if "suite_executions se" in sql:
        assert "se.project_id = :se_project_id" in sql and params["se_project_id"] == PROJECT
    # No null-tolerant bind anywhere.
    assert "IS NULL OR" not in sql
