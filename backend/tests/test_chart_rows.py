"""VIZ-208 -- ``/analytics/chart-data/rows``, everything a database cannot answer.

The reconciliation property (rows of a mark add up to the mark) lives in
``tests/integration/test_chart_rows_postgres.py``. Here:

1. **Every refusal** is a VIZ-210 body with a rule id and an allow-list, raised
   before the database, and never echoes the value that was sent.
2. **Nothing from the request reaches the SQL text.** For every dimension the
   statement is byte-identical whatever the selector value is; the value is a
   bind.
3. **Which rows count** is a table with one line per chart metric, and the
   reconciliation each one promises.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.analytics_errors import AnalyticsQueryError
from app.services import chart_data_service as charts
from app.services import chart_rows_service as svc
from app.services.analytics_scope import AnalyticsScope

FROZEN = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
PROJECT = uuid.UUID("11111111-1111-1111-1111-111111111111")

HOSTILE = (
    "x'; DROP TABLE test_runs; --",
    "x' OR '1'='1",
    'x") UNION SELECT NULL --',
    "x/*comment*/",
    "ｘ",
    "x‮",
    "<img src=x onerror=alert(1)>",
    "__proto__",
    "constructor",
    ":period_start",
)


def _scope(**kwargs) -> AnalyticsScope:
    base = dict(
        project_id=PROJECT, allowed_project_ids=None, release_ids=(), suite_names=(), days=30,
    )
    base.update(kwargs)
    return AnalyticsScope(**base)  # type: ignore[arg-type]


def _req(metric=None, group_by=("suite",), top_n=None, page=1, size=50,
         scope=None, **selectors) -> svc.RowsRequest:
    """Selectors by dimension (``suite="a"``) or by parameter (``bucket_suite="a"``).
    The default metric is ``executions``, or ``failures`` for a failure-group drill."""
    if metric is None:
        metric = "failures" if "error_signature" in group_by else "executions"
    sent = {key.removeprefix("bucket_"): value for key, value in selectors.items()}
    return svc.parse_rows_request(
        metric, list(group_by), top_n, sent, page, size, scope=scope or _scope(),
    )


def _refused(code: str, **kwargs) -> AnalyticsQueryError:
    with pytest.raises(AnalyticsQueryError) as exc:
        _req(**kwargs)
    assert exc.value.code == code, exc.value.code
    assert exc.value.status_code == 422
    return exc.value


def _never_echoed(value: str, error: AnalyticsQueryError) -> None:
    if value.strip():
        assert value not in error.message
        assert value not in str(error.allowed)


# ── 1. which rows count ────────────────────────────────────────────────────


def test_every_chart_metric_says_which_rows_count() -> None:
    """A metric added to chart-data without a line here would drill into
    rows nobody decided on: the table must cover the allow-list exactly."""
    assert set(svc.ROW_PREDICATES) == set(charts.METRICS)
    assert set(svc.RECONCILIATION) == set(charts.METRICS)


@pytest.mark.parametrize("metric,predicate", [
    ("executions", "TRUE"),
    ("failed", "tc.status = 'FAILED'"),
    ("failures", "tc.status IN ('FAILED', 'BROKEN')"),
    ("pass_rate", "tc.status IN ('PASSED', 'FAILED', 'BROKEN')"),
    ("failure_rate", "tc.status IN ('PASSED', 'FAILED', 'BROKEN')"),
    ("duration_p95", "tc.duration_ms IS NOT NULL"),
    ("retried_tests", "COALESCE(tc.retry_count, 0) > 0"),
    ("flaky_tests", "tc.is_flaky_run IS TRUE"),
])
def test_the_metric_predicate_reaches_the_statement(metric, predicate) -> None:
    sql, _ = svc.build_rows_statement(_req(metric, bucket_suite="a"), _scope(), now=FROZEN)
    assert f"AND ({predicate})" in sql


def test_counts_reconcile_with_y_and_rates_with_n() -> None:
    """The precise form of the EPIC's "total equals the mark": a count's n is
    every execution in the bucket, so a ``failed`` drill adds up to y."""
    for metric in ("executions", "passed", "failed", "broken", "skipped", "unknown",
                   "failures", "retried_tests"):
        assert svc.RECONCILIATION[metric] == ("y", "rows"), metric
    for metric in ("pass_rate", "failure_rate", "duration_p50", "duration_p95", "duration_total"):
        assert svc.RECONCILIATION[metric] == ("n", "rows"), metric
    assert svc.RECONCILIATION["unique_tests"] == ("y", "distinct_tests")
    assert svc.RECONCILIATION["flaky_tests"] == ("y", "distinct_tests")
    assert svc.RECONCILIATION["run_count"] == ("y", "distinct_runs")


def test_a_distinct_count_mark_is_counted_distinctly_only_when_asked() -> None:
    sql, _ = svc.build_rows_statement(_req("unique_tests", bucket_suite="a"), _scope(), now=FROZEN)
    assert "COUNT(DISTINCT tc.test_fingerprint) AS distinct_total" in sql
    sql, _ = svc.build_rows_statement(_req("run_count", bucket_suite="a"), _scope(), now=FROZEN)
    assert "COUNT(DISTINCT tr.id) AS distinct_total" in sql
    # Every other drill pays for no DISTINCT: it forces a sort over the bucket.
    sql, _ = svc.build_rows_statement(_req("failed", bucket_suite="a"), _scope(), now=FROZEN)
    assert "DISTINCT" not in sql


# ── 2. refusals ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", HOSTILE)
def test_a_hostile_metric_is_refused_and_never_echoed(value) -> None:
    error = _refused("metric_enum", metric=value, bucket_suite="a")
    assert error.param == "metric"
    assert set(error.allowed) == set(charts.METRICS)
    _never_echoed(value, error)


@pytest.mark.parametrize("value", HOSTILE)
def test_a_hostile_group_by_is_refused_and_never_echoed(value) -> None:
    error = _refused("dimension_enum", group_by=(value,), bucket_suite="a")
    assert list(error.allowed) == list(svc.ROWS_DIMENSIONS)
    assert "error_signature" in error.allowed
    _never_echoed(value, error)


def test_group_by_is_required_capped_and_unique() -> None:
    _refused("missing_parameter", group_by=())
    _refused("group_by_cap", group_by=("day", "suite", "status"), bucket_suite="a")
    _refused("unique_dimension", group_by=("suite", "suite"), bucket_suite="a")


def test_a_selector_must_be_one_of_the_chart_s_dimensions() -> None:
    """M-208d: accept a selector the chart is not grouped by, and a click on a
    day bar would silently filter by a suite nobody drew."""
    error = _refused("selector_not_in_group_by", group_by=("day",), bucket_day="2026-09-20",
                     bucket_suite="payments")
    assert error.param == "bucket_suite"
    assert error.allowed == ["bucket_day"]


def test_a_drill_selects_at_least_one_bucket() -> None:
    error = _refused("missing_parameter", group_by=("day", "suite"))
    assert error.allowed == ["bucket_day", "bucket_suite"]


def test_the_chart_s_own_rules_apply_to_a_chart_data_drill() -> None:
    """The request names a chart; one chart-data would refuse is refused here
    with the same rule id."""
    _refused("high_cardinality_pair", group_by=("suite", "test"), bucket_suite="a")
    _refused("test_requires_scope", group_by=("test",), bucket_test="fp")
    _refused("time_dimension_position", group_by=("suite", "day"), bucket_suite="a")
    # ...and with the bound the chart had, it is a legal drill.
    assert _req(group_by=("test",), top_n=20, bucket_test="fp").selectors[0].value == "fp"


def test_a_signature_drill_is_not_a_chart_data_chart() -> None:
    req = _req("failures", group_by=("error_signature",), bucket_error_signature="timeout #")
    assert req.chart_dimensions == ()
    assert svc.chart_grain(req, _scope()) == charts.GRAIN_ROW


@pytest.mark.parametrize("dimension,value", [
    ("day", "2026-9-1"),
    ("day", "2026-02-30"),
    ("day", "yesterday"),
    ("week", "2026-09-22"),  # a Tuesday
    ("project", "not-a-uuid"),
    ("release", "1.0.0"),
    ("test", "f" * 65),
    ("error_signature", "s" * 81),
    ("suite", "s" * 2001),
    ("suite", ""),
    ("branch", "main" + chr(0)),
    ("suite", charts.OTHER_KEY),
])
def test_a_malformed_selector_value_is_refused(dimension, value) -> None:
    error = _refused("bucket_value", group_by=(dimension,), top_n=5 if dimension == "test" else None,
                     **{dimension: value})
    assert error.param == f"bucket_{dimension}"
    _never_echoed(value, error)


@pytest.mark.parametrize("value", ["FAILED", "Passed", "<img src=x onerror=alert(1)>", ""])
def test_a_status_outside_the_vocabulary_is_refused(value) -> None:
    error = _refused("status_vocab", group_by=("status",), status=value)
    assert set(error.allowed) == {"passed", "failed", "broken", "skipped", "unknown"}
    _never_echoed(value, error)


@pytest.mark.parametrize("page,size,code", [
    (0, 50, "page_range"),
    (-1, 50, "page_range"),
    (1, 0, "size_range"),
    (1, 201, "size_range"),
    (202, 50, "page_cap"),       # starts at row 10 050
    (52, 200, "page_cap"),       # starts at row 10 200
])
def test_paging_is_bounded(page, size, code) -> None:
    error = _refused(code, page=page, size=size, bucket_suite="a")
    if code == "page_cap":
        assert error.allowed["max_offset"] == svc.MAX_OFFSET


def test_the_last_page_under_the_cap_is_allowed() -> None:
    assert _req(page=201, size=50, bucket_suite="a").offset == 10_000
    assert _req(page=51, size=200, bucket_suite="a").offset == 10_000


# ── 3. the statement ───────────────────────────────────────────────────────

_SELECTABLE = {
    "suite": "payments",
    "failure_category": "product_bug",
    "branch": "main",
    "environment": "staging",
    "ingestion_source": "upload",
    "test": "f" * 64,
    "status": "failed",
    "project": str(PROJECT),
    "release": str(uuid.UUID(int=7)),
    "day": "2026-09-20",
    "week": "2026-09-14",
    "error_signature": "timeoutexception: checkout-service did not respond within #ms",
}


@pytest.mark.parametrize("dimension", sorted(_SELECTABLE))
def test_a_selector_value_is_always_a_bind(dimension) -> None:
    """M-208 injection: the statement text does not change with the value."""
    top_n = 5 if dimension == "test" else None
    texts = set()
    for value in (_SELECTABLE[dimension],):
        sql, params = svc.build_rows_statement(
            _req(group_by=(dimension,), top_n=top_n, **{dimension: value}), _scope(), now=FROZEN,
        )
        texts.add(sql)
        assert value not in sql
    if dimension not in ("day", "week", "project", "release", "status"):
        for value in HOSTILE:
            if dimension == "test" and len(value) > 64:
                continue
            sql, params = svc.build_rows_statement(
                _req(group_by=(dimension,), top_n=top_n, **{dimension: value}), _scope(), now=FROZEN,
            )
            texts.add(sql)
            assert params[f"bucket_{dimension}"] == value
    assert len(texts) == 1, f"{dimension}: the value changed the statement text"


@pytest.mark.parametrize("dimension", ["suite", "failure_category", "branch", "environment",
                                       "ingestion_source"])
def test_a_text_selector_compares_the_chart_s_own_bucket_expression(dimension) -> None:
    """The selector and the chart's bucket are ONE expression, so a key the
    chart returned selects exactly the rows it counted -- '(none)' included."""
    sql, _ = svc.build_rows_statement(
        _req(group_by=(dimension,), **{dimension: "k"}), _scope(), now=FROZEN,
    )
    assert f"AND {charts.DIMENSIONS[dimension].sql} = :bucket_{dimension}" in sql


def test_a_day_is_a_half_open_utc_range_on_the_run_s_clock() -> None:
    """M-208a: a local-time day would move rows across midnight."""
    sql, params = svc.build_rows_statement(
        _req(group_by=("day",), day="2026-09-20"), _scope(), now=FROZEN,
    )
    assert "AND tr.created_at >= :bucket_day_from AND tr.created_at < :bucket_day_to" in sql
    assert "TO_CHAR" not in sql
    assert params["bucket_day_from"] == datetime(2026, 9, 20, tzinfo=timezone.utc)
    assert params["bucket_day_to"] == datetime(2026, 9, 21, tzinfo=timezone.utc)
    assert params["bucket_day_from"].utcoffset().total_seconds() == 0


def test_a_week_is_seven_utc_days_from_its_monday() -> None:
    _, params = svc.build_rows_statement(
        _req(group_by=("week",), week="2026-09-14"), _scope(), now=FROZEN,
    )
    assert params["bucket_week_from"] == datetime(2026, 9, 14, tzinfo=timezone.utc)
    assert params["bucket_week_to"] == datetime(2026, 9, 21, tzinfo=timezone.utc)


def test_the_unattributed_release_is_a_null_test_not_a_bind() -> None:
    sql, params = svc.build_rows_statement(
        _req(group_by=("release",), release="Unattributed"), _scope(), now=FROZEN,
    )
    assert "AND tr.primary_release_id IS NULL" in sql
    assert "bucket_release" not in params
    sql, params = svc.build_rows_statement(
        _req(group_by=("release",), release=str(uuid.UUID(int=7))), _scope(), now=FROZEN,
    )
    assert "AND tr.primary_release_id = :bucket_release" in sql
    assert params["bucket_release"] == uuid.UUID(int=7)


def test_a_status_selector_binds_the_column_s_uppercase_value() -> None:
    sql, params = svc.build_rows_statement(
        _req(group_by=("status",), status="broken"), _scope(), now=FROZEN,
    )
    assert "AND tc.status = :bucket_status" in sql
    assert params["bucket_status"] == "BROKEN"


def test_the_chart_s_scope_fragments_bound_the_rows() -> None:
    """M-208b: rows that ignore the scope's release filter would list
    executions the chart never counted."""
    rel = str(uuid.UUID(int=9))
    scope = _scope(release_ids=(rel,), suite_names=("Payments",))
    sql, params = svc.build_rows_statement(_req(group_by=("day",), day="2026-09-20", scope=scope),
                                           scope, now=FROZEN)
    assert "tr.created_at >= :period_start" in sql
    assert "AND tr.project_id = :project_id" in sql
    assert "AND tr.primary_release_id = :release_id" in sql
    assert params["release_id"] == rel
    assert params["suite_name"] == "payments"
    assert "is_active" in sql
    assert params["period_start"] == charts.window_start(30, now=FROZEN)


def test_all_projects_carries_the_membership_set() -> None:
    allowed = frozenset({uuid.UUID(int=1), uuid.UUID(int=2)})
    scope = _scope(project_id=None, allowed_project_ids=allowed)
    sql, params = svc.build_rows_statement(_req(bucket_suite="a", scope=scope), scope, now=FROZEN)
    assert "tr.project_id IN" in sql


def test_the_page_is_ordered_newest_run_first_and_bounded_by_binds() -> None:
    sql, params = svc.build_rows_statement(
        _req(bucket_suite="a", page=3, size=20), _scope(), now=FROZEN,
    )
    assert "ORDER BY tr.created_at DESC, tc.id DESC" in sql
    assert "LIMIT :rows_limit OFFSET :rows_offset" in sql
    assert (params["rows_limit"], params["rows_offset"]) == (20, 40)


def test_the_statement_holds_no_backslash() -> None:
    sql, _ = svc.build_rows_statement(_req(bucket_suite="a"), _scope(), now=FROZEN)
    assert "\\" not in sql


# ── 3b. the failure-group selector (VIZ-207's signature) ───────────────────


def test_a_signature_selector_uses_the_failure_groups_own_expression() -> None:
    """M-208e: comparing the RAW first line would select nothing for a group
    whose members differ only in their numbers."""
    from app.services.failure_signature import FAILURE_SIGNATURE_PARAMS, FAILURE_SIGNATURE_SQL

    req = _req("failures", group_by=("error_signature",), error_signature="boom #")
    sql, params = svc.build_rows_statement(req, _scope(), now=FROZEN)
    assert f"WHERE {FAILURE_SIGNATURE_SQL} = :bucket_error_signature" in sql
    assert params["bucket_error_signature"] == "boom #"
    for name, pattern in FAILURE_SIGNATURE_PARAMS.items():
        assert params[name] == pattern, "the signature's regexes travel as binds"
        assert pattern not in sql


def test_the_signature_runs_after_the_window_behind_a_fence() -> None:
    """Measured: without the MATERIALIZED fence the planner evaluated the
    regex on every failing execution the project ever had, twice -- 534 ms
    p95 at 90 d on the 1M-row seed against a 300 ms budget."""
    req = _req("failures", group_by=("day", "error_signature"), day="2026-09-20",
               error_signature="boom #")
    sql, _ = svc.build_rows_statement(req, _scope(), now=FROZEN)
    candidates = sql.index("WITH candidates AS MATERIALIZED (")
    matched = sql.index("matched AS MATERIALIZED (")
    window = sql.index("tr.created_at >= :period_start")
    day = sql.index("AND tr.created_at >= :bucket_day_from")
    signature = sql.index(":bucket_error_signature")
    assert candidates < window < day < matched < signature
    assert sql.count(":bucket_error_signature") == 1, "the regex runs once, not per CTE"
    assert "FROM candidates tc" in sql
    assert "ORDER BY m.run_created_at DESC, m.id DESC" in sql


@pytest.mark.parametrize("metric", ["executions", "pass_rate", "unique_tests", "skipped"])
def test_a_failure_group_drill_is_a_failing_metric(metric) -> None:
    error = _refused("metric_enum", metric=metric, group_by=("error_signature",),
                     error_signature="boom #")
    assert error.allowed == ["failures", "failed", "broken"]


def test_a_failing_metric_may_drill_a_group() -> None:
    for metric in ("failures", "failed", "broken"):
        req = _req(metric, group_by=("error_signature",), error_signature="boom #")
        assert req.metric == metric


def test_the_no_message_group_selects_the_empty_signature() -> None:
    from app.services.failure_signature import NO_MESSAGE_ID

    req = _req("failures", group_by=("error_signature",), error_signature=NO_MESSAGE_ID)
    _, params = svc.build_rows_statement(req, _scope(), now=FROZEN)
    assert params["bucket_error_signature"] == ""


def test_the_singletons_roll_up_is_not_one_group() -> None:
    from app.services.failure_signature import SINGLETONS_ID

    _refused("bucket_value", group_by=("error_signature",), error_signature=SINGLETONS_ID)


def test_every_statement_binds_the_first_line_patterns() -> None:
    """``error_line`` reads the shared first-line rule, whose pattern is a
    bind: a statement without the binds would fail on every drill."""
    from app.services.failure_signature import FAILURE_SIGNATURE_PARAMS

    _, params = svc.build_rows_statement(_req(bucket_suite="a"), _scope(), now=FROZEN)
    assert set(FAILURE_SIGNATURE_PARAMS) <= set(params)


# ── 4. assembly ────────────────────────────────────────────────────────────


def _row(**kwargs):
    base = dict(
        total=3, distinct_total=None, id=uuid.UUID(int=5), test_name="<img src=x onerror=1>",
        test_fingerprint="fp", suite="Payments", status="BROKEN", duration_ms=12,
        run_id=uuid.UUID(int=6), created_at=datetime(2026, 9, 20, 23, 59, 59, tzinfo=timezone.utc),
        failure_category="PRODUCT_BUG", error_line="boom", release_id=None, release_name=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_a_row_is_the_panel_s_fields_in_the_contract_vocabulary() -> None:
    item = svc.row_item(_row(release_id=uuid.UUID(int=7), release_name="constructor"))
    assert item == {
        "id": str(uuid.UUID(int=5)),
        "test_name": "<img src=x onerror=1>",
        "test_fingerprint": "fp",
        "suite": "Payments",
        "status": "broken",
        "duration_ms": 12,
        "run_id": str(uuid.UUID(int=6)),
        "release": {"id": str(uuid.UUID(int=7)), "name": "constructor"},
        "created_at": "2026-09-20T23:59:59+00:00",
        "failure_category": "product_bug",
        "error_line": "boom",
    }


def test_absent_values_stay_null() -> None:
    item = svc.row_item(_row(duration_ms=None, failure_category=None, error_line=None, suite=None))
    assert item["duration_ms"] is None
    assert item["failure_category"] is None
    assert item["error_line"] is None
    assert item["suite"] is None
    assert item["release"] is None


def test_the_page_carries_the_total_and_the_reconciliation() -> None:
    req = _req("failed", bucket_suite="a", size=2)
    page = svc.assemble_page(req, [_row(total=5), _row(total=5, id=uuid.UUID(int=8))])
    assert (page["total"], page["pages"], page["page"], page["size"]) == (5, 3, 1, 2)
    assert len(page["items"]) == 2
    assert page["reconciliation"] == {"mark_field": "y", "measure": "rows", "value": 5}


def test_a_distinct_count_reconciles_with_its_distinct_total() -> None:
    req = _req("unique_tests", bucket_suite="a")
    page = svc.assemble_page(req, [_row(total=9, distinct_total=4)])
    assert page["reconciliation"] == {"mark_field": "y", "measure": "distinct_tests", "value": 4}


def test_a_page_past_the_end_keeps_the_total() -> None:
    req = _req(bucket_suite="a", page=9)
    empty_page = _row(total=5, id=None)
    page = svc.assemble_page(req, [empty_page])
    assert page["items"] == [] and page["total"] == 5


def test_nothing_selected_is_zero_not_null() -> None:
    page = svc.assemble_page(_req(bucket_suite="a"), [])
    assert page["total"] == 0 and page["items"] == [] and page["pages"] == 0


def test_the_definitions_name_the_chart_grain_and_the_reconciliation() -> None:
    run = _req("executions", group_by=("day",), day="2026-09-20")
    assert svc.chart_grain(run, _scope()) == charts.GRAIN_RUN
    defs = svc.definitions(run, charts.GRAIN_RUN)
    assert defs["chart_grain"] == charts.GRAIN_RUN
    assert "per-test rows never landed" in defs["chart_grain_note"]
    assert "mark's y" in defs["reconciliation"]
    assert defs["selected"] == ["day"]

    row = _req("pass_rate", group_by=("suite",), suite="a")
    assert svc.chart_grain(row, _scope()) == charts.GRAIN_ROW
    defs = svc.definitions(row, charts.GRAIN_ROW)
    assert "chart_grain_note" not in defs
    assert "mark's n" in defs["reconciliation"]
    assert "denominator" in defs["rows"]
    # A suite filter moves the chart to rows, and the drill says so.
    filtered = _scope(suite_names=("a",))
    assert svc.chart_grain(_req("executions", group_by=("day",), day="2026-09-20",
                                scope=filtered), filtered) == charts.GRAIN_ROW
