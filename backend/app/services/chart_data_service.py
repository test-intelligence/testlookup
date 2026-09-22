"""VIZ-203 -- one guarded endpoint for "a metric, grouped by up to two things".

``GET /api/v1/analytics/chart-data`` answers every time series, bar chart and
ranked list the visualization upgrade needs, so a new chart does not need a new
route. The response is contract C3 ``chart_series`` (``contracts/viz/README.md``)
plus the C2 envelope, which means the table view, CSV export and keyboard
navigation read it with no transformation.

**Nothing from the request is ever interpolated.** ``metric`` and ``group_by``
are looked up in :data:`METRICS` and :data:`DIMENSIONS`; the value the caller
sent is a KEY, and what reaches the statement is the hard-coded fragment the
table holds. Everything else -- project, releases, suites, the window -- is a
bind, built by ``analytics_scope``'s fragments (conditional, never a
null-tolerant ``(:x IS NULL OR col = :x)``, never a join through
``release_test_run_links``). An unknown key is a 422 listing the allow-list,
raised before the database is touched, and never echoes what was sent.

**Two grains, always declared.** The product counts a run's tests two ways and
both are right for what they answer:

* ``run_aggregate`` -- ``SUM(test_runs.passed_tests)`` and its siblings. This
  is what ``/metrics/trends`` and ``/reports/summary`` count, and it is the
  only grain that sees a run whose per-test rows never landed (the live-stream
  gap). Used when every dimension is run-level and the metric is derivable
  from the aggregates.
* ``execution_row`` -- ``test_cases`` joined to its run. The only grain that
  can answer "by suite", "by status", "by test", a distinct count or a
  duration percentile, and the only honest one under a suite filter: a
  run-level suite filter keeps the run WHOLE, so its other suites' tests would
  be counted into a chart that says it is filtered.

``meta.definitions.grain`` names which one produced the numbers. The
equivalence with ``/metrics/trends`` (``tests/integration/test_chart_data_postgres.py``)
holds exactly where the grain is ``run_aggregate``.

**A rate is never a zero.** ``pass_rate`` and ``failure_rate`` are computed in
Python from the component counts through :mod:`app.core.pass_rate`, so skipped
and unknown are outside the denominator by construction and a bucket with
nothing evaluated is ``y: null, measured: false`` with a reason -- never 0%,
which would read as "everything failed" for a day that ran nothing. Quarantine
does not enter the rate: it is a property of a test, not of an execution's
verdict.

**"other" is recomputed, never averaged.** ``top_n`` keeps the largest keys and
merges the rest. For an additive metric the bucket is the sum; for a rate it is
recomputed from the merged counts (averaging a 100%-of-1 series with a
0%-of-99 series reports 50% where the truth is 1%); for a percentile or a
distinct count, which cannot be combined at all, it is ``measured: false`` with
the reason.

**Caching is VIZ-209's.** This module is a pure read: same scope, same answer,
no writes. :func:`cache_identity_parts` is the seam that story keys on -- the
canonical, order-insensitive identity of one request. Nothing here sets an
ETag, a TTL, a rate limit or a statement timeout.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, NamedTuple, Optional, Sequence

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import AnalyticsQueryError
from app.core.pass_rate import canonical_pass_rate, executed_count
from app.models.postgres import TestStatus
from app.models.viz_contracts import MAX_POINTS_PER_SERIES, MAX_SERIES
# The tenant rule, spelled once for every raw-SQL analytics query: the pin, the
# membership set, the fail-closed empty set, and the ``is_active`` guard in
# every branch. Re-spelling it here is exactly the drift that read a deleted
# project's runs into the trend chart at 229x the true number.
from app.services.analytics_service import _tenant_filter as tenant_filter_sql
from app.services.analytics_scope import (
    AnalyticsScope,
    cache_identity,
    effective_suite_sql,
    release_filter_sql,
    scoped_text,
    suite_filter_sql,
    suite_keys,
)

logger = structlog.get_logger(__name__)

#: The bucket a row with no value of this dimension falls into. A NULL branch
#: is the common case for uploads and must be visible, not dropped.
NO_VALUE = "(none)"
#: The key of the merged remainder when ``top_n`` is applied.
OTHER_KEY = "__other__"
#: The single series' key when only one dimension was asked for.
SINGLE_SERIES_KEY = "value"
#: The smallest sample a rate is reported for. One evaluated execution is
#: enough to have a rate; zero is not, and must never read as 0%.
MIN_RATE_SAMPLE = 1

GRAIN_RUN = "run_aggregate"
GRAIN_ROW = "execution_row"

MAX_GROUP_BY = 2
#: ``top_n`` on the SERIES axis, so ``top_n`` series plus "other" fit the cap.
MAX_TOP_N_SERIES = MAX_SERIES - 1
#: ``top_n`` on a category x axis, for the same reason: the kept buckets plus
#: "other" must fit the point cap.
MAX_TOP_N_POINTS = MAX_POINTS_PER_SERIES - 1
#: The hard ceiling on the groups one statement may return, derived from the C3
#: caps: every kept bucket (plus an "other") for every kept series (plus an
#: "other"). The ranking CTEs bound each axis already, so nothing legitimate
#: reaches it; it is the last line of defence for a dimension whose cardinality
#: nobody predicted, and it is why no statement can stream a suite's 1.4M
#: groups into this process again.
MAX_GROUPS = (MAX_POINTS_PER_SERIES + 1) * (MAX_SERIES + 1)


# ── The dimension allow-list ───────────────────────────────────────────────


@dataclass(frozen=True)
class DimensionDef:
    """One group-by dimension: its hard-coded SQL and how it is labelled."""

    #: The GROUP BY / SELECT expression. Hard-coded, never built from input.
    sql: str
    #: An aggregate giving the display label when the key is normalised (the
    #: suite's real spelling, a test's name). ``None`` means key == label.
    label_sql: Optional[str] = None
    #: ``True`` when the expression reads ``test_cases``.
    row_level: bool = False
    #: ``True`` for the time axis: buckets are generated, not observed.
    time: bool = False
    #: ``True`` when the axis can be large enough that two of them together
    #: are a cross product nobody can read (suite x test).
    high_cardinality: bool = False
    #: ``True`` when the key is a UUID whose name is looked up afterwards.
    entity: Optional[str] = None


_EFFECTIVE_SUITE = effective_suite_sql()

#: Every dimension the story names, each pinned to ONE fragment.
#:
#: * ``day``/``week`` are UTC calendar buckets. ``date_trunc('week')`` is
#:   Postgres's ISO week: it starts on Monday, so the label is that Monday.
#: * ``suite`` is the EFFECTIVE suite (a live-stream run's label, else the
#:   row's own), matched case-insensitively as every suite filter in the
#:   product is -- ``Orders`` and ``orders`` are one series, not two -- and
#:   labelled with the spelling that was ingested.
#: * ``status`` and ``failure_category`` are lower-cased to the vocabulary
#:   contract C3/C5 fix (``passed, failed, broken, skipped, unknown``); the
#:   COLUMN holds the uppercase ``TestStatus`` values, which is what the
#:   metric filters below compare against.
#: * ``branch`` is raw: it is a git ref and its case is meaningful.
#:   ``environment`` is lower-cased on write (migration 0129) and lowered again
#:   here so rows written before that migration bucket with their successors.
#: * ``commit_hash`` is the commit COLUMN, not a dimension: a chart with one
#:   point per commit is a run list, which ``/runs`` already is.
DIMENSIONS: dict[str, DimensionDef] = {
    "day": DimensionDef(
        sql="TO_CHAR(DATE_TRUNC('day', tr.created_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD')",
        time=True,
    ),
    "week": DimensionDef(
        sql="TO_CHAR(DATE_TRUNC('week', tr.created_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD')",
        time=True,
    ),
    "project": DimensionDef(sql="tr.project_id::text", entity="project"),
    "release": DimensionDef(
        sql="COALESCE(tr.primary_release_id::text, 'unattributed')", entity="release"
    ),
    "suite": DimensionDef(
        sql=f"LOWER(COALESCE(NULLIF({_EFFECTIVE_SUITE}, ''), '{NO_VALUE}'))",
        label_sql=f"MIN(COALESCE(NULLIF({_EFFECTIVE_SUITE}, ''), '{NO_VALUE}'))",
        row_level=True,
        high_cardinality=True,
    ),
    "status": DimensionDef(sql="LOWER(tc.status)", row_level=True),
    "failure_category": DimensionDef(
        sql="LOWER(COALESCE(tc.failure_category, 'UNKNOWN'))", row_level=True
    ),
    "branch": DimensionDef(
        sql=f"COALESCE(NULLIF(TRIM(tr.branch), ''), '{NO_VALUE}')"
    ),
    "environment": DimensionDef(
        sql=f"LOWER(COALESCE(NULLIF(TRIM(tr.environment), ''), '{NO_VALUE}'))",
        label_sql=f"MIN(COALESCE(NULLIF(TRIM(tr.environment), ''), '{NO_VALUE}'))",
    ),
    "ingestion_source": DimensionDef(
        sql=f"COALESCE(NULLIF(TRIM(tr.ingestion_source), ''), '{NO_VALUE}')"
    ),
    "test": DimensionDef(
        sql="tc.test_fingerprint",
        label_sql="MIN(tc.test_name)",
        row_level=True,
        high_cardinality=True,
    ),
}


# ── The metric allow-list ──────────────────────────────────────────────────


@dataclass(frozen=True)
class MetricDef:
    """One metric: how it is aggregated in each grain, and how it combines."""

    #: ``COUNT``/``SUM``/percentile over ``test_cases``. ``None`` is a rate,
    #: computed in Python from the component counts.
    row_sql: Optional[str]
    #: The same over ``test_runs`` aggregates; ``None`` forces the row grain.
    run_sql: Optional[str]
    #: The sample behind the value: the rate's denominator, the executions
    #: with a duration, or the executions in the bucket.
    row_sample: str
    run_sample: str
    #: ``True`` when merging two groups is a sum (an "other" bucket exists).
    #: A percentile and a distinct count are NOT additive; neither is a run
    #: count on the row grain, where one run can sit in several buckets.
    additive: bool
    #: ``True`` for a rate: recomputed from counts, never averaged, and
    #: ``measured: false`` below :data:`MIN_RATE_SAMPLE`.
    rate: bool = False
    integer: bool = True
    #: ``True`` when "nothing here" really is zero. A bucket with no rows ran
    #: no tests (0 executions, 0 distinct tests, 0 ms of runtime), but it has
    #: no median: a percentile of an empty set is not measured, and neither is
    #: a rate. Zero-filling either would draw a line along the floor for days
    #: nobody tested, which reads as "everything was instant" / "everything
    #: failed".
    zero_fill: bool = True


_EVALUATED_ROW = (
    f"COUNT(*) FILTER (WHERE tc.status IN "
    f"('{TestStatus.PASSED.value}', '{TestStatus.FAILED.value}', '{TestStatus.BROKEN.value}'))"
)
_EVALUATED_RUN = "COALESCE(SUM(tr.passed_tests + tr.failed_tests + tr.broken_tests), 0)"
_EXECUTIONS_ROW = "COUNT(*)"
_EXECUTIONS_RUN = "COALESCE(SUM(tr.total_tests), 0)"
_TIMED_ROW = "COUNT(*) FILTER (WHERE tc.duration_ms IS NOT NULL)"


def _status_count(status: TestStatus) -> str:
    return f"COUNT(*) FILTER (WHERE tc.status = '{status.value}')"


def _percentile(fraction: str) -> str:
    # FILTER keeps the ordered-set aggregate off the NULL durations a format
    # that carries no timings leaves behind, so "no duration" never reads as 0.
    return (
        f"PERCENTILE_CONT({fraction}) WITHIN GROUP (ORDER BY tc.duration_ms) "
        "FILTER (WHERE tc.duration_ms IS NOT NULL)"
    )


METRICS: dict[str, MetricDef] = {
    "executions": MetricDef(_EXECUTIONS_ROW, _EXECUTIONS_RUN, _EXECUTIONS_ROW, _EXECUTIONS_RUN, True),
    "passed": MetricDef(
        _status_count(TestStatus.PASSED), "COALESCE(SUM(tr.passed_tests), 0)",
        _EXECUTIONS_ROW, _EXECUTIONS_RUN, True,
    ),
    "failed": MetricDef(
        _status_count(TestStatus.FAILED), "COALESCE(SUM(tr.failed_tests), 0)",
        _EXECUTIONS_ROW, _EXECUTIONS_RUN, True,
    ),
    "broken": MetricDef(
        _status_count(TestStatus.BROKEN), "COALESCE(SUM(tr.broken_tests), 0)",
        _EXECUTIONS_ROW, _EXECUTIONS_RUN, True,
    ),
    "skipped": MetricDef(
        _status_count(TestStatus.SKIPPED), "COALESCE(SUM(tr.skipped_tests), 0)",
        _EXECUTIONS_ROW, _EXECUTIONS_RUN, True,
    ),
    "unknown": MetricDef(
        _status_count(TestStatus.UNKNOWN), "COALESCE(SUM(tr.unknown_tests), 0)",
        _EXECUTIONS_ROW, _EXECUTIONS_RUN, True,
    ),
    # One row per test per run: a retried test is ONE row carrying
    # ``retry_count``, so this is a count of tests that were retried, never a
    # series of attempts.
    "retried_tests": MetricDef(
        "COUNT(*) FILTER (WHERE COALESCE(tc.retry_count, 0) > 0)", None,
        _EXECUTIONS_ROW, _EXECUTIONS_RUN, True,
    ),
    "pass_rate": MetricDef(None, None, _EVALUATED_ROW, _EVALUATED_RUN, True, rate=True, integer=False),
    "failure_rate": MetricDef(None, None, _EVALUATED_ROW, _EVALUATED_RUN, True, rate=True, integer=False),
    "flaky_tests": MetricDef(
        "COUNT(DISTINCT tc.test_fingerprint) FILTER (WHERE tc.is_flaky_run)", None,
        _EXECUTIONS_ROW, _EXECUTIONS_RUN, False,
    ),
    "unique_tests": MetricDef(
        "COUNT(DISTINCT tc.test_fingerprint)", None, _EXECUTIONS_ROW, _EXECUTIONS_RUN, False
    ),
    # ``n`` is the executions in the bucket on BOTH grains, as
    # ``definitions.n`` promises. It was ``COUNT(*)`` on the run grain, which
    # made n == y: the chart said "12 runs, sample 12", a sample that measures
    # the value it is the sample OF and tells a reader nothing about how much
    # work those runs did.
    "run_count": MetricDef(
        "COUNT(DISTINCT tr.id)", "COUNT(*)", _EXECUTIONS_ROW, _EXECUTIONS_RUN, False
    ),
    "duration_p50": MetricDef(
        _percentile("0.5"), None, _TIMED_ROW, _TIMED_ROW, False,
        integer=False, zero_fill=False,
    ),
    "duration_p95": MetricDef(
        _percentile("0.95"), None, _TIMED_ROW, _TIMED_ROW, False,
        integer=False, zero_fill=False,
    ),
    "duration_total": MetricDef(
        "COALESCE(SUM(tc.duration_ms), 0)", None, _TIMED_ROW, _TIMED_ROW, True
    ),
}

#: The metrics whose numbers only exist per execution row.
_ROW_ONLY = frozenset(name for name, m in METRICS.items() if m.run_sql is None and not m.rate)


# ── The parsed request ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChartSpec:
    """A validated request: nothing here came from the caller as text."""

    metric: str
    group_by: tuple[str, ...]
    top_n: Optional[int]

    @property
    def bucket_dimension(self) -> str:
        return self.group_by[0]

    @property
    def series_dimension(self) -> Optional[str]:
        return self.group_by[1] if len(self.group_by) > 1 else None

    @property
    def x_type(self) -> str:
        return "time" if DIMENSIONS[self.bucket_dimension].time else "category"


def _allowed(param: str, code: str, table: dict) -> AnalyticsQueryError:
    return AnalyticsQueryError(
        code,
        f"{param} is not one of the values this endpoint supports",
        param=param,
        allowed=sorted(table),
    )


def parse_chart_spec(
    metric: Any, group_by: Any, top_n: Any, *, scope: AnalyticsScope
) -> ChartSpec:
    """Validate the chart half of the request, before any database access.

    Every refusal is a C1/VIZ-210 body with the allow-list in ``allowed``. The
    value that was sent is NEVER echoed: it is untrusted text that would be
    rendered straight into a toast.
    """
    if not isinstance(metric, str) or metric not in METRICS:
        raise _allowed("metric", "metric_enum", METRICS)

    raw = [group_by] if isinstance(group_by, str) else list(group_by or ())
    if not raw:
        raise AnalyticsQueryError(
            "missing_parameter",
            "group_by is required: name the dimension to chart, and optionally "
            "a second one to key the series by",
            param="group_by",
            allowed=sorted(DIMENSIONS),
        )
    if len(raw) > MAX_GROUP_BY:
        raise AnalyticsQueryError(
            "group_by_cap",
            f"at most {MAX_GROUP_BY} group_by values: the first is the axis, "
            "the second keys the series",
            param="group_by",
            allowed={"max": MAX_GROUP_BY},
        )
    dims: list[str] = []
    for value in raw:
        if not isinstance(value, str) or value not in DIMENSIONS:
            raise _allowed("group_by", "dimension_enum", DIMENSIONS)
        if value in dims:
            raise AnalyticsQueryError(
                "unique_dimension",
                "a dimension may be named once",
                param="group_by",
                allowed=sorted(DIMENSIONS),
            )
        dims.append(value)

    if len(dims) == 2 and all(DIMENSIONS[d].high_cardinality for d in dims):
        raise AnalyticsQueryError(
            "high_cardinality_pair",
            "these two dimensions together are a cross product no chart can "
            "show; group by one of them and filter by the other",
            param="group_by",
            allowed={"not_together": sorted(
                d for d in DIMENSIONS if DIMENSIONS[d].high_cardinality
            )},
        )
    if DIMENSIONS[dims[0]].time and len(dims) == 2 and DIMENSIONS[dims[1]].time:
        raise AnalyticsQueryError(
            "unique_dimension", "day and week are the same axis",
            param="group_by", allowed=sorted(DIMENSIONS),
        )
    if len(dims) == 2 and DIMENSIONS[dims[1]].time:
        raise AnalyticsQueryError(
            "time_dimension_position",
            "a time dimension is the axis, so it comes first: "
            "group_by=day&group_by=suite, not the other way round",
            param="group_by",
            allowed={"first": ["day", "week"]},
        )

    parsed_top_n = _parse_top_n(top_n, dims)

    if "test" in dims and parsed_top_n is None and len(suite_keys(scope.suite_names)) != 1:
        raise AnalyticsQueryError(
            "test_requires_scope",
            "group_by=test needs a bound: one suite_name, or a top_n",
            param="group_by",
            allowed={"requires": ["exactly one suite_name", "top_n"]},
        )
    return ChartSpec(metric=metric, group_by=tuple(dims), top_n=parsed_top_n)


def _parse_top_n(top_n: Any, dims: Sequence[str]) -> Optional[int]:
    if top_n is None:
        return None
    if isinstance(top_n, bool) or not isinstance(top_n, int):
        raise AnalyticsQueryError(
            "top_n_range", "top_n is an integer", param="top_n",
            allowed={"min": 1, "max": MAX_POINTS_PER_SERIES},
        )
    if len(dims) == 2:
        # It names SERIES, and the series plus "other" must fit the C3 cap.
        if not 1 <= top_n <= MAX_TOP_N_SERIES:
            raise AnalyticsQueryError(
                "top_n_range",
                f"top_n names series here, so it is 1-{MAX_TOP_N_SERIES}: the "
                f"contract allows {MAX_SERIES} series and one of them is 'other'",
                param="top_n",
                allowed={"min": 1, "max": MAX_TOP_N_SERIES},
            )
        return int(top_n)
    if DIMENSIONS[dims[0]].time:
        raise AnalyticsQueryError(
            "top_n_unsupported",
            "top_n ranks categories; a time axis is bounded by the window "
            "instead. Add a second group_by to rank its series.",
            param="top_n",
            allowed={"requires": "a category dimension"},
        )
    if not 1 <= top_n <= MAX_TOP_N_POINTS:
        raise AnalyticsQueryError(
            "top_n_range",
            f"top_n names buckets here, so it is 1-{MAX_TOP_N_POINTS}: the "
            f"contract allows {MAX_POINTS_PER_SERIES} points and one of them "
            "is 'other'",
            param="top_n",
            allowed={"min": 1, "max": MAX_TOP_N_POINTS},
        )
    return int(top_n)


# ── The grain ──────────────────────────────────────────────────────────────


def grain_for(spec: ChartSpec, scope: AnalyticsScope) -> str:
    """Which population answers this request. See the module docstring."""
    if spec.metric in _ROW_ONLY:
        return GRAIN_ROW
    if any(DIMENSIONS[dim].row_level for dim in spec.group_by):
        return GRAIN_ROW
    if suite_keys(scope.suite_names):
        # A run-level suite filter keeps the run whole, so its other suites'
        # tests would be counted into a chart that says it is filtered.
        return GRAIN_ROW
    return GRAIN_RUN


def grain_forced_by(spec: ChartSpec, scope: AnalyticsScope) -> Optional[str]:
    """The FILTER that moved this request off the grain its metric and
    dimensions would otherwise have used, or ``None``.

    Adding ``suite_name`` to ``run_count`` or ``executions`` moves them from
    run aggregates to execution rows -- a different population for the same
    axis and the same legend. ``meta.definitions.grain`` has always named the
    grain, but a reader comparing two screenshots cannot see WHY it changed,
    and "the number moved because I filtered" reads as "the number moved".
    There is no leak here: the chart frame badges it.
    """
    if spec.metric in _ROW_ONLY:
        return None
    if any(DIMENSIONS[dim].row_level for dim in spec.group_by):
        return None
    if suite_keys(scope.suite_names):
        return "suite_name"
    return None


# ── The statement ──────────────────────────────────────────────────────────


class Cell(NamedTuple):
    """One ``(bucket, series)`` group as SQL returned it."""

    x: str
    x_label: str
    series: str
    series_label: str
    passed: int
    failed: int
    broken: int
    skipped: int
    unknown: int
    executions: int
    runs: int
    #: The metric's own aggregate; ``None`` for a rate (computed in Python).
    value: Optional[float]
    #: The sample behind ``value``.
    sample: int
    #: How many source groups this cell already merges. ``1`` for a kept key;
    #: more for an ``other`` roll-up SQL did, which is what tells a percentile
    #: or a distinct count that it cannot answer for this cell.
    merged: int = 1


@dataclass(frozen=True)
class AxisCounts:
    """How many distinct keys each axis really had, before ranking.

    SQL ranks and caps now, so the keys Python sees ARE the kept ones and
    counting them would report the cap back as the truth. ``None`` means
    "count what you were given" -- the pure-assembly path the unit tests drive.
    """

    series: Optional[int] = None
    bucket: Optional[int] = None


#: The component counts every cell carries, in the order the statement selects
#: them and :class:`Cell` reads them back.
_COUNT_COLUMNS = (
    "passed", "failed", "broken", "skipped", "unknown", "executions", "runs",
)

#: A placeholder column carries its TYPE. A bare ``NULL`` in a CTE is ``text``,
#: and the roll-up above it then asks for ``sum(text)``, which does not exist:
#: every rate chart was a 500 the moment the aggregate moved into a CTE.
_NULL_TEXT = "CAST(NULL AS TEXT)"
_NULL_NUMBER = "CAST(NULL AS DOUBLE PRECISION)"


def _rank_total_sql(metric: MetricDef) -> str:
    """The SQL twin of :func:`_rank`, over one key's grouped rows.

    It has to agree with the Python one exactly: they rank the same keys, and
    a disagreement would keep one series in the chart and roll a bigger one
    into "other".
    """
    sample_first = "COALESCE(NULLIF(SUM(metric_sample), 0), SUM(executions))"
    if metric.rate or not metric.additive:
        # A rate ranks by its sample size, a percentile and a distinct count by
        # theirs: ranking a rate by its value puts a 100%-of-one series at the
        # top of a chart about volume.
        return sample_first
    return (
        f"CASE WHEN SUM(metric_value) IS NULL THEN {sample_first} "
        "ELSE SUM(metric_value) END"
    )


def _ranking_ctes(name: str, key: str, metric: MetricDef, keep: str) -> str:
    """Rank one axis's keys IN SQL and keep the largest.

    ``ROW_NUMBER`` rather than ``LIMIT`` because the number to keep depends on
    how many keys there are (the cap gives one of its slots to "other" only
    when it actually bites), and because the full count has to come back with
    the rows: it is what ``meta.truncated_total`` reports.
    """
    return f"""
        {name}_totals AS (
            SELECT {key} AS rank_key, {_rank_total_sql(metric)} AS rank_total
            FROM grouped
            GROUP BY {key}
        ), {name}_ranked AS (
            SELECT
                rank_key,
                ROW_NUMBER() OVER (ORDER BY rank_total DESC, rank_key ASC) AS rn,
                COUNT(*) OVER () AS key_total
            FROM {name}_totals
        ), {name}_kept AS (
            SELECT rank_key FROM {name}_ranked WHERE rn <= {keep}
        )"""


def build_statement(
    spec: ChartSpec, scope: AnalyticsScope, *, now: Optional[datetime] = None
) -> tuple[str, dict]:
    """``(sql, params)`` for one chart. Exposed so the injection tests can read
    the text without a database.

    Three stages, all server-side:

    1. ``grouped`` -- the aggregate, one row per ``(bucket, series)``. This is
       the statement this module used to return whole, and it is unchanged:
       every fragment still comes from :data:`DIMENSIONS` / :data:`METRICS` and
       every filter value is still a bind.
    2. the ranking CTEs -- each axis's keys totalled, ordered and cut to the
       ``top_n`` or the C3 cap, with the full key count carried out.
    3. the projection -- kept keys as themselves, the rest folded into
       ``__other__`` (where the caller asked for one), re-aggregated from the
       COMPONENT COUNTS so a rate is recomputed and never averaged.

    Before this, ranking happened in Python after every group was fetched:
    ``day x test`` on a 200-fingerprint seed returned 73 000 rows for a chart
    of 8 series, and a real suite would return ~1.4M ``Cell`` tuples per
    request. ``LIMIT :chart_row_cap`` is the hard ceiling under all of it.
    """
    grain = grain_for(spec, scope)
    metric = METRICS[spec.metric]
    params: dict[str, Any] = {"period_start": window_start(scope.window_days, now=now)}

    if grain == GRAIN_ROW:
        source = "FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id"
        counts = {
            "passed": _status_count(TestStatus.PASSED),
            "failed": _status_count(TestStatus.FAILED),
            "broken": _status_count(TestStatus.BROKEN),
            "skipped": _status_count(TestStatus.SKIPPED),
            "unknown": _status_count(TestStatus.UNKNOWN),
            "executions": _EXECUTIONS_ROW,
            "runs": "COUNT(DISTINCT tr.id)",
        }
        value_sql = metric.row_sql
        sample_sql = metric.row_sample
        # Rows carry the effective suite, so the suite filter is the exact one.
        suite_fragment = suite_filter_sql(params, scope.suite_arg)
    else:
        source = "FROM test_runs tr"
        counts = {
            "passed": "COALESCE(SUM(tr.passed_tests), 0)",
            "failed": "COALESCE(SUM(tr.failed_tests), 0)",
            "broken": "COALESCE(SUM(tr.broken_tests), 0)",
            "skipped": "COALESCE(SUM(tr.skipped_tests), 0)",
            "unknown": "COALESCE(SUM(tr.unknown_tests), 0)",
            "executions": _EXECUTIONS_RUN,
            "runs": "COUNT(*)",
        }
        value_sql = metric.run_sql
        sample_sql = metric.run_sample
        suite_fragment = ""

    bucket = DIMENSIONS[spec.bucket_dimension]
    series = DIMENSIONS[spec.series_dimension] if spec.series_dimension else None

    select_parts = [f"{bucket.sql} AS bucket_key"]
    select_parts.append(
        f"{bucket.label_sql} AS bucket_label" if bucket.label_sql else f"{_NULL_TEXT} AS bucket_label"
    )
    if series is not None:
        select_parts.append(f"{series.sql} AS series_key")
        select_parts.append(
            f"{series.label_sql} AS series_label" if series.label_sql else f"{_NULL_TEXT} AS series_label"
        )
    else:
        select_parts.append(f"'{SINGLE_SERIES_KEY}' AS series_key")
        select_parts.append(f"{_NULL_TEXT} AS series_label")
    select_parts += [f"{expr} AS {name}" for name, expr in counts.items()]
    select_parts.append(f"{value_sql} AS metric_value" if value_sql else f"{_NULL_NUMBER} AS metric_value")
    select_parts.append(f"{sample_sql} AS metric_sample")

    group_parts = [bucket.sql] + ([series.sql] if series is not None else [])

    grouped = f"""
        SELECT
            {', '.join(select_parts)}
        {source}
        WHERE tr.created_at >= :period_start
          {tenant_filter_sql(
              params,
              project_id=scope.project,
              allowed_project_ids=scope.allowed_project_ids,
          )}
          {release_filter_sql(params, scope.release_arg)}
          {suite_fragment}
        GROUP BY {', '.join(group_parts)}"""

    ctes = [f"grouped AS ({grouped}\n        )"]
    params["chart_other_key"] = OTHER_KEY
    params["chart_row_cap"] = MAX_GROUPS

    # ── the series axis: always ranked, because the C3 cap always applies ──
    if series is not None:
        if spec.top_n is not None:
            params["chart_series_top_n"] = spec.top_n
            keep = ":chart_series_top_n"
        else:
            # Nobody named a top_n, so the cap decides -- and when it bites it
            # spends one of its slots on "other", exactly as the assembly did.
            params["chart_series_cap"] = MAX_SERIES
            params["chart_series_capped"] = MAX_TOP_N_SERIES
            keep = (
                "CASE WHEN key_total > :chart_series_cap "
                "THEN :chart_series_capped ELSE :chart_series_cap END"
            )
        ctes.append(_ranking_ctes("series", "series_key", metric, keep))
        series_key_out = (
            "CASE WHEN sk.rank_key IS NULL THEN :chart_other_key ELSE g.series_key END"
        )
        series_label_out = "MIN(g.series_label) FILTER (WHERE sk.rank_key IS NOT NULL)"
        series_join = "LEFT JOIN series_kept sk ON sk.rank_key IS NOT DISTINCT FROM g.series_key"
        series_total = "(SELECT COUNT(*) FROM series_totals)"
    else:
        series_key_out = "g.series_key"
        series_label_out = "MIN(g.series_label)"
        series_join = ""
        series_total = "NULL"

    # ── the x axis ──
    #
    # A time axis is generated, not observed: its buckets are the window and
    # there is nothing to rank. A category axis is ranked -- into an "other"
    # bucket when ``top_n`` asked for one, and otherwise cut at the point cap
    # with ``truncated`` saying so rather than inventing a bucket nobody asked
    # for.
    rolls_up_buckets = spec.top_n is not None and series is None
    if bucket.time:
        bucket_key_out = "g.bucket_key"
        bucket_label_out = "MIN(g.bucket_label)"
        bucket_join = ""
        bucket_total = "NULL"
    else:
        if rolls_up_buckets:
            params["chart_bucket_top_n"] = spec.top_n
            keep = ":chart_bucket_top_n"
        else:
            params["chart_bucket_cap"] = MAX_POINTS_PER_SERIES
            keep = ":chart_bucket_cap"
        ctes.append(_ranking_ctes("bucket", "bucket_key", metric, keep))
        bucket_total = "(SELECT COUNT(*) FROM bucket_totals)"
        if rolls_up_buckets:
            bucket_key_out = (
                "CASE WHEN bk.rank_key IS NULL THEN :chart_other_key ELSE g.bucket_key END"
            )
            bucket_label_out = "MIN(g.bucket_label) FILTER (WHERE bk.rank_key IS NOT NULL)"
            bucket_join = (
                "LEFT JOIN bucket_kept bk ON bk.rank_key IS NOT DISTINCT FROM g.bucket_key"
            )
        else:
            bucket_key_out = "g.bucket_key"
            bucket_label_out = "MIN(g.bucket_label)"
            bucket_join = "JOIN bucket_kept bk ON bk.rank_key IS NOT DISTINCT FROM g.bucket_key"

    sums = ', '.join(f"SUM(g.{name}) AS {name}" for name in _COUNT_COLUMNS)
    sql = f"""
        WITH {', '.join(ctes)}
        SELECT
            {bucket_key_out} AS bucket_key,
            {bucket_label_out} AS bucket_label,
            {series_key_out} AS series_key,
            {series_label_out} AS series_label,
            {sums},
            SUM(g.metric_value) AS metric_value,
            SUM(g.metric_sample) AS metric_sample,
            COUNT(*) AS merged,
            {series_total} AS series_key_total,
            {bucket_total} AS bucket_key_total
        FROM grouped g
        {bucket_join}
        {series_join}
        GROUP BY 1, 3
        ORDER BY 1, 3
        LIMIT :chart_row_cap
    """
    return sql, params


async def _fetch_cells(
    db: AsyncSession, spec: ChartSpec, scope: AnalyticsScope, *, now: Optional[datetime] = None
) -> tuple[list[Cell], AxisCounts]:
    sql, params = build_statement(spec, scope, now=now)
    result = await db.execute(scoped_text(sql, params), params)
    cells: list[Cell] = []
    series_total: Optional[int] = None
    bucket_total: Optional[int] = None
    for row in result.fetchall():
        key = "" if row.bucket_key is None else str(row.bucket_key)
        series_key = "" if row.series_key is None else str(row.series_key)
        if row.series_key_total is not None:
            series_total = int(row.series_key_total)
        if row.bucket_key_total is not None:
            bucket_total = int(row.bucket_key_total)
        cells.append(Cell(
            x=key or NO_VALUE,
            x_label=str(row.bucket_label) if row.bucket_label is not None else (key or NO_VALUE),
            series=series_key or NO_VALUE,
            series_label=(
                str(row.series_label) if row.series_label is not None else (series_key or NO_VALUE)
            ),
            passed=int(row.passed or 0),
            failed=int(row.failed or 0),
            broken=int(row.broken or 0),
            skipped=int(row.skipped or 0),
            unknown=int(row.unknown or 0),
            executions=int(row.executions or 0),
            runs=int(row.runs or 0),
            value=None if row.metric_value is None else float(row.metric_value),
            sample=int(row.metric_sample or 0),
            merged=int(row.merged or 1),
        ))
    return cells, AxisCounts(series=series_total, bucket=bucket_total)


# ── Buckets ────────────────────────────────────────────────────────────────


def request_clock() -> datetime:
    """The ONE instant a chart-data request is answered at.

    The route takes it here rather than calling ``datetime.now()`` itself, so
    the window bound, the generated axis, ``partial_bucket`` and the envelope's
    ``as_of`` all read the same clock -- and so a test that freezes this
    module's clock freezes the whole request, instead of freezing the service
    and leaving the route on the wall clock.
    """
    return datetime.now(timezone.utc)


def window_start(days: int, *, now: Optional[datetime] = None) -> datetime:
    """UTC midnight ``days - 1`` days ago -- the first whole bucket of a window
    that ends today, the boundary ``/metrics/trends`` uses.

    ``now`` is threaded from the route so ONE clock decides the window, the
    axis and ``partial_bucket``. Three independent ``datetime.now()`` calls
    (service, statement, envelope) can straddle UTC midnight, and the answer is
    then a chart whose last bucket is not in its own window.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)


def time_buckets(dimension: str, now: datetime, *, days: int) -> list[str]:
    """Every UTC day, or every ISO week (labelled by its Monday), in the window.

    The first week bucket reaches back to its Monday, which can predate the
    window: the query's lower bound is unchanged, so that bucket holds only the
    part of the week inside the window. ``meta.definitions.bucket`` says so.
    """
    today = now.astimezone(timezone.utc).date()
    first = today - timedelta(days=days - 1)
    if dimension == "day":
        return [(first + timedelta(days=offset)).isoformat() for offset in range(days)]
    cursor = first - timedelta(days=first.isoweekday() - 1)
    end = today - timedelta(days=today.isoweekday() - 1)
    out: list[str] = []
    while cursor <= end:
        out.append(cursor.isoformat())
        cursor += timedelta(days=7)
    return out


def partial_bucket(dimension: str, now: datetime) -> Optional[str]:
    """The bucket that is still accumulating: the current UTC day, or the week
    it belongs to. ``None`` for a category axis, which has no "now"."""
    today = now.astimezone(timezone.utc).date()
    if dimension == "day":
        return today.isoformat()
    if dimension == "week":
        return (today - timedelta(days=today.isoweekday() - 1)).isoformat()
    return None


# ── Assembly ───────────────────────────────────────────────────────────────


def _rate(metric: str, passed: int, failed: int, broken: int) -> Optional[float]:
    """The rate, or ``None`` when nothing was evaluated.

    ``canonical_pass_rate`` is the ONLY place the denominator is decided:
    skipped and unknown are outside it, so a bucket of nothing but skips has
    no rate to report rather than a 0% that reads as total failure.
    """
    evaluated = executed_count(passed, failed, broken)
    if evaluated < MIN_RATE_SAMPLE:
        return None
    if metric == "pass_rate":
        return canonical_pass_rate(passed, failed, broken)
    return round((failed + broken) / evaluated * 100, 2)


_NO_SAMPLE_REASON = (
    "no evaluated executions in this bucket: every test was skipped or had no "
    "verdict, and a rate over nothing is not 0%"
)
_NOT_COMBINABLE = {
    "duration_p50": "a median cannot be combined across groups; ask for these groups directly",
    "duration_p95": "a 95th percentile cannot be combined across groups; ask for these groups directly",
    "unique_tests": "distinct tests cannot be summed across groups without double counting",
    "flaky_tests": "distinct flaky tests cannot be summed across groups without double counting",
    "run_count": "one run can appear in several groups, so runs cannot be summed across them",
}


class _Accumulator:
    """The component counts of one ``(series, bucket)`` cell, mergeable."""

    __slots__ = ("passed", "failed", "broken", "skipped", "unknown",
                 "executions", "runs", "value", "sample", "merged")

    def __init__(self) -> None:
        self.passed = self.failed = self.broken = self.skipped = self.unknown = 0
        self.executions = self.runs = self.sample = 0
        self.value: Optional[float] = None
        self.merged = 0

    def add(self, cell: Cell) -> None:
        self.passed += cell.passed
        self.failed += cell.failed
        self.broken += cell.broken
        self.skipped += cell.skipped
        self.unknown += cell.unknown
        self.executions += cell.executions
        self.runs += cell.runs
        self.sample += cell.sample
        if cell.value is not None:
            self.value = cell.value if self.value is None else self.value + cell.value
        # The cell may ALREADY be a roll-up SQL did, and a percentile that was
        # combined out of three groups is no more answerable for having been
        # combined in the database.
        self.merged += max(cell.merged, 1)


def _point(spec: ChartSpec, acc: Optional[_Accumulator], *, combined: bool) -> dict:
    """One C3 point. ``combined`` marks the "other" bucket, where a
    non-additive metric has no answer to give."""
    metric = METRICS[spec.metric]
    if acc is None:
        # Zero-fill. A count is genuinely 0; a rate and a percentile are not.
        if metric.rate:
            return {"y": None, "n": 0, "measured": False, "reason": _NO_SAMPLE_REASON}
        if not metric.zero_fill:
            return {"y": None, "n": 0, "measured": False,
                    "reason": "no execution in this bucket carries a duration"}
        return {"y": 0, "n": 0, "measured": True, "reason": None}

    if metric.rate:
        value = _rate(spec.metric, acc.passed, acc.failed, acc.broken)
        if value is None:
            return {"y": None, "n": 0, "measured": False, "reason": _NO_SAMPLE_REASON}
        return {"y": value, "n": acc.sample, "measured": True, "reason": None}

    if combined and acc.merged > 1 and not metric.additive:
        return {
            "y": None, "n": acc.sample, "measured": False,
            "reason": _NOT_COMBINABLE.get(spec.metric, "this metric cannot be combined across groups"),
        }
    if acc.value is None:
        return {"y": None, "n": acc.sample, "measured": False,
                "reason": "no execution in this bucket carries this measure"}
    value = acc.value
    if metric.integer:
        value = int(round(value))
    else:
        value = round(value, 2)
    return {"y": value, "n": acc.sample, "measured": True, "reason": None}


def _rank(spec: ChartSpec, acc: _Accumulator) -> int:
    """How big a key is, for top-N and for the cap. A rate ranks by its sample
    size: ranking by the rate itself would put a 100%-of-one series at the top
    of a chart about volume."""
    metric = METRICS[spec.metric]
    if metric.rate or not metric.additive or acc.value is None:
        return acc.sample or acc.executions
    return int(acc.value)


def assemble(
    spec: ChartSpec,
    cells: Iterable[Cell],
    *,
    buckets: Optional[Sequence[str]],
    x_type: str,
    labels: Optional[dict[str, str]] = None,
    counts: Optional[AxisCounts] = None,
) -> dict:
    """The C3 ``series`` payload: zero-filled, top-N'd, capped.

    ``buckets`` is the generated axis for a time dimension; ``None`` means the
    axis is whatever the data showed, ranked.

    ``counts`` carries the FULL key count of each axis when SQL already ranked
    and cut (:func:`build_statement`). Without it the ranking here is the whole
    ranking -- the same rule, the same tie-break -- which is what the unit
    tests drive and what makes the two agree.

    An ``__other__`` key among the cells is one SQL rolled up: it is never
    ranked against the keys it stands for, it is always last, and its points
    are always marked combined.
    """
    cells = list(cells)
    labels = dict(labels or {})
    counts = counts or AxisCounts()

    by_series: dict[str, dict[str, _Accumulator]] = {}
    series_total: dict[str, _Accumulator] = {}
    bucket_total: dict[str, _Accumulator] = {}
    series_labels: dict[str, str] = {}
    bucket_labels: dict[str, str] = {}

    for cell in cells:
        series_labels.setdefault(cell.series, cell.series_label)
        bucket_labels.setdefault(cell.x, cell.x_label)
        by_series.setdefault(cell.series, {}).setdefault(cell.x, _Accumulator()).add(cell)
        series_total.setdefault(cell.series, _Accumulator()).add(cell)
        bucket_total.setdefault(cell.x, _Accumulator()).add(cell)

    # ── the x axis ──
    #
    # ``top_n`` names buckets only when there is no series dimension to rank
    # instead; otherwise the axis is whatever the data showed, capped.
    ranks_buckets = spec.top_n is not None and spec.series_dimension is None
    outside_axis: list[str] = []
    x_total: Optional[int] = None
    if buckets is not None:
        axis = list(buckets)
        other_buckets: list[str] = []
        on_axis = set(axis)
        # A run whose ``created_at`` is in the future buckets past the last day
        # of a generated axis. It used to vanish silently; it is counted and
        # declared instead (``outside_window``).
        outside_axis = sorted(
            key for key in bucket_total if key not in on_axis and key != OTHER_KEY
        )
    else:
        observed = [key for key in bucket_total if key != OTHER_KEY]
        ordered = sorted(observed, key=lambda key: (-_rank(spec, bucket_total[key]), key))
        keep = min(spec.top_n if ranks_buckets and spec.top_n else len(ordered),
                   MAX_POINTS_PER_SERIES)
        axis, other_buckets = ordered[:keep], ordered[keep:]
        x_total = counts.bucket if counts.bucket is not None else len(ordered)
        if ranks_buckets:
            # ``top_n`` asked for these buckets, so the rest are a bucket --
            # whether SQL rolled them up or they are still here to merge.
            if other_buckets or OTHER_KEY in bucket_total:
                axis = axis + [OTHER_KEY]
        else:
            # Nobody asked: the cap bit, and the response says so rather than
            # inventing an "other" the caller did not request.
            other_buckets = []

    kept_buckets = len([key for key in axis if key != OTHER_KEY])
    truncated_x = (
        buckets is None and x_total is not None and x_total > kept_buckets and not ranks_buckets
    )

    # ── the series axis ──
    series_rolled = OTHER_KEY in series_total
    series_count: Optional[int] = None
    if spec.series_dimension is None:
        # One series, always, whenever there is an axis to draw it on. An
        # empty window used to answer ``series: []`` while a window with one
        # matching run answered a zero-filled series -- two shapes for
        # "nothing happened", and a chart that changes shape when the data
        # runs out.
        kept = [SINGLE_SERIES_KEY] if (by_series or buckets is not None) else []
        dropped: list[str] = []
    else:
        observed = [key for key in series_total if key != OTHER_KEY]
        ordered = sorted(observed, key=lambda key: (-_rank(spec, series_total[key]), key))
        limit = spec.top_n if spec.top_n else MAX_SERIES
        if spec.top_n is None and len(ordered) > MAX_SERIES:
            limit = MAX_SERIES - 1
        kept, dropped = ordered[:limit], ordered[limit:]
        series_count = counts.series if counts.series is not None else len(ordered)
        if series_rolled and not dropped:
            kept = kept + [OTHER_KEY]

    kept_series = len([key for key in kept if key != OTHER_KEY])
    truncated_series = (
        series_count is not None and series_count > kept_series and spec.top_n is None
    )

    out_series: list[dict] = []
    for key in kept:
        cells_of = by_series.get(key, {})
        combined_series = key == OTHER_KEY
        points = []
        for x in axis:
            if x == OTHER_KEY and other_buckets:
                acc = _merge(cells_of.get(bucket) for bucket in other_buckets)
            else:
                acc = cells_of.get(x)
            points.append({
                "x": x,
                **_point(spec, acc, combined=combined_series or x == OTHER_KEY),
            })
        out_series.append({
            "key": key,
            "label": "Other" if combined_series else _label(spec, key, series_labels, labels),
            "points": points,
        })

    if dropped:
        merged_cells = {
            x: _merge(by_series.get(key, {}).get(x) for key in dropped)
            for x in axis if x != OTHER_KEY
        }
        points = []
        for x in axis:
            if x == OTHER_KEY and other_buckets:
                acc = _merge(
                    by_series.get(key, {}).get(bucket)
                    for key in dropped for bucket in other_buckets
                )
            else:
                acc = merged_cells.get(x)
            points.append({"x": x, **_point(spec, acc, combined=True)})
        out_series.append({"key": OTHER_KEY, "label": "Other", "points": points})

    truncated = truncated_x or truncated_series
    if len(out_series) > MAX_SERIES:  # pragma: no cover - the caps above hold
        truncated = True
        out_series = out_series[:MAX_SERIES]

    # Per AXIS, because one number cannot honestly answer for both: 400 suite
    # buckets keyed by 12 environments truncates BOTH, and ``truncated_total``
    # used to report 12 while the axis had silently lost 34 buckets.
    truncated_axes: dict[str, dict] = {}
    if truncated_x:
        truncated_axes["x"] = {
            "dimension": spec.bucket_dimension, "kept": kept_buckets, "total": x_total,
        }
    if truncated_series:
        truncated_axes["series"] = {
            "dimension": spec.series_dimension, "kept": kept_series, "total": series_count,
        }
    #: The C2 scalar keeps naming the axis that lost whole buckets first: a
    #: dropped bucket is a hole in every series, a dropped series is one line.
    truncated_total = next(
        (truncated_axes[axis_name]["total"] for axis_name in ("x", "series")
         if axis_name in truncated_axes),
        None,
    )

    # The x key is the NORMALISED bucket (a lower-cased suite, a project id) --
    # what a drill-down sends back. ``x_labels`` is what the axis shows, so a
    # suite chart is not a wall of lower-case and a project chart is not a wall
    # of UUIDs. Only keys whose label differs are sent.
    axis_labels = {
        key: bucket_labels[key]
        for key in axis
        if key in bucket_labels and bucket_labels[key] != key
    }

    outside_window: Optional[dict] = None
    if outside_axis:
        beyond = _merge(bucket_total[key] for key in outside_axis)
        outside_window = {
            "buckets": len(outside_axis),
            "executions": beyond.executions if beyond else 0,
            "first": outside_axis[0],
            "last": outside_axis[-1],
        }

    payload = {
        "kind": "series",
        "dimensions": list(spec.group_by),
        "x_type": x_type,
        "series": out_series,
        # The metric is NOT repeated here: ``meta.definitions.metric`` names it,
        # with its semantics beside it, and one name in two places is one that
        # can disagree.
        "truncated": truncated,
        "truncated_total": truncated_total if truncated else None,
        "truncated_axes": truncated_axes or None,
        "outside_window": outside_window,
    }
    if axis_labels:
        payload["x_labels"] = axis_labels
    return payload


def _merge(accumulators: Iterable[Optional[_Accumulator]]) -> Optional[_Accumulator]:
    out: Optional[_Accumulator] = None
    for acc in accumulators:
        if acc is None:
            continue
        if out is None:
            out = _Accumulator()
        out.passed += acc.passed
        out.failed += acc.failed
        out.broken += acc.broken
        out.skipped += acc.skipped
        out.unknown += acc.unknown
        out.executions += acc.executions
        out.runs += acc.runs
        out.sample += acc.sample
        if acc.value is not None:
            out.value = acc.value if out.value is None else out.value + acc.value
        out.merged += max(acc.merged, 1)
    return out


def _label(spec: ChartSpec, key: str, observed: dict, resolved: dict) -> str:
    if spec.series_dimension is None:
        return spec.metric
    return resolved.get(key) or observed.get(key) or key


# ── Entity labels (project, release) ───────────────────────────────────────


async def _entity_labels(
    db: AsyncSession, dimension: str, keys: Iterable[str], scope: AnalyticsScope
) -> dict[str, str]:
    """Names for the UUID keys of ``project``/``release``, in one lookup.

    Read AFTER the aggregate rather than joined into it: the join would cost
    every request the name columns.

    The ids can only have come out of an aggregate the tenant filter already
    bounded, so this lookup cannot today name a release the caller may not
    read. It carries the project predicate anyway: "the ids are already safe"
    is a property of the CALLER, and one denormalisation -- a release id on a
    row, a second caller, a cached key reused -- turns a name lookup with no
    tenancy of its own into the leak. The ``is_active`` guard is the same one
    ``_tenant_filter`` spells for the aggregate.
    """
    ids = []
    for key in keys:
        try:
            ids.append(uuid.UUID(key))
        except (ValueError, AttributeError, TypeError):
            continue
    if not ids:
        return {}
    from sqlalchemy import bindparam, text

    params: dict[str, Any] = {"label_ids": ids}
    binds: list[Any] = [bindparam("label_ids", expanding=True)]
    if dimension == "project":
        where = ["p.id IN :label_ids", "p.is_active"]
        if scope.project_id is not None:
            params["label_project"] = scope.project_id
            where.append("p.id = :label_project")
        elif scope.allowed_project_ids is not None:
            params["label_projects"] = list(scope.allowed_project_ids)
            binds.append(bindparam("label_projects", expanding=True))
            where.append("p.id IN :label_projects")
        sql = f"SELECT p.id::text AS id, p.name FROM projects p WHERE {' AND '.join(where)}"
    else:
        where = [
            "r.id IN :label_ids",
            "r.project_id IN (SELECT id FROM projects WHERE is_active)",
        ]
        if scope.project_id is not None:
            params["label_project"] = scope.project_id
            where.append("r.project_id = :label_project")
        elif scope.allowed_project_ids is not None:
            params["label_projects"] = list(scope.allowed_project_ids)
            binds.append(bindparam("label_projects", expanding=True))
            where.append("r.project_id IN :label_projects")
        sql = f"SELECT r.id::text AS id, r.name FROM releases r WHERE {' AND '.join(where)}"

    statement = text(sql).bindparams(*binds)
    rows = (await db.execute(statement, params)).fetchall()
    return {row.id: row.name for row in rows}


# ── meta.definitions ───────────────────────────────────────────────────────


def definitions(
    spec: ChartSpec, grain: str, now: datetime, *, grain_changed_by: Optional[str] = None
) -> dict:
    """What the numbers mean, shipped with them.

    A chart is read by someone who was not in the room when the metric was
    chosen. Every semantic decision the story pins down is stated here rather
    than in a wiki page nobody opens.
    """
    out: dict[str, Any] = {
        "metric": spec.metric,
        "dimensions": list(spec.group_by),
        "grain": grain,
        "grain_note": (
            "Counts are run aggregates (test_runs.passed_tests and its "
            "siblings), the same population /metrics/trends and /reports/summary "
            "count."
            if grain == GRAIN_RUN else
            "Counts are individual test executions (test_cases rows joined to "
            "their run). A run whose per-test rows have not landed yet "
            "contributes nothing here."
        ),
        "timezone": "UTC",
        "bucket": (
            "ISO weeks, labelled by their Monday, in UTC. The first week reaches "
            "back to its Monday, so it holds only the part inside the window."
            if spec.bucket_dimension == "week" else
            "UTC calendar days." if spec.bucket_dimension == "day" else
            f"One bucket per distinct {spec.bucket_dimension}."
        ),
        "n": (
            "The sample behind y: the rate's denominator for a rate, the "
            "executions carrying a duration for a duration metric, otherwise "
            "the executions in the bucket."
        ),
        "zero_fill": (
            "Every bucket in the window is present. A count with no data is 0; "
            "a rate or a percentile with no data is null with measured:false, "
            "never 0."
        ),
        "in_progress": (
            "In-progress runs are included, as everywhere else in the product; "
            "meta.includes_in_progress counts them and meta.partial_day names "
            "the UTC day that is still accumulating."
        ),
        "partial_bucket": partial_bucket(spec.bucket_dimension, now),
        "no_value_bucket": (
            f"A row with no value for a dimension buckets as '{NO_VALUE}'. An "
            f"ingested value that is literally '{NO_VALUE}' -- or, for "
            "failure_category, literally 'Unknown' -- merges into that bucket "
            "and is not distinguishable from an absent one."
        ),
    }
    if DIMENSIONS[spec.bucket_dimension].time:
        out["outside_window"] = (
            "A run whose created_at is after the current UTC day falls outside "
            "the generated axis. It is not drawn; meta.outside_window counts "
            "what it held, so a clock-skewed run is missing loudly rather than "
            "quietly."
        )
    if grain_changed_by:
        out["grain_changed_by"] = grain_changed_by
        out["grain_changed_note"] = (
            f"The {grain_changed_by} filter moved this metric from run "
            "aggregates to execution rows: a run-level filter keeps the run "
            "whole, so its other suites' tests would be counted into a chart "
            "that says it is filtered. The number answers a different "
            "population than the same chart without the filter."
        )
    if spec.top_n is not None or spec.series_dimension is not None or (
        not DIMENSIONS[spec.bucket_dimension].time
    ):
        out["ranking"] = (
            "Keys are ranked by the metric's own total; a rate, a percentile "
            "and a distinct count are ranked by their sample size instead, "
            "because ranking a rate by its value puts a 100%-of-one series at "
            "the top of a chart about volume. Ties are broken by the key "
            "ascending, so the same data always gives the same chart. The "
            "ranking, the cut and the 'other' roll-up all happen in SQL."
        )
    if spec.top_n is not None:
        out["top_n"] = (
            f"The {spec.top_n} largest keys are kept and the rest merged into "
            f"'{OTHER_KEY}'. 'other' for a rate is RECOMPUTED from the merged "
            "counts, never averaged; for a percentile or a distinct count, "
            "which cannot be combined at all, it is measured:false."
        )
    if spec.metric in ("pass_rate", "failure_rate"):
        out[spec.metric] = (
            "passed / (passed + failed + broken) x 100"
            if spec.metric == "pass_rate" else
            "(failed + broken) / (passed + failed + broken) x 100"
        ) + (
            ". skipped and unknown are outside the denominator (app/core/pass_rate.py): "
            "a bucket that is entirely skipped is measured:false, not 0%. "
            "Quarantine is a property of a test, not of a verdict, and does not "
            "change this rate."
        )
    if spec.metric == "flaky_tests":
        out["flaky_tests"] = (
            "Distinct test fingerprints with at least one execution flagged "
            "is_flaky_run inside this scope. It is NOT FlakyScore, which is a "
            "project-level statistic over all history and is never bucketed by "
            "day."
        )
    if spec.metric == "retried_tests":
        out["retried_tests"] = (
            "Executions whose retry_count is above zero. Retries collapse to "
            "one row per test per run, so this counts tests that were retried, "
            "not attempts."
        )
    if spec.metric == "unique_tests":
        out["unique_tests"] = "Distinct test fingerprints that ran in the bucket."
    if spec.metric.startswith("duration"):
        out[spec.metric] = (
            "Milliseconds, over individual test executions. Executions with no "
            "recorded duration are excluded and are not counted in n."
        ) + (
            " percentile_cont on live SQL." if spec.metric != "duration_total" else ""
        )
    if "branch" in spec.group_by:
        out["branch"] = (
            f"The run's branch exactly as it was recorded. It is often absent "
            f"for uploads; those runs bucket as '{NO_VALUE}'."
        )
    if "environment" in spec.group_by:
        out["environment"] = (
            "The run's environment, lower-cased (it is lower-cased on write; "
            f"rows written before that are folded here). Absent is '{NO_VALUE}'."
        )
    if "suite" in spec.group_by:
        out["suite"] = (
            "The effective suite: a live-stream run's own label, otherwise the "
            "row's suite. Matched case-insensitively, as every suite filter in "
            "the product is; the label keeps the spelling that was ingested."
        )
    if "status" in spec.group_by:
        out["status"] = (
            "The TestStatus vocabulary, lower-cased for the chart contract: "
            "passed, failed, broken, skipped, unknown."
        )
    return out


# ── The VIZ-209 seam ───────────────────────────────────────────────────────


def cache_identity_parts(scope: AnalyticsScope, spec: ChartSpec) -> tuple[str, ...]:
    """The canonical identity of one chart request, for VIZ-209's cache key.

    Order-insensitive and normalised: two requests that mean the same thing
    produce the same tuple, so the cache cannot hold two entries for one
    answer. It deliberately does NOT include the epoch, the caller's project
    set or the schema version -- VIZ-209 owns those and composes them with this.
    """
    return (
        f"metric={spec.metric}",
        "group_by=" + ",".join(spec.group_by),
        f"top_n={spec.top_n if spec.top_n is not None else ''}",
        f"project={scope.project or ''}",
        f"release={cache_identity(scope.release_ids) or ''}",
        f"suite={cache_identity(suite_keys(scope.suite_names)) or ''}",
        f"days={scope.days if scope.days is not None else ''}",
    )


# ── The entry point ────────────────────────────────────────────────────────


async def build_chart_data(
    db: AsyncSession,
    scope: AnalyticsScope,
    spec: ChartSpec,
    *,
    now: Optional[datetime] = None,
) -> dict:
    """The C3 payload for ``spec`` under ``scope``, plus its ``definitions``.

    The caller lifts :data:`ENVELOPE_KEYS` and ``definitions`` into the C2
    envelope; everything else is the chart.

    ``now`` is the request's ONE clock. The route takes it, hands it here and
    hands the same instant to ``window_start`` for the envelope, so the window
    the SQL bounded, the axis that was generated and the ``as_of`` the reader
    is shown cannot straddle UTC midnight and disagree.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    grain = grain_for(spec, scope)

    if scope.denied:
        cells: list[Cell] = []
        counts = AxisCounts()
    else:
        cells, counts = await _fetch_cells(db, spec, scope, now=now)

    buckets = (
        time_buckets(spec.bucket_dimension, now, days=scope.window_days)
        if DIMENSIONS[spec.bucket_dimension].time
        else None
    )

    labels: dict[str, str] = {}
    series_dim = spec.series_dimension
    if series_dim and DIMENSIONS[series_dim].entity:
        labels = await _entity_labels(
            db, series_dim, {cell.series for cell in cells}, scope
        )

    payload = assemble(
        spec, cells, buckets=buckets, x_type=spec.x_type, labels=labels, counts=counts
    )

    bucket_dim = DIMENSIONS[spec.bucket_dimension]
    if bucket_dim.entity and payload["series"]:
        on_axis = {point["x"] for point in payload["series"][0]["points"]}
        names = await _entity_labels(db, spec.bucket_dimension, on_axis, scope)
        if names:
            payload["x_labels"] = {**payload.get("x_labels", {}), **names}

    payload["definitions"] = definitions(
        spec, grain, now, grain_changed_by=grain_forced_by(spec, scope)
    )
    logger.info(
        "chart_data_built",
        metric=spec.metric,
        group_by=",".join(spec.group_by),
        grain=grain,
        series=len(payload["series"]),
        cells=len(cells),
        truncated=payload["truncated"],
    )
    return payload


#: What the route lifts out of the chart payload and into the C2 envelope.
#: Named once so the handler, the contract fixture test and any other reader
#: cannot drift over which keys are the chart and which are the envelope.
ENVELOPE_KEYS = ("truncated", "truncated_total", "truncated_axes", "outside_window")


__all__ = [
    "AxisCounts",
    "Cell",
    "ChartSpec",
    "DIMENSIONS",
    "ENVELOPE_KEYS",
    "GRAIN_ROW",
    "GRAIN_RUN",
    "MAX_GROUPS",
    "METRICS",
    "MAX_TOP_N_SERIES",
    "MIN_RATE_SAMPLE",
    "NO_VALUE",
    "OTHER_KEY",
    "SINGLE_SERIES_KEY",
    "assemble",
    "build_chart_data",
    "build_statement",
    "cache_identity_parts",
    "definitions",
    "grain_for",
    "grain_forced_by",
    "parse_chart_spec",
    "partial_bucket",
    "request_clock",
    "time_buckets",
    "window_start",
]
