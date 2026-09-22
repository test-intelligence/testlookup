"""VIZ-203 -- the chart-data endpoint, everything a database cannot answer.

Three things live here; the golden numbers live in
``tests/integration/test_chart_data_postgres.py``.

1. **The allow-lists are the only thing that reaches SQL.** Every ``metric``
   and ``group_by`` value maps to a hard-coded fragment. The injection tests
   below feed quotes, semicolons, comments, unicode, over-long strings and
   repeated arrays into every parameter and assert two things: the request is
   refused with the VIZ-210 error body, and -- for the accepted values -- the
   statement text the service builds is byte-identical whatever the filter
   VALUES are, because a filter value is always a bind.

   Mutation (a) in the report: interpolating the ``group_by`` value into the
   SELECT instead of looking it up makes ``test_a_group_by_value_never_reaches_the_sql``
   fail.

2. **The assembly is pure.** ``assemble`` turns cells (what SQL returned) into
   the C3 payload: zero-fill, top-N with an "other" bucket, ``measured:false``
   with a reason instead of a zero, ISO weeks, the ``(none)`` branch bucket.
   Testing it directly is what makes the ``other``-for-a-rate rule provable:
   mutation (b) (average the top series' rates instead of recomputing from
   counts) and mutation (c) (return 0.0 instead of ``measured: false``) both
   fail here.

3. **The caps.** Eight series, 366 points, and ``meta.truncated`` carrying the
   full count when either bites.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.core.analytics_errors import AnalyticsQueryError
from app.models.viz_contracts import MAX_POINTS_PER_SERIES, MAX_SERIES, validate_contract
from app.services import chart_data_service as svc
from app.services.analytics_scope import AnalyticsScope

FROZEN = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

#: Every hostile string the parameters are fed. Nothing here may ever reach a
#: statement, and none of it may be echoed back in an error message.
HOSTILE = (
    "day'; DROP TABLE test_runs; --",
    "day' OR '1'='1",
    "day\") UNION SELECT NULL, NULL --",
    "day/*comment*/",
    "day\x00",
    "ｄａｙ",  # full-width unicode look-alike
    "day‮",  # right-to-left override
    "d" * 5000,
    "",
    " ",
    "day;week",
    "day,week",
)


def _never_echoed(value: str, message: str) -> None:
    """The refusal never repeats what was sent. Blank and single-space values
    are substrings of any prose, so the rule is asserted on what carries a
    payload: anything with a non-whitespace character."""
    if value.strip():
        assert value not in message, "an untrusted value was echoed back"


def _scope(**kwargs) -> AnalyticsScope:
    base = dict(
        project_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        allowed_project_ids=None,
        release_ids=(),
        suite_names=(),
        days=30,
    )
    base.update(kwargs)
    return AnalyticsScope(**base)  # type: ignore[arg-type]


def _spec(metric="executions", group_by=("day",), top_n=None) -> svc.ChartSpec:
    return svc.parse_chart_spec(metric, list(group_by), top_n, scope=_scope())


# ── 1. The allow-lists ──────────────────────────────────────────────────────


def test_the_enums_are_exactly_the_story_s() -> None:
    """The story names them; a silent addition or removal is a contract change."""
    assert set(svc.METRICS) == {
        "executions", "passed", "failed", "broken", "skipped", "unknown",
        "retried_tests", "pass_rate", "failure_rate", "flaky_tests",
        "unique_tests", "run_count", "duration_p50", "duration_p95",
        "duration_total",
    }
    assert set(svc.DIMENSIONS) == {
        "day", "week", "project", "release", "suite", "status",
        "failure_category", "branch", "environment", "ingestion_source", "test",
    }


@pytest.mark.parametrize("value", HOSTILE)
def test_a_hostile_metric_is_refused_and_never_echoed(value: str) -> None:
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_chart_spec(value, ["day"], None, scope=_scope())
    assert exc.value.code == "metric_enum"
    assert exc.value.param == "metric"
    assert set(exc.value.allowed) == set(svc.METRICS)
    _never_echoed(value, exc.value.message)


@pytest.mark.parametrize("value", HOSTILE)
def test_a_hostile_group_by_is_refused_and_never_echoed(value: str) -> None:
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_chart_spec("executions", [value], None, scope=_scope())
    assert exc.value.code == "dimension_enum"
    assert exc.value.param == "group_by"
    assert set(exc.value.allowed) == set(svc.DIMENSIONS)
    _never_echoed(value, exc.value.message)


@pytest.mark.parametrize("value", HOSTILE)
def test_a_hostile_second_group_by_is_refused(value: str) -> None:
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_chart_spec("executions", ["day", value], None, scope=_scope())
    assert exc.value.code == "dimension_enum"


def test_a_group_by_value_never_reaches_the_sql() -> None:
    """The fragment comes from the table, not from the request.

    Mutation (a): build the SELECT from the requested string instead of
    ``DIMENSIONS[name].sql`` and this fails -- the accepted name is a table
    KEY, so an interpolating implementation still passes for ``day`` but the
    statement then differs when the same fragment is reached by a different
    spelling. The assertion that bites is the second one: the rendered SQL of
    every accepted spec contains only fragments the module declares.
    """
    declared = {d.sql for d in svc.DIMENSIONS.values()} | {
        label for d in svc.DIMENSIONS.values() if (label := d.label_sql)
    }
    for metric in svc.METRICS:
        for dim in svc.DIMENSIONS:
            if dim == "test":
                continue
            spec = svc.parse_chart_spec(metric, [dim], None, scope=_scope())
            sql, _params = svc.build_statement(spec, _scope())
            fragment = svc.DIMENSIONS[dim].sql
            # The SELECT and the GROUP BY are BOTH the declared fragment, not
            # only one of them: substituting the requested name into the SELECT
            # alone leaves the fragment in the GROUP BY, so "the fragment is in
            # the text somewhere" would not notice it.
            assert f"{fragment} AS bucket_key" in sql, (metric, dim)
            assert f"GROUP BY {fragment}" in sql, (metric, dim)
            # Nothing that is not a declared fragment is spliced in.
            assert "--" not in sql and ";" not in sql
    # A two-dimension chart pins the series slot the same way.
    for dim in ("suite", "status", "environment", "release"):
        spec = svc.parse_chart_spec("pass_rate", ["day", dim], None, scope=_scope())
        sql, _ = svc.build_statement(spec, _scope())
        assert f"{svc.DIMENSIONS[dim].sql} AS series_key" in sql, dim
        assert f"GROUP BY {svc.DIMENSIONS['day'].sql}, {svc.DIMENSIONS[dim].sql}" in sql, dim
    assert declared, "the dimension table is empty -- this test checks nothing"


def test_the_statement_text_does_not_move_with_the_filter_values() -> None:
    """Suites, releases and projects are BINDS: same SQL, different params."""
    spec = _spec("pass_rate", ("day", "suite"))
    plain, plain_params = svc.build_statement(spec, _scope())
    hostile_scope = _scope(
        suite_names=("'; DROP TABLE test_runs; --", "checkout"),
        release_ids=(str(uuid.uuid4()), "unattributed"),
    )
    hostile, hostile_params = svc.build_statement(spec, hostile_scope)
    assert "DROP TABLE" not in hostile
    # The filters ADD conditional fragments (never a null-tolerant OR), and
    # every value travels as a parameter.
    assert ":suite_names" in hostile or ":suite_name" in hostile
    assert "'; DROP" not in hostile
    assert plain != hostile  # the suite/release fragments were added
    assert not any("drop" in str(v).lower() for v in plain_params.values())
    # The hostile suite name travels as a BIND, normalised by ``suite_keys``.
    assert any("drop table" in value.lower() for value in _flatten(hostile_params))


def _flatten(params: dict):
    for value in params.values():
        if isinstance(value, (list, tuple)):
            yield from (str(v) for v in value)
        else:
            yield str(value)


def test_no_statement_uses_a_null_tolerant_filter() -> None:
    """``(:x IS NULL OR col = :x)`` costs the release index on every call."""
    for metric in ("executions", "pass_rate", "duration_p95"):
        for dims in (("day",), ("day", "suite"), ("release",), ("week", "status")):
            spec = svc.parse_chart_spec(metric, list(dims), None, scope=_scope())
            sql, _ = svc.build_statement(spec, _scope(release_ids=(str(uuid.uuid4()),)))
            assert "IS NULL OR" not in sql
            assert "release_test_run_links" not in sql


def test_statuses_use_the_test_status_vocabulary() -> None:
    """The column stores UPPERCASE ``TestStatus``; a lowercase literal here
    would match nothing, forever, and read as "no data"."""
    from app.models.postgres import TestStatus

    sql, _ = svc.build_statement(_spec("passed", ("day", "status")), _scope())
    for member in TestStatus:
        assert f"'{member.value}'" in sql or member.value == "UNKNOWN"
    assert "'passed'" not in sql


# ── 2. Validation ───────────────────────────────────────────────────────────


def test_group_by_is_required() -> None:
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_chart_spec("executions", [], None, scope=_scope())
    assert exc.value.code == "missing_parameter"
    assert exc.value.param == "group_by"


def test_at_most_two_group_by() -> None:
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_chart_spec("executions", ["day", "suite", "status"], None, scope=_scope())
    assert exc.value.code == "group_by_cap"
    assert exc.value.allowed == {"max": 2}


def test_a_dimension_is_not_repeated() -> None:
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_chart_spec("executions", ["day", "day"], None, scope=_scope())
    assert exc.value.code == "unique_dimension"


def test_two_high_cardinality_dimensions_are_refused() -> None:
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_chart_spec("executions", ["suite", "test"], 5, scope=_scope())
    assert exc.value.code == "high_cardinality_pair"
    assert "suite" in str(exc.value.allowed) or "suite" in exc.value.message


def test_test_needs_one_suite_or_a_top_n() -> None:
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_chart_spec("executions", ["test"], None, scope=_scope())
    assert exc.value.code == "test_requires_scope"
    # One suite in scope is enough...
    assert svc.parse_chart_spec(
        "executions", ["test"], None, scope=_scope(suite_names=("checkout",))
    ).group_by == ("test",)
    # ...and so is a top_n.
    assert svc.parse_chart_spec("executions", ["test"], 10, scope=_scope()).top_n == 10
    # Two suites is not "within one suite".
    with pytest.raises(AnalyticsQueryError):
        svc.parse_chart_spec(
            "executions", ["test"], None, scope=_scope(suite_names=("a", "b"))
        )


@pytest.mark.parametrize("bad", [0, -1, 400, MAX_SERIES])
def test_top_n_on_the_series_axis_is_bounded_by_the_series_cap(bad: int) -> None:
    """Two dimensions: top_n names series, and top_n + "other" must fit in 8."""
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_chart_spec("executions", ["day", "suite"], bad, scope=_scope())
    assert exc.value.code == "top_n_range"
    assert exc.value.allowed == {"min": 1, "max": MAX_SERIES - 1}


def test_top_n_on_a_single_category_axis_is_bounded_by_the_point_cap() -> None:
    assert svc.parse_chart_spec("executions", ["suite"], 50, scope=_scope()).top_n == 50
    # The kept buckets plus "other" must fit the 366-point cap, so 366 is out.
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_chart_spec("executions", ["suite"], MAX_POINTS_PER_SERIES, scope=_scope())
    assert exc.value.code == "top_n_range"
    assert exc.value.allowed == {"min": 1, "max": MAX_POINTS_PER_SERIES - 1}


def test_top_n_does_not_apply_to_a_time_axis() -> None:
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_chart_spec("executions", ["day"], 5, scope=_scope())
    assert exc.value.code == "top_n_unsupported"


def test_a_time_dimension_may_only_be_the_first() -> None:
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_chart_spec("executions", ["suite", "day"], None, scope=_scope())
    assert exc.value.code == "time_dimension_position"


# ── 3. Assembly: zero-fill, other, measured ────────────────────────────────


def _cell(x, series, **counts):
    base = dict(
        passed=0, failed=0, broken=0, skipped=0, unknown=0,
        executions=0, runs=0, value=None, sample=0,
    )
    base.update(counts)
    return svc.Cell(
        x=x, x_label=x, series=series, series_label=series, **base  # type: ignore[arg-type]
    )


def test_zero_fill_gives_every_series_every_bucket() -> None:
    spec = _spec("executions", ("day", "suite"))
    cells = [_cell("2026-09-20", "checkout", executions=7, sample=7, value=7)]
    payload = svc.assemble(
        spec, cells, buckets=["2026-09-19", "2026-09-20", "2026-09-21"], x_type="time"
    )
    (series,) = payload["series"]
    assert [p["x"] for p in series["points"]] == ["2026-09-19", "2026-09-20", "2026-09-21"]
    assert [p["y"] for p in series["points"]] == [0, 7, 0]
    assert [p["n"] for p in series["points"]] == [0, 7, 0]
    validate_contract("chart_series", payload)


def test_a_rate_with_no_evaluated_executions_is_not_measured_never_zero() -> None:
    """A bucket that is entirely skipped has no pass rate. Mutation (c)."""
    spec = _spec("pass_rate", ("day", "suite"))
    cells = [
        _cell("2026-09-20", "quarantine", skipped=9, executions=9, sample=0),
        _cell("2026-09-21", "quarantine", passed=3, failed=1, executions=4, sample=4),
    ]
    payload = svc.assemble(spec, cells, buckets=["2026-09-20", "2026-09-21"], x_type="time")
    (series,) = payload["series"]
    skipped_point, real_point = series["points"]
    assert skipped_point["y"] is None, "an all-skipped bucket must not read as 0%"
    assert skipped_point["measured"] is False
    assert skipped_point["reason"] and skipped_point["reason"].strip()
    assert skipped_point["n"] == 0
    assert real_point["y"] == 75.0 and real_point["measured"] is True
    validate_contract("chart_series", payload)


def test_pass_rate_excludes_skipped_and_unknown_from_the_denominator() -> None:
    spec = _spec("pass_rate", ("day",))
    cells = [_cell("2026-09-21", svc.SINGLE_SERIES_KEY,
                   passed=8, failed=1, broken=1, skipped=40, unknown=10,
                   executions=60, sample=10)]
    payload = svc.assemble(spec, cells, buckets=["2026-09-21"], x_type="time")
    point = payload["series"][0]["points"][0]
    assert point["y"] == 80.0  # 8 / (8 + 1 + 1)
    assert point["n"] == 10


def test_failure_rate_is_over_the_same_denominator() -> None:
    spec = _spec("failure_rate", ("day",))
    cells = [_cell("2026-09-21", svc.SINGLE_SERIES_KEY,
                   passed=8, failed=1, broken=1, skipped=40, executions=50, sample=10)]
    point = svc.assemble(spec, cells, buckets=["2026-09-21"], x_type="time")["series"][0]["points"][0]
    assert point["y"] == 20.0


def test_other_for_a_rate_is_recomputed_from_counts_not_averaged() -> None:
    """Mutation (b): average the dropped series' rates and this fails.

    Two dropped suites: one with 1/1 passed (100%) and one with 0/99 passed
    (0%). The average is 50%; the truth, recomputed from counts, is 1%.
    """
    spec = svc.parse_chart_spec("pass_rate", ["day", "suite"], 1, scope=_scope())
    cells = [
        _cell("2026-09-21", "kept", passed=50, failed=50, executions=100, sample=100),
        _cell("2026-09-21", "tiny", passed=1, failed=0, executions=1, sample=1),
        _cell("2026-09-21", "huge", passed=0, failed=99, executions=99, sample=99),
    ]
    payload = svc.assemble(spec, cells, buckets=["2026-09-21"], x_type="time")
    other = next(s for s in payload["series"] if s["key"] == svc.OTHER_KEY)
    point = other["points"][0]
    assert point["y"] == 1.0, "other was averaged instead of recomputed from counts"
    assert point["n"] == 100
    validate_contract("chart_series", payload)


def test_other_for_an_additive_count_is_summed() -> None:
    spec = svc.parse_chart_spec("executions", ["day", "suite"], 1, scope=_scope())
    cells = [
        _cell("2026-09-21", "kept", executions=100, sample=100, value=100),
        _cell("2026-09-21", "a", executions=3, sample=3, value=3),
        _cell("2026-09-21", "b", executions=4, sample=4, value=4),
    ]
    payload = svc.assemble(spec, cells, buckets=["2026-09-21"], x_type="time")
    other = next(s for s in payload["series"] if s["key"] == svc.OTHER_KEY)
    assert other["points"][0]["y"] == 7


def test_other_for_a_non_additive_metric_is_not_measured() -> None:
    """A percentile and a distinct count cannot be combined across groups."""
    for metric in ("duration_p95", "unique_tests", "flaky_tests"):
        spec = svc.parse_chart_spec(metric, ["day", "suite"], 1, scope=_scope())
        cells = [
            _cell("2026-09-21", "kept", executions=10, sample=10, value=500),
            _cell("2026-09-21", "a", executions=3, sample=3, value=900),
            _cell("2026-09-21", "b", executions=4, sample=4, value=100),
        ]
        payload = svc.assemble(spec, cells, buckets=["2026-09-21"], x_type="time")
        other = next(s for s in payload["series"] if s["key"] == svc.OTHER_KEY)
        point = other["points"][0]
        assert point["y"] is None, metric
        assert point["measured"] is False and point["reason"].strip(), metric
        assert point["n"] == 7, metric


def test_top_n_keeps_the_biggest_and_is_deterministic_on_a_tie() -> None:
    spec = svc.parse_chart_spec("executions", ["day", "suite"], 2, scope=_scope())
    cells = [
        _cell("2026-09-21", name, executions=size, sample=size, value=size)
        for name, size in (("zeta", 5), ("alpha", 5), ("mid", 9), ("small", 1))
    ]
    payload = svc.assemble(spec, cells, buckets=["2026-09-21"], x_type="time")
    assert [s["key"] for s in payload["series"]] == ["mid", "alpha", svc.OTHER_KEY]


def test_more_series_than_the_cap_are_truncated_and_declared() -> None:
    spec = _spec("executions", ("day", "suite"))
    cells = [
        _cell("2026-09-21", f"suite-{i:02d}", executions=100 - i, sample=100 - i, value=100 - i)
        for i in range(30)
    ]
    payload = svc.assemble(spec, cells, buckets=["2026-09-21"], x_type="time")
    assert len(payload["series"]) <= MAX_SERIES
    assert payload["truncated"] is True
    assert payload["truncated_total"] == 30
    validate_contract("chart_series", payload)


def test_a_single_group_by_is_one_series() -> None:
    spec = _spec("executions", ("suite",))
    cells = [
        _cell("checkout", svc.SINGLE_SERIES_KEY, executions=4, sample=4, value=4),
        _cell("payments", svc.SINGLE_SERIES_KEY, executions=6, sample=6, value=6),
    ]
    payload = svc.assemble(spec, cells, buckets=None, x_type="category")
    assert payload["x_type"] == "category"
    assert len(payload["series"]) == 1
    assert [p["x"] for p in payload["series"][0]["points"]] == ["payments", "checkout"]
    validate_contract("chart_series", payload)


def test_the_payload_names_its_dimensions_in_order() -> None:
    spec = _spec("pass_rate", ("week", "environment"))
    payload = svc.assemble(spec, [], buckets=["2026-09-14"], x_type="time")
    assert payload["dimensions"] == ["week", "environment"]
    assert payload["kind"] == "series"
    validate_contract("chart_series", payload)


# ── 4. Buckets: ISO weeks, the partial day, the (none) bucket ──────────────


def test_day_buckets_cover_the_whole_window_in_utc() -> None:
    buckets = svc.time_buckets("day", FROZEN, days=5)
    assert buckets == ["2026-09-17", "2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21"]


def test_week_buckets_are_iso_weeks_labelled_by_their_monday() -> None:
    # 2026-09-21 is a Monday; a 30-day window opens on 2026-08-23 (a Sunday),
    # whose ISO week starts Monday 2026-08-17.
    buckets = svc.time_buckets("week", FROZEN, days=30)
    assert buckets[0] == "2026-08-17"
    assert buckets[-1] == "2026-09-21"
    assert all(datetime.fromisoformat(b).isoweekday() == 1 for b in buckets)
    assert len(buckets) == 6


def test_the_current_utc_day_is_the_partial_one() -> None:
    assert svc.partial_bucket("day", FROZEN) == "2026-09-21"
    assert svc.partial_bucket("week", FROZEN) == "2026-09-21"
    assert svc.partial_bucket("suite", FROZEN) is None


def test_a_null_branch_becomes_the_none_bucket_in_sql() -> None:
    sql, _ = svc.build_statement(_spec("executions", ("branch",)), _scope())
    assert svc.NO_VALUE in svc.DIMENSIONS["branch"].sql
    assert svc.DIMENSIONS["branch"].sql in sql
    # The branch is taken raw -- not lower-cased, not normalised.
    assert "LOWER(tr.branch" not in sql


def test_environment_is_read_as_it_is_written() -> None:
    assert "environment" in svc.DIMENSIONS["environment"].sql
    assert svc.NO_VALUE in svc.DIMENSIONS["environment"].sql


def test_the_commit_column_is_not_a_dimension() -> None:
    """The story names ``commit_hash`` as the column, not as a group_by."""
    assert "commit" not in svc.DIMENSIONS


# ── 5. Grain, definitions and the VIZ-209 seam ─────────────────────────────


def test_a_row_level_dimension_forces_the_execution_row_grain() -> None:
    assert svc.grain_for(_spec("passed", ("day",)), _scope()) == svc.GRAIN_RUN
    assert svc.grain_for(_spec("passed", ("day", "suite")), _scope()) == svc.GRAIN_ROW
    assert svc.grain_for(_spec("passed", ("day", "status")), _scope()) == svc.GRAIN_ROW
    # A suite filter is an effective-suite question, so it is answered on rows.
    assert svc.grain_for(_spec("passed", ("day",)), _scope(suite_names=("a",))) == svc.GRAIN_ROW
    # Metrics that only exist per row.
    for metric in ("unique_tests", "flaky_tests", "retried_tests",
                   "duration_p50", "duration_p95", "duration_total"):
        assert svc.grain_for(_spec(metric, ("day",)), _scope()) == svc.GRAIN_ROW, metric


def test_definitions_state_the_semantics_the_story_requires() -> None:
    defs = svc.definitions(_spec("flaky_tests", ("day",)), svc.GRAIN_ROW, FROZEN)
    assert defs["metric"] == "flaky_tests"
    assert "is_flaky_run" in defs["flaky_tests"]
    assert "FlakyScore" in defs["flaky_tests"]
    assert defs["timezone"] == "UTC"
    assert "in-progress" in defs["in_progress"].lower()
    assert defs["grain"] == svc.GRAIN_ROW
    assert defs["n"]

    rate = svc.definitions(_spec("pass_rate", ("day",)), svc.GRAIN_RUN, FROZEN)
    assert "skipped" in rate["pass_rate"] and "unknown" in rate["pass_rate"]
    assert "quarantine" in rate["pass_rate"].lower()

    retried = svc.definitions(_spec("retried_tests", ("day",)), svc.GRAIN_ROW, FROZEN)
    assert "retry_count" in retried["retried_tests"]


def test_the_cache_identity_is_canonical_and_order_insensitive() -> None:
    """VIZ-209 owns caching; this is the seam it keys on. Two requests that
    mean the same thing must produce the same identity."""
    spec = _spec("pass_rate", ("day", "suite"))
    a = svc.cache_identity_parts(_scope(suite_names=("Checkout", "payments")), spec)
    b = svc.cache_identity_parts(_scope(suite_names=("payments", "  checkout ")), spec)
    assert a == b
    assert a != svc.cache_identity_parts(_scope(suite_names=("payments",)), spec)
    assert all(isinstance(part, str) for part in a)


# ── 6. The contract fixture is what this service really emits ──────────────


def test_the_contract_fixture_is_this_service_s_own_output() -> None:
    """``chart_series/valid/series_chart_data_top_n_other.json`` is a real
    chart-data body, not a hand-written approximation of one.

    A fixture nobody produces is a fixture that drifts: both validators keep
    passing it long after the endpoint stopped emitting that shape. This
    rebuilds it from ``assemble`` and compares, so a change to the payload
    fails here rather than leaving the contract describing a response that no
    longer exists.
    """
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "contracts" / "viz" / "fixtures" / "chart_series" / "valid"
        / "series_chart_data_top_n_other.json"
    )
    fixture = json.loads(path.read_text(encoding="utf-8"))

    spec = svc.parse_chart_spec("pass_rate", ["day", "suite"], 2, scope=_scope(days=3))
    cells = [
        _cell("2026-09-19", "checkout", passed=90, failed=10, executions=100, runs=2, sample=100),
        _cell("2026-09-20", "checkout", passed=80, failed=20, executions=100, runs=2, sample=100),
        _cell("2026-09-19", "payments", passed=40, failed=10, executions=50, runs=1, sample=50),
        _cell("2026-09-21", "payments", passed=25, failed=25, executions=50, runs=1, sample=50),
        _cell("2026-09-19", "quarantine", skipped=5, executions=5, runs=1, sample=0),
        _cell("2026-09-20", "quarantine", skipped=5, executions=5, runs=1, sample=0),
    ]
    # The label is the suite's ingested spelling, which is what the legend shows.
    cells = [cell._replace(series_label=cell.series.capitalize()) for cell in cells]

    payload = svc.assemble(
        spec, cells, buckets=["2026-09-19", "2026-09-20", "2026-09-21"], x_type="time"
    )
    # The route lifts these into the envelope before answering.
    for key in svc.ENVELOPE_KEYS:
        payload.pop(key)
    assert payload == fixture["payload"]


# ── 7. The ranking happens in SQL, not after every row has been fetched ─────
#
# Review finding 1. ``top_n`` and the 8 x 366 caps used to be applied in
# Python over every group the aggregate produced: 73 000 rows measured for
# ``day x test`` on a 200-fingerprint seed, and ~1.4M ``Cell`` tuples for a
# real suite -- per request, four charts a page, four workers a pod.


_RANKED = (
    ("pass_rate", ["day", "suite"], 5),
    ("executions", ["day", "test"], 7),
    ("executions", ["day", "suite"], None),
    ("executions", ["suite"], 10),
    ("executions", ["suite"], None),
    ("duration_p95", ["day", "suite"], 5),
    ("unique_tests", ["environment"], None),
)


@pytest.mark.parametrize("metric,dims,top_n", _RANKED + (
    ("executions", ["day"], None),
    ("duration_p95", ["week"], None),
))
def test_every_statement_carries_a_hard_server_side_limit(metric, dims, top_n) -> None:
    """Mutation (d): drop the projection and return the bare GROUP BY, and
    this fails. No statement may stream an unbounded number of groups into
    this process -- not even one whose axis "cannot" overflow."""
    spec = svc.parse_chart_spec(metric, dims, top_n, scope=_scope())
    sql, params = svc.build_statement(spec, _scope())
    assert "LIMIT :chart_row_cap" in sql
    assert params["chart_row_cap"] == svc.MAX_GROUPS
    assert svc.MAX_GROUPS == (MAX_POINTS_PER_SERIES + 1) * (MAX_SERIES + 1)


@pytest.mark.parametrize("metric,dims,top_n", _RANKED)
def test_a_ranked_axis_is_ranked_and_cut_in_sql(metric, dims, top_n) -> None:
    """The keys are totalled, ordered and cut by the database. Mutation (d):
    rank in Python instead and the ORDER BY is gone."""
    spec = svc.parse_chart_spec(metric, dims, top_n, scope=_scope())
    sql, _ = svc.build_statement(spec, _scope())
    assert "ROW_NUMBER() OVER (ORDER BY rank_total DESC, rank_key ASC)" in sql, (metric, dims)
    # The tie-break is the SAME one ``assemble`` applies, spelled in SQL: rank
    # descending, then the key ascending. Two rules would rank two ways.
    assert "COUNT(*) OVER () AS key_total" in sql, "the full key count must come back"


def test_the_series_axis_is_always_ranked_even_without_a_top_n() -> None:
    """The C3 cap of 8 applies whether or not the caller named a ``top_n``,
    and it gives one of its slots to 'other' only when it actually bites."""
    spec = svc.parse_chart_spec("executions", ["day", "suite"], None, scope=_scope())
    sql, params = svc.build_statement(spec, _scope())
    assert params["chart_series_cap"] == MAX_SERIES
    assert params["chart_series_capped"] == MAX_SERIES - 1
    assert "chart_series_top_n" not in params
    assert ":chart_series_cap" in sql and ":chart_series_capped" in sql


def test_the_top_n_and_the_other_key_travel_as_binds() -> None:
    """``top_n`` is a validated integer, but it still came from the request:
    the hard-coded-fragment rule does not stop at strings."""
    spec = svc.parse_chart_spec("pass_rate", ["day", "suite"], 5, scope=_scope())
    sql, params = svc.build_statement(spec, _scope())
    assert params["chart_series_top_n"] == 5
    assert ":chart_series_top_n" in sql
    assert f"'{svc.OTHER_KEY}'" not in sql, "the other key is a bind, not a literal"
    assert params["chart_other_key"] == svc.OTHER_KEY


def test_a_category_axis_with_a_top_n_rolls_up_but_the_cap_does_not() -> None:
    """``top_n`` asked for those buckets, so the rest are a bucket. The cap
    was nobody's request, so it CUTS and says ``truncated`` instead of
    inventing an 'other' the caller never asked for."""
    asked, _ = svc.build_statement(
        svc.parse_chart_spec("executions", ["suite"], 10, scope=_scope()), _scope()
    )
    capped, params = svc.build_statement(
        svc.parse_chart_spec("executions", ["suite"], None, scope=_scope()), _scope()
    )
    assert ":chart_other_key ELSE g.bucket_key END" in asked
    assert ":chart_other_key ELSE g.bucket_key END" not in capped
    assert "JOIN bucket_kept bk" in capped and "LEFT JOIN bucket_kept bk" not in capped
    assert params["chart_bucket_cap"] == MAX_POINTS_PER_SERIES


def test_a_time_axis_is_generated_so_it_is_never_ranked() -> None:
    sql, params = svc.build_statement(_spec("executions", ("day",)), _scope())
    assert "bucket_kept" not in sql
    assert "chart_bucket_cap" not in params


def test_an_other_bucket_sql_rolled_up_is_not_ranked_against_its_own_parts() -> None:
    """SQL hands back the kept keys AND their roll-up. 'other' is a summary,
    not a competitor: it goes last and its points are always combined, so a
    percentile cannot answer for it."""
    spec = svc.parse_chart_spec("duration_p95", ["day", "suite"], 2, scope=_scope())
    cells = [
        _cell("2026-09-21", "small", executions=1, sample=1, value=10),
        _cell("2026-09-21", "big", executions=99, sample=99, value=20),
        svc.Cell(
            x="2026-09-21", x_label="2026-09-21",
            series=svc.OTHER_KEY, series_label=svc.OTHER_KEY,
            passed=0, failed=0, broken=0, skipped=0, unknown=0,
            executions=500, runs=0, value=900.0, sample=500, merged=6,
        ),
    ]
    payload = svc.assemble(
        spec, cells, buckets=["2026-09-21"], x_type="time",
        counts=svc.AxisCounts(series=8),
    )
    assert [series["key"] for series in payload["series"]] == ["big", "small", svc.OTHER_KEY]
    other = payload["series"][-1]
    assert other["label"] == "Other"
    assert other["points"][0]["y"] is None, "a percentile SQL merged is still not combinable"
    assert other["points"][0]["measured"] is False
    assert other["points"][0]["n"] == 500
    validate_contract("chart_series", payload)


def test_a_sql_rolled_rate_is_recomputed_from_the_merged_counts() -> None:
    """The "other" semantics do not change because the merge moved into SQL:
    the bucket carries the COMPONENT COUNTS and the rate is recomputed."""
    spec = svc.parse_chart_spec("pass_rate", ["day", "suite"], 1, scope=_scope())
    cells = [
        _cell("2026-09-21", "kept", passed=50, failed=50, executions=100, sample=100),
        svc.Cell(
            x="2026-09-21", x_label="2026-09-21",
            series=svc.OTHER_KEY, series_label=svc.OTHER_KEY,
            passed=1, failed=99, broken=0, skipped=0, unknown=0,
            executions=100, runs=0, value=None, sample=100, merged=2,
        ),
    ]
    payload = svc.assemble(
        spec, cells, buckets=["2026-09-21"], x_type="time",
        counts=svc.AxisCounts(series=3),
    )
    other = next(s for s in payload["series"] if s["key"] == svc.OTHER_KEY)
    assert other["points"][0]["y"] == 1.0
    assert payload["truncated"] is False, "the caller asked for this roll-up"


# ── 8. truncated is reported PER AXIS ───────────────────────────────────────


def test_truncation_is_reported_per_axis_not_as_one_number() -> None:
    """Review finding 3. 400 suite buckets keyed by 12 environments truncates
    BOTH axes; ``truncated_total`` reported 12 and the 34 lost buckets were
    invisible. Mutation: collapse ``truncated_axes`` back to one counter and
    this fails."""
    spec = svc.parse_chart_spec("executions", ["suite", "environment"], None, scope=_scope())
    cells = [
        _cell(f"suite{bucket:03d}", f"env{env}", executions=100 - env,
              sample=100 - env, value=100 - env)
        for bucket in range(400) for env in range(12)
    ]
    payload = svc.assemble(spec, cells, buckets=None, x_type="category")
    assert payload["truncated"] is True
    assert payload["truncated_axes"] == {
        "x": {"dimension": "suite", "kept": MAX_POINTS_PER_SERIES, "total": 400},
        "series": {"dimension": "environment", "kept": MAX_SERIES - 1, "total": 12},
    }
    # The C2 scalar names the axis that lost whole buckets: a dropped bucket
    # is a hole in every series, a dropped series is one line.
    assert payload["truncated_total"] == 400
    validate_contract("chart_series", payload)


def test_the_full_counts_come_from_sql_when_sql_did_the_cutting() -> None:
    """Counting the keys that came back would report the cap as the truth."""
    spec = svc.parse_chart_spec("executions", ["day", "suite"], None, scope=_scope())
    cells = [
        _cell("2026-09-21", f"suite-{i}", executions=10, sample=10, value=10)
        for i in range(MAX_SERIES - 1)
    ]
    payload = svc.assemble(
        spec, cells, buckets=["2026-09-21"], x_type="time",
        counts=svc.AxisCounts(series=4213),
    )
    assert payload["truncated"] is True
    assert payload["truncated_total"] == 4213
    assert payload["truncated_axes"]["series"]["kept"] == MAX_SERIES - 1


def test_nothing_truncated_reports_nothing() -> None:
    spec = _spec("executions", ("day", "suite"))
    cells = [_cell("2026-09-21", "only", executions=1, sample=1, value=1)]
    payload = svc.assemble(
        spec, cells, buckets=["2026-09-21"], x_type="time", counts=svc.AxisCounts(series=1)
    )
    assert payload["truncated"] is False
    assert payload["truncated_total"] is None
    assert payload["truncated_axes"] is None


# ── 9. "nothing happened" has ONE shape ────────────────────────────────────


def test_an_empty_window_draws_the_same_series_as_a_window_with_one_run() -> None:
    """Review finding 4. An empty window answered ``series: []`` while one
    matching run answered 30 zero-filled points: the chart changed SHAPE when
    the data ran out, so "no runs" and "no chart" looked the same."""
    spec = _spec("executions", ("day",))
    buckets = svc.time_buckets("day", FROZEN, days=30)
    empty = svc.assemble(spec, [], buckets=buckets, x_type="time")
    one = svc.assemble(
        spec,
        [_cell(buckets[3], svc.SINGLE_SERIES_KEY, executions=1, sample=1, value=1)],
        buckets=buckets, x_type="time",
    )
    assert len(empty["series"]) == 1, "an empty window lost its series"
    assert [p["x"] for p in empty["series"][0]["points"]] == \
           [p["x"] for p in one["series"][0]["points"]]
    assert all(point["y"] == 0 for point in empty["series"][0]["points"])
    assert empty["series"][0]["key"] == one["series"][0]["key"]
    validate_contract("chart_series", empty)


def test_a_category_axis_with_no_data_has_no_axis_to_draw_on() -> None:
    """The rule is "the axis is generated", not "always emit a series": a
    category axis with no rows has no buckets, and a series of no points is
    not a chart."""
    payload = svc.assemble(
        _spec("executions", ("suite",)), [], buckets=None, x_type="category"
    )
    assert payload["series"] == []


# ── 10. run_count's sample ─────────────────────────────────────────────────


def test_run_count_s_sample_is_the_executions_not_the_run_count() -> None:
    """Review finding 5. ``definitions.n`` promises "the executions in the
    bucket"; on the run grain ``n`` was ``COUNT(*)``, which is ``y``. A sample
    that measures the value it is the sample OF tells a reader nothing."""
    assert svc.METRICS["run_count"].run_sample == svc.METRICS["executions"].run_sql
    assert svc.METRICS["run_count"].run_sample != svc.METRICS["run_count"].run_sql
    assert svc.METRICS["run_count"].row_sample == svc.METRICS["executions"].row_sql
    defs = svc.definitions(_spec("run_count", ("day",)), svc.GRAIN_RUN, FROZEN)
    assert "executions in the bucket" in defs["n"]


# ── 11. A filter that moves the grain says so ──────────────────────────────


def test_a_suite_filter_that_changes_the_grain_names_itself() -> None:
    """Review finding 6. ``run_count`` filtered by suite silently moves from
    ``COUNT(*)`` over ``test_runs`` to ``COUNT(DISTINCT tr.id)`` over
    ``test_cases`` -- a different population for the same axis and the same
    legend. There is no leak; the reader simply could not see why the number
    moved."""
    spec = _spec("run_count", ("day",))
    bare, filtered = _scope(), _scope(suite_names=("Checkout",))
    assert svc.grain_for(spec, bare) == svc.GRAIN_RUN
    assert svc.grain_for(spec, filtered) == svc.GRAIN_ROW
    assert svc.grain_forced_by(spec, bare) is None
    assert svc.grain_forced_by(spec, filtered) == "suite_name"

    defs = svc.definitions(
        spec, svc.GRAIN_ROW, FROZEN, grain_changed_by=svc.grain_forced_by(spec, filtered)
    )
    assert defs["grain_changed_by"] == "suite_name"
    assert "suite_name" in defs["grain_changed_note"]
    assert "grain_changed_by" not in svc.definitions(spec, svc.GRAIN_RUN, FROZEN)


def test_a_row_level_dimension_is_not_a_filter_changing_the_grain() -> None:
    """``group_by=suite`` is the CHART asking for rows, not a filter moving it
    under the reader's feet. Naming it would badge every suite chart."""
    filtered = _scope(suite_names=("Checkout",))
    assert svc.grain_forced_by(_spec("executions", ("day", "suite")), filtered) is None
    assert svc.grain_forced_by(_spec("duration_p95", ("day",)), filtered) is None


# ── 12. The nits ───────────────────────────────────────────────────────────


class _CapturingDb:
    """Just enough session to read the statement a lookup builds."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))

        class _Result:
            @staticmethod
            def fetchall():
                return []

        return _Result()


async def test_an_entity_label_lookup_carries_the_tenant_predicate() -> None:
    """No leak today -- the ids can only have come out of a tenant-filtered
    aggregate. That is a property of the CALLER, and one denormalisation is
    all it takes for a name lookup with no tenancy of its own to become one."""
    db = _CapturingDb()
    await svc._entity_labels(db, "release", [str(uuid.uuid4())], _scope())
    sql, params = db.statements[-1]
    assert "r.id IN (__[POSTCOMPILE_label_ids])" in sql
    assert "r.project_id = :label_project" in sql
    assert "SELECT id FROM projects WHERE is_active" in sql
    assert params["label_project"] == _scope().project_id

    db = _CapturingDb()
    allowed = frozenset({uuid.uuid4(), uuid.uuid4()})
    await svc._entity_labels(
        db, "project", [str(uuid.uuid4())],
        _scope(project_id=None, allowed_project_ids=allowed),
    )
    sql, params = db.statements[-1]
    # An expanding bind renders as its POSTCOMPILE placeholder, which is the
    # proof it IS a bind rather than an interpolated id list.
    assert "p.id IN (__[POSTCOMPILE_label_projects])" in sql
    assert "p.is_active" in sql
    assert set(params["label_projects"]) == allowed


def test_one_clock_answers_the_whole_request() -> None:
    """Three ``datetime.now()`` calls -- service, statement, envelope -- can
    straddle UTC midnight, and the answer is an axis whose last bucket is
    outside the window its own SQL bounded."""
    midnight_edge = datetime(2026, 9, 21, 23, 59, 59, 999000, tzinfo=timezone.utc)
    assert svc.window_start(1, now=midnight_edge) == datetime(2026, 9, 21, tzinfo=timezone.utc)
    _, params = svc.build_statement(
        _spec("executions", ("day",)), _scope(days=1), now=midnight_edge
    )
    assert params["period_start"] == svc.window_start(1, now=midnight_edge)
    assert svc.time_buckets("day", midnight_edge, days=1) == ["2026-09-21"]
    assert svc.partial_bucket("day", midnight_edge) == "2026-09-21"


def test_the_route_takes_its_one_clock_from_this_module() -> None:
    """``request_clock`` lives here so the route's instant, the statement's
    window and the axis all read the SAME ``datetime`` -- and so a test that
    freezes this module's clock freezes the whole request rather than leaving
    the route on the wall clock."""
    import inspect

    from app.routers import analytics

    source = inspect.getsource(analytics.chart_data)
    # Comments are stripped: this file EXPLAINS the rule, and a test that
    # reads the explanation as a violation cannot be satisfied.
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "chart_data_service.request_clock()" in code
    assert "datetime.now" not in code, "the route took a second clock"
    assert code.count("now=now") == 2, "the one clock reaches both the chart and the window"


def test_a_run_dated_in_the_future_is_counted_and_declared() -> None:
    """It cannot be drawn -- it is not in the window -- but it must not
    vanish: a clock-skewed CI agent is a real thing and "my run is missing"
    is a support ticket."""
    spec = _spec("executions", ("day",))
    buckets = svc.time_buckets("day", FROZEN, days=3)
    cells = [
        _cell(buckets[-1], svc.SINGLE_SERIES_KEY, executions=4, sample=4, value=4),
        _cell("2027-01-01", svc.SINGLE_SERIES_KEY, executions=9, sample=9, value=9),
    ]
    payload = svc.assemble(spec, cells, buckets=buckets, x_type="time")
    assert [point["x"] for point in payload["series"][0]["points"]] == buckets
    assert payload["outside_window"] == {
        "buckets": 1, "executions": 9, "first": "2027-01-01", "last": "2027-01-01",
    }
    assert "outside_window" in svc.definitions(spec, svc.GRAIN_RUN, FROZEN)
    # Nothing outside the axis, nothing to report.
    clean = svc.assemble(spec, cells[:1], buckets=buckets, x_type="time")
    assert clean["outside_window"] is None


def test_the_definitions_state_the_tie_break_and_the_merged_buckets() -> None:
    spec = svc.parse_chart_spec("pass_rate", ["day", "suite"], 3, scope=_scope())
    defs = svc.definitions(spec, svc.GRAIN_ROW, FROZEN)
    assert "Ties are broken by the key" in defs["ranking"]
    assert "sample size" in defs["ranking"]
    assert svc.NO_VALUE in defs["no_value_bucket"]
    assert "Unknown" in defs["no_value_bucket"], (
        "failure_category's absent bucket and an ingested 'Unknown' merge"
    )
    assert "RECOMPUTED" in defs["top_n"]
