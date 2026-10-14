"""EXEC-03: the chart-data run-grain metrics ``wall_clock_total`` / ``_p50`` / ``_p95``.

A run's wall clock (``test_runs.wall_clock_ms``, EXEC-02) only exists per run,
so these metrics are answered on the ``run_aggregate`` grain and a request that
would force the row grain -- a row-level dimension or a suite filter -- is a
422 ``run_only_metric``, never a number from the wrong population. A run
without a wall clock is left out (not 0) and ``n`` counts the runs carrying
one. The acceptance matrix over every ``group_by`` is
``tests/test_chart_data_combinations.py``; the real SQL is
``tests/integration/test_execution_metrics_postgres.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.core.analytics_errors import AnalyticsQueryError
from app.services import chart_data_service as svc
from app.services import chart_rows_service as rows
from app.services.analytics_scope import AnalyticsScope

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
WALL = ("wall_clock_total", "wall_clock_p50", "wall_clock_p95")


def _scope(**kwargs) -> AnalyticsScope:
    base = dict(
        project_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        allowed_project_ids=None, release_ids=(), suite_names=(), days=30,
    )
    base.update(kwargs)
    return AnalyticsScope(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize("metric", WALL)
@pytest.mark.parametrize("group_by", [("day",), ("week",), ("branch",), ("day", "environment"),
                                      ("release",), ("day", "ingestion_source")])
def test_run_level_requests_are_answered_on_the_run_grain(metric, group_by):
    spec = svc.parse_chart_spec(metric, list(group_by), None, scope=_scope())
    assert svc.grain_for(spec, _scope()) == svc.GRAIN_RUN
    assert svc.grain_forced_by(spec, _scope()) is None
    sql, _params = svc.build_statement(spec, _scope(), now=NOW)
    assert "FROM test_runs tr" in sql and "test_cases" not in sql


@pytest.mark.parametrize("metric", WALL)
@pytest.mark.parametrize("group_by", [("suite",), ("status",), ("day", "failure_category"), ("day", "status")])
def test_a_row_level_dimension_is_refused(metric, group_by):
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_chart_spec(metric, list(group_by), None, scope=_scope())
    assert exc.value.code == "run_only_metric" and exc.value.status_code == 422
    assert exc.value.param == "metric"
    assert exc.value.allowed["row_level_dimensions"] == ["suite", "status", "failure_category", "test"]


@pytest.mark.parametrize("metric", WALL)
def test_a_suite_filter_is_refused(metric):
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_chart_spec(metric, ["day"], None, scope=_scope(suite_names=("checkout",)))
    assert exc.value.code == "run_only_metric"
    # A whitespace-only suite filters nothing anywhere, so it refuses nothing here.
    svc.parse_chart_spec(metric, ["day"], None, scope=_scope(suite_names=("  ",)))


def test_other_refusals_keep_their_precedence():
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_chart_spec("wall_clock_p95", ["test"], None, scope=_scope())
    assert exc.value.code == "test_requires_scope"
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_chart_spec("wall_clock_p95", ["suite", "test"], None, scope=_scope())
    assert exc.value.code == "high_cardinality_pair"


def test_a_spec_that_skipped_the_parser_is_refused_too():
    spec = svc.ChartSpec(metric="wall_clock_total", group_by=("suite",), top_n=None)
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.build_statement(spec, _scope(), now=NOW)
    assert exc.value.code == "run_only_metric"


def test_the_statement_reads_the_wall_clock_and_leaves_runs_without_one_out():
    for metric, value in (
        ("wall_clock_total", "SUM(tr.wall_clock_ms)"),
        ("wall_clock_p50", "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY tr.wall_clock_ms) "
                           "FILTER (WHERE tr.wall_clock_ms IS NOT NULL)"),
        ("wall_clock_p95", "PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY tr.wall_clock_ms) "
                           "FILTER (WHERE tr.wall_clock_ms IS NOT NULL)"),
    ):
        spec = svc.parse_chart_spec(metric, ["day"], None, scope=_scope())
        sql, _ = svc.build_statement(spec, _scope(), now=NOW)
        assert f"{value} AS metric_value" in sql, metric
        assert "COUNT(tr.wall_clock_ms) AS metric_sample" in sql
        # Never COALESCEd to 0: a bucket of runs without a wall clock stays NULL.
        assert "COALESCE(SUM(tr.wall_clock_ms)" not in sql


def _cell(x, value, sample):
    return svc.Cell(x=x, x_label=x, series="value", series_label="value", passed=0, failed=0, broken=0,
                    skipped=0, unknown=0, executions=3, value=value, sample=sample)


def test_a_bucket_whose_runs_have_no_wall_clock_is_not_measured():
    for metric in WALL:
        spec = svc.parse_chart_spec(metric, ["day"], None, scope=_scope())
        payload = svc.assemble(spec, [_cell("2026-10-13", None, 0), _cell("2026-10-14", 9000.0, 2)],
                               buckets=["2026-10-12", "2026-10-13", "2026-10-14"], x_type="time")
        points = payload["series"][0]["points"]
        assert points[1]["y"] is None and points[1]["measured"] is False, metric
        assert points[2]["y"] == 9000 and points[2]["n"] == 2
        empty = points[0]
        if metric == "wall_clock_total":
            # Nothing ran that day: zero ms on the clock is a true zero.
            assert empty["y"] == 0 and empty["measured"] is True
        else:
            # A percentile of nothing is not measured.
            assert empty["y"] is None and empty["measured"] is False


def test_a_percentile_cannot_be_combined_into_other():
    spec = svc.parse_chart_spec("wall_clock_p95", ["branch"], 1, scope=_scope())
    payload = svc.assemble(spec, [_cell("main", 9000.0, 3), _cell("dev", 4000.0, 2), _cell("x", 1.0, 1)],
                           buckets=None, x_type="category")
    other = payload["series"][0]["points"][-1]
    assert other["x"] == svc.OTHER_KEY and other["measured"] is False
    assert "95th percentile" in other["reason"]


def test_definitions_say_what_a_wall_clock_is():
    for metric in WALL:
        spec = svc.parse_chart_spec(metric, ["day"], None, scope=_scope())
        text = svc.definitions(spec, svc.GRAIN_RUN, NOW)[metric]
        assert "wall clock" in text and "not counted in n" in text


def test_a_wall_clock_drill_lists_the_runs_that_carry_one():
    for metric in WALL:
        assert rows.ROW_PREDICATES[metric] == "tr.wall_clock_ms IS NOT NULL"
        assert rows.RECONCILIATION[metric] == ("n", "distinct_runs")
    req = rows.parse_rows_request("wall_clock_p95", ["day"], None, {"day": "2026-10-14"}, 1, 50, scope=_scope())
    sql, _ = rows.build_rows_statement(req, _scope(), now=NOW)
    assert "AND (tr.wall_clock_ms IS NOT NULL)" in sql
    with pytest.raises(AnalyticsQueryError) as exc:
        rows.parse_rows_request("wall_clock_p95", ["suite"], None, {"suite": "a"}, 1, 50, scope=_scope())
    assert exc.value.code == "run_only_metric"
