"""VIZ-206 -- the coverage map: where is testing thin, one level at a time.

``GET /api/v1/analytics/coverage-map`` answers a treemap that is loaded level by
level: suites (``depth=1``), the classes or files of one suite (``depth=2``),
the tests of one class (``depth=3``). Each response is contract C3 ``tree``
(``contracts/viz/README.md``): the PARENT as the single root and its children,
each child carrying the Wave 3 ``stats`` block. A level is a request, not a
client-side zoom, which is what keeps every response under the 500-node cap.

**Test-execution coverage, not code coverage.** A rectangle is sized by how many
tests sit under it and coloured by how they ran in the window. Nothing here
knows which lines of the product those tests touch.

**Two populations, one tree.**

* *Executed* tests: every ``test_cases`` row in the window (by the RUN's
  ``created_at``, the clock chart-data and the heatmap use), grouped per test
  fingerprint under its EFFECTIVE suite -- the one suite rule
  (``analytics_scope.effective_suite_sql``), keyed exactly as chart-data keys
  its ``suite`` dimension, so a drill value means the same thing on both routes.
* *Idle* tests: canonical tests of the project (not ``deleted``) with NO
  execution anywhere in the scope, under their CANONICAL suite. They are drawn
  with ``executions: 0`` and a null pass rate; their recency comes from
  ``canonical_test_cases.last_seen_run_id``. That foreign key is ``SET NULL``
  when the run is deleted, so a missing link is ``recency: "unknown"`` -- the
  record was lost. ``"never"`` is only a test with no first sighting, no
  execution row, and a catalogue ``source`` that does not imply one: a row
  ingestion created (``execution``, ``linked``) WAS executed, even when the one
  run that saw it was deleted and took both links and every row with it.

A test executed under suite A whose canonical suite is B appears under A only:
it has executions, so it is not idle. After a manual case move the map
therefore follows the rows, and ``meta.definitions`` says so.

**Every level partitions its parent.** The level-2 and level-3 statements select
their parent with the SAME key expression that produced the parent's id, so the
children's ``test_count`` always sums to the parent's ``value``. That is why the
drill predicate is the chart-data suite KEY and not ``suite_filter_sql``: the
latter matches ``LOWER(effective suite)``, which can never select the
``(none)`` bucket of rows that carry no suite, so drilling into it would return
nothing under a parent that had tests.

**Nothing from the request is interpolated.** ``depth`` picks a hard-coded
fragment; ``suite`` and ``class_key`` are binds, echoed from ids this module
issued and never parsed; the scope's project, releases, suites and window come
from ``analytics_scope`` / ``analytics_service._tenant_filter`` fragments (the
``is_active`` guard is applied to the canonical side too). The statement does
the ranking and the "Other" roll-up itself, so a class of 40 000 Robot tests
returns at most 499 rows, not 40 000.

**Null, never zero.** ``pass_rate`` (``app.core.pass_rate``: skipped and
unknown are outside the denominator) is ``null`` when nothing was evaluated;
``flaky_share`` is ``null`` when there are no tests; a date and a staleness
exist only for ``recency: "seen"``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import AnalyticsQueryError
from app.core.pass_rate import canonical_pass_rate, executed_count
from app.models.postgres import TestStatus
from app.models.viz_contracts import MAX_TREE_NODES
from app.services.analytics_service import _tenant_filter as tenant_filter_sql
from app.services.analytics_scope import (
    AnalyticsScope,
    release_filter_sql,
    scoped_text,
    suite_filter_sql,
    suite_keys,
    suite_label_in_sql,
)
from app.services.chart_data_service import DIMENSIONS, NO_VALUE, window_start

logger = structlog.get_logger(__name__)

#: The levels: suites, the classes (or files) of one suite, the tests of one class.
DEPTHS = (1, 2, 3)
#: Longest ``suite`` / ``class_key`` accepted. Both columns are 500 characters,
#: so no key this route issued is longer.
MAX_KEY_LENGTH = 500
#: The class key of tests that carry no class (Robot, a bare function). A class
#: literally named ``__none__`` merges into it and is not distinguishable.
NO_CLASS_KEY = "__none__"
NO_CLASS_LABEL = "(ungrouped)"
#: The suite key and label of rows that carry no suite: chart-data's bucket.
NO_SUITE = NO_VALUE
#: Separates the suite key from the class key in a class node's id. A printable
#: character (U+241F, SYMBOL FOR UNIT SEPARATOR) so an id survives a URL, a log
#: line and a copy-paste unchanged. A reader never splits on it: it strips the
#: prefix it already knows (``c:`` + the suite key it asked for + this).
KEY_SEPARATOR = "␟"
ROOT_ID = "all"
ROOT_LABEL = "All suites"
#: Children per level. The contract's 500-node cap counts the root.
MAX_CHILDREN = MAX_TREE_NODES - 1
#: When the children exceed :data:`MAX_CHILDREN`, the largest this many are kept
#: and the rest become ONE "Other (n)" node, so the level is still 500 nodes.
KEPT_WHEN_FOLDED = MAX_CHILDREN - 1

GRAIN = "execution_row"

#: The chart-data ``suite`` dimension, reused verbatim: the drill key a node id
#: carries is the key a chart-data request for the same suite would use.
_SUITE_KEY_SQL = DIMENSIONS["suite"].sql
_SUITE_LABEL_SQL = DIMENSIONS["suite"].label_sql
#: The canonical side's spelling of the same key, over ``test_suites.name``.
_CANONICAL_SUITE_LABEL_SQL = f"COALESCE(NULLIF(TRIM(ts.name), ''), '{NO_SUITE}')"
_CANONICAL_SUITE_KEY_SQL = f"LOWER({_CANONICAL_SUITE_LABEL_SQL})"


@dataclass(frozen=True)
class CoverageLevel:
    """One validated level request: which parent's children to return."""

    depth: int
    suite: Optional[str] = None
    class_key: Optional[str] = None

    @property
    def root_id(self) -> str:
        if self.depth == 1:
            return ROOT_ID
        if self.depth == 2:
            return suite_node_id(self.suite or "")
        return class_node_id(self.suite or "", self.class_key or "")


def suite_node_id(suite_key: str) -> str:
    return f"s:{suite_key}"


def class_node_id(suite_key: str, class_key: str) -> str:
    return f"c:{suite_key}{KEY_SEPARATOR}{class_key}"


def test_node_id(fingerprint: str) -> str:
    return f"t:{fingerprint}"


def other_node_id(parent_id: str) -> str:
    return f"other:{parent_id}"


def class_label(class_key: str) -> str:
    return NO_CLASS_LABEL if class_key == NO_CLASS_KEY else class_key


# ── Parsing ────────────────────────────────────────────────────────────────


def _parent_key(param: str, value: Any) -> str:
    """A drill key echoed back by the client: bounded, printable, never parsed.

    The value is never echoed in the error (it is untrusted text); NUL is
    refused here because the driver would refuse it later as a 500.
    """
    if not isinstance(value, str) or value == "":
        raise AnalyticsQueryError(
            "parent_required", f"{param} is required at this depth.", param=param,
        )
    if len(value) > MAX_KEY_LENGTH:
        raise AnalyticsQueryError(
            "parent_length",
            f"{param} is at most {MAX_KEY_LENGTH} characters.",
            param=param,
            allowed={"max": MAX_KEY_LENGTH},
        )
    if "\x00" in value:
        raise AnalyticsQueryError(
            "parent_format", f"{param} contains a NUL character.", param=param,
        )
    return value


def parse_level(depth: Any, suite: Any = None, class_key: Any = None) -> CoverageLevel:
    """The level a request asks for, or a 422 naming the rule it broke.

    A parent key sent at a depth that does not read it is refused rather than
    ignored: a filter is applied or refused, never silently dropped.
    """
    if isinstance(depth, bool) or not isinstance(depth, int) or depth not in DEPTHS:
        raise AnalyticsQueryError(
            "depth_enum", "depth is 1 (suites), 2 (classes) or 3 (tests).",
            param="depth", allowed=list(DEPTHS),
        )
    if depth == 1:
        for param, value in (("suite", suite), ("class_key", class_key)):
            if value is not None:
                raise AnalyticsQueryError(
                    "parent_unexpected", f"{param} is not read at depth 1.", param=param,
                )
        return CoverageLevel(depth=1)
    suite_key = _parent_key("suite", suite)
    if depth == 2:
        if class_key is not None:
            raise AnalyticsQueryError(
                "parent_unexpected", "class_key is not read at depth 2.", param="class_key",
            )
        return CoverageLevel(depth=2, suite=suite_key)
    return CoverageLevel(depth=3, suite=suite_key, class_key=_parent_key("class_key", class_key))


# ── The statement ──────────────────────────────────────────────────────────


def _status_count(status: TestStatus) -> str:
    return f"COUNT(*) FILTER (WHERE tc.status = '{status.value}')"


#: The column a level groups its children by, and the child's display text.
#: Hard-coded per depth; ``depth`` selects one, nothing else reaches the SQL.
_CHILD_SQL = {
    1: ("suite_key", "suite_label", "suite_label"),
    2: (f"COALESCE(class_name, '{NO_CLASS_KEY}')", "class_name", "suite_label"),
    3: ("fp", "test_name", "suite_label"),
}


def build_statement(
    level: CoverageLevel, scope: AnalyticsScope, *, now: Optional[datetime] = None
) -> tuple[str, dict]:
    """``(sql, params)`` for one level. Exposed so the injection tests read the
    text without a database.

    Stages, all server-side:

    1. ``ex`` -- one row per (effective suite, test) executed in scope, with the
       five status counts, whether any execution was flagged flaky, and the
       latest run's ``created_at``.
    2. ``seen`` / ``idle`` -- canonical tests with no execution anywhere in the
       scope (window, project, releases: NOT the suite filters, so a test that
       ran under another suite is not "idle" here), with their last sighting.
    3. ``children`` -- both populations grouped by the level's child key;
       ranked by size, cut at :data:`MAX_CHILDREN` with the remainder folded
       into one ``other`` row; plus a ``root`` row over every child.
    """
    params: dict[str, Any] = {"period_start": window_start(scope.window_days, now=now)}
    tenant_runs = tenant_filter_sql(
        params, project_id=scope.project, allowed_project_ids=scope.allowed_project_ids,
    )
    tenant_canonical = tenant_filter_sql(
        params,
        project_id=scope.project,
        allowed_project_ids=scope.allowed_project_ids,
        table_alias="c",
    )
    releases = release_filter_sql(params, scope.release_arg)
    # The scope's own suite filter narrows level 1 (and bounds every level):
    # rows by their effective suite, idle tests by their canonical suite.
    suite_scope_rows = suite_filter_sql(params, scope.suite_arg)
    scoped_suites = suite_keys(scope.suite_arg)
    suite_scope_canonical = (
        "AND " + suite_label_in_sql(params, "ts.name", scoped_suites) if scoped_suites else ""
    )
    # The drill: the parent's own key expression, so a level partitions its
    # parent exactly (module docstring).
    if level.depth >= 2:
        params["cm_suite"] = level.suite
        parent_rows = f"AND {_SUITE_KEY_SQL} = :cm_suite"
        parent_canonical = f"AND {_CANONICAL_SUITE_KEY_SQL} = :cm_suite"
    else:
        parent_rows = parent_canonical = ""
    if level.depth == 3:
        params["cm_class"] = level.class_key
        parent_class = f"WHERE COALESCE(class_name, '{NO_CLASS_KEY}') = :cm_class"
    else:
        parent_class = ""

    # Level 1 with no suite filter: ``ex`` already IS the executed population,
    # so the idle anti-join reads it instead of scanning the window twice.
    if level.depth == 1 and not suite_scope_rows:
        seen = "SELECT fp FROM ex"
    else:
        seen = f"""
            SELECT DISTINCT tc.test_fingerprint AS fp
            FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id
            WHERE tr.created_at >= :period_start
              {tenant_runs}
              {releases}"""

    child_key, child_label, parent_label = _CHILD_SQL[level.depth]
    params["cm_cap"] = MAX_CHILDREN
    params["cm_keep"] = KEPT_WHEN_FOLDED
    sums = """
            SUM(test_count) AS test_count,
            SUM(executions) AS executions,
            SUM(passed) AS passed,
            SUM(failed) AS failed,
            SUM(broken) AS broken,
            SUM(skipped) AS skipped,
            SUM(unknown) AS unknown,
            SUM(flaky_count) AS flaky_count,
            MAX(last_at) AS last_at,
            SUM(unknown_tests) AS unknown_tests,
            COUNT(*) AS merged,
            MAX(child_total) AS child_total"""
    sql = f"""
        WITH ex AS (
            SELECT
                {_SUITE_KEY_SQL} AS suite_key,
                {_SUITE_LABEL_SQL} AS suite_label,
                MIN(NULLIF(TRIM(tc.class_name), '')) AS class_name,
                tc.test_fingerprint AS fp,
                MIN(tc.test_name) AS test_name,
                COUNT(*) AS executions,
                {_status_count(TestStatus.PASSED)} AS passed,
                {_status_count(TestStatus.FAILED)} AS failed,
                {_status_count(TestStatus.BROKEN)} AS broken,
                {_status_count(TestStatus.SKIPPED)} AS skipped,
                {_status_count(TestStatus.UNKNOWN)} AS unknown,
                BOOL_OR(tc.is_flaky_run IS TRUE) AS flaky,
                MAX(tr.created_at) AS last_at
            FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id
            WHERE tr.created_at >= :period_start
              {tenant_runs}
              {releases}
              {suite_scope_rows}
              {parent_rows}
            GROUP BY 1, tc.test_fingerprint
        ), seen AS ({seen}
        ), idle AS (
            SELECT
                {_CANONICAL_SUITE_KEY_SQL} AS suite_key,
                {_CANONICAL_SUITE_LABEL_SQL} AS suite_label,
                NULLIF(TRIM(c.class_name), '') AS class_name,
                c.test_fingerprint AS fp,
                c.test_name AS test_name,
                lr.created_at AS last_at,
                (
                    lr.created_at IS NULL
                    AND (
                        c.first_seen_run_id IS NOT NULL
                        OR c.source IN ('execution', 'linked')
                        OR EXISTS (
                            SELECT 1 FROM test_cases x WHERE x.canonical_test_case_id = c.id
                        )
                    )
                ) AS lost
            FROM canonical_test_cases c
            JOIN test_suites ts ON ts.id = c.test_suite_id
            LEFT JOIN test_runs lr ON lr.id = c.last_seen_run_id
            WHERE c.status <> 'deleted'
              {tenant_canonical}
              {suite_scope_canonical}
              {parent_canonical}
              AND NOT EXISTS (SELECT 1 FROM seen WHERE seen.fp = c.test_fingerprint)
        ), tests AS (
            SELECT suite_key, suite_label, class_name, fp, test_name,
                   executions, passed, failed, broken, skipped, unknown,
                   flaky, last_at, FALSE AS lost, TRUE AS executed
            FROM ex
            UNION ALL
            SELECT suite_key, suite_label, class_name, fp, test_name,
                   0, 0, 0, 0, 0, 0,
                   FALSE, last_at, lost, FALSE
            FROM idle
        ), placed AS (
            SELECT {child_key} AS child_key, {child_label} AS child_label,
                   {parent_label} AS parent_label, tests.*
            FROM tests
            {parent_class}
        ), children AS (
            SELECT
                child_key,
                COALESCE(MIN(child_label) FILTER (WHERE executed), MIN(child_label)) AS child_label,
                MIN(parent_label) FILTER (WHERE executed) AS parent_label_executed,
                MIN(parent_label) AS parent_label_any,
                COUNT(*) AS test_count,
                SUM(executions) AS executions,
                SUM(passed) AS passed,
                SUM(failed) AS failed,
                SUM(broken) AS broken,
                SUM(skipped) AS skipped,
                SUM(unknown) AS unknown,
                COUNT(*) FILTER (WHERE flaky) AS flaky_count,
                MAX(last_at) AS last_at,
                COUNT(*) FILTER (WHERE lost) AS unknown_tests
            FROM placed
            GROUP BY child_key
        ), ranked AS (
            SELECT
                children.*,
                ROW_NUMBER() OVER (
                    ORDER BY test_count DESC, child_key COLLATE "C" ASC
                ) AS rn,
                COUNT(*) OVER () AS child_total
            FROM children
        )
        SELECT
            'child' AS part, rn, child_key, child_label, CAST(NULL AS TEXT) AS parent_label,
            test_count, executions, passed, failed, broken, skipped, unknown,
            flaky_count, last_at, unknown_tests, 1 AS merged, child_total
        FROM ranked
        WHERE child_total <= :cm_cap OR rn <= :cm_keep
        UNION ALL
        SELECT
            'other', NULL, NULL, NULL, NULL,{sums}
        FROM ranked
        WHERE child_total > :cm_cap AND rn > :cm_keep
        HAVING COUNT(*) > 0
        UNION ALL
        SELECT
            'root', NULL, NULL, NULL,
            COALESCE(MIN(parent_label_executed), MIN(parent_label_any)),{sums}
        FROM ranked
        HAVING COUNT(*) > 0
        ORDER BY 1, 2
    """
    return sql, params


# ── Assembly ───────────────────────────────────────────────────────────────


def _int(value: Any) -> int:
    return int(value or 0)


def _instant(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def staleness_days(last_at: datetime, now: datetime) -> int:
    """Whole UTC calendar days from the last execution to the request clock.

    The run's own ``created_at`` against the request's ONE clock -- not the
    database's ``now()``, not the time of the cache fill. A run dated in the
    future (clock skew) is 0 days stale, never negative.
    """
    days = (now.astimezone(timezone.utc).date() - last_at.astimezone(timezone.utc).date()).days
    return max(days, 0)


def node_stats(row: Any, now: datetime, *, rate: bool = True) -> dict:
    """The contract's ``stats`` block for one child, the Other roll-up or the root.

    ``rate=False`` (the Other node) leaves the pass rate out on purpose: a rate
    over unrelated leftovers answers no question, and its counts are still
    there for the table.
    """
    test_count = _int(row.test_count)
    passed, failed, broken = _int(row.passed), _int(row.failed), _int(row.broken)
    flaky = _int(row.flaky_count)
    pass_rate = (
        canonical_pass_rate(passed, failed, broken)
        if rate and executed_count(passed, failed, broken) > 0
        else None
    )
    last_at = row.last_at
    if last_at is not None:
        recency = "seen"
    elif _int(row.unknown_tests) > 0:
        recency = "unknown"
    else:
        recency = "never"
    return {
        "test_count": test_count,
        "executions": _int(row.executions),
        "pass_rate": pass_rate,
        "flaky_count": flaky,
        "flaky_share": round(flaky / test_count, 4) if test_count > 0 else None,
        "last_executed_at": _instant(last_at) if last_at is not None else None,
        "staleness_days": staleness_days(last_at, now) if last_at is not None else None,
        "recency": recency,
    }


def _child_node(level: CoverageLevel, row: Any, parent_id: str, now: datetime) -> dict:
    key = str(row.child_key)
    if level.depth == 1:
        node_id, label = suite_node_id(key), str(row.child_label or key)
    elif level.depth == 2:
        node_id, label = class_node_id(level.suite or "", key), class_label(key)
    else:
        node_id, label = test_node_id(key), str(row.child_label or key)
    stats = node_stats(row, now)
    return {
        "id": node_id,
        "parent_id": parent_id,
        "label": label,
        "value": stats["test_count"],
        "measure": stats["pass_rate"],
        "stats": stats,
    }


def _root_label(level: CoverageLevel, row: Any) -> str:
    if level.depth == 1:
        return ROOT_LABEL
    if level.depth == 2:
        return str(row.parent_label or level.suite)
    return class_label(level.class_key or "")


def assemble(level: CoverageLevel, rows: list[Any], now: datetime) -> dict:
    """The C3 ``tree`` for one level plus the envelope's truncation facts.

    No children is ``nodes: []`` -- the contract's "no data" -- rather than a
    root of zero tests, which a renderer would draw as one empty rectangle.
    """
    root = next((row for row in rows if row.part == "root"), None)
    if root is None:
        return {"kind": "tree", "nodes": [], "truncated": False, "truncated_total": None}
    root_id = level.root_id
    root_stats = node_stats(root, now)
    nodes: list[dict] = [{
        "id": root_id,
        "parent_id": None,
        "label": _root_label(level, root),
        "value": root_stats["test_count"],
        "measure": root_stats["pass_rate"],
        "stats": root_stats,
    }]
    children = sorted((row for row in rows if row.part == "child"), key=lambda row: row.rn)
    nodes.extend(_child_node(level, row, root_id, now) for row in children)
    other = next((row for row in rows if row.part == "other"), None)
    if other is not None:
        stats = node_stats(other, now, rate=False)
        nodes.append({
            "id": other_node_id(root_id),
            "parent_id": root_id,
            "label": f"Other ({_int(other.merged)})",
            "value": stats["test_count"],
            "measure": None,
            "stats": stats,
        })
    return {
        "kind": "tree",
        "nodes": nodes,
        "truncated": other is not None,
        "truncated_total": _int(root.child_total) if other is not None else None,
    }


def definitions(level: CoverageLevel, now: datetime) -> dict:
    """What the numbers mean, shipped with them (``meta.definitions``)."""
    return {
        "coverage": "test_execution",
        "coverage_note": (
            "Test-execution coverage: how many tests sit under a node and how "
            "they ran in the window. It is not code coverage."
        ),
        "grain": GRAIN,
        "depth": level.depth,
        "level": {1: "suite", 2: "class", 3: "test"}[level.depth],
        "timezone": "UTC",
        "window_clock": (
            "An execution counts when its RUN's created_at is in the window "
            "(test_runs.created_at, UTC), the clock chart-data and the heatmap "
            "use. /analytics/coverage counts by the row's own created_at, so the "
            "two can differ by ingestion lag at the window edge."
        ),
        "suite": (
            "Executed tests sit under the effective suite of their rows (a "
            "live-stream run's label, otherwise the row's suite), keyed "
            "case-insensitively exactly as chart-data's suite dimension; rows "
            f"with no suite are '{NO_SUITE}'. Tests with no execution in scope "
            "sit under their canonical suite. A test executed under a suite other "
            "than its canonical one appears under the suite it ran in, so after a "
            "manual move the map follows the rows."
        ),
        "suite_totals": (
            "A test that ran under two effective suites is counted under both, "
            "so the root of level 1 can exceed the number of distinct tests."
        ),
        "class": (
            "The class_name the results carry: a class for TestNG/xUnit/NUnit/"
            "TRX/Allure, a file path for pytest/Playwright/Cypress, a feature URI "
            f"for Cucumber. Tests without one are '{NO_CLASS_LABEL}' (key "
            f"'{NO_CLASS_KEY}')."
        ),
        "test": "One test fingerprint; the label is its name.",
        "node_id": (
            "s:<suite key>, c:<suite key>U+241F<class key>, t:<fingerprint>, "
            "other:<parent id>, 'all' for the level-1 root. Keys are opaque: a "
            "reader strips the prefix it already knows and sends the rest back "
            "as suite / class_key, never splitting on the separator."
        ),
        "value": "test_count: the tests under the node (the rectangle's size).",
        "measure": "The pass rate, the same number as stats.pass_rate.",
        "pass_rate": (
            "passed / (passed + failed + broken) x 100 over the node's executions "
            "in scope. Skipped and unknown are outside the denominator; null when "
            "nothing was evaluated, never 0."
        ),
        "flaky": (
            "flaky_count: tests with at least one execution in scope flagged "
            "is_flaky_run. flaky_share = flaky_count / test_count, null with no "
            "tests. This is not FlakyScore."
        ),
        "idle_tests": (
            "Canonical tests (status not 'deleted'; 'needs_review' included) "
            "with no execution in the window, the project and the releases of "
            "this scope appear with executions 0 and a null pass rate."
        ),
        "recency": (
            "seen: last_executed_at is the latest execution in scope, or for an "
            "idle test the run that last saw it anywhere in the project. unknown: "
            "that run was deleted and its link cleared, so the date is lost (not "
            "'never run'). never: no execution was ever recorded. A node is seen "
            "when any test under it is; staleness_days counts UTC calendar days "
            "from last_executed_at to as_of."
        ),
        "other": (
            f"A level holds at most {MAX_CHILDREN} children. Beyond that the "
            f"{KEPT_WHEN_FOLDED} largest are kept and the rest are one 'Other (n)' "
            "node: counts are sums, the pass rate is left out (a rate over "
            "unrelated leftovers answers nothing), and it does not expand. "
            "meta.truncated_total is the true number of children."
        ),
        "ordering": (
            "Children by test_count descending, then key ascending in code-point "
            "order (not the database's locale), so the same data gives the same "
            "map, and the same Other, on every server."
        ),
        "in_progress": (
            "In-progress runs are included, as everywhere else in the product; "
            "meta.includes_in_progress counts them."
        ),
        "as_of_day": now.astimezone(timezone.utc).date().isoformat(),
    }


async def build_coverage_map(
    db: AsyncSession,
    scope: AnalyticsScope,
    level: CoverageLevel,
    *,
    now: Optional[datetime] = None,
) -> dict:
    """The C3 ``tree`` payload for ``level`` under ``scope`` plus ``definitions``
    and the envelope keys (``truncated``, ``truncated_total``) the route lifts."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if scope.denied:
        rows: list[Any] = []
    else:
        sql, params = build_statement(level, scope, now=now)
        result = await db.execute(scoped_text(sql, params), params)
        rows = list(result.fetchall())
    payload = assemble(level, rows, now)
    payload["definitions"] = definitions(level, now)
    logger.info(
        "coverage_map_built",
        depth=level.depth,
        nodes=len(payload["nodes"]),
        truncated=payload["truncated"],
    )
    return payload


#: What the route lifts out of the payload and into the C2 envelope.
ENVELOPE_KEYS = ("truncated", "truncated_total")


__all__ = [
    "CoverageLevel",
    "DEPTHS",
    "ENVELOPE_KEYS",
    "KEPT_WHEN_FOLDED",
    "KEY_SEPARATOR",
    "MAX_CHILDREN",
    "MAX_KEY_LENGTH",
    "NO_CLASS_KEY",
    "NO_CLASS_LABEL",
    "NO_SUITE",
    "ROOT_ID",
    "ROOT_LABEL",
    "assemble",
    "build_coverage_map",
    "build_statement",
    "class_label",
    "class_node_id",
    "definitions",
    "node_stats",
    "other_node_id",
    "parse_level",
    "staleness_days",
    "suite_node_id",
    "test_node_id",
]
