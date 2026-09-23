"""VIZ-404 -- ``meta.comparability`` on chart-data, everything a database cannot answer.

The defect: the multi-series chart's "not comparable" banner could never appear
from real data. Nothing computed comparability, so two releases that ran
DIFFERENT suites were drawn side by side as if like-for-like. The judgement is
now made here, over the chart's own scope, and sent as the optional C2
``comparability`` object (``contracts/viz/README.md``).

What is proved here (the real rows are in
``tests/integration/test_chart_data_comparability_postgres.py``):

1. **The judgement.** Same suites is comparable; different suites is
   ``different_suites`` with the counts; a series with no per-test rows is
   ``partial_coverage`` (checked first -- an unknown set is not a different
   one); one series is not a comparison, so there is no judgement at all.
2. **Where it is assessed.** Only when the series are keyed by ``release`` or
   ``branch``. Absent everywhere else, because absent means "not assessed".
3. **The statement.** One grouped query; nothing from the request is
   interpolated (the compared keys and every filter value are binds); it
   carries the same tenant, release and suite fragments as the chart; the
   suite key is the chart's own lower-cased effective suite.
4. **The cache identity** moves for exactly the requests whose payload
   changed shape, so an entry cached without ``comparability`` is never served
   as the new shape.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.models.viz_contracts import COMPARABILITY_REASON_CODES, validate_contract
from app.services import chart_data_service as svc
from app.services.analytics_scope import AnalyticsScope

FROZEN = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
R1, R2, R3 = (str(uuid.UUID(int=n)) for n in (1, 2, 3))


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


def _spec(metric="executions", group_by=("day", "release"), top_n=None) -> svc.ChartSpec:
    return svc.parse_chart_spec(metric, list(group_by), top_n, scope=_scope())


def _coverage(per_series: dict[str, int], union: int, common: int) -> svc.SuiteCoverage:
    return svc.SuiteCoverage(per_series=per_series, union=union, common=common)


def _envelope_with(comparability: dict) -> dict:
    """The contract's own valid envelope, carrying this judgement."""
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "contracts" / "viz" / "fixtures" / "envelope" / "valid" / "filtered.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))["payload"]
    return payload | {"comparability": comparability}


# ── 1. The judgement ───────────────────────────────────────────────────────


def test_the_same_suites_are_comparable_and_explain_nothing() -> None:
    judged = svc.judge_comparability(
        "release", [R1, R2], _coverage({R1: 3, R2: 3}, union=3, common=3)
    )
    assert judged == {"comparable": True, "reason": None, "reason_code": None}
    validate_contract("envelope", _envelope_with(judged))


def test_different_suites_are_not_comparable_and_the_reason_counts_them() -> None:
    judged = svc.judge_comparability(
        "release", [R1, R2, R3], _coverage({R1: 12, R2: 9, R3: 10}, union=12, common=9)
    )
    assert judged is not None
    assert judged["comparable"] is False
    assert judged["reason_code"] == "different_suites"
    assert judged["reason"] == (
        "The 3 series compared by release did not run the same suites in this "
        "scope: 12 suites ran in at least one of them, 9 in all of them."
    )
    validate_contract("envelope", _envelope_with(judged))


def test_the_same_count_of_different_suites_is_still_different() -> None:
    """Two releases that each ran 2 suites, but not the same 2: counting per
    series alone would call this comparable."""
    judged = svc.judge_comparability(
        "branch", ["main", "dev"], _coverage({"main": 2, "dev": 2}, union=3, common=1)
    )
    assert judged is not None and judged["reason_code"] == "different_suites"
    assert "by branch" in judged["reason"]


def test_a_series_with_no_per_test_rows_is_partial_coverage_first() -> None:
    """Its suite set is UNKNOWN, not empty: calling that 'different suites'
    would blame the release for a gap in the evidence."""
    judged = svc.judge_comparability(
        "release", [R1, R2, R3], _coverage({R1: 4}, union=4, common=4)
    )
    assert judged is not None
    assert judged["comparable"] is False
    assert judged["reason_code"] == "partial_coverage"
    assert judged["reason"] == (
        "2 of the 3 series compared by release have no per-test results in this "
        "scope, so the suites they ran cannot be compared."
    )
    one = svc.judge_comparability("release", [R1, R2], _coverage({R1: 4}, union=4, common=4))
    assert one is not None
    assert one["reason"] == (
        "1 of the 2 series compared by release has no per-test results in this "
        "scope, so the suites it ran cannot be compared."
    )
    validate_contract("envelope", _envelope_with(judged))


def test_a_zero_count_is_no_rows_too() -> None:
    judged = svc.judge_comparability(
        "release", [R1, R2], _coverage({R1: 4, R2: 0}, union=4, common=0)
    )
    assert judged is not None and judged["reason_code"] == "partial_coverage"


def test_one_series_is_not_a_comparison_so_nothing_is_judged() -> None:
    assert svc.judge_comparability("release", [R1], _coverage({R1: 3}, 3, 3)) is None
    assert svc.judge_comparability("release", [], _coverage({}, 0, 0)) is None


def test_every_reason_code_the_service_emits_is_in_the_contract() -> None:
    assert {svc.REASON_DIFFERENT_SUITES, svc.REASON_PARTIAL_COVERAGE} == set(
        COMPARABILITY_REASON_CODES
    )


def test_the_reason_never_names_a_key() -> None:
    """Counts only: a release or branch name in the banner would be text the
    caller's scope did not produce."""
    hostile = "<img src=x onerror=alert(1)>"
    for coverage in (
        _coverage({hostile: 2, "main": 3}, union=3, common=2),
        _coverage({hostile: 2}, union=2, common=2),
    ):
        judged = svc.judge_comparability("branch", [hostile, "main"], coverage)
        assert judged is not None
        assert hostile not in judged["reason"] and "main" not in judged["reason"]


# ── 2. Where it is assessed ────────────────────────────────────────────────


@pytest.mark.parametrize("group_by,assessed", [
    (("day", "release"), True),
    (("week", "branch"), True),
    (("suite", "release"), True),
    (("environment", "branch"), True),
    (("day",), False),
    (("release",), False),
    (("branch",), False),
    (("day", "suite"), False),
    (("day", "environment"), False),
    (("release", "suite"), False),
])
def test_only_release_and_branch_series_are_assessed(group_by, assessed) -> None:
    assert svc.compares_series(_spec(group_by=group_by)) is assessed


class _ScriptedDb:
    """Answers the chart statement with ``cells`` and the coverage statement
    with ``coverage``; records every statement."""

    def __init__(self, cells: list[dict], coverage: list[dict]) -> None:
        self.cells = cells
        self.coverage = coverage
        self.statements: list[str] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        rows = self.coverage if "suite_coverage" in sql else (
            self.cells if "bucket_key" in sql else []
        )

        class _Row(dict):
            __getattr__ = dict.get

        class _Result:
            @staticmethod
            def fetchall():
                return [_Row(row) for row in rows]

        return _Result()


def _chart_row(bucket: str, series: str, executions: int = 3) -> dict:
    return dict(
        bucket_key=bucket, bucket_label=None, series_key=series, series_label=None,
        passed=executions, failed=0, broken=0, skipped=0, unknown=0,
        executions=executions, runs=1, metric_value=executions,
        metric_sample=executions, merged=1, series_key_total=None,
        bucket_key_total=None,
    )


async def test_two_release_series_carry_a_judgement() -> None:
    db = _ScriptedDb(
        cells=[_chart_row("2026-09-20", R1), _chart_row("2026-09-21", R2)],
        coverage=[
            dict(series_key=R1, suites=2, union_suites=3, common_suites=1),
            dict(series_key=R2, suites=2, union_suites=3, common_suites=1),
        ],
    )
    payload = await svc.build_chart_data(db, _scope(days=2), _spec(), now=FROZEN)  # type: ignore[arg-type]
    assert payload["comparability"]["comparable"] is False
    assert payload["comparability"]["reason_code"] == "different_suites"
    assert sum("suite_coverage" in sql for sql in db.statements) == 1, "one query, no N+1"


async def test_one_release_series_carries_no_key_and_runs_no_query() -> None:
    db = _ScriptedDb(cells=[_chart_row("2026-09-20", R1)], coverage=[])
    payload = await svc.build_chart_data(db, _scope(days=2), _spec(), now=FROZEN)  # type: ignore[arg-type]
    assert "comparability" not in payload
    assert not any("suite_coverage" in sql for sql in db.statements)


async def test_a_suite_series_carries_no_key_and_runs_no_query() -> None:
    db = _ScriptedDb(
        cells=[_chart_row("2026-09-20", "checkout"), _chart_row("2026-09-21", "orders")],
        coverage=[],
    )
    payload = await svc.build_chart_data(
        db, _scope(days=2), _spec(group_by=("day", "suite")), now=FROZEN  # type: ignore[arg-type]
    )
    assert "comparability" not in payload
    assert not any("suite_coverage" in sql for sql in db.statements)


async def test_a_denied_scope_judges_nothing() -> None:
    db = _ScriptedDb(cells=[], coverage=[])
    denied = _scope(denied=True)
    payload = await svc.build_chart_data(db, denied, _spec(), now=FROZEN)  # type: ignore[arg-type]
    assert "comparability" not in payload
    assert db.statements == []


async def test_the_route_lifts_the_judgement_into_meta_and_only_when_present() -> None:
    import inspect

    from app.routers import analytics

    code = "\n".join(
        line for line in inspect.getsource(analytics.chart_data).splitlines()
        if not line.lstrip().startswith("#")
    )
    assert 'payload.pop("comparability", None)' in code
    assert 'meta["comparability"]' in code


# ── 3. The statement ───────────────────────────────────────────────────────


def _statement(spec=None, scope=None, keys=(R1, R2)) -> tuple[str, dict]:
    return svc.build_comparability_statement(
        spec or _spec(), scope or _scope(), list(keys), now=FROZEN
    )


def test_the_compared_keys_and_the_filters_are_binds_never_text() -> None:
    hostile = "x'); DROP TABLE test_runs; --"
    sql_a, params_a = _statement(keys=(R1, hostile))
    sql_b, params_b = _statement(keys=(R2, R3))
    assert sql_a == sql_b
    assert hostile not in sql_a
    assert params_a["cmp_series_keys"] == [R1, hostile]
    filtered_a, _ = _statement(scope=_scope(release_ids=(R1, R2), suite_names=("Zq9", "b")))
    filtered_b, _ = _statement(scope=_scope(release_ids=(R2, R3), suite_names=("c", "D")))
    assert filtered_a == filtered_b
    assert "Zq9" not in filtered_a.lower() and "zq9" not in filtered_a.lower()
    assert R1 not in filtered_a


def test_it_carries_the_chart_s_scope_fragments() -> None:
    chart_sql, chart_params = svc.build_statement(
        _spec("pass_rate", ("day", "release")),
        _scope(release_ids=(R1, R2), suite_names=("Checkout",)),
        now=FROZEN,
    )
    sql, params = _statement(
        _spec("pass_rate", ("day", "release")),
        _scope(release_ids=(R1, R2), suite_names=("Checkout",)),
    )
    for fragment in (
        "tr.created_at >= :period_start",
        "tr.project_id = :project_id",
        "tr.primary_release_id IN :release_ids",
        "IN :suite_names" if "suite_names" in chart_params else "= :suite_name",
    ):
        assert fragment in chart_sql and fragment in sql, fragment
    assert params["period_start"] == chart_params["period_start"]
    assert params["release_ids"] == chart_params["release_ids"]


def test_the_suite_key_is_the_chart_s_lower_cased_effective_suite() -> None:
    sql, _ = _statement()
    assert f"{svc.DIMENSIONS['suite'].sql} AS suite_key" in sql
    assert svc.DIMENSIONS["suite"].sql.startswith("LOWER(")


def test_it_is_one_grouped_statement_over_the_rows() -> None:
    sql, params = _statement()
    assert sql.count("SELECT DISTINCT") == 1
    assert "FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id" in sql
    assert "LIMIT :cmp_row_cap" in sql
    assert params["cmp_row_cap"] == svc.MAX_SERIES + 1


def test_an_other_series_is_the_rest_of_the_scope_rolled_into_one() -> None:
    sql, params = _statement(keys=(R1, R2, svc.OTHER_KEY))
    assert params["cmp_series_keys"] == [R1, R2]
    assert "ELSE :chart_other_key END" in sql
    assert params["chart_other_key"] == svc.OTHER_KEY
    plain, _ = _statement(keys=(R1, R2))
    assert "ELSE :chart_other_key" not in plain
    assert "IN :cmp_series_keys" in plain


# ── 4. The cache identity ──────────────────────────────────────────────────


def test_the_cache_identity_moves_only_where_the_payload_changed_shape() -> None:
    scope = _scope()
    judged = svc.cache_identity_parts(scope, _spec(group_by=("day", "release")))
    assert f"comparability={svc.COMPARABILITY_SHAPE_VERSION}" in judged
    assert any(
        part.startswith("comparability=")
        for part in svc.cache_identity_parts(scope, _spec(group_by=("day", "branch")))
    )
    # Everything else keeps the identity it had, so no other entry is orphaned.
    unchanged = svc.cache_identity_parts(scope, _spec(group_by=("day", "suite")))
    assert not any(part.startswith("comparability=") for part in unchanged)
    assert len(unchanged) == 7
