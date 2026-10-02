"""VIZ-205 -- the heatmap endpoint, everything a database cannot answer.

The golden numbers, authorisation, caching and the reconciliation with
chart-data live in ``tests/integration/test_heatmap_postgres.py``. Here:

1. **The request.** ``kind`` is an allow-list; ``rows``/``runs`` are bounded;
   ``suite_day`` refuses a window longer than its 90 columns; the two kinds
   that answer for one project refuse All Projects. Every refusal is a VIZ-210
   body and never echoes the value that was sent.
2. **The statement.** Only declared fragments reach SQL; every filter value
   and every cap is a bind; statuses are the ``TestStatus`` vocabulary; one
   statement carries a hard ``LIMIT``.
3. **The assembly is pure.** A full grid; ``null`` (never 0) for a cell with
   no execution AND for a cell of nothing but skips; the pass rate's
   denominator excludes skipped and unknown; ``counts`` add up to ``n``;
   ``unit: "percent"`` explicit; unique ``x_keys``/``y_keys``; rows in SQL's
   rank order; truncation per axis; hostile names round-trip as data. Every
   payload built here validates as C3 ``matrix``.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone

import pytest

from app.core.analytics_errors import AnalyticsQueryError
from app.models.postgres import TestStatus as Status
from app.models.viz_contracts import MAX_MATRIX_CELLS, validate_contract
from app.services import heatmap_service as svc
from app.services.analytics_scope import AnalyticsScope
from app.services.chart_data_service import DIMENSIONS as CHART_DIMENSIONS

FROZEN = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
PROJECT = uuid.UUID("11111111-1111-1111-1111-111111111111")

HOSTILE = (
    "suite_day'; DROP TABLE test_runs; --",
    "suite_day' OR '1'='1",
    'suite_day") UNION SELECT NULL --',
    "suite_day/*x*/",
    "suite_day\x00",
    "ｓｕｉｔｅ_ｄａｙ",
    "suite_day‮",
    "SUITE_DAY",
    "k" * 5000,
    "",
    " ",
    "suite_day,test_run",
)

#: Names that are dangerous somewhere downstream: as JS object keys, as markup,
#: as a very long label. They must come back exactly as they went in.
HOSTILE_NAMES = (
    "constructor",
    "__proto__",
    "prototype",
    '<img src=x onerror="window.__xss=1">',
    "s" * 4000,
    "'; DROP TABLE test_runs; --",
    "名前 ‮ rtl",
)


def _scope(**kwargs) -> AnalyticsScope:
    base = dict(
        project_id=PROJECT,
        allowed_project_ids=None,
        release_ids=(),
        suite_names=(),
        days=30,
    )
    base.update(kwargs)
    return AnalyticsScope(**base)  # type: ignore[arg-type]


def _spec(kind="suite_day", rows=None, runs=None, **scope_kw) -> svc.HeatmapSpec:
    return svc.parse_heatmap_spec(kind, rows, runs, scope=_scope(**scope_kw))


def _never_echoed(value: str, message: str) -> None:
    if value.strip():
        assert value not in message, "an untrusted value was echoed back"


def _validate(payload: dict) -> None:
    body = {k: v for k, v in payload.items() if k not in svc.ENVELOPE_KEYS and k != "definitions"}
    validate_contract("chart_series", body)


# ── 1. The request ─────────────────────────────────────────────────────────


def test_the_kinds_are_exactly_the_plan_s() -> None:
    assert set(svc.KINDS) == {"suite_day", "test_run", "suite_environment", "suite_release"}


@pytest.mark.parametrize("value", HOSTILE)
def test_a_hostile_kind_is_refused_and_never_echoed(value: str) -> None:
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_heatmap_spec(value, None, None, scope=_scope())
    assert exc.value.code == "kind_enum"
    assert exc.value.param == "kind"
    assert exc.value.allowed == sorted(svc.KINDS)
    _never_echoed(value, exc.value.message)


@pytest.mark.parametrize("value", [3, 1.5, ["suite_day"], {"kind": "suite_day"}, True])
def test_a_kind_of_the_wrong_type_is_refused(value) -> None:
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_heatmap_spec(value, None, None, scope=_scope())
    assert exc.value.code == "kind_enum"


def test_kind_is_required() -> None:
    with pytest.raises(AnalyticsQueryError) as exc:
        svc.parse_heatmap_spec(None, None, None, scope=_scope())
    assert exc.value.code == "missing_parameter"
    assert exc.value.param == "kind"
    assert exc.value.allowed == sorted(svc.KINDS)


def test_rows_default_and_bounds() -> None:
    assert _spec().rows == svc.DEFAULT_ROWS == 40
    assert _spec(rows=1).rows == 1
    assert _spec(rows=svc.MAX_ROWS).rows == 60
    for bad in (0, -1, 61, 10_000, True, "40", 4.0):
        with pytest.raises(AnalyticsQueryError) as exc:
            _spec(rows=bad)
        assert exc.value.code == "rows_range", bad
        assert exc.value.allowed == {"min": 1, "max": 60}


def test_runs_belong_to_test_run_only() -> None:
    assert _spec("test_run").runs == svc.DEFAULT_RUNS == 30
    assert _spec("test_run", runs=90).runs == 90
    assert _spec("suite_day").runs is None
    for bad in (0, 91, True, "30"):
        with pytest.raises(AnalyticsQueryError) as exc:
            _spec("test_run", runs=bad)
        assert exc.value.code == "runs_range", bad
        assert exc.value.allowed == {"min": 1, "max": 90}
    for kind in ("suite_day", "suite_environment", "suite_release"):
        with pytest.raises(AnalyticsQueryError) as exc:
            _spec(kind, runs=30)
        assert exc.value.code == "runs_unsupported"
        assert exc.value.param == "runs"


def test_suite_day_refuses_a_window_longer_than_its_columns() -> None:
    """M-205f: its columns ARE days, and 90 x 60 is the C3 cell cap."""
    assert _spec("suite_day", days=90).kind == "suite_day"
    with pytest.raises(AnalyticsQueryError) as exc:
        _spec("suite_day", days=91)
    assert exc.value.code == "window_cap"
    assert exc.value.param == "days"
    assert exc.value.allowed == {"min": 1, "max": 90}
    # The other kinds take the scope's own 365.
    for kind in ("test_run", "suite_environment", "suite_release"):
        assert _spec(kind, days=365).kind == kind


def test_the_route_s_scope_policy_is_the_plan_s() -> None:
    assert svc.HEATMAP_SCOPE.default_days == 30
    assert svc.HEATMAP_SCOPE.max_days == 365
    # project_id is optional on the route; the KIND decides (a 422 with its
    # own rule id, not FastAPI's "field required").
    assert svc.HEATMAP_SCOPE.project_required is False


@pytest.mark.parametrize("kind", ["test_run", "suite_release"])
def test_the_one_project_kinds_refuse_all_projects(kind: str) -> None:
    for allowed in (None, frozenset({PROJECT})):
        with pytest.raises(AnalyticsQueryError) as exc:
            _spec(kind, project_id=None, allowed_project_ids=allowed)
        assert exc.value.code == "project_required"
        assert exc.value.param == "project_id"


@pytest.mark.parametrize("kind", ["suite_day", "suite_environment"])
def test_the_other_kinds_answer_for_all_projects(kind: str) -> None:
    assert _spec(kind, project_id=None, allowed_project_ids=frozenset({PROJECT})).kind == kind


# ── 2. The statement ───────────────────────────────────────────────────────


def _all_specs():
    for kind in svc.KINDS:
        yield _spec(kind)


def test_only_declared_fragments_reach_the_sql() -> None:
    """The grouped kinds use chart-data's OWN day/suite/environment/release
    fragments -- the same text in the SELECT and the GROUP BY -- which is
    what makes a suite x day cell reconcile with chart-data."""
    suite = CHART_DIMENSIONS["suite"]
    for kind, x_dim in (
        ("suite_day", "day"), ("suite_environment", "environment"), ("suite_release", "release"),
    ):
        sql, _ = svc.build_statement(_spec(kind), _scope(), now=FROZEN)
        x_sql = CHART_DIMENSIONS[x_dim].sql
        assert f"{x_sql} AS x_key" in sql, kind
        assert f"{suite.sql} AS y_key" in sql, kind
        assert f"{suite.label_sql} AS y_label" in sql, kind
        assert f"GROUP BY {x_sql}, {suite.sql}" in sql, kind
    for spec in _all_specs():
        sql, _ = svc.build_statement(spec, _scope(), now=FROZEN)
        assert "--" not in sql and ";" not in sql, spec.kind


def test_the_statement_text_does_not_move_with_the_filter_values() -> None:
    """Suites, releases, the project, the window and the caps are BINDS."""
    for kind in svc.KINDS:
        plain, _ = svc.build_statement(_spec(kind), _scope(), now=FROZEN)
        other_rows, params = svc.build_statement(_spec(kind, rows=7), _scope(), now=FROZEN)
        assert plain == other_rows, kind
        assert params["heatmap_rows"] == 7
        filtered_a, _ = svc.build_statement(
            _spec(kind), _scope(suite_names=("'; DROP TABLE test_runs; --", "checkout"),
                                release_ids=(str(uuid.uuid4()), "unattributed")), now=FROZEN,
        )
        filtered_b, params_b = svc.build_statement(
            _spec(kind), _scope(suite_names=("a", "b"),
                                release_ids=(str(uuid.uuid4()), "unattributed")), now=FROZEN,
        )
        assert filtered_a == filtered_b, kind
        assert "DROP TABLE" not in filtered_a
        assert "IS NULL OR" not in filtered_a.replace("primary_release_id IS NULL", "")
        assert "release_test_run_links" not in filtered_a
        assert params_b["suite_names"] == ["a", "b"]
    _, run_params = svc.build_statement(_spec("test_run", runs=12), _scope(), now=FROZEN)
    assert run_params["heatmap_runs"] == 12


def test_every_statement_carries_a_hard_limit() -> None:
    for spec in _all_specs():
        sql, params = svc.build_statement(spec, _scope(), now=FROZEN)
        assert sql.rstrip().endswith("LIMIT :heatmap_row_cap"), spec.kind
        assert params["heatmap_row_cap"] == svc.HARD_ROW_CAP


def test_rows_and_columns_are_ranked_and_cut_in_sql() -> None:
    for kind in ("suite_day", "suite_environment", "suite_release"):
        sql, params = svc.build_statement(_spec(kind), _scope(), now=FROZEN)
        # Worst first: failures, then volume, then the key by code point
        # (M-205c; R1-4: never the server's collation).
        assert 'ORDER BY fails DESC, n DESC, y_key COLLATE "C" ASC' in sql, kind
        assert "rn <= :heatmap_rows" in sql
    for kind in ("suite_environment", "suite_release"):
        sql, params = svc.build_statement(_spec(kind), _scope(), now=FROZEN)
        assert params["heatmap_columns"] == svc.MAX_COLUMNS == 20
        assert "rn <= :heatmap_columns" in sql
    sql, _ = svc.build_statement(_spec("suite_environment"), _scope(), now=FROZEN)
    assert 'ORDER BY n DESC, x_key COLLATE "C" ASC' in sql
    sql, _ = svc.build_statement(_spec("suite_release"), _scope(), now=FROZEN)
    assert "ORDER BY r.sort_key DESC NULLS LAST" in sql
    # The release lookup carries the tenant predicate on the release row too.
    assert "r.project_id = :project_id" in sql


def test_test_run_columns_are_the_last_runs_by_time_then_id() -> None:
    """M-205e: never by build label -- labels repeat and sort as text."""
    sql, params = svc.build_statement(_spec("test_run"), _scope(), now=FROZEN)
    assert "ORDER BY tr.created_at DESC, tr.id DESC" in sql
    assert "LIMIT :heatmap_runs" in sql
    assert "build_number DESC" not in sql
    assert 'ORDER BY fails DESC, y_key COLLATE "C" ASC' in sql
    assert params["heatmap_runs"] == svc.DEFAULT_RUNS


def test_the_failing_tests_are_read_through_the_window_s_runs() -> None:
    """The cold-path bound. Joined plainly, the planner reads every failing
    row in the table through ``ix_test_cases_status_only`` (21,010 heap pages
    on the 1M seed, whatever the window) and only then applies the window.
    A fenced LATERAL per run keeps the read to the window's runs' failing
    rows (``docs/viz-work/w3/perf/be1``)."""
    sql, _ = svc.build_statement(_spec("test_run"), _scope(), now=FROZEN)
    failing = sql[sql.index("failing AS ("):sql.index("y_ranked AS (")]
    assert "FROM test_runs tr\n            CROSS JOIN LATERAL (" in failing
    assert "WHERE tc.test_run_id = tr.id" in failing
    assert "OFFSET 0" in failing
    assert "JOIN test_runs tr ON tr.id = tc.test_run_id" not in failing


def test_a_suite_filter_selects_the_runs_through_the_same_fragment() -> None:
    sql, _ = svc.build_statement(
        _spec("test_run"), _scope(suite_names=("checkout",)), now=FROZEN,
    )
    assert "AND EXISTS (SELECT 1 FROM test_cases tc WHERE tc.test_run_id = tr.id" in sql
    assert sql.count("= :suite_name") >= 3  # runs, failing rows, cells


def test_statuses_use_the_test_status_vocabulary() -> None:
    for spec in _all_specs():
        sql, _ = svc.build_statement(spec, _scope(), now=FROZEN)
        assert "'passed'" not in sql and "'failed'" not in sql, spec.kind
    sql, _ = svc.build_statement(_spec("suite_day"), _scope(), now=FROZEN)
    for member in Status:
        assert f"tc.status = '{member.value}'" in sql
    sql, _ = svc.build_statement(_spec("test_run"), _scope(), now=FROZEN)
    assert "tc.status IN ('FAILED', 'BROKEN')" in sql


def test_the_window_starts_at_utc_midnight_of_the_first_day() -> None:
    _, params = svc.build_statement(_spec("suite_day", days=7), _scope(days=7), now=FROZEN)
    assert params["period_start"] == datetime(2026, 9, 15, tzinfo=timezone.utc)


def test_all_projects_is_bounded_by_the_caller_s_projects() -> None:
    other = uuid.UUID("22222222-2222-2222-2222-222222222222")
    sql, params = svc.build_statement(
        _spec("suite_day", project_id=None, allowed_project_ids=frozenset({PROJECT, other})),
        _scope(project_id=None, allowed_project_ids=frozenset({PROJECT, other})),
        now=FROZEN,
    )
    assert "tr.project_id IN (:pid_0, :pid_1)" in sql
    assert "is_active" in sql
    assert {params["pid_0"], params["pid_1"]} == {str(PROJECT), str(other)}
    sql, _ = svc.build_statement(
        _spec("suite_day"), _scope(project_id=None, allowed_project_ids=frozenset()), now=FROZEN,
    )
    assert "AND FALSE" in sql


# ── 3. The assembly ────────────────────────────────────────────────────────


def _row(key, rank, label=None):
    return svc.AxisEntry(key, label if label is not None else key.upper(), rank)


def _cell(y, x, passed=0, failed=0, broken=0, skipped=0, unknown=0, status=None):
    n = passed + failed + broken + skipped + unknown
    if status is not None:
        n = 0
    return svc.CellCounts(y, x, passed, failed, broken, skipped, unknown, n, status)


DAYS = [("2026-09-19", "2026-09-19"), ("2026-09-20", "2026-09-20"), ("2026-09-21", "2026-09-21")]


def test_a_full_grid_with_null_never_zero() -> None:
    """M-205b: a pair with no execution is null with n 0, never 0%."""
    fetched = svc.Fetched(
        rows=[_row("checkout", 1), _row("search", 2)],
        cells=[
            _cell("checkout", "2026-09-19", passed=3, failed=1),
            _cell("search", "2026-09-21", failed=2),
        ],
        row_total=2,
    )
    payload = svc.assemble(_spec(), fetched, columns=DAYS)
    _validate(payload)
    assert len(payload["cells"]) == 6
    grid = {(c["x"], c["y"]): c for c in payload["cells"]}
    assert grid[(0, 0)]["value"] == 75.0 and grid[(0, 0)]["n"] == 4
    assert grid[(2, 1)]["value"] == 0.0 and grid[(2, 1)]["n"] == 2  # a real 0%
    empty = grid[(1, 0)]
    assert empty["value"] is None and empty["n"] == 0
    assert empty["counts"] == {"passed": 0, "failed": 0, "broken": 0, "skipped": 0, "unknown": 0}


def test_a_cell_of_only_skips_is_not_measured_but_keeps_its_n() -> None:
    fetched = svc.Fetched(
        rows=[_row("quarantined", 1)],
        cells=[_cell("quarantined", "2026-09-20", skipped=4, unknown=1)],
        row_total=1,
    )
    payload = svc.assemble(_spec(), fetched, columns=DAYS)
    _validate(payload)
    cell = payload["cells"][1]
    assert cell["value"] is None
    assert cell["n"] == 5
    assert cell["counts"]["skipped"] == 4 and cell["counts"]["unknown"] == 1


def test_the_pass_rate_excludes_skipped_and_unknown() -> None:
    """M-205a: ``passed / executions`` would read 3/10 = 30% here."""
    fetched = svc.Fetched(
        rows=[_row("checkout", 1)],
        cells=[_cell("checkout", "2026-09-19", passed=3, failed=1, broken=0, skipped=5, unknown=1)],
        row_total=1,
    )
    cell = svc.assemble(_spec(), fetched, columns=DAYS)["cells"][0]
    assert cell["value"] == 75.0
    assert cell["n"] == 10
    assert sum(cell["counts"].values()) == cell["n"]


def test_the_rate_is_rounded_by_the_canonical_rule() -> None:
    fetched = svc.Fetched(rows=[_row("a", 1)], cells=[_cell("a", "2026-09-19", passed=2, broken=1)],
                          row_total=1)
    assert svc.assemble(_spec(), fetched, columns=DAYS)["cells"][0]["value"] == 66.67


def test_the_unit_is_sent_and_the_keys_are_parallel_to_the_labels() -> None:
    fetched = svc.Fetched(rows=[_row("a", 1, "Alpha"), _row("b", 2, "Alpha")], row_total=2)
    payload = svc.assemble(_spec(), fetched, columns=DAYS)
    _validate(payload)
    assert payload["unit"] == "percent"
    assert payload["value_type"] == "rate"
    # Labels may repeat (two suites spelled alike); keys never do.
    assert payload["y_labels"] == ["Alpha", "Alpha"]
    assert payload["y_keys"] == ["a", "b"]
    assert payload["x_keys"] == [key for key, _ in DAYS]
    assert len(payload["x_keys"]) == len(payload["x_labels"])


def test_rows_come_in_sql_s_rank_order_not_by_name() -> None:
    """M-205c (pure half): the assembly never re-sorts the rows."""
    rows = [_row("zeta", 1), _row("alpha", 3), _row("mid", 2)]
    fetched = svc.read_parts([
        _db_row("row", entry.key, entry.label, None, entry.rank, 3) for entry in rows
    ])
    payload = svc.assemble(_spec(), fetched, columns=DAYS)
    assert payload["y_keys"] == ["zeta", "mid", "alpha"]


def test_the_cap_is_declared_per_axis() -> None:
    """M-205d (pure half): rows beyond the cap are counted, not silent."""
    fetched = svc.Fetched(rows=[_row(f"s{i:02d}", i + 1) for i in range(3)], row_total=61)
    payload = svc.assemble(_spec(rows=3), fetched, columns=DAYS)
    assert payload["truncated"] is True
    assert payload["truncated_total"] == 61
    assert payload["truncated_axes"] == {"series": {"dimension": "suite", "kept": 3, "total": 61}}
    fetched = svc.Fetched(rows=[_row("a", 1)], row_total=1)
    payload = svc.assemble(_spec("suite_environment"), fetched,
                           columns=[("qa", "QA")] * 1, column_total=25)
    assert payload["truncated_axes"] == {"x": {"dimension": "environment", "kept": 1, "total": 25}}
    assert payload["truncated_total"] == 25


def test_both_axes_truncated_name_the_columns_first() -> None:
    fetched = svc.Fetched(rows=[_row("a", 1)], row_total=70)
    payload = svc.assemble(_spec("suite_release"), fetched,
                           columns=[("r1", "1.0")], column_total=30)
    assert set(payload["truncated_axes"]) == {"x", "series"}
    assert payload["truncated_total"] == 30


def test_nothing_truncated_reports_nothing() -> None:
    fetched = svc.Fetched(rows=[_row("a", 1)], row_total=1)
    payload = svc.assemble(_spec(), fetched, columns=DAYS)
    assert payload["truncated"] is False
    assert payload["truncated_total"] is None
    assert payload["truncated_axes"] is None
    assert payload["outside_window"] is None


def test_a_future_dated_run_is_counted_outside_the_day_axis() -> None:
    fetched = svc.Fetched(
        rows=[_row("a", 1)],
        cells=[_cell("a", "2026-09-21", passed=1), _cell("a", "2026-09-25", passed=2, failed=1)],
        row_total=1,
    )
    payload = svc.assemble(_spec(), fetched, columns=DAYS)
    assert payload["outside_window"] == {
        "buckets": 1, "executions": 3, "first": "2026-09-25", "last": "2026-09-25",
    }
    assert len(payload["cells"]) == 3


def test_the_test_run_matrix_is_a_status_matrix() -> None:
    fetched = svc.Fetched(
        rows=[_row("fp1", 1, "test_pay"), _row("fp2", 2, "test_ship")],
        cells=[
            _cell("fp1", "run-a", status="failed"),
            _cell("fp1", "run-b", status="passed"),
            _cell("fp2", "run-b", status="broken"),
            _cell("fp2", "run-a", status="ERRORED"),  # outside the vocabulary
        ],
        row_total=2,
    )
    payload = svc.assemble(_spec("test_run"), fetched,
                           columns=[("run-a", "41"), ("run-b", "42"), ("run-c", "42")])
    _validate(payload)
    assert payload["value_type"] == "status"
    assert "unit" not in payload
    grid = {(c["x"], c["y"]): c for c in payload["cells"]}
    assert grid[(0, 0)] == {"x": 0, "y": 0, "value": "failed", "n": 1}
    assert grid[(1, 0)]["value"] == "passed"
    assert grid[(2, 0)] == {"x": 2, "y": 0, "value": None, "n": 0}
    assert grid[(0, 1)]["value"] == "unknown"
    # Two runs with one build label: labels repeat, keys do not.
    assert payload["x_labels"] == ["41", "42", "42"]


def test_the_grid_at_the_caps_fits_the_contract() -> None:
    rows = [_row(f"s{i:02d}", i + 1) for i in range(svc.MAX_ROWS)]
    columns = [(f"2026-06-{i:02d}", f"d{i}") for i in range(svc.SUITE_DAY_MAX_DAYS)]
    cells = [_cell(r.key, key, passed=1) for r in rows for key, _ in columns]
    payload = svc.assemble(_spec(rows=60), svc.Fetched(rows=rows, cells=cells, row_total=200),
                           columns=columns)
    assert len(payload["cells"]) == MAX_MATRIX_CELLS
    _validate(payload)


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_hostile_names_round_trip_as_data(name: str) -> None:
    fetched = svc.Fetched(
        rows=[_row(name.lower(), 1, name)],
        cells=[_cell(name.lower(), name, passed=1)],
        row_total=1,
    )
    payload = svc.assemble(_spec("suite_environment"), fetched, columns=[(name, name)])
    _validate(payload)
    assert payload["y_labels"] == [name]
    assert payload["x_labels"] == [name]
    assert payload["x_keys"] == [name]
    assert payload["cells"][0]["value"] == 100.0


# ── the release columns ────────────────────────────────────────────────────


def test_release_columns_follow_the_version_order_and_unattributed_is_last() -> None:
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    infos = [
        svc.ReleaseInfo("unattributed", None, None, None),
        svc.ReleaseInfo("id-210", "2.10.0", "1|000002.000010.000000.000000|~", t),
        svc.ReleaseInfo("id-29", "2.9.0", "1|000002.000009.000000.000000|~", t),
        svc.ReleaseInfo("id-rc", "2.9.0-rc1", "1|000002.000009.000000.000000|rc1", t),
        svc.ReleaseInfo("id-none", "legacy", None, t),
    ]
    assert svc.order_release_columns(infos) == [
        ("id-none", "legacy"),
        ("id-rc", "2.9.0-rc1"),
        ("id-29", "2.9.0"),
        ("id-210", "2.10.0"),
        ("unattributed", svc.UNATTRIBUTED_LABEL),
    ]


def test_an_unknown_release_keeps_its_id_as_its_label() -> None:
    columns = svc.order_release_columns([svc.ReleaseInfo("abc", None, None, None)])
    assert columns == [("abc", "abc")]


# ── reading the tagged result ──────────────────────────────────────────────


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _db_row(part, key, label, x_key, rank, total, counts=(None,) * 6, status=None):
    names = ("passed", "failed", "broken", "skipped", "unknown", "n")
    return _Row(part=part, key=key, label=label, x_key=x_key, rank=rank, total=total,
                status=status, **dict(zip(names, counts)))


def test_read_parts_splits_rows_columns_and_cells() -> None:
    fetched = svc.read_parts([
        _db_row("cell", "a", None, "x1", None, None, (1, 2, 0, 0, 0, 3)),
        _db_row("row", "b", "B", None, 2, 9),
        _db_row("row", "a", None, None, 1, 9),
        _db_row("col", "x1", "X one", None, 1, 4),
    ])
    assert [r.key for r in fetched.rows] == ["a", "b"]
    assert fetched.rows[0].label == "a"  # a NULL label falls back to the key
    assert fetched.row_total == 9
    assert fetched.columns == [svc.AxisEntry("x1", "X one", 1)]
    assert fetched.column_total == 4
    assert fetched.cells == [svc.CellCounts("a", "x1", 1, 2, 0, 0, 0, 3, None)]


# ── definitions, identity, the route ───────────────────────────────────────


def test_definitions_state_the_semantics() -> None:
    rate = svc.definitions(_spec(), FROZEN)
    assert rate["grain"] == "execution_row"
    assert "percent" in rate["value"] and "skipped and unknown" in rate["value"]
    assert "null" in rate["null"] and "0%" in rate["null"]
    assert rate["partial_bucket"] == "2026-09-21"
    assert "created_at" in rate["window_clock"]
    status = svc.definitions(_spec("test_run"), FROZEN)
    assert "partial_bucket" not in status
    assert "oldest to newest" in status["columns"]
    for kind in svc.KINDS:
        defs = svc.definitions(_spec(kind), FROZEN)
        assert all(isinstance(key, str) and not key.startswith("__") for key in defs)


def test_the_cache_identity_is_canonical() -> None:
    scope = _scope(suite_names=("Checkout", "search"))
    same = _scope(suite_names=("SEARCH", "checkout"))
    assert svc.cache_identity_parts(scope, _spec()) == svc.cache_identity_parts(
        same, _spec(rows=40))
    assert svc.cache_identity_parts(scope, _spec()) != svc.cache_identity_parts(
        scope, _spec(rows=41))
    assert svc.cache_identity_parts(scope, _spec("suite_day")) != svc.cache_identity_parts(
        scope, _spec("suite_environment"))
    assert svc.cache_identity_parts(_scope(), _spec("test_run")) != svc.cache_identity_parts(
        _scope(), _spec("test_run", runs=31))
    assert svc.cache_identity_parts(_scope(days=7), _spec()) != svc.cache_identity_parts(
        _scope(days=8), _spec(days=8))


def test_the_route_is_wrapped_in_the_read_layer() -> None:
    from app.core import analytics_read_layer as layer
    from app.routers import analytics_heatmap as route

    paths = {getattr(r, "path", None) for r in route.router.routes}
    assert paths == {"/api/v1/analytics/heatmap"}
    endpoint = next(r.endpoint for r in route.router.routes)
    assert getattr(endpoint, "__analytics_read__", None) == "heatmap"
    assert getattr(endpoint, "__analytics_error_contract__", False)
    # 60 a minute, declared in the registry the layer reads per request.
    assert layer.RATE_LIMITED_ROUTES["/api/v1/analytics/heatmap"] == "60/minute"
    params = inspect.signature(endpoint).parameters
    assert {"kind", "rows", "runs", "scope", "db"} <= set(params)


def test_the_identity_hook_normalises_the_resolved_request() -> None:
    from app.routers import analytics_heatmap as route

    scope = _scope()
    absent = route._heatmap_identity({"scope": scope, "kind": "suite_day", "rows": None, "runs": None})
    explicit = route._heatmap_identity({"scope": scope, "kind": "suite_day", "rows": 40, "runs": None})
    assert absent == explicit
    with pytest.raises(AnalyticsQueryError):
        route._heatmap_identity({"scope": scope, "kind": "nope", "rows": None, "runs": None})


def _param_description(router, path: str, name: str) -> str:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    params = app.openapi()["paths"][path]["get"]["parameters"]
    return next(p for p in params if p["name"] == name).get("description", "")


def test_the_two_all_projects_refusals_are_documented_where_they_apply() -> None:
    """R1-8: "All Projects is not allowed" has two codes. A route that always
    needs a project answers ``missing_parameter`` (the scope's own check); the
    heatmap takes All Projects but two of its kinds do not, and answer
    ``project_required``. No behaviour change: the OpenAPI text names both."""
    from app.routers import analytics_coverage_map, analytics_heatmap, analytics_test_scatter

    kind = _param_description(analytics_heatmap.router, "/api/v1/analytics/heatmap", "kind")
    assert "project_required" in kind and "test_run" in kind and "suite_release" in kind
    assert "missing_parameter" not in kind
    for module, path in (
        (analytics_coverage_map, "/api/v1/analytics/coverage-map"),
        (analytics_test_scatter, "/api/v1/analytics/test-scatter"),
    ):
        text = _param_description(module.router, path, "project_id")
        assert "missing_parameter" in text, path
