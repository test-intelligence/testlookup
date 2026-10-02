"""VIZ-506-BE -- one point per test: p95 duration x failure rate x volume.

``GET /api/v1/analytics/test-scatter`` feeds the scatter/bubble chart: which
tests are slow AND flaky. It cannot be served by ``/chart-data`` (one metric per
request, eight series, and every metric ranks its own top-N, so p95, failure
rate and executions would come back for three different sets of tests); it is
its own per-test aggregate, answered as contract C3 kind ``points``.

**One pass, one population.** A single ``GROUP BY tc.test_fingerprint`` over
the chart's population (window on the run's ``created_at``, the tenant,
release and suite fragments of ``analytics_scope``) yields every number: the
executions, the evaluated executions, the failures, the timed executions and
the p95. The points, the exclusion counts and the qualifying total come out of
the same statement, so they cannot disagree.

**Nothing is placed that cannot be placed.**

* ``x`` is the p95 of ``duration_ms`` on a LOG axis. The column is an integer
  and a log axis cannot hold 0, so a p95 below 1 ms is drawn at 1 ms
  (:data:`X_FLOOR_MS`) and ``meta.definitions.x_floor`` says so. A test with no
  timed execution has no x at all: it is left out and counted
  (``excluded.no_duration``), never drawn at 0.
* ``y`` is the failure rate over the EVALUATED executions
  (``app.core.pass_rate``: skipped and unknown are outside the denominator). A
  test with nothing evaluated has no rate -- not 0% -- and is left out and
  counted (``excluded.no_evaluated``).
* A test with fewer than ``min_executions`` executions is left out and counted
  (``excluded.below_min_executions``): EPIC VIZ-506, "excluded and counted".

A test is counted under the FIRST reason that applies, in that order
(below_min_executions, no_evaluated, no_duration), so the three counts add up
to the tests left out.

**Cost.** The p95 is a per-test ordered-set aggregate over every timed row in
the window, so the statement scales with the rows in the window, not with the
points returned. Measured on the 1M-row seed: see ``docs/viz-work/w3/BE4.md``.
The UI clamps every request to 90 days (``catalogueScope.ts``); a direct caller
may ask for up to :data:`MAX_WINDOW_DAYS`.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import AnalyticsQueryError
from app.core.pass_rate import executed_count
from app.models.postgres import TestStatus
from app.models.viz_contracts import MAX_POINTS
from app.services import chart_data_service as charts
from app.services.analytics_scope import (
    AnalyticsScope,
    release_filter_sql,
    scoped_text,
    suite_filter_sql,
)
from app.services.analytics_service import _tenant_filter as tenant_filter_sql

logger = structlog.get_logger(__name__)

DEFAULT_MIN_EXECUTIONS = 5
MAX_MIN_EXECUTIONS = 1_000
DEFAULT_LIMIT = 2_000
#: C3 ``MAX_POINTS``: the contract's cap is the endpoint's.
MAX_LIMIT = MAX_POINTS
#: Which tests survive when more qualify than ``limit``.
ORDERS = ("failures", "volume")
DEFAULT_ORDER = "failures"
#: The server's window ceiling for a direct caller (the UI sends <= 90).
MAX_WINDOW_DAYS = 365
#: The smallest x a log axis is given. ``duration_ms`` is an integer column,
#: so a p95 under 1 ms is a sub-millisecond test, drawn at the axis floor.
X_FLOOR_MS = 1.0

REASON_BELOW_MIN = "below_min_executions"
REASON_NO_EVALUATED = "no_evaluated"
REASON_NO_DURATION = "no_duration"
#: The precedence a test is counted under, first match wins.
REASONS = (REASON_BELOW_MIN, REASON_NO_EVALUATED, REASON_NO_DURATION)

_PASSED, _FAILED, _BROKEN = (
    TestStatus.PASSED.value, TestStatus.FAILED.value, TestStatus.BROKEN.value,
)

#: ``ORDER BY`` per ``order``. Hard-coded; the request picks a KEY. Every one
#: ends in the fingerprint so the same data always keeps the same tests.
_ORDER_SQL = {
    "failures": "bad DESC, executions DESC, fp ASC",
    "volume": "executions DESC, bad DESC, fp ASC",
}


@dataclass(frozen=True)
class ScatterSpec:
    min_executions: int
    limit: int
    order: str


def _int_param(value: Any, name: str, code: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise AnalyticsQueryError(
            code, f"{name} is an integer, {low}-{high}", param=name,
            allowed={"min": low, "max": high},
        )
    return int(value)


def parse_scatter_spec(min_executions: Any, limit: Any, order: Any) -> ScatterSpec:
    """Validate before any database access; nothing sent is ever echoed."""
    if not isinstance(order, str) or order not in ORDERS:
        raise AnalyticsQueryError(
            "order_enum", "order is one of the values this endpoint supports",
            param="order", allowed=list(ORDERS),
        )
    return ScatterSpec(
        min_executions=_int_param(
            min_executions, "min_executions", "min_executions_range", 1, MAX_MIN_EXECUTIONS,
        ),
        limit=_int_param(limit, "limit", "limit_range", 1, MAX_LIMIT),
        order=order,
    )


def build_statement(
    spec: ScatterSpec, scope: AnalyticsScope, *, now: Optional[datetime] = None
) -> tuple[str, dict]:
    """``(sql, params)``: the points, the exclusion counts and the qualifying
    total, as ONE result set tagged by ``part``. Exposed so the injection
    tests read the text without a database."""
    params: dict[str, Any] = {
        "period_start": charts.window_start(scope.window_days, now=now),
        "scatter_min_executions": spec.min_executions,
        "scatter_limit": spec.limit,
        "reason_below_min": REASON_BELOW_MIN,
        "reason_no_evaluated": REASON_NO_EVALUATED,
        "reason_no_duration": REASON_NO_DURATION,
    }
    tenant = tenant_filter_sql(
        params, project_id=scope.project, allowed_project_ids=scope.allowed_project_ids,
    )
    sql = f"""
        WITH per_test AS (
            SELECT
                tc.test_fingerprint AS fp,
                MIN(tc.test_name) AS label,
                COUNT(*) AS executions,
                COUNT(*) FILTER (WHERE tc.status = '{_PASSED}') AS passed,
                COUNT(*) FILTER (WHERE tc.status = '{_FAILED}') AS failed,
                COUNT(*) FILTER (WHERE tc.status = '{_BROKEN}') AS broken,
                COUNT(*) FILTER (WHERE tc.status IN ('{_FAILED}', '{_BROKEN}')) AS bad,
                COUNT(*) FILTER (WHERE tc.duration_ms IS NOT NULL) AS timed,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY tc.duration_ms)
                    FILTER (WHERE tc.duration_ms IS NOT NULL) AS p95
            FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id
            WHERE tr.created_at >= :period_start
              {tenant}
              {release_filter_sql(params, scope.release_arg)}
              {suite_filter_sql(params, scope.suite_arg)}
            GROUP BY tc.test_fingerprint
        ), classified AS (
            SELECT
                per_test.*,
                CASE
                    WHEN executions < :scatter_min_executions THEN :reason_below_min
                    WHEN passed + failed + broken = 0 THEN :reason_no_evaluated
                    WHEN timed = 0 THEN :reason_no_duration
                END AS excluded_by
            FROM per_test
        ), ranked AS (
            SELECT
                classified.*,
                ROW_NUMBER() OVER (ORDER BY {_ORDER_SQL[spec.order]}) AS rn,
                COUNT(*) OVER () AS qualifying
            FROM classified
            WHERE excluded_by IS NULL
        )
        SELECT 'point' AS part, fp, label, executions, passed, failed, broken, p95,
               qualifying, CAST(NULL AS TEXT) AS reason, CAST(NULL AS BIGINT) AS tests, rn
        FROM ranked
        WHERE rn <= :scatter_limit
        UNION ALL
        SELECT 'excluded', NULL, NULL, NULL, NULL, NULL, NULL, NULL,
               NULL, excluded_by, COUNT(*), NULL
        FROM classified
        WHERE excluded_by IS NOT NULL
        GROUP BY excluded_by
        ORDER BY part DESC, rn ASC
    """
    return sql, params


# ── Assembly ───────────────────────────────────────────────────────────────


def failure_rate(passed: int, failed: int, broken: int) -> Optional[float]:
    """``(failed + broken) / evaluated x 100``, the chart-data ``failure_rate``.
    ``None`` when nothing was evaluated: never 0%."""
    evaluated = executed_count(passed, failed, broken)
    if evaluated < 1:
        return None
    return round((failed + broken) / evaluated * 100, 2)


def place_x(p95: Optional[float]) -> Optional[float]:
    """The x of a point: the p95, floored at :data:`X_FLOOR_MS` for the log
    axis. ``None`` (no timed execution) is NOT floored: such a test is
    excluded, not drawn at the floor."""
    if p95 is None:
        return None
    return max(round(float(p95), 2), X_FLOOR_MS)


def assemble(spec: ScatterSpec, rows: Sequence[Any]) -> dict:
    """The C3 ``points`` payload plus ``truncated``/``truncated_total``."""
    points: list[dict] = []
    excluded = {reason: 0 for reason in REASONS}
    qualifying = 0
    for row in rows:
        if row.part == "excluded":
            excluded[str(row.reason)] = int(row.tests or 0)
            continue
        qualifying = int(row.qualifying or 0)
        passed, failed, broken = int(row.passed or 0), int(row.failed or 0), int(row.broken or 0)
        y = failure_rate(passed, failed, broken)
        x = place_x(row.p95)
        if x is None or y is None:  # pragma: no cover - the SQL classified it out
            continue
        points.append({
            "id": str(row.fp),
            "label": str(row.label),
            "x": x,
            "y": y,
            "size": int(row.executions),
            "n": executed_count(passed, failed, broken),
        })
    payload: dict[str, Any] = {
        "kind": "points",
        "x": {"key": "p95_duration_ms", "label": "p95 duration (ms)", "unit": "ms", "scale": "log"},
        "y": {"key": "failure_rate", "label": "Failure rate (%)", "unit": "percent", "scale": "linear"},
        "size": {"key": "executions", "label": "Executions"},
        "points": points,
    }
    if points:
        # Unweighted medians over the RETURNED points: the guides split the
        # plot the reader sees. Absent with no points -- never (0, 0).
        payload["medians"] = {
            "x": round(statistics.median(point["x"] for point in points), 2),
            "y": round(statistics.median(point["y"] for point in points), 2),
        }
    payload["excluded"] = excluded
    truncated = qualifying > len(points)
    payload["truncated"] = truncated
    payload["truncated_total"] = qualifying if truncated else None
    return payload


def definitions(spec: ScatterSpec) -> dict:
    return {
        "grain": charts.GRAIN_ROW,
        "point": "One test (its fingerprint) with at least min_executions executions in scope.",
        "x": (
            "p95 of duration_ms (percentile_cont) over the test's executions that "
            "carry a duration, on a log axis."
        ),
        "x_floor": (
            f"A p95 below {X_FLOOR_MS:g} ms is drawn at {X_FLOOR_MS:g} ms: duration_ms "
            "is an integer and a log axis cannot hold 0."
        ),
        "y": (
            "Failure rate: (failed + broken) / (passed + failed + broken) x 100. "
            "Skipped and unknown are outside the denominator (app/core/pass_rate.py)."
        ),
        "size": "Executions of the test in scope, every status.",
        "n": "The evaluated executions behind y.",
        "medians": (
            "Unweighted medians of x and y over the returned points; absent when "
            "no point is returned."
        ),
        "excluded": (
            "Counts of TESTS left out, each under the first reason that applies: "
            f"fewer than {spec.min_executions} executions (below_min_executions), "
            "nothing evaluated -- only skipped or unknown -- so no rate "
            "(no_evaluated), no execution with a duration, so no x (no_duration)."
        ),
        "order": (
            "When more tests qualify than limit, the ones kept are the most "
            + ("failing (failures, then executions)" if spec.order == "failures"
               else "executed (executions, then failures)")
            + ", ties by fingerprint; meta.truncated_total is how many qualified."
        ),
        "window": "The run's created_at on or after the window start (UTC midnight), as on every chart.",
        "in_progress": "In-progress runs are included, as everywhere else in the product.",
    }


def cache_identity_parts(scope: AnalyticsScope, spec: ScatterSpec) -> tuple[str, ...]:
    """The canonical identity of one request (VIZ-209 keys on it)."""
    from app.services.analytics_scope import cache_identity, suite_keys

    return (
        f"min_executions={spec.min_executions}",
        f"limit={spec.limit}",
        f"order={spec.order}",
        f"project={scope.project or ''}",
        f"release={cache_identity(scope.release_ids) or ''}",
        f"suite={cache_identity(suite_keys(scope.suite_names)) or ''}",
        f"days={scope.days if scope.days is not None else ''}",
    )


async def build_test_scatter(
    db: AsyncSession,
    scope: AnalyticsScope,
    spec: ScatterSpec,
    *,
    now: Optional[datetime] = None,
) -> dict:
    """The ``points`` payload, ``truncated``/``truncated_total`` and
    ``definitions`` (the route lifts the last three into ``meta``)."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if scope.denied:
        rows: list[Any] = []
    else:
        sql, params = build_statement(spec, scope, now=now)
        rows = list((await db.execute(scoped_text(sql, params), params)).fetchall())
    payload = assemble(spec, rows)
    payload["definitions"] = definitions(spec)
    logger.info(
        "test_scatter_built",
        points=len(payload["points"]),
        truncated=payload["truncated"],
        excluded=sum(payload["excluded"].values()),
    )
    return payload


#: What the route lifts out of the payload and into the C2 envelope.
ENVELOPE_KEYS = ("truncated", "truncated_total")

__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_MIN_EXECUTIONS",
    "DEFAULT_ORDER",
    "ENVELOPE_KEYS",
    "MAX_LIMIT",
    "MAX_MIN_EXECUTIONS",
    "MAX_WINDOW_DAYS",
    "ORDERS",
    "REASONS",
    "ScatterSpec",
    "X_FLOOR_MS",
    "assemble",
    "build_statement",
    "build_test_scatter",
    "cache_identity_parts",
    "definitions",
    "failure_rate",
    "parse_scatter_spec",
    "place_x",
]
