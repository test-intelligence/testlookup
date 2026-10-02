"""VIZ-208 -- the executions behind one chart mark.

``GET /api/v1/analytics/chart-data/rows`` answers "which test executions make
up this bar, this cell, this point". The caller sends the SAME scope, ``metric``
and ``group_by`` the chart was drawn with, plus one ``bucket_<dimension>`` per
selected dimension (the mark's KEY -- a point's ``x``, a series' ``key`` --
never its display label). What comes back is a page of execution rows and the
number that reconciles with the mark.

**The population is the chart's, by construction.** The window
(``tr.created_at >= window_start``), the tenant fragment, the release and suite
fragments are the ones ``chart_data_service.build_statement`` uses, and a
selector compares the dimension's OWN hard-coded expression
(``chart_data_service.DIMENSIONS``) with the key: a suite key is matched on the
effective suite, lower-cased and ``(none)``-filled exactly as the chart
bucketed it. Two selectors are rewritten into an index-friendly but EQUIVALENT
form: ``day``/``week`` are half-open UTC ranges on ``tr.created_at`` (never
``TO_CHAR(...) = :day``), and ``release``/``project``/``status``/``test`` are
plain column equalities.

**Which rows count** is decided per metric (:data:`ROW_PREDICATES`), and so is
what they reconcile with (:data:`RECONCILIATION`). The EPIC says "the total
equals the n of the mark", which is exact for a rate (n is its denominator) and
a percentile (n is the timed executions) but not for a count: a ``failed``
mark's ``n`` is every execution in the bucket, and the drill wants the failed
ones, which is the mark's ``y``. ``reconciliation`` in the response names the
mark field and the measure, so a client can compare without knowing the table.

**Row grain only.** Rows are ``test_cases``. A chart drawn from run aggregates
(``chart_data_service.grain_for``) counts a run whose per-test rows never
landed; there are no rows to list for it, so the total can be lower than the
mark and ``meta.definitions.chart_grain`` says so instead of hiding it.

**Nothing from the request is interpolated.** ``metric`` and the dimensions
are looked up in allow-lists; every value is a bind. A refusal never echoes the
value that was sent.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import AnalyticsQueryError
from app.core.release_filter import UNATTRIBUTED, is_unattributed
from app.models.postgres import TestStatus
from app.models.viz_contracts import STATUS_VOCAB
from app.services import chart_data_service as charts
from app.services.analytics_scope import (
    AnalyticsScope,
    effective_suite_sql,
    release_filter_sql,
    scoped_text,
    suite_filter_sql,
)
from app.services.analytics_service import _tenant_filter as tenant_filter_sql
# VIZ-207's ONE signature expression (BE3's M0): the failure-groups route
# groups by it and this route selects by it, so a group and its rows cannot
# disagree about which executions belong to it. Its regexes are BINDS, which
# is why ``FAILURE_SIGNATURE_PARAMS`` is merged into every statement here.
from app.services.failure_signature import (
    FAILURE_SIGNATURE_PARAMS,
    FAILURE_SIGNATURE_SQL,
    NO_MESSAGE_ID,
    SINGLETONS_ID,
    first_line_sql,
    signature_from_bucket_id,
)

logger = structlog.get_logger(__name__)

#: The rows-only dimension: a failure group's signature (VIZ-207). It is not a
#: chart-data ``group_by`` -- failure groups have their own route -- but a
#: group's "View rows" drills through this endpoint.
ERROR_SIGNATURE = "error_signature"
#: Every dimension a selector may name, in the order the 422 lists them.
ROWS_DIMENSIONS: tuple[str, ...] = (*charts.DIMENSIONS, ERROR_SIGNATURE)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
#: OD-11: page/size with an offset cap, not a keyset cursor. Measured on the
#: 1M-row seed the ordered nested loop reaches any offset under this in a few
#: milliseconds; a cursor would be a second, opaque contract for a deep page
#: nobody drills into.
MAX_OFFSET = 10_000

#: C5: a dimension value is at most 2 000 characters.
MAX_SELECTOR_LENGTH = 2_000
#: ``test_cases.test_fingerprint`` is ``VARCHAR(64)``.
MAX_TEST_SELECTOR_LENGTH = 64
#: A failure-group signature is cut to 80 characters (VIZ-207).
MAX_SIGNATURE_SELECTOR_LENGTH = 80
#: The ``error_line`` field: the first line of the message, cut like the
#: failure-group label source.
ERROR_LINE_CHARS = 300

_PASSED, _FAILED, _BROKEN = (
    TestStatus.PASSED.value, TestStatus.FAILED.value, TestStatus.BROKEN.value,
)
_SKIPPED, _UNKNOWN = TestStatus.SKIPPED.value, TestStatus.UNKNOWN.value

#: Which executions a mark of this metric stands on. Hard-coded per metric:
#: the ``row_sample`` idea of ``chart_data_service.METRICS`` made a predicate.
#: A metric added there without a line here fails
#: ``test_every_chart_metric_says_which_rows_count``.
ROW_PREDICATES: dict[str, str] = {
    "executions": "TRUE",
    "unique_tests": "TRUE",
    "run_count": "TRUE",
    "passed": f"tc.status = '{_PASSED}'",
    "failed": f"tc.status = '{_FAILED}'",
    "broken": f"tc.status = '{_BROKEN}'",
    "skipped": f"tc.status = '{_SKIPPED}'",
    "unknown": f"tc.status = '{_UNKNOWN}'",
    "failures": f"tc.status IN ('{_FAILED}', '{_BROKEN}')",
    "retried_tests": "COALESCE(tc.retry_count, 0) > 0",
    "flaky_tests": "tc.is_flaky_run IS TRUE",
    # A rate stands on its denominator: skipped and unknown are outside it.
    "pass_rate": f"tc.status IN ('{_PASSED}', '{_FAILED}', '{_BROKEN}')",
    "failure_rate": f"tc.status IN ('{_PASSED}', '{_FAILED}', '{_BROKEN}')",
    # A duration stands on the executions that carry one.
    "duration_p50": "tc.duration_ms IS NOT NULL",
    "duration_p95": "tc.duration_ms IS NOT NULL",
    "duration_total": "tc.duration_ms IS NOT NULL",
}

#: What the selected rows reconcile with: ``(mark field, measure)``. The
#: measure is ``rows`` (the total), ``distinct_tests`` or ``distinct_runs``
#: (a distinct count over the selected rows -- the only way a distinct-count
#: mark can be checked from its rows).
RECONCILIATION: dict[str, tuple[str, str]] = {
    "executions": ("y", "rows"),
    "passed": ("y", "rows"),
    "failed": ("y", "rows"),
    "broken": ("y", "rows"),
    "skipped": ("y", "rows"),
    "unknown": ("y", "rows"),
    "failures": ("y", "rows"),
    "retried_tests": ("y", "rows"),
    "unique_tests": ("y", "distinct_tests"),
    "flaky_tests": ("y", "distinct_tests"),
    "run_count": ("y", "distinct_runs"),
    "pass_rate": ("n", "rows"),
    "failure_rate": ("n", "rows"),
    "duration_p50": ("n", "rows"),
    "duration_p95": ("n", "rows"),
    "duration_total": ("n", "rows"),
}

_ROW_TEXT = {
    "TRUE": "every execution in the bucket",
    "tc.status IN ('FAILED', 'BROKEN')": "the failed and broken executions",
    "COALESCE(tc.retry_count, 0) > 0": "the executions that were retried",
    "tc.is_flaky_run IS TRUE": "the executions flagged is_flaky_run",
    "tc.status IN ('PASSED', 'FAILED', 'BROKEN')": (
        "the evaluated executions (passed, failed, broken): the rate's denominator"
    ),
    "tc.duration_ms IS NOT NULL": "the executions that carry a duration",
}

#: The effective suite as DISPLAY text (the ingested spelling), ``NULL`` when
#: there is none. The selector matches the chart's lower-cased key instead.
_SUITE_DISPLAY_SQL = effective_suite_sql()

#: The first line of the error message, trimmed, at most 300 characters,
#: ``NULL`` when there is none: the SAME first-line rule the failure groups
#: label with (``failure_signature.first_line_sql``), so the line a row shows
#: is the line its group was computed from.
_ERROR_LINE_SQL = f"NULLIF({first_line_sql('tc.error_message', ERROR_LINE_CHARS)}, '')"

#: One row of the page, over ``tc``/``tr``/``r``; shared by both statement shapes.
_PAGE_COLUMNS = f"""
                tc.id AS id,
                tc.test_name AS test_name,
                tc.test_fingerprint AS test_fingerprint,
                {_SUITE_DISPLAY_SQL} AS suite,
                tc.status AS status,
                tc.duration_ms AS duration_ms,
                tr.id AS run_id,
                tr.created_at AS created_at,
                tc.failure_category AS failure_category,
                {_ERROR_LINE_SQL} AS error_line,
                tr.primary_release_id AS release_id,
                r.name AS release_name"""
_OUTER_COLUMNS = """
            s.total, s.distinct_total,
            p.id, p.test_name, p.test_fingerprint, p.suite, p.status,
            p.duration_ms, p.run_id, p.created_at, p.failure_category,
            p.error_line, p.release_id, p.release_name"""

#: The metrics a failure-group drill may carry: a group is made of failing
#: executions (VIZ-207), so any other metric would select rows no group holds
#: -- and would run the signature regex over every execution in the window.
SIGNATURE_METRICS = ("failures", "failed", "broken")


@dataclass(frozen=True)
class Selector:
    """One validated ``bucket_<dimension>`` value."""

    dimension: str
    #: The bind value: a string key, a UUID, an uppercase status, or ``None``
    #: for the ``unattributed`` release.
    value: Any
    #: ``[start, end)`` for a day or week.
    start: Optional[datetime] = None
    end: Optional[datetime] = None


@dataclass(frozen=True)
class RowsRequest:
    """A validated request: nothing here came from the caller as text."""

    metric: str
    group_by: tuple[str, ...]
    top_n: Optional[int]
    selectors: tuple[Selector, ...]
    page: int
    size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size

    @property
    def chart_dimensions(self) -> tuple[str, ...]:
        return tuple(dim for dim in self.group_by if dim != ERROR_SIGNATURE)


# ── Parsing (no database) ──────────────────────────────────────────────────


def selector_param(dimension: str) -> str:
    return f"bucket_{dimension}"


def _bad_value(dimension: str, why: str, allowed: Any = None) -> AnalyticsQueryError:
    # ``why`` is server text; the value that was sent is never quoted.
    return AnalyticsQueryError(
        "bucket_value",
        f"{selector_param(dimension)} {why}",
        param=selector_param(dimension),
        allowed=allowed,
    )


def _parse_date(dimension: str, value: str) -> date:
    if len(value) != 10:
        raise _bad_value(dimension, "is a UTC date, YYYY-MM-DD", {"format": "YYYY-MM-DD"})
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise _bad_value(
            dimension, "is a UTC date, YYYY-MM-DD", {"format": "YYYY-MM-DD"}
        ) from None


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def parse_selector(dimension: str, value: str) -> Selector:
    """One ``bucket_<dimension>`` value, validated and normalised to its bind."""
    if not isinstance(value, str):
        raise _bad_value(dimension, "is a string")
    if chr(0) in value:
        raise _bad_value(dimension, "may not contain a NUL character")
    if value == charts.OTHER_KEY or (dimension == ERROR_SIGNATURE and value == SINGLETONS_ID):
        raise _bad_value(
            dimension,
            "names a roll-up, which is not one bucket: drill into one of the "
            "keys it merged instead",
        )
    if dimension == "day":
        day = _parse_date(dimension, value)
        return Selector(dimension, value, _midnight(day), _midnight(day + timedelta(days=1)))
    if dimension == "week":
        monday = _parse_date(dimension, value)
        if monday.isoweekday() != 1:
            raise _bad_value(
                dimension, "is an ISO week, named by its Monday", {"weekday": "Monday"}
            )
        return Selector(dimension, value, _midnight(monday), _midnight(monday + timedelta(days=7)))
    if dimension == "project":
        try:
            return Selector(dimension, uuid.UUID(value))
        except ValueError:
            raise _bad_value(dimension, "is a project UUID") from None
    if dimension == "release":
        if is_unattributed(value):
            return Selector(dimension, None)
        try:
            return Selector(dimension, uuid.UUID(value))
        except ValueError:
            raise _bad_value(
                dimension, f"is a release UUID or '{UNATTRIBUTED}'"
            ) from None
    if dimension == "status":
        if value not in STATUS_VOCAB:
            raise AnalyticsQueryError(
                "status_vocab",
                f"{selector_param(dimension)} is one of the TestStatus vocabulary",
                param=selector_param(dimension),
                allowed=list(STATUS_VOCAB),
            )
        return Selector(dimension, value.upper())
    limit = {
        "test": MAX_TEST_SELECTOR_LENGTH,
        ERROR_SIGNATURE: MAX_SIGNATURE_SELECTOR_LENGTH,
    }.get(dimension, MAX_SELECTOR_LENGTH)
    if not 1 <= len(value) <= limit:
        raise _bad_value(dimension, f"is 1-{limit} characters", {"min": 1, "max": limit})
    if dimension == ERROR_SIGNATURE:
        # A group id is its signature, except the no-message group, whose
        # signature is '' (a NULL or blank message).
        return Selector(dimension, signature_from_bucket_id(value))
    return Selector(dimension, value)


def _parse_group_by(group_by: Any) -> tuple[str, ...]:
    raw = [group_by] if isinstance(group_by, str) else list(group_by or ())
    if not raw:
        raise AnalyticsQueryError(
            "missing_parameter",
            "group_by is required: the chart's own dimensions, which decide the "
            "selectors this request may carry",
            param="group_by",
            allowed=list(ROWS_DIMENSIONS),
        )
    if len(raw) > charts.MAX_GROUP_BY:
        raise AnalyticsQueryError(
            "group_by_cap",
            f"at most {charts.MAX_GROUP_BY} group_by values, as on the chart",
            param="group_by",
            allowed={"max": charts.MAX_GROUP_BY},
        )
    dims: list[str] = []
    for value in raw:
        if not isinstance(value, str) or value not in ROWS_DIMENSIONS:
            raise AnalyticsQueryError(
                "dimension_enum",
                "group_by is not one of the values this endpoint supports",
                param="group_by",
                allowed=list(ROWS_DIMENSIONS),
            )
        if value in dims:
            raise AnalyticsQueryError(
                "unique_dimension", "a dimension may be named once",
                param="group_by", allowed=list(ROWS_DIMENSIONS),
            )
        dims.append(value)
    return tuple(dims)


def _parse_page(page: Any, size: Any) -> tuple[int, int]:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise AnalyticsQueryError(
            "page_range", "page counts from 1", param="page", allowed={"min": 1},
        )
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_PAGE_SIZE:
        raise AnalyticsQueryError(
            "size_range", f"size is 1-{MAX_PAGE_SIZE}", param="size",
            allowed={"min": 1, "max": MAX_PAGE_SIZE},
        )
    if (page - 1) * size > MAX_OFFSET:
        raise AnalyticsQueryError(
            "page_cap",
            f"a page may start at most {MAX_OFFSET} rows in: narrow the bucket "
            "(a day, a suite, a status) instead of paging deeper",
            param="page",
            allowed={"max_offset": MAX_OFFSET, "max_page": MAX_OFFSET // size + 1},
        )
    return page, size


def parse_rows_request(
    metric: Any,
    group_by: Any,
    top_n: Any,
    selectors: Mapping[str, Optional[str]],
    page: Any,
    size: Any,
    *,
    scope: AnalyticsScope,
) -> RowsRequest:
    """Validate the request before any database access.

    ``selectors`` maps a dimension to the raw ``bucket_<dimension>`` value
    (``None`` = not sent). A chart-data drill (no ``error_signature``) is also
    run through ``chart_data_service.parse_chart_spec``: the request must name
    a chart that endpoint would draw, with the same refusal for the same
    mistake.
    """
    if not isinstance(metric, str) or metric not in charts.METRICS:
        raise AnalyticsQueryError(
            "metric_enum",
            "metric is not one of the values this endpoint supports",
            param="metric",
            allowed=sorted(charts.METRICS),
        )
    dims = _parse_group_by(group_by)
    if ERROR_SIGNATURE not in dims:
        charts.parse_chart_spec(metric, list(dims), top_n, scope=scope)
    elif metric not in SIGNATURE_METRICS:
        raise AnalyticsQueryError(
            "metric_enum",
            "an error_signature drill opens a failure group, which holds failed "
            "and broken executions: metric is one of those",
            param="metric",
            allowed=list(SIGNATURE_METRICS),
        )

    sent = {dim: value for dim, value in selectors.items() if value is not None}
    unknown = sorted(dim for dim in sent if dim not in ROWS_DIMENSIONS)
    if unknown:  # pragma: no cover - the route declares only the known ones
        raise AnalyticsQueryError(
            "selector_not_in_group_by", "an unknown selector was sent",
            param="bucket", allowed=[selector_param(dim) for dim in dims],
        )
    for dim in sorted(sent):
        if dim not in dims:
            raise AnalyticsQueryError(
                "selector_not_in_group_by",
                f"{selector_param(dim)} selects a dimension this chart is not "
                "grouped by",
                param=selector_param(dim),
                allowed=[selector_param(name) for name in dims],
            )
    if not sent:
        raise AnalyticsQueryError(
            "missing_parameter",
            "select at least one bucket: rows of the whole scope are not a drill",
            param="bucket",
            allowed=[selector_param(name) for name in dims],
        )
    parsed = tuple(parse_selector(dim, sent[dim]) for dim in dims if dim in sent)
    page, size = _parse_page(page, size)
    return RowsRequest(
        metric=metric,
        group_by=dims,
        top_n=top_n if isinstance(top_n, int) and not isinstance(top_n, bool) else None,
        selectors=parsed,
        page=page,
        size=size,
    )


# ── The statement ──────────────────────────────────────────────────────────


def _selector_sql(selector: Selector, params: dict) -> str:
    """The predicate for one selector. Every fragment is hard-coded; the value
    is always a bind under a ``bucket_`` name no scope fragment uses."""
    dim = selector.dimension
    name = selector_param(dim)
    if dim in ("day", "week"):
        params[f"{name}_from"] = selector.start
        params[f"{name}_to"] = selector.end
        return f"AND tr.created_at >= :{name}_from AND tr.created_at < :{name}_to"
    if dim == "release":
        if selector.value is None:
            return "AND tr.primary_release_id IS NULL"
        params[name] = selector.value
        return f"AND tr.primary_release_id = :{name}"
    params[name] = selector.value
    if dim == "project":
        return f"AND tr.project_id = :{name}"
    if dim == "status":
        return f"AND tc.status = :{name}"
    if dim == "test":
        return f"AND tc.test_fingerprint = :{name}"
    # suite, failure_category, branch, environment, ingestion_source: the
    # chart's own bucket expression against the chart's own key.
    return f"AND {charts.DIMENSIONS[dim].sql} = :{name}"


def build_rows_statement(
    req: RowsRequest, scope: AnalyticsScope, *, now: Optional[datetime] = None
) -> tuple[str, dict]:
    """``(sql, params)`` for one page and its total, in ONE statement.

    One statement is one snapshot: the total and the page cannot disagree
    because ingestion committed between two reads. The page is a LATERAL
    subquery so it keeps its own ``ORDER BY ... LIMIT`` plan (an ordered
    nested loop from ``ix_test_runs_created_at``) instead of being computed
    after the count.
    """
    params: dict[str, Any] = {
        "period_start": charts.window_start(scope.window_days, now=now),
        # The first-line and signature regexes are binds in every statement:
        # ``error_line`` reads them even when no signature is selected.
        **FAILURE_SIGNATURE_PARAMS,
    }
    where = [
        "tr.created_at >= :period_start",
        tenant_filter_sql(
            params, project_id=scope.project, allowed_project_ids=scope.allowed_project_ids,
        ),
        release_filter_sql(params, scope.release_arg),
        suite_filter_sql(params, scope.suite_arg),
        f"AND ({ROW_PREDICATES[req.metric]})",
    ]
    signature = next((s for s in req.selectors if s.dimension == ERROR_SIGNATURE), None)
    where += [
        _selector_sql(selector, params) for selector in req.selectors if selector is not signature
    ]
    where_sql = "\n          ".join(part for part in where if part)

    measure = RECONCILIATION[req.metric][1]
    params["rows_limit"] = req.size
    params["rows_offset"] = req.offset
    if signature is not None:
        # Every SIGNATURE_METRICS member reconciles on the row total.
        params[selector_param(ERROR_SIGNATURE)] = signature.value
        return _signature_statement(where_sql), params

    distinct = {
        "distinct_tests": "COUNT(DISTINCT tc.test_fingerprint)",
        "distinct_runs": "COUNT(DISTINCT tr.id)",
    }.get(measure, "CAST(NULL AS BIGINT)")
    sql = f"""
        WITH selected AS (
            SELECT COUNT(*) AS total, {distinct} AS distinct_total
            FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id
            WHERE {where_sql}
        )
        SELECT {_OUTER_COLUMNS}
        FROM selected s
        LEFT JOIN LATERAL (
            SELECT {_PAGE_COLUMNS}
            FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id
            LEFT JOIN releases r ON r.id = tr.primary_release_id
            WHERE {where_sql}
            ORDER BY tr.created_at DESC, tc.id DESC
            LIMIT :rows_limit OFFSET :rows_offset
        ) p ON TRUE
        ORDER BY p.created_at DESC NULLS LAST, p.id DESC NULLS LAST
    """
    return sql, params


def _signature_statement(where_sql: str) -> str:
    """The failure-group drill: the window's candidate rows FIRST, behind a
    ``MATERIALIZED`` fence, and the signature over those only, once.

    Without the fence the planner pushes the signature filter -- a regex per
    row -- below the join to the window's runs and evaluates it on every
    failing execution the project ever had, twice (count and page): measured
    462/534 ms p50/p95 at 90 days on the 1M-row seed. The signature is
    ``FAILURE_SIGNATURE_SQL`` verbatim, read off a CTE aliased ``tc`` that
    carries ``error_message``, so it is the failure groups' own expression.
    """
    return f"""
        WITH candidates AS MATERIALIZED (
            SELECT tc.id, tc.error_message, tr.created_at AS run_created_at
            FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id
            WHERE {where_sql}
        ), matched AS MATERIALIZED (
            SELECT tc.id, tc.run_created_at
            FROM candidates tc
            WHERE {FAILURE_SIGNATURE_SQL} = :{selector_param(ERROR_SIGNATURE)}
        ), selected AS (
            SELECT COUNT(*) AS total, CAST(NULL AS BIGINT) AS distinct_total
            FROM matched m
        ), page_ids AS (
            SELECT m.id
            FROM matched m
            ORDER BY m.run_created_at DESC, m.id DESC
            LIMIT :rows_limit OFFSET :rows_offset
        )
        SELECT {_OUTER_COLUMNS}
        FROM selected s
        LEFT JOIN LATERAL (
            SELECT {_PAGE_COLUMNS}
            FROM page_ids pi
            JOIN test_cases tc ON tc.id = pi.id
            JOIN test_runs tr ON tr.id = tc.test_run_id
            LEFT JOIN releases r ON r.id = tr.primary_release_id
        ) p ON TRUE
        ORDER BY p.created_at DESC NULLS LAST, p.id DESC NULLS LAST
    """


# ── Assembly ───────────────────────────────────────────────────────────────


def _instant(moment: datetime) -> str:
    """RFC 3339 UTC, the C2 ``utc_instant`` profile ``meta`` uses."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _lower(value: Any) -> Optional[str]:
    return None if value is None else str(value).lower()


def row_item(row: Any) -> dict:
    """One execution, as the panel shows it. Names are untrusted text and
    travel as data; nothing here is markup."""
    release = None
    if row.release_id is not None:
        release = {"id": str(row.release_id), "name": row.release_name}
    return {
        "id": str(row.id),
        "test_name": row.test_name,
        "test_fingerprint": row.test_fingerprint,
        "suite": row.suite,
        "status": _lower(row.status),
        "duration_ms": None if row.duration_ms is None else int(row.duration_ms),
        "run_id": str(row.run_id),
        "release": release,
        "created_at": _instant(row.created_at),
        "failure_category": _lower(row.failure_category),
        "error_line": row.error_line,
    }


def assemble_page(req: RowsRequest, rows: Sequence[Any]) -> dict:
    total = int(rows[0].total) if rows else 0
    items = [row_item(row) for row in rows if row.id is not None]
    field, measure = RECONCILIATION[req.metric]
    value = total
    if measure != "rows":
        value = int(rows[0].distinct_total or 0) if rows else 0
    return {
        "items": items,
        "total": total,
        "page": req.page,
        "size": req.size,
        "pages": -(-total // req.size),
        "reconciliation": {"mark_field": field, "measure": measure, "value": value},
    }


def chart_grain(req: RowsRequest, scope: AnalyticsScope) -> str:
    """The grain the chart this drills from was drawn on."""
    dims = req.chart_dimensions
    if ERROR_SIGNATURE in req.group_by or not dims:
        return charts.GRAIN_ROW
    spec = charts.ChartSpec(metric=req.metric, group_by=dims, top_n=req.top_n)
    return charts.grain_for(spec, scope)


def definitions(req: RowsRequest, grain: str) -> dict:
    field, measure = RECONCILIATION[req.metric]
    predicate = ROW_PREDICATES[req.metric]
    rows_text = _ROW_TEXT.get(predicate) or (
        f"the executions with status {req.metric}"
    )
    out: dict[str, Any] = {
        "metric": req.metric,
        "dimensions": list(req.group_by),
        "selected": [selector.dimension for selector in req.selectors],
        "rows": f"Rows are {rows_text}, inside the chart's scope and the selected bucket.",
        "reconciliation": (
            f"reconciliation.value equals the mark's {field}"
            + (
                "" if measure == "rows" else
                f" ({'distinct tests' if measure == 'distinct_tests' else 'distinct runs'} "
                "among the selected rows)"
            )
            + "; summed over every bucket of an additive chart it equals the chart total."
        ),
        "selector": (
            "A bucket_<dimension> value is the chart's KEY for that bucket (a "
            "point's x, a series' key), never its display label. day and week "
            "are UTC calendar ranges on the run's created_at; suite is the "
            "effective suite, lower-cased, '(none)' when absent; error_signature "
            f"is a failure group's id (its signature, '{NO_MESSAGE_ID}' for the "
            "executions with no message), matched with the failure groups' own "
            "signature expression."
        ),
        "chart_grain": grain,
        "order": "Newest run first (the run's created_at, UTC), then the test case id, descending.",
        "created_at": "The RUN's created_at, the clock every chart buckets by.",
        "window": "The run's created_at on or after the window start (UTC midnight), as on the chart.",
        "error_line": (
            f"The first line of the error message, trimmed, at most {ERROR_LINE_CHARS} "
            "characters, by the rule the failure groups read their lines with; "
            "null when there is none."
        ),
        "in_progress": "In-progress runs are included, as on the chart.",
        "as_of": (
            "meta.as_of is when these rows were read. A mark drawn earlier can "
            "differ because data arrived since; compare against it, not the clock."
        ),
        "page_cap": f"(page - 1) x size is at most {MAX_OFFSET}.",
    }
    if grain == charts.GRAIN_RUN:
        out["chart_grain_note"] = (
            "The chart counted run aggregates (test_runs.passed_tests and its "
            "siblings). A run whose per-test rows never landed is in the mark "
            "and has no rows to list, so the total here can be lower."
        )
    return out


async def build_rows(
    db: AsyncSession,
    scope: AnalyticsScope,
    req: RowsRequest,
    *,
    now: Optional[datetime] = None,
) -> dict:
    """One page of rows, its total, the reconciliation and ``definitions``."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if scope.denied:
        rows: list[Any] = []
    else:
        sql, params = build_rows_statement(req, scope, now=now)
        rows = list((await db.execute(scoped_text(sql, params), params)).fetchall())
    payload = assemble_page(req, rows)
    payload["definitions"] = definitions(req, chart_grain(req, scope))
    logger.info(
        "chart_rows_built",
        metric=req.metric,
        group_by=",".join(req.group_by),
        selected=",".join(selector.dimension for selector in req.selectors),
        total=payload["total"],
        page=req.page,
    )
    return payload


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "ERROR_SIGNATURE",
    "MAX_OFFSET",
    "MAX_PAGE_SIZE",
    "RECONCILIATION",
    "ROWS_DIMENSIONS",
    "ROW_PREDICATES",
    "RowsRequest",
    "Selector",
    "assemble_page",
    "build_rows",
    "build_rows_statement",
    "chart_grain",
    "definitions",
    "parse_rows_request",
    "parse_selector",
    "row_item",
    "selector_param",
]
