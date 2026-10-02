"""VIZ-205 -- ``GET /api/v1/analytics/heatmap``: one guarded matrix endpoint.

A heatmap is a question about TWO things at once ("which suite, on which
day?"), which ``/analytics/chart-data`` cannot answer: its series cap is 8 and
a heatmap has up to 60 rows. This module answers four of them, each a
``kind``, and returns contract C3 ``matrix`` plus the C2 envelope:

==================== ============================== ===============================
kind                 columns (x)                    rows (y)
==================== ============================== ===============================
``suite_day``        every UTC day of the window    effective suites
``suite_environment`` environments (<= 20)          effective suites
``suite_release``    releases (<= 20) + unattributed effective suites
``test_run``         the last ``runs`` runs          tests that failed in the window
==================== ============================== ===============================

**Nothing from the request is interpolated.** ``kind`` is looked up in
:data:`KINDS`; every fragment that reaches the statement is hard-coded here or
comes from the shared builders (``analytics_scope`` for suites and releases,
``_tenant_filter`` for the project rule, ``chart_data_service.DIMENSIONS`` for
the day, suite and environment expressions). Every value -- the window, the
row and column caps, the filters -- is a bind. An unknown kind is a 422 that
lists the allow-list and never echoes what was sent.

**One meaning for every rate cell.** The value is the PASS rate in percentage
points, computed in Python from the five status counts through
:mod:`app.core.pass_rate`: skipped and unknown are outside the denominator, so
a cell whose executions were all skipped is ``null`` (with ``n > 0``: it ran,
but nothing was evaluated), and a cell with no executions is ``null`` with
``n: 0``. Neither is ever ``0``, which would read as "everything failed". The
EPIC gave ``suite_environment`` a failure rate; the plan's OD-8 uses the pass
rate for all three rate kinds so the matrix has one ramp and one meaning
("low is the concern"), and the failure rate is ``100 - value`` over the same
denominator. ``unit: "percent"`` is sent explicitly (OD-7).

**The same numbers as chart-data.** The day bucket, the effective suite and
the environment are ``chart_data_service.DIMENSIONS``' own fragments, read
over the same population (``test_cases`` joined to the run, the run's
``created_at`` as the clock, the same tenant/release/suite fragments), so a
``suite_day`` cell's rate equals ``chart-data?metric=pass_rate&group_by=day&
group_by=suite`` for the same scope. ``n`` here is every execution in the cell
(the contract's ``counts`` must add up to it); chart-data's ``n`` for a rate is
the evaluated subset, ``counts.passed + counts.failed + counts.broken`` here.

**Caps, not "Other".** At most :data:`MAX_ROWS` rows (default
:data:`DEFAULT_ROWS`), ranked by ``failed + broken`` descending, then by
executions, then by key. Rows beyond the cap are dropped and COUNTED
(``meta.truncated``, ``truncated_total``, ``truncated_axes.series``): a pass
rate over 140 unrelated suites rolled into one row means nothing. Columns
that do not fit (environments and releases beyond :data:`MAX_COLUMNS`, runs
beyond ``runs``) are counted the same way on ``truncated_axes.x``. The
ranking and both cuts happen in SQL, so a project with thousands of suites
returns at most a full grid.

Caching, ETags, the statement timeout and the rate limit are the VIZ-209
layer's (``@analytics_read`` on the route); :func:`cache_identity_parts` is the
canonical identity it keys on.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

import structlog
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import AnalyticsQueryError
from app.core.pass_rate import canonical_pass_rate, executed_count
from app.core.release_filter import UNATTRIBUTED
from app.models.postgres import TestStatus
from app.models.viz_contracts import MAX_MATRIX_CELLS, STATUS_VOCAB
from app.services.analytics_scope import (
    AnalyticsScope,
    ScopePolicy,
    cache_identity,
    release_filter_sql,
    scoped_text,
    suite_filter_sql,
    suite_keys,
)
# The tenant rule, spelled once for every raw-SQL analytics query (the pin,
# the membership set, the fail-closed empty set, and ``is_active``).
from app.services.analytics_service import _tenant_filter as tenant_filter_sql
from app.services.chart_data_service import DIMENSIONS as CHART_DIMENSIONS
from app.services.chart_data_service import (
    request_clock,
    time_buckets,
    window_start,
)

logger = structlog.get_logger(__name__)

KIND_SUITE_DAY = "suite_day"
KIND_TEST_RUN = "test_run"
KIND_SUITE_ENVIRONMENT = "suite_environment"
KIND_SUITE_RELEASE = "suite_release"
KINDS: tuple[str, ...] = (
    KIND_SUITE_DAY, KIND_TEST_RUN, KIND_SUITE_ENVIRONMENT, KIND_SUITE_RELEASE,
)
#: The kinds whose cells are a pass rate (the others are a status).
RATE_KINDS = frozenset({KIND_SUITE_DAY, KIND_SUITE_ENVIRONMENT, KIND_SUITE_RELEASE})
#: A release belongs to one project and runs of several projects are not one
#: sequence, so these two answer for exactly one project.
PROJECT_KINDS = frozenset({KIND_TEST_RUN, KIND_SUITE_RELEASE})

DEFAULT_ROWS = 40
#: The EPIC's row cap. 60 rows x 90 columns is the C3 cell cap exactly.
MAX_ROWS = 60
DEFAULT_RUNS = 30
#: ``test_run`` columns ARE runs, and 90 columns x 60 rows is the cell cap.
MAX_RUNS = 90
#: ``suite_day`` columns ARE days: more than 90 would break the cell cap, so a
#: longer window is refused for that kind rather than silently cut.
SUITE_DAY_MAX_DAYS = 90
#: Environments and releases shown as columns. 20 is the C1 release cap, so a
#: caller who names their releases always gets every one of them.
MAX_COLUMNS = 20
#: The last line of defence on what one statement may return (rows, columns
#: and cells together). The ranking CTEs bound every part already; a future
#: run (clock-skewed agent) adds at most one day column per distinct day.
HARD_ROW_CAP = 50_000

#: The scope this route reads. 365 days for direct callers; the UI clamps to
#: 90 (``catalogueParams``), and ``suite_day`` refuses more than 90 itself.
HEATMAP_SCOPE = ScopePolicy(default_days=30, max_days=365)

#: The column label of runs no release claims (the key is ``unattributed``).
UNATTRIBUTED_LABEL = "(unattributed)"

#: Bump when the PAYLOAD changes shape or meaning: an entry cached before must
#: never be served after.
HEATMAP_SHAPE_VERSION = 1

#: The axis names ``meta.truncated_axes`` uses. Matrix rows map to ``series``
#: and columns to ``x``, so a reader of chart-data's envelope reads this one.
_ROW_DIMENSION = {
    KIND_SUITE_DAY: "suite", KIND_SUITE_ENVIRONMENT: "suite",
    KIND_SUITE_RELEASE: "suite", KIND_TEST_RUN: "test",
}
_COLUMN_DIMENSION = {
    KIND_SUITE_DAY: "day", KIND_SUITE_ENVIRONMENT: "environment",
    KIND_SUITE_RELEASE: "release", KIND_TEST_RUN: "run",
}


# ── The parsed request ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class HeatmapSpec:
    """A validated request: nothing here came from the caller as text."""

    kind: str
    rows: int
    #: ``test_run`` only; ``None`` for every other kind.
    runs: Optional[int] = None

    @property
    def is_rate(self) -> bool:
        return self.kind in RATE_KINDS


def _bounded(param: str, value: Any, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise AnalyticsQueryError(
            f"{param}_range",
            f"{param} is an integer 1-{maximum}",
            param=param,
            allowed={"min": 1, "max": maximum},
        )
    return int(value)


def parse_heatmap_spec(
    kind: Any, rows: Any, runs: Any, *, scope: AnalyticsScope
) -> HeatmapSpec:
    """Validate the heatmap half of the request, before any database access.

    Every refusal is a C1/VIZ-210 body with the allow-list in ``allowed``. The
    value that was sent is NEVER echoed: it is untrusted text that would be
    rendered straight into a toast.
    """
    if kind is None:
        raise AnalyticsQueryError(
            "missing_parameter",
            "kind is required: name the matrix to draw",
            param="kind",
            allowed=sorted(KINDS),
        )
    if not isinstance(kind, str) or kind not in KINDS:
        raise AnalyticsQueryError(
            "kind_enum",
            "kind is not one of the values this endpoint supports",
            param="kind",
            allowed=sorted(KINDS),
        )
    parsed_rows = _bounded("rows", rows, default=DEFAULT_ROWS, maximum=MAX_ROWS)
    parsed_runs: Optional[int] = None
    if kind == KIND_TEST_RUN:
        parsed_runs = _bounded("runs", runs, default=DEFAULT_RUNS, maximum=MAX_RUNS)
    elif runs is not None:
        # Declared, so it is never silently ignored: only a test x run matrix
        # has runs for columns.
        raise AnalyticsQueryError(
            "runs_unsupported",
            "runs sets the columns of kind=test_run only",
            param="runs",
            allowed={"requires": {"kind": KIND_TEST_RUN}},
        )

    if kind == KIND_SUITE_DAY and scope.window_days > SUITE_DAY_MAX_DAYS:
        raise AnalyticsQueryError(
            "window_cap",
            f"kind=suite_day draws one column per day, so its window is at most "
            f"{SUITE_DAY_MAX_DAYS} days",
            param="days",
            allowed={"min": 1, "max": SUITE_DAY_MAX_DAYS},
        )
    if kind in PROJECT_KINDS and scope.project_id is None:
        raise AnalyticsQueryError(
            "project_required",
            "this kind answers for one project: a release belongs to one "
            "project, and runs of several projects are not one sequence",
            param="project_id",
            allowed="UUID",
        )
    return HeatmapSpec(kind=kind, rows=parsed_rows, runs=parsed_runs)


# ── The statement ──────────────────────────────────────────────────────────


def _status_count(status: TestStatus) -> str:
    return f"COUNT(*) FILTER (WHERE tc.status = '{status.value}')"


_COUNTS = {
    "passed": _status_count(TestStatus.PASSED),
    "failed": _status_count(TestStatus.FAILED),
    "broken": _status_count(TestStatus.BROKEN),
    "skipped": _status_count(TestStatus.SKIPPED),
    "unknown": _status_count(TestStatus.UNKNOWN),
}
_COUNT_NAMES = tuple(_COUNTS)
_FAILING = f"('{TestStatus.FAILED.value}', '{TestStatus.BROKEN.value}')"

_TEXT = "CAST(NULL AS TEXT)"
_BIGINT = "CAST(NULL AS BIGINT)"

#: The x/y fragments of the three grouped kinds. ``x_label`` is ``None`` when
#: the key IS the label (a day) or the label is looked up afterwards (a
#: release's name).
_SUITE = CHART_DIMENSIONS["suite"]
_GROUPED_X = {
    KIND_SUITE_DAY: (CHART_DIMENSIONS["day"].sql, None),
    KIND_SUITE_ENVIRONMENT: (
        CHART_DIMENSIONS["environment"].sql, CHART_DIMENSIONS["environment"].label_sql,
    ),
    KIND_SUITE_RELEASE: (CHART_DIMENSIONS["release"].sql, None),
}


def _scope_filters(params: dict, scope: AnalyticsScope) -> str:
    return " ".join((
        tenant_filter_sql(
            params, project_id=scope.project, allowed_project_ids=scope.allowed_project_ids,
        ),
        release_filter_sql(params, scope.release_arg),
    ))


def _part(part: str, *, key: str, label: str, x_key: str, rank: str, total: str,
          counts: Optional[Sequence[str]] = None, status: str = _TEXT) -> str:
    """One arm of the result UNION: every arm has the same typed columns."""
    count_cols = counts or [_BIGINT] * (len(_COUNT_NAMES) + 1)
    names = list(_COUNT_NAMES) + ["n"]
    return (
        f"SELECT '{part}' AS part, {key} AS key, {label} AS label, {x_key} AS x_key, "
        f"{rank} AS rank, {total} AS total, "
        + ", ".join(f"{expr} AS {name}" for expr, name in zip(count_cols, names))
        + f", {status} AS status"
    )


def _grouped_statement(spec: HeatmapSpec, scope: AnalyticsScope, params: dict) -> str:
    x_sql, x_label_sql = _GROUPED_X[spec.kind]
    filters = _scope_filters(params, scope)
    suites = suite_filter_sql(params, scope.suite_arg)
    params["heatmap_rows"] = spec.rows

    counts = ", ".join(f"{expr} AS {name}" for name, expr in _COUNTS.items())
    x_label = x_label_sql or _TEXT
    grouped = f"""
        SELECT
            {x_sql} AS x_key,
            {x_label} AS x_label,
            {_SUITE.sql} AS y_key,
            {_SUITE.label_sql} AS y_label,
            {counts},
            COUNT(*) AS n
        FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id
        WHERE tr.created_at >= :period_start
          {filters}
          {suites}
        GROUP BY {x_sql}, {_SUITE.sql}"""

    ctes = [
        f"grouped AS ({grouped}\n        )",
        # Worst first: the most failures, then the most executions, then the
        # key by code point (COLLATE "C", as the coverage map and failure
        # groups rank), so the same data draws the same rows on every server
        # whatever its collation.
        """y_totals AS (
            SELECT y_key, MIN(y_label) AS y_label,
                   SUM(failed + broken) AS fails, SUM(n) AS n
            FROM grouped GROUP BY y_key
        ), y_ranked AS (
            SELECT y_key, y_label,
                   ROW_NUMBER() OVER (ORDER BY fails DESC, n DESC, y_key COLLATE "C" ASC) AS rn,
                   COUNT(*) OVER () AS total
            FROM y_totals
        ), y_kept AS (
            SELECT y_key, y_label, rn, total FROM y_ranked WHERE rn <= :heatmap_rows
        )""",
    ]
    arms = [_part("row", key="y_key", label="y_label", x_key=_TEXT, rank="rn", total="total")
            + " FROM y_kept"]
    cell_counts = [f"g.{name}" for name in _COUNT_NAMES] + ["g.n"]
    cell = _part("cell", key="g.y_key", label=_TEXT, x_key="g.x_key", rank=_BIGINT,
                 total=_BIGINT, counts=cell_counts)

    if spec.kind == KIND_SUITE_DAY:
        # The day axis is generated, not observed: nothing to rank or cut.
        arms.append(f"{cell} FROM grouped g JOIN y_kept yk ON yk.y_key = g.y_key")
    else:
        params["heatmap_columns"] = MAX_COLUMNS
        if spec.kind == KIND_SUITE_ENVIRONMENT:
            # The busiest environments, then the key by code point.
            x_ranked = """x_ranked AS (
                SELECT x_key, x_label,
                       ROW_NUMBER() OVER (ORDER BY n DESC, x_key COLLATE "C" ASC) AS rn,
                       COUNT(*) OVER () AS total
                FROM x_totals
            ), x_kept AS (
                SELECT x_key, x_label, rn, total FROM x_ranked WHERE rn <= :heatmap_columns
            )"""
        else:
            # The most recent releases by sort key (a version order, not a
            # name order: 2.10.0 follows 2.9.0), then created_at, then id.
            # "unattributed" joins no release row, so its NULL sort key ranks
            # it after every release (it never displaces one) and the OR keeps
            # it whatever its rank. The release row carries the tenant
            # predicate too: the ids came out of a tenant-bounded aggregate,
            # but "already safe" is a property of this caller, not of the
            # lookup.
            params["heatmap_unattributed"] = UNATTRIBUTED
            release_tenant = tenant_filter_sql(
                params, project_id=scope.project,
                allowed_project_ids=scope.allowed_project_ids, table_alias="r",
            )
            x_ranked = f"""x_ranked AS (
                SELECT xt.x_key, xt.x_label,
                       ROW_NUMBER() OVER (
                           ORDER BY r.sort_key DESC NULLS LAST,
                                    r.created_at DESC NULLS LAST,
                                    xt.x_key DESC
                       ) AS rn,
                       COUNT(*) OVER () AS total
                FROM x_totals xt
                LEFT JOIN releases r
                  ON r.id = CAST(NULLIF(xt.x_key, :heatmap_unattributed) AS UUID)
                 {release_tenant}
            ), x_kept AS (
                SELECT x_key, x_label, rn, total FROM x_ranked
                WHERE x_key = :heatmap_unattributed OR rn <= :heatmap_columns
            )"""
        ctes.append(
            "x_totals AS (\n"
            "            SELECT x_key, MIN(x_label) AS x_label, SUM(n) AS n\n"
            "            FROM grouped GROUP BY x_key\n"
            f"        ), {x_ranked}"
        )
        arms.append(
            _part("col", key="x_key", label="x_label", x_key=_TEXT, rank="rn", total="total")
            + " FROM x_kept"
        )
        arms.append(
            f"{cell} FROM grouped g JOIN y_kept yk ON yk.y_key = g.y_key "
            "JOIN x_kept xk ON xk.x_key = g.x_key"
        )
    return f"WITH {', '.join(ctes)}\n" + "\nUNION ALL\n".join(arms)


def _test_run_statement(spec: HeatmapSpec, scope: AnalyticsScope, params: dict) -> str:
    filters = _scope_filters(params, scope)
    suites = suite_filter_sql(params, scope.suite_arg)
    params["heatmap_rows"] = spec.rows
    params["heatmap_runs"] = spec.runs
    # A run is a column when it has a row in the requested suites -- the SAME
    # effective-suite fragment the cells use, so no column is empty because
    # the run only carried the suite in its label.
    run_in_suite = (
        f"AND EXISTS (SELECT 1 FROM test_cases tc WHERE tc.test_run_id = tr.id {suites})"
        if suites else ""
    )
    # ``failing`` is driven FROM the window's runs, one index probe per run on
    # ix_test_cases_run_status (test_run_id, status). Left to itself
    # the planner reads ix_test_cases_status_only instead: every
    # failing row in the TABLE (every project, all history) and only
    # then the window join -- 21,010 random heap pages on the 1M seed
    # whatever the window (cold: 674 ms with the pages in the OS cache,
    # 1.76 s in the plan's prototype). Per run, the pages read are that
    # run's failing rows, which sit together (a run's rows are written
    # together): 1,578 pages at 30 days, 5,002 at 90 (cold: 94 ms). The
    # bound is the window's failing rows, not the table's history.
    # OFFSET 0 keeps the subquery from being pulled up into a plain
    # join, which is what lets the planner pick the table-wide scan again.
    row_arm = _part("row", key="y_key", label="y_label", x_key=_TEXT, rank="rn", total="total")
    col_arm = _part(
        "col", key="lr.id::text", label="lr.build_number", x_key=_TEXT, rank="lr.rn",
        total="(SELECT total FROM run_total)",
    )
    cell_arm = _part(
        "cell", key="tc.test_fingerprint", label=_TEXT, x_key="tc.test_run_id::text",
        rank=_BIGINT, total=_BIGINT, status="LOWER(tc.status)",
    )
    return f"""
        WITH last_runs AS (
            SELECT tr.id, tr.build_number,
                   ROW_NUMBER() OVER (ORDER BY tr.created_at DESC, tr.id DESC) AS rn
            FROM test_runs tr
            WHERE tr.created_at >= :period_start
              {filters}
              {run_in_suite}
            ORDER BY tr.created_at DESC, tr.id DESC
            LIMIT :heatmap_runs
        ), run_total AS (
            SELECT COUNT(*) AS total
            FROM test_runs tr
            WHERE tr.created_at >= :period_start
              {filters}
              {run_in_suite}
        ), failing AS (
            SELECT f.y_key, MIN(f.y_label) AS y_label, COUNT(*) AS fails
            FROM test_runs tr
            CROSS JOIN LATERAL (
                SELECT tc.test_fingerprint AS y_key, tc.test_name AS y_label
                FROM test_cases tc
                WHERE tc.test_run_id = tr.id
                  AND tc.status IN {_FAILING}
                  {suites}
                OFFSET 0
            ) f
            WHERE tr.created_at >= :period_start
              {filters}
            GROUP BY f.y_key
        ), y_ranked AS (
            SELECT y_key, y_label,
                   ROW_NUMBER() OVER (ORDER BY fails DESC, y_key COLLATE "C" ASC) AS rn,
                   COUNT(*) OVER () AS total
            FROM failing
        ), y_kept AS (
            SELECT y_key, y_label, rn, total FROM y_ranked WHERE rn <= :heatmap_rows
        )
        {row_arm}
        FROM y_kept
        UNION ALL
        {col_arm}
        FROM last_runs lr
        UNION ALL
        {cell_arm}
        FROM last_runs lr
        JOIN test_runs tr ON tr.id = lr.id
        JOIN test_cases tc ON tc.test_run_id = lr.id
        JOIN y_kept yk ON yk.y_key = tc.test_fingerprint
        WHERE TRUE {suites}
    """


def build_statement(
    spec: HeatmapSpec, scope: AnalyticsScope, *, now: Optional[datetime] = None
) -> tuple[str, dict]:
    """``(sql, params)`` for one heatmap. Exposed so the injection tests can
    read the text without a database.

    ONE statement, one snapshot: the kept rows, the kept columns and the cells
    come back as one result tagged by ``part`` (``row``, ``col``, ``cell``), so
    the row ranking, the column cut and the cells cannot disagree because
    ingestion committed between two statements. ``LIMIT :heatmap_row_cap`` is
    the hard ceiling under all of it.
    """
    params: dict[str, Any] = {"period_start": window_start(scope.window_days, now=now)}
    if spec.kind == KIND_TEST_RUN:
        body = _test_run_statement(spec, scope, params)
    else:
        body = _grouped_statement(spec, scope, params)
    params["heatmap_row_cap"] = HARD_ROW_CAP
    return f"SELECT * FROM (\n{body}\n) AS heatmap_parts\nLIMIT :heatmap_row_cap", params


# ── What SQL returned ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class AxisEntry:
    """One kept row or column, with its rank (1 = first kept)."""

    key: str
    label: str
    rank: int


@dataclass(frozen=True)
class CellCounts:
    """One ``(row, column)`` cell as SQL returned it."""

    y: str
    x: str
    passed: int = 0
    failed: int = 0
    broken: int = 0
    skipped: int = 0
    unknown: int = 0
    n: int = 0
    #: ``test_run`` only: the execution's status, lower-cased.
    status: Optional[str] = None


@dataclass
class Fetched:
    rows: list[AxisEntry] = field(default_factory=list)
    columns: list[AxisEntry] = field(default_factory=list)
    cells: list[CellCounts] = field(default_factory=list)
    #: How many distinct row keys existed before the cut; ``None`` = unknown
    #: (no row came back, so nothing was cut).
    row_total: Optional[int] = None
    column_total: Optional[int] = None


def read_parts(result_rows: Iterable[Any]) -> Fetched:
    """Split the tagged result into rows, columns and cells."""
    out = Fetched()
    for row in result_rows:
        if row.part == "row":
            out.rows.append(AxisEntry(str(row.key), _text(row.label, row.key), int(row.rank)))
            out.row_total = int(row.total)
        elif row.part == "col":
            out.columns.append(AxisEntry(str(row.key), _text(row.label, row.key), int(row.rank)))
            out.column_total = int(row.total)
        else:
            out.cells.append(CellCounts(
                y=str(row.key),
                x=str(row.x_key),
                passed=int(row.passed or 0),
                failed=int(row.failed or 0),
                broken=int(row.broken or 0),
                skipped=int(row.skipped or 0),
                unknown=int(row.unknown or 0),
                n=int(row.n or 0),
                status=None if row.status is None else str(row.status),
            ))
    out.rows.sort(key=lambda entry: entry.rank)
    return out


def _text(label: Any, key: Any) -> str:
    return str(label) if label is not None else str(key)


# ── Columns ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReleaseInfo:
    """What orders a release column and names it."""

    key: str
    name: Optional[str]
    sort_key: Optional[str]
    created_at: Optional[datetime]


def order_release_columns(infos: Iterable[ReleaseInfo]) -> list[tuple[str, str]]:
    """``(key, label)`` per release column, oldest version first, unattributed last.

    The SQL twin is the ``x_ranked`` window in :func:`build_statement`, which
    picks the MOST recent by the same three keys descending (``NULLS LAST``),
    so this ascending order is exactly its reverse: a NULL sort key is the
    least recent and comes first.
    """
    infos = list(infos)
    releases = [info for info in infos if info.key != UNATTRIBUTED]
    releases.sort(key=lambda info: (
        info.sort_key is not None,
        info.sort_key or "",
        info.created_at is not None,
        info.created_at or datetime.min.replace(tzinfo=timezone.utc),
        info.key,
    ))
    columns = [(info.key, info.name if info.name is not None else info.key) for info in releases]
    if any(info.key == UNATTRIBUTED for info in infos):
        columns.append((UNATTRIBUTED, UNATTRIBUTED_LABEL))
    return columns


async def _release_infos(
    db: AsyncSession, keys: Sequence[str], scope: AnalyticsScope
) -> list[ReleaseInfo]:
    """Names and sort keys for the release columns, in one tenant-bounded lookup.

    A key that is not a UUID (``unattributed``) is not looked up; a release the
    lookup cannot see keeps its id as its label rather than vanishing.
    """
    ids: list[uuid.UUID] = []
    for key in keys:
        try:
            ids.append(uuid.UUID(key))
        except (ValueError, AttributeError, TypeError):
            continue
    found: dict[str, ReleaseInfo] = {}
    if ids:
        params: dict[str, Any] = {"heatmap_release_ids": ids}
        tenant = tenant_filter_sql(
            params, project_id=scope.project,
            allowed_project_ids=scope.allowed_project_ids, table_alias="r",
        )
        statement = text(
            "SELECT r.id::text AS id, r.name, r.sort_key, r.created_at "
            f"FROM releases r WHERE r.id IN :heatmap_release_ids {tenant}"
        ).bindparams(bindparam("heatmap_release_ids", expanding=True))
        for row in (await db.execute(statement, params)).fetchall():
            found[str(row.id)] = ReleaseInfo(str(row.id), row.name, row.sort_key, row.created_at)
    return [found.get(key) or ReleaseInfo(key, None, None, None) for key in keys]


async def _columns(
    db: AsyncSession,
    spec: HeatmapSpec,
    scope: AnalyticsScope,
    fetched: Fetched,
    now: datetime,
) -> tuple[list[tuple[str, str]], Optional[int]]:
    """The ordered ``(key, label)`` columns and, when a cap cut them, the full
    count of columns there were."""
    if spec.kind == KIND_SUITE_DAY:
        days = time_buckets("day", now, days=scope.window_days)
        return [(day, day) for day in days], None
    if spec.kind == KIND_TEST_RUN:
        # SQL ranks newest first; the matrix reads left to right in time.
        runs = sorted(fetched.columns, key=lambda entry: -entry.rank)
        return [(entry.key, entry.label) for entry in runs], fetched.column_total
    if spec.kind == KIND_SUITE_ENVIRONMENT:
        envs = sorted(fetched.columns, key=lambda entry: entry.rank)
        return [(entry.key, entry.label) for entry in envs], fetched.column_total
    # suite_release: the releases the caller named (every one of them, a
    # release with no run in the window included: its column is null), else
    # the most recent ones that ran.
    if scope.release_ids:
        keys = list(scope.release_ids)
        total: Optional[int] = None
    else:
        keys = [entry.key for entry in fetched.columns]
        total = fetched.column_total
    infos = await _release_infos(db, keys, scope)
    return order_release_columns(infos), total


# ── Assembly ───────────────────────────────────────────────────────────────


def _rate(passed: int, failed: int, broken: int) -> Optional[float]:
    """The pass rate in percentage points, or ``None`` when nothing was
    evaluated. ``canonical_pass_rate`` decides the denominator."""
    if executed_count(passed, failed, broken) < 1:
        return None
    return canonical_pass_rate(passed, failed, broken)


def _truncated_axis(dimension: str, kept: int, total: Optional[int]) -> Optional[dict]:
    if total is None or total <= kept:
        return None
    return {"dimension": dimension, "kept": kept, "total": total}


def assemble(
    spec: HeatmapSpec,
    fetched: Fetched,
    *,
    columns: Sequence[tuple[str, str]],
    column_total: Optional[int] = None,
) -> dict:
    """The C3 ``matrix`` payload (plus :data:`ENVELOPE_KEYS`): a full grid.

    Every ``(row, column)`` pair is a cell. A pair with no execution is
    ``value: null, n: 0``; for a rate, a pair whose executions were all
    skipped or unknown is ``value: null`` with its real ``n``. Rows come in
    SQL's rank order; ``columns`` are already ordered by the caller.
    """
    rows = list(fetched.rows)
    column_index = {key: index for index, (key, _label) in enumerate(columns)}
    row_index = {entry.key: index for index, entry in enumerate(rows)}
    by_pair = {(cell.y, cell.x): cell for cell in fetched.cells}

    out_cells: list[dict] = []
    for y, entry in enumerate(rows):
        for x, (key, _label) in enumerate(columns):
            cell = by_pair.get((entry.key, key))
            out_cells.append(_cell(spec, x, y, cell))

    # Rows a future-dated run put outside the generated day axis are not
    # drawn; they are counted, so a clock-skewed run is missing loudly.
    outside = sorted({
        cell.x for cell in fetched.cells
        if cell.x not in column_index and cell.y in row_index
    })
    outside_window: Optional[dict] = None
    if spec.kind == KIND_SUITE_DAY and outside:
        outside_window = {
            "buckets": len(outside),
            "executions": sum(
                cell.n for cell in fetched.cells
                if cell.x in set(outside) and cell.y in row_index
            ),
            "first": outside[0],
            "last": outside[-1],
        }

    truncated_axes: dict[str, dict] = {}
    x_axis = _truncated_axis(_COLUMN_DIMENSION[spec.kind], len(columns), column_total)
    if x_axis:
        truncated_axes["x"] = x_axis
    series_axis = _truncated_axis(_ROW_DIMENSION[spec.kind], len(rows), fetched.row_total)
    if series_axis:
        truncated_axes["series"] = series_axis
    # The scalar names the column axis first, as chart-data's does: a dropped
    # column is a hole in every row.
    truncated_total = next(
        (truncated_axes[axis]["total"] for axis in ("x", "series") if axis in truncated_axes),
        None,
    )

    payload: dict[str, Any] = {
        "kind": "matrix",
        "value_type": "rate" if spec.is_rate else "status",
        "x_labels": [label for _key, label in columns],
        "y_labels": [entry.label for entry in rows],
        "x_keys": [key for key, _label in columns],
        "y_keys": [entry.key for entry in rows],
        "cells": out_cells,
        "truncated": bool(truncated_axes),
        "truncated_total": truncated_total,
        "truncated_axes": truncated_axes or None,
        "outside_window": outside_window,
    }
    if spec.is_rate:
        payload["unit"] = "percent"
    if len(out_cells) > MAX_MATRIX_CELLS:  # pragma: no cover - the caps above hold
        raise AssertionError("heatmap caps let a grid past the C3 cell cap")
    return payload


def _cell(spec: HeatmapSpec, x: int, y: int, cell: Optional[CellCounts]) -> dict:
    if not spec.is_rate:
        if cell is None or cell.status is None:
            return {"x": x, "y": y, "value": None, "n": 0}
        status = cell.status if cell.status in STATUS_VOCAB else TestStatus.UNKNOWN.value.lower()
        return {"x": x, "y": y, "value": status, "n": 1}
    if cell is None:
        return {
            "x": x, "y": y, "value": None, "n": 0,
            "counts": dict.fromkeys(_COUNT_NAMES, 0),
        }
    return {
        "x": x,
        "y": y,
        "value": _rate(cell.passed, cell.failed, cell.broken),
        "n": cell.n,
        "counts": {
            "passed": cell.passed, "failed": cell.failed, "broken": cell.broken,
            "skipped": cell.skipped, "unknown": cell.unknown,
        },
    }


# ── meta.definitions ───────────────────────────────────────────────────────


def definitions(spec: HeatmapSpec, now: datetime) -> dict:
    """What the numbers mean, shipped with them."""
    out: dict[str, Any] = {
        "kind": spec.kind,
        "grain": "execution_row",
        "grain_note": (
            "Counts are individual test executions (test_cases rows joined to "
            "their run). A run whose per-test rows have not landed yet "
            "contributes nothing here."
        ),
        "window_clock": "test_runs.created_at, UTC",
        "timezone": "UTC",
        "rows": (
            f"At most {spec.rows} rows, worst first: the most failed + broken "
            "executions in the window, then the most executions, then the key "
            "ascending by code point (not the server's collation), so the same "
            "data always draws the same rows. Rows "
            "beyond the cap are dropped and counted in meta.truncated_axes.series; "
            "they are not merged into an 'Other' row."
        ) if spec.is_rate else (
            f"At most {spec.rows} tests: those with the most failed + broken "
            "executions in the window (a test that never failed is not a row), "
            "ties broken by fingerprint ascending by code point."
        ),
        "in_progress": (
            "In-progress runs are included, as everywhere else in the product; "
            "meta.includes_in_progress counts them and meta.partial_day names "
            "the UTC day that is still accumulating."
        ),
    }
    if spec.is_rate:
        out.update({
            "value": (
                "Pass rate in percentage points (unit: percent, 0-100): passed / "
                "(passed + failed + broken) x 100. skipped and unknown are "
                "outside the denominator (app/core/pass_rate.py). The failure "
                "rate over the same executions is 100 - value."
            ),
            "n": (
                "Every execution in the cell; counts holds the five statuses and "
                "adds up to n. The rate's denominator is counts.passed + "
                "counts.failed + counts.broken."
            ),
            "null": (
                "A cell with no execution is null with n 0. A cell whose "
                "executions were all skipped or unknown is null with n above 0: "
                "it ran, but nothing was evaluated. Neither is 0%."
            ),
            "suite": (
                "The effective suite: a live-stream run's own label, otherwise "
                "the row's suite. Matched case-insensitively, as every suite "
                "filter in the product is; the label keeps the spelling that was "
                "ingested. The key is the lower-cased suite."
            ),
        })
    if spec.kind == KIND_SUITE_DAY:
        out["columns"] = "Every UTC calendar day of the window, oldest first."
        out["partial_bucket"] = now.astimezone(timezone.utc).date().isoformat()
        out["outside_window"] = (
            "A run whose created_at is after the current UTC day falls outside "
            "the day axis. It is not drawn; meta.outside_window counts what the "
            "shown rows held there."
        )
    elif spec.kind == KIND_SUITE_ENVIRONMENT:
        out["columns"] = (
            f"At most {MAX_COLUMNS} environments, the busiest first. The run's "
            "environment, lower-cased (it is lower-cased on write; rows written "
            "before that are folded here). Absent is '(none)'."
        )
    elif spec.kind == KIND_SUITE_RELEASE:
        out["columns"] = (
            "The releases named in release_id, every one of them; otherwise the "
            f"{MAX_COLUMNS} most recent releases (by version sort key) with a "
            "run in the window. Oldest version first. A run's release is its "
            "primary release; runs no release claims are the 'unattributed' "
            "column, always last."
        )
    else:
        out.update({
            "columns": (
                f"The last {spec.runs} runs in the window by created_at (then id), "
                "shown oldest to newest. The key is the run id; the label is the "
                "build number, which can repeat."
            ),
            "value": "The execution's status (passed, failed, broken, skipped, unknown).",
            "n": "1 when the test has a result in that run, 0 when it does not.",
            "null": "The test has no result in that run (it did not run there).",
        })
    return out


# ── The VIZ-209 seam ───────────────────────────────────────────────────────


def cache_identity_parts(scope: AnalyticsScope, spec: HeatmapSpec) -> tuple[str, ...]:
    """The canonical identity of one heatmap request, for VIZ-209's cache key.

    Normalised: an absent ``rows`` and an explicit 40 are one answer and one
    entry. The epoch, the caller's project set and the schema version are the
    layer's own.
    """
    return (
        f"shape={HEATMAP_SHAPE_VERSION}",
        f"kind={spec.kind}",
        f"rows={spec.rows}",
        f"runs={spec.runs if spec.runs is not None else ''}",
        f"project={scope.project or ''}",
        f"release={cache_identity(scope.release_ids) or ''}",
        f"suite={cache_identity(suite_keys(scope.suite_names)) or ''}",
        f"days={scope.days if scope.days is not None else ''}",
    )


# ── The entry point ────────────────────────────────────────────────────────


async def build_heatmap(
    db: AsyncSession,
    scope: AnalyticsScope,
    spec: HeatmapSpec,
    *,
    now: Optional[datetime] = None,
) -> dict:
    """The C3 matrix for ``spec`` under ``scope``, plus ``definitions`` and the
    :data:`ENVELOPE_KEYS` the route lifts into ``meta``.

    ``now`` is the request's ONE clock: the window bound, the day axis and the
    partial day all read it.
    """
    now = (now or request_clock()).astimezone(timezone.utc)
    if scope.denied:
        fetched = Fetched()
    else:
        sql, params = build_statement(spec, scope, now=now)
        result = await db.execute(scoped_text(sql, params), params)
        rows = result.fetchall()
        if len(rows) >= HARD_ROW_CAP:  # pragma: no cover - the ranking bounds it
            logger.warning("heatmap_row_cap_reached", kind=spec.kind, cap=HARD_ROW_CAP)
        fetched = read_parts(rows)

    columns, column_total = await _columns(db, spec, scope, fetched, now)
    payload = assemble(spec, fetched, columns=columns, column_total=column_total)
    payload["definitions"] = definitions(spec, now)
    logger.info(
        "heatmap_built",
        kind=spec.kind,
        rows=len(payload["y_labels"]),
        columns=len(payload["x_labels"]),
        truncated=payload["truncated"],
    )
    return payload


#: What the route lifts out of the payload and into the C2 envelope.
ENVELOPE_KEYS = ("truncated", "truncated_total", "truncated_axes", "outside_window")


__all__ = [
    "AxisEntry",
    "CellCounts",
    "DEFAULT_ROWS",
    "DEFAULT_RUNS",
    "ENVELOPE_KEYS",
    "Fetched",
    "HARD_ROW_CAP",
    "HEATMAP_SCOPE",
    "HEATMAP_SHAPE_VERSION",
    "HeatmapSpec",
    "KINDS",
    "MAX_COLUMNS",
    "MAX_ROWS",
    "MAX_RUNS",
    "ReleaseInfo",
    "SUITE_DAY_MAX_DAYS",
    "UNATTRIBUTED_LABEL",
    "assemble",
    "build_heatmap",
    "build_statement",
    "cache_identity_parts",
    "definitions",
    "order_release_columns",
    "parse_heatmap_spec",
    "read_parts",
    "request_clock",
    "window_start",
]
