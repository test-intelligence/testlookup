"""VIZ-506-BE -- ``/analytics/test-scatter``, everything a database cannot answer.

The golden numbers (p95 against ``percentile_cont`` computed in Python, the
exclusion counts of a world with one test of each kind) live in
``tests/integration/test_test_scatter_postgres.py``. Here: refusals, the
statement text (every value a bind), and the assembly rules that make a point
placeable -- the log-axis floor, the rate over evaluated executions, medians
only when there are points, and the C3 ``points`` contract on the output.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.analytics_errors import AnalyticsQueryError
from app.models.viz_contracts import validate_contract
from app.services import chart_data_service as charts
from app.services import test_scatter_service as svc
from app.services.analytics_scope import AnalyticsScope

FROZEN = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
PROJECT = uuid.UUID("11111111-1111-1111-1111-111111111111")
HOSTILE = (
    "failures'; DROP TABLE test_cases; --",
    "volume OR 1=1",
    "<img src=x onerror=alert(1)>",
    "Failures",
    "",
)


def _scope(**kwargs) -> AnalyticsScope:
    base = dict(project_id=PROJECT, allowed_project_ids=None, release_ids=(), suite_names=(), days=30)
    base.update(kwargs)
    return AnalyticsScope(**base)  # type: ignore[arg-type]


def _spec(min_executions=5, limit=2000, order="failures") -> svc.ScatterSpec:
    return svc.parse_scatter_spec(min_executions, limit, order)


def _point(fp, *, label=None, executions=10, passed=8, failed=1, broken=1, p95=120.0,
           qualifying=None, rn=1):
    return SimpleNamespace(
        part="point", fp=fp, label=label or f"test_{fp}", executions=executions, passed=passed,
        failed=failed, broken=broken, p95=p95, qualifying=qualifying, reason=None, tests=None, rn=rn,
    )


def _excluded(reason, tests):
    return SimpleNamespace(part="excluded", reason=reason, tests=tests, qualifying=None)


def _chart(payload: dict) -> dict:
    return {key: value for key, value in payload.items()
            if key not in (*svc.ENVELOPE_KEYS, "definitions")}


# ── 1. refusals ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", HOSTILE)
def test_an_unknown_order_is_refused_and_never_echoed(value) -> None:
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_scatter_spec(5, 2000, value)
    assert exc.value.code == "order_enum"
    assert exc.value.allowed == ["failures", "volume"]
    if value.strip():
        assert value not in exc.value.message


@pytest.mark.parametrize("value", [0, -1, 1001, True, "5", 2.5])
def test_min_executions_is_bounded(value) -> None:
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_scatter_spec(value, 2000, "failures")
    assert exc.value.code == "min_executions_range"
    assert exc.value.allowed == {"min": 1, "max": 1000}


@pytest.mark.parametrize("value", [0, 5001, False])
def test_limit_is_bounded_by_the_contract_s_point_cap(value) -> None:
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_scatter_spec(5, value, "failures")
    assert exc.value.code == "limit_range"
    assert exc.value.allowed == {"min": 1, "max": 5000}


def test_the_defaults_are_the_epic_s() -> None:
    assert (svc.DEFAULT_MIN_EXECUTIONS, svc.DEFAULT_LIMIT, svc.DEFAULT_ORDER) == (5, 2000, "failures")
    assert svc.MAX_LIMIT == 5000
    assert _spec(1, 5000, "volume") == svc.ScatterSpec(1, 5000, "volume")


# ── 2. the statement ───────────────────────────────────────────────────────


def test_every_value_is_a_bind() -> None:
    texts = set()
    for min_executions, limit in ((1, 1), (5, 2000), (1000, 5000)):
        sql, params = svc.build_statement(_spec(min_executions, limit), _scope(), now=FROZEN)
        texts.add(sql)
        assert params["scatter_min_executions"] == min_executions
        assert params["scatter_limit"] == limit
    assert len(texts) == 1


def test_the_order_picks_a_hard_coded_ranking() -> None:
    failures, _ = svc.build_statement(_spec(order="failures"), _scope(), now=FROZEN)
    volume, _ = svc.build_statement(_spec(order="volume"), _scope(), now=FROZEN)
    assert "ORDER BY bad DESC, executions DESC, fp ASC" in failures
    assert "ORDER BY executions DESC, bad DESC, fp ASC" in volume


def test_the_p95_ignores_untimed_executions() -> None:
    sql, _ = svc.build_statement(_spec(), _scope(), now=FROZEN)
    assert (
        "PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY tc.duration_ms)\n"
        "                    FILTER (WHERE tc.duration_ms IS NOT NULL) AS p95"
    ) in sql


def test_exclusion_reasons_are_tested_in_their_stated_order() -> None:
    """A test is counted ONCE, under the first reason: the three counts add up
    to the tests left out."""
    sql, params = svc.build_statement(_spec(), _scope(), now=FROZEN)
    below = sql.index("THEN :reason_below_min")
    evaluated = sql.index("THEN :reason_no_evaluated")
    duration = sql.index("THEN :reason_no_duration")
    assert below < evaluated < duration
    assert "WHEN executions < :scatter_min_executions" in sql
    assert "WHEN passed + failed + broken = 0" in sql
    assert "WHEN timed = 0" in sql
    assert (params["reason_below_min"], params["reason_no_evaluated"], params["reason_no_duration"]) == svc.REASONS


def test_the_chart_s_scope_bounds_the_aggregate() -> None:
    rel = str(uuid.UUID(int=4))
    scope = _scope(release_ids=(rel,), suite_names=("Payments", "Orders"))
    sql, params = svc.build_statement(_spec(), scope, now=FROZEN)
    assert "tr.created_at >= :period_start" in sql
    assert params["period_start"] == charts.window_start(30, now=FROZEN)
    assert "AND tr.project_id = :project_id" in sql and "is_active" in sql
    assert "AND tr.primary_release_id = :release_id" in sql
    assert params["suite_names"] == ["payments", "orders"]


# ── 3. assembly ────────────────────────────────────────────────────────────


def test_a_point_is_p95_by_failure_rate_by_volume() -> None:
    payload = svc.assemble(_spec(), [_point("a", executions=10, passed=3, failed=1, broken=0,
                                             p95=1840.456, qualifying=1)])
    (point,) = payload["points"]
    # 6 of the 10 were skipped or unknown: outside the denominator.
    assert point == {"id": "a", "label": "test_a", "x": 1840.46, "y": 25.0, "size": 10, "n": 4}
    validate_contract("chart_series", _chart(payload))


def test_a_sub_millisecond_p95_is_drawn_at_the_log_axis_floor() -> None:
    """M-506e's neighbour: 0 cannot sit on a log axis, and is not left out
    either -- the test HAS a duration, it is just under a millisecond."""
    payload = svc.assemble(_spec(), [_point("a", p95=0.0, qualifying=1),
                                     _point("b", p95=0.4, qualifying=1, rn=2)])
    assert [point["x"] for point in payload["points"]] == [1.0, 1.0]
    assert payload["medians"]["x"] == 1.0
    validate_contract("chart_series", _chart(payload))


def test_no_duration_is_not_floored() -> None:
    assert svc.place_x(None) is None
    assert svc.place_x(0) == 1.0
    assert svc.place_x(2.345) == 2.35


def test_failure_rate_over_nothing_evaluated_is_none_not_zero() -> None:
    """M-506a's guard: skipped in the denominator would read 0% here."""
    assert svc.failure_rate(0, 0, 0) is None
    assert svc.failure_rate(3, 1, 0) == 25.0
    assert svc.failure_rate(0, 1, 1) == 100.0


def test_medians_are_unweighted_over_the_returned_points() -> None:
    rows = [
        _point("a", executions=1000, passed=1000, failed=0, broken=0, p95=10, qualifying=3),
        _point("b", executions=5, passed=4, failed=1, broken=0, p95=300, qualifying=3, rn=2),
        _point("c", executions=5, passed=0, failed=5, broken=0, p95=50, qualifying=3, rn=3),
    ]
    payload = svc.assemble(_spec(), rows)
    assert payload["medians"] == {"x": 50.0, "y": 20.0}


def test_medians_of_an_even_count_are_the_midpoint() -> None:
    rows = [_point("a", p95=10, passed=1, failed=0, broken=0, qualifying=2),
            _point("b", p95=30, passed=0, failed=1, broken=0, qualifying=2, rn=2)]
    assert svc.assemble(_spec(), rows)["medians"] == {"x": 20.0, "y": 50.0}


def test_no_point_means_no_medians_and_the_exclusions_say_why() -> None:
    rows = [_excluded("below_min_executions", 14), _excluded("no_evaluated", 1)]
    payload = svc.assemble(_spec(), rows)
    assert payload["points"] == []
    assert "medians" not in payload, "medians of nothing must be absent, never (0, 0)"
    assert payload["excluded"] == {"below_min_executions": 14, "no_evaluated": 1, "no_duration": 0}
    assert payload["truncated"] is False and payload["truncated_total"] is None
    validate_contract("chart_series", _chart(payload))


def test_more_qualifying_tests_than_the_limit_is_truncated_and_counted() -> None:
    payload = svc.assemble(_spec(limit=2), [
        _point("a", qualifying=7), _point("b", qualifying=7, rn=2),
    ])
    assert len(payload["points"]) == 2
    assert payload["truncated"] is True and payload["truncated_total"] == 7


def test_hostile_labels_round_trip_as_data() -> None:
    rows = [_point(fp, label=label, qualifying=4, rn=i)
            for i, (fp, label) in enumerate((
                ("__proto__", "constructor"),
                ("constructor", "<img src=x onerror=alert(1)>"),
                ("p", "x" * 1000),
                ("q", "__proto__"),
            ), start=1)]
    payload = svc.assemble(_spec(), rows)
    assert [point["label"] for point in payload["points"]] == [
        "constructor", "<img src=x onerror=alert(1)>", "x" * 1000, "__proto__",
    ]
    validate_contract("chart_series", _chart(payload))


def test_the_axes_are_the_contract_s() -> None:
    payload = svc.assemble(_spec(), [])
    assert payload["x"] == {"key": "p95_duration_ms", "label": "p95 duration (ms)", "unit": "ms",
                            "scale": "log"}
    assert payload["y"]["unit"] == "percent" and payload["y"]["scale"] == "linear"
    assert payload["size"] == {"key": "executions", "label": "Executions"}


def test_the_definitions_state_the_floor_the_denominator_and_the_precedence() -> None:
    defs = svc.definitions(_spec(min_executions=7))
    assert "1 ms" in defs["x_floor"]
    assert "outside the denominator" in defs["y"]
    assert "fewer than 7 executions" in defs["excluded"]
    assert "first reason" in defs["excluded"]
    assert "absent" in defs["medians"]


def test_the_cache_identity_is_canonical() -> None:
    a = svc.cache_identity_parts(_scope(suite_names=("B", "a")), _spec())
    b = svc.cache_identity_parts(_scope(suite_names=("A", "b")), _spec())
    assert a == b
    assert a != svc.cache_identity_parts(_scope(), _spec(order="volume"))
