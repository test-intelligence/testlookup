"""VIZ-207 -- failure groups: the failing executions of a scope, grouped by cause.

``GET /api/v1/analytics/failure-groups`` answers "what is failing, and is it
one thing or many?". A group is every FAILED or BROKEN execution in the scope
whose error message has the same signature
(:mod:`app.services.failure_signature`: the first line with run-specific noise
removed). The response is contract C3 ``graph`` -- one node per group, sized by
its failures, ``group`` = its dominant failure category -- plus the ranked
``groups`` the table, the side panel and the sparkline read, and the two
roll-ups that are deliberately NOT nodes: ``no_message`` (failures with no
error text) and ``singletons`` (signatures seen once).

**One grouping stage, in SQL (OD-3).** The epic grouped raw first lines in SQL
and merged them in Python; real messages carry counts and ids, so raw lines
are nearly all distinct and a cap on raw buckets would see a fraction of the
failures (``failure_signature``'s docstring has the numbers). Here the
statement groups on the signature itself, ranks the groups, and keeps the top
:data:`MAX_GROUPS`; nothing is merged afterwards. The rows endpoint selects a
group's executions with the same expression, so a group and its rows
reconcile exactly.

**One statement, one snapshot.** The failing rows are read once (a
``MATERIALIZED`` CTE) and every part of the answer -- totals, groups,
categories, the trend, top tests, the pairs behind the edges -- comes from
that one read, as one result set tagged by ``part``. Separate statements under
READ COMMITTED could each see a different set of committed runs, and the
groups, their trend and the denominator would then disagree.

**Shares have one denominator.** ``share_of_failures`` is a group's failures
over ALL failing executions in the scope -- grouped, singletons, no message and
the groups beyond the cap alike -- so the shares of ``groups`` +
``singletons`` + ``no_message`` + ``omitted`` sum to exactly 1 and the shown
part to at most 1. With no failure in scope there is no denominator: every
share is ``null``, never 0.

**Edges are evidence, not layout.** With ``include=edges`` two groups are
linked when they fail in the same TESTS: the weight is the Jaccard index of
their affected-test sets (top :data:`EDGE_NODES` groups, weight >=
:data:`EDGE_THRESHOLD`, the :data:`MAX_EDGES` strongest). Nothing here has a
position; laying it out is the client's.

**Hostile, possibly sensitive text.** Labels, signatures and test names are
raw CI output (markup, quotes, tokens, paths). They are returned as data,
capped in length, and never interpreted; readers render them as text. The
audience is the one that can already read these runs' failures
(``analytics_scope``).

**A rate is not involved.** These are counts of failing executions; skipped,
unknown and passing executions are outside every number here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import AnalyticsQueryError
from app.models.postgres import TestStatus
from app.services.analytics_scope import (
    AnalyticsScope,
    release_filter_sql,
    scoped_text,
    suite_filter_sql,
)
# The tenant rule, spelled once (pin, membership set, fail-closed empty set,
# and the ``is_active`` guard in every branch).
from app.services.analytics_service import _tenant_filter as tenant_filter_sql
from app.services.chart_data_service import window_start
from app.services.failure_signature import (
    FAILURE_SIGNATURE_PARAMS,
    FAILURE_SIGNATURE_SQL,
    LABEL_LINE_CAP,
    NO_MESSAGE_ID,
    SINGLETONS_ID,
    first_line_sql,
)

#: Groups returned (and graph nodes: C3 caps a graph at 200).
MAX_GROUPS = 200
#: A signature seen fewer times than this is a singleton, not a group.
MIN_GROUP_FAILURES = 2
#: Only the largest groups take part in edges (the client lays out <= 60).
EDGE_NODES = 60
#: Weakest Jaccard index that is still an edge.
EDGE_THRESHOLD = 0.2
#: Strongest edges kept.
MAX_EDGES = 300
#: Characters of a group label (the most frequent raw first line).
LABEL_LENGTH = 160
#: Tests listed per group for the side panel.
TOP_TESTS = 5
#: Categories listed per group; beyond it the rest fold into ``other``.
MAX_CATEGORIES = 8
OTHER_CATEGORY = "other"
#: The trend is daily up to this window, weekly beyond it.
DAILY_TREND_MAX_DAYS = 90

INCLUDE_EDGES = "edges"
INCLUDE_TOKENS = frozenset({INCLUDE_EDGES})

_FAILING = f"tc.status IN ('{TestStatus.FAILED.value}', '{TestStatus.BROKEN.value}')"


def request_clock() -> datetime:
    """The ONE instant a request is answered at (window, trend axis, ``as_of``)."""
    return datetime.now(timezone.utc)


def parse_include(include: Any) -> frozenset[str]:
    """The ``include`` tokens, validated before the database is touched.

    An unknown token is a 422 ``include_enum`` with the allow-list; the value
    sent is never echoed (it is untrusted text a toast would render).
    """
    raw = [include] if isinstance(include, str) else list(include or ())
    tokens: set[str] = set()
    for token in raw:
        if not isinstance(token, str) or token not in INCLUDE_TOKENS:
            raise AnalyticsQueryError(
                "include_enum",
                "include is not one of the values this endpoint supports",
                param="include",
                allowed=sorted(INCLUDE_TOKENS),
            )
        tokens.add(token)
    return frozenset(tokens)


def trend_grain(days: int) -> str:
    return "day" if days <= DAILY_TREND_MAX_DAYS else "week"


def build_statement(
    scope: AnalyticsScope,
    *,
    include_edges: bool,
    now: Optional[datetime] = None,
) -> tuple[str, dict]:
    """``(sql, params)`` for one request. Exposed so the injection tests read
    the text without a database: every fragment is hard-coded here or comes
    from ``analytics_scope`` / the tenant helper, and every value is a bind."""
    grain = trend_grain(scope.window_days)  # 'day' | 'week', from code, never the request
    params: dict[str, Any] = {
        "period_start": window_start(scope.window_days, now=now),
        "fg_min_failures": MIN_GROUP_FAILURES,
        "fg_max_groups": MAX_GROUPS,
        "fg_top_tests": TOP_TESTS,
        "fg_edge_nodes": EDGE_NODES,
        **FAILURE_SIGNATURE_PARAMS,
    }
    tenant = tenant_filter_sql(
        params, project_id=scope.project, allowed_project_ids=scope.allowed_project_ids
    )
    release = release_filter_sql(params, scope.release_arg)
    suite = suite_filter_sql(params, scope.suite_arg)

    # Columns of every part: part, rn, sig, k, k2, label, n1..n4, t1, t2.
    def part(name: str, *, rn="NULL::bigint", sig="NULL::text", k="NULL::text",
             k2="NULL::text", label="NULL::text", n=("NULL::bigint",) * 4,
             t=("NULL::timestamptz",) * 2) -> str:
        return (
            f"'{name}'::text AS part, {rn} AS rn, {sig} AS sig, {k} AS k, {k2} AS k2, "
            f"{label} AS label, "
            f"{n[0]} AS n1, {n[1]} AS n2, {n[2]} AS n3, {n[3]} AS n4, {t[0]} AS t1, {t[1]} AS t2"
        )

    pairs = ""
    if include_edges:
        pairs = f"""
        UNION ALL
        SELECT {part("pair", sig="a.sig", k="b.sig", n=("COUNT(*)", "NULL::bigint", "NULL::bigint", "NULL::bigint"))}
        FROM edge_tests a
        JOIN edge_tests b
          ON b.project_id = a.project_id AND b.fp = a.fp
         AND b.sig COLLATE "C" > a.sig COLLATE "C"
        GROUP BY a.sig, b.sig"""

    # A TEST is ``(project_id, fp)``: a fingerprint is ``sha256(class::name)``
    # and two projects running one suite share it. Without a project pinned
    # (All Projects) keying on the fingerprint alone counted staging's and
    # prod's test once, merged their counts in ``top_tests`` and linked groups
    # by a test they did not share (R1-1).
    sql = f"""
        WITH f AS MATERIALIZED (
            SELECT
                {FAILURE_SIGNATURE_SQL} AS sig,
                {first_line_sql("tc.error_message", LABEL_LINE_CAP)} AS raw_line,
                tr.project_id AS project_id,
                tc.test_fingerprint AS fp,
                tc.test_name AS test_name,
                tc.test_run_id AS run_id,
                tr.created_at AS at,
                LOWER(COALESCE(tc.failure_category, 'UNKNOWN')) AS category
            FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id
            WHERE tr.created_at >= :period_start
              {tenant}
              {release}
              {suite}
              AND {_FAILING}
        ), counts AS (
            SELECT sig, COUNT(*) AS n FROM f GROUP BY sig
        ), ranked AS (
            SELECT sig, n, ROW_NUMBER() OVER (ORDER BY n DESC, sig COLLATE "C") AS rn
            FROM counts
            WHERE sig <> '' AND n >= :fg_min_failures
        ), kf AS MATERIALIZED (
            SELECT f.sig, f.raw_line, f.project_id, f.fp, f.test_name, f.run_id, f.at,
                   f.category, r.rn
            FROM f JOIN ranked r ON r.sig = f.sig
            WHERE r.rn <= :fg_max_groups
        ), edge_tests AS (
            SELECT DISTINCT sig, project_id, fp FROM kf WHERE rn <= :fg_edge_nodes
        )
        SELECT {part("totals", n=(
            "(SELECT COUNT(*) FROM f)",
            "(SELECT COUNT(*) FROM f WHERE sig = '')",
            "(SELECT COUNT(DISTINCT (project_id, fp)) FROM f WHERE sig = '')",
            "(SELECT COUNT(DISTINCT run_id) FROM f WHERE sig = '')",
        ))}
        UNION ALL
        SELECT {part("rollup", n=(
            "(SELECT COUNT(*) FROM counts WHERE sig <> '' AND n < :fg_min_failures)",
            "(SELECT COALESCE(SUM(n), 0) FROM counts WHERE sig <> '' AND n < :fg_min_failures)",
            "(SELECT COUNT(*) FROM ranked)",
            "(SELECT COALESCE(SUM(n), 0) FROM ranked)",
        ))}
        UNION ALL
        SELECT {part(
            "group", rn="MIN(rn)", sig="sig",
            label='mode() WITHIN GROUP (ORDER BY raw_line COLLATE "C")',
            n=("COUNT(*)", "COUNT(DISTINCT (project_id, fp))", "COUNT(DISTINCT run_id)",
               "COUNT(DISTINCT raw_line)"),
            t=("MIN(at)", "MAX(at)"),
        )}
        FROM kf GROUP BY sig
        UNION ALL
        SELECT {part("category", rn="MIN(rn)", sig="sig", k="category",
                     n=("COUNT(*)", "NULL::bigint", "NULL::bigint", "NULL::bigint"))}
        FROM kf GROUP BY sig, category
        UNION ALL
        SELECT {part("trend", rn="MIN(rn)", sig="sig", k="bucket",
                     n=("COUNT(*)", "NULL::bigint", "NULL::bigint", "NULL::bigint"))}
        FROM (
            SELECT sig, rn, to_char(date_trunc('{grain}', at AT TIME ZONE 'UTC'), 'YYYY-MM-DD') AS bucket
            FROM kf
        ) t
        GROUP BY sig, bucket
        UNION ALL
        SELECT {part("test", rn="rn", sig="sig", k="fp", k2="project_id::text", label="name",
                     n=("n", "NULL::bigint", "NULL::bigint", "NULL::bigint"))}
        FROM (
            SELECT sig, MIN(rn) AS rn, project_id, fp, MIN(test_name) AS name, COUNT(*) AS n,
                   ROW_NUMBER() OVER (
                       PARTITION BY sig
                       ORDER BY COUNT(*) DESC, fp COLLATE "C", project_id::text COLLATE "C"
                   ) AS r
            FROM kf GROUP BY sig, project_id, fp
        ) t
        WHERE r <= :fg_top_tests{pairs}
    """
    return sql, params


@dataclass
class _Group:
    rn: int
    signature: str
    label: str
    failure_count: int
    affected_tests: int
    affected_runs: int
    distinct_raw_lines: int
    first_seen: datetime
    last_seen: datetime


def _instant(moment: datetime) -> str:
    if moment.tzinfo is None:  # asyncpg returns aware datetimes; be safe
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _share(count: int, total: int) -> Optional[float]:
    return None if total <= 0 else count / total


def _label(text: Optional[str]) -> str:
    value = text or ""
    return value if len(value) <= LABEL_LENGTH else value[: LABEL_LENGTH - 1] + "…"


def trend_axis(days: int, now: datetime) -> list[str]:
    """Every bucket key of the window, oldest first: UTC days, or the Monday
    of each ISO week when the trend is weekly."""
    start = window_start(days, now=now).date()
    end = now.astimezone(timezone.utc).date()
    if trend_grain(days) == "week":
        start -= timedelta(days=start.weekday())
        end -= timedelta(days=end.weekday())
        step = timedelta(days=7)
    else:
        step = timedelta(days=1)
    out: list[str] = []
    cursor: date = start
    while cursor <= end:
        out.append(cursor.isoformat())
        cursor += step
    return out


def _categories(counts: dict[str, int]) -> list[dict]:
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if len(ordered) > MAX_CATEGORIES:
        kept = ordered[: MAX_CATEGORIES - 1]
        rest = sum(count for _, count in ordered[MAX_CATEGORIES - 1:])
        merged = dict(kept)
        merged[OTHER_CATEGORY] = merged.get(OTHER_CATEGORY, 0) + rest
        ordered = sorted(merged.items(), key=lambda item: (-item[1], item[0]))
    return [{"category": name, "count": count} for name, count in ordered]


def edges_from_pairs(
    pairs: Iterable[tuple[str, str, int]], tests_by_signature: dict[str, int]
) -> list[dict]:
    """Jaccard edges over affected-test sets: ``shared / (|A| + |B| - shared)``.

    Pure, so the maths is unit-tested by hand. Pairs whose groups are not both
    known are ignored; a pair below :data:`EDGE_THRESHOLD` is not an edge; the
    :data:`MAX_EDGES` strongest survive (ties by source, then target, by code
    point -- never by locale).
    """
    out: list[tuple[float, str, str]] = []
    for left, right, shared in pairs:
        a, b = tests_by_signature.get(left), tests_by_signature.get(right)
        if a is None or b is None or shared <= 0:
            continue
        union = a + b - shared
        if union <= 0:
            continue
        weight = shared / union
        if weight < EDGE_THRESHOLD:
            continue
        source, target = (left, right) if left < right else (right, left)
        out.append((min(weight, 1.0), source, target))
    out.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [
        {"source": source, "target": target, "weight": round(weight, 4)}
        for weight, source, target in out[:MAX_EDGES]
    ]


def assemble(
    rows: Iterable[Any],
    *,
    days: int,
    now: datetime,
    include_edges: bool,
) -> dict:
    """The response body (without ``meta``) from the statement's rows. Pure."""
    totals = rollup = None
    groups: dict[str, _Group] = {}
    categories: dict[str, dict[str, int]] = {}
    trends: dict[str, dict[str, int]] = {}
    tests: dict[str, list[dict]] = {}
    pairs: list[tuple[str, str, int]] = []
    for row in rows:
        kind = row.part
        if kind == "totals":
            totals = row
        elif kind == "rollup":
            rollup = row
        elif kind == "group":
            groups[row.sig] = _Group(
                rn=int(row.rn), signature=row.sig, label=_label(row.label),
                failure_count=int(row.n1), affected_tests=int(row.n2),
                affected_runs=int(row.n3), distinct_raw_lines=int(row.n4),
                first_seen=row.t1, last_seen=row.t2,
            )
        elif kind == "category":
            categories.setdefault(row.sig, {})[row.k] = int(row.n1)
        elif kind == "trend":
            trends.setdefault(row.sig, {})[row.k] = int(row.n1)
        elif kind == "test":
            tests.setdefault(row.sig, []).append({
                "fingerprint": row.k, "project_id": row.k2,
                "name": row.label or "", "count": int(row.n1),
            })
        elif kind == "pair":
            pairs.append((row.sig, row.k, int(row.n1)))

    total = int(totals.n1) if totals is not None else 0
    no_message = int(totals.n2) if totals is not None else 0
    singleton_groups = int(rollup.n1) if rollup is not None else 0
    singleton_failures = int(rollup.n2) if rollup is not None else 0
    group_total = int(rollup.n3) if rollup is not None else 0
    grouped_failures = int(rollup.n4) if rollup is not None else 0

    axis = trend_axis(days, now)
    on_axis = set(axis)
    ordered = sorted(groups.values(), key=lambda g: g.rn)
    shown_failures = sum(g.failure_count for g in ordered)
    out_groups: list[dict] = []
    nodes: list[dict] = []
    for g in ordered:
        cats = _categories(categories.get(g.signature, {}))
        per_bucket = trends.get(g.signature, {})
        top = sorted(
            tests.get(g.signature, []),
            key=lambda t: (-t["count"], t["fingerprint"], t["project_id"] or ""),
        )
        dominant = cats[0]["category"] if cats else None
        out_groups.append({
            "id": g.signature,
            "signature": g.signature,
            "label": g.label,
            "distinct_raw_lines": g.distinct_raw_lines,
            "failure_count": g.failure_count,
            "affected_tests": g.affected_tests,
            "affected_runs": g.affected_runs,
            "first_seen": _instant(g.first_seen),
            "last_seen": _instant(g.last_seen),
            "share_of_failures": _share(g.failure_count, total),
            "categories": cats,
            "dominant_category": dominant,
            "trend": [{"x": key, "y": per_bucket.get(key, 0)} for key in axis],
            "top_tests": top[:TOP_TESTS],
        })
        node: dict[str, Any] = {"id": g.signature, "label": g.label, "size": g.failure_count}
        if dominant is not None:  # C3: an absent group is omitted, never null
            node["group"] = dominant
        nodes.append(node)

    edges: list[dict] = []
    if include_edges:
        edges = edges_from_pairs(
            pairs, {g.signature: g.affected_tests for g in ordered if g.rn <= EDGE_NODES}
        )

    # A failing run dated after today (a CI agent's clock ahead) is in every
    # count but in no trend bucket. Counted here, as the heatmap counts it, so
    # a sparkline that sums short of failure_count says why (R1-9).
    outside: dict[str, int] = {}
    for g in ordered:
        for key, count in trends.get(g.signature, {}).items():
            if key not in on_axis:
                outside[key] = outside.get(key, 0) + count
    outside_window: Optional[dict] = None
    if outside:
        outside_window = {
            "buckets": len(outside),
            "executions": sum(outside.values()),
            "first": min(outside),
            "last": max(outside),
        }

    omitted_groups = max(group_total - len(ordered), 0)
    omitted_failures = max(grouped_failures - shown_failures, 0)
    return {
        "kind": "graph",
        "nodes": nodes,
        "edges": edges,
        "groups": out_groups,
        "trend_grain": trend_grain(days),
        "total_failures": total,
        "no_message": {
            "id": NO_MESSAGE_ID,
            "failure_count": no_message,
            "affected_tests": int(totals.n3) if totals is not None else 0,
            "affected_runs": int(totals.n4) if totals is not None else 0,
            "share_of_failures": _share(no_message, total),
        },
        "singletons": {
            "id": SINGLETONS_ID,
            "group_count": singleton_groups,
            "failure_count": singleton_failures,
            "share_of_failures": _share(singleton_failures, total),
        },
        "omitted": {
            "group_count": omitted_groups,
            "failure_count": omitted_failures,
            "share_of_failures": _share(omitted_failures, total),
        },
        "truncated": omitted_groups > 0,
        "truncated_total": group_total if omitted_groups > 0 else None,
        "outside_window": outside_window,
        "definitions": definitions(days, include_edges=include_edges),
    }


def definitions(days: int, *, include_edges: bool) -> dict:
    """``meta.definitions``: what the numbers are, and what they are not."""
    return {
        "grain": "execution_row",
        "population": "FAILED and BROKEN executions in scope; passed, skipped and unknown are not counted",
        "window_clock": "test_runs.created_at, UTC; in-progress runs included",
        "group": (
            "executions whose error message has the same signature: the first line, with hex "
            "addresses, long hex ids, timestamps and numbers replaced by '#', lower-cased, 80 characters"
        ),
        "group_identity": "the signature; two different causes can share one ('distinct_raw_lines')",
        "affected_tests": (
            "distinct tests, a test being (project, fingerprint): without a project_id the same "
            "test in two projects is two tests; each top_tests entry names its project_id"
        ),
        "first_seen": (
            "the first failing execution of the group in the window (test_runs.created_at, UTC), "
            "not the first ever: a group failing since before the window starts shows the window"
        ),
        "last_seen": (
            "the last failing execution of the group in the window (test_runs.created_at, UTC), "
            "not necessarily the latest run"
        ),
        "label": f"the most frequent raw first line of the group, <= {LABEL_LENGTH} characters, untrusted text",
        "denominator": "share_of_failures = failures of the group / total_failures (every failing execution in scope)",
        "null": "share_of_failures is null when there is no failing execution in scope",
        "singletons": f"signatures with fewer than {MIN_GROUP_FAILURES} failures, rolled up, not nodes",
        "no_message": "failures with no error text, rolled up, not a node",
        "cap": f"the {MAX_GROUPS} largest groups (failures desc, signature asc); the rest are counted in 'omitted'",
        "trend": (
            "failures per UTC day" if trend_grain(days) == "day"
            else "failures per ISO week (x = the Monday, UTC)"
        ) + "; zero-filled over the window",
        "outside_window": (
            "a failing run dated after the current UTC day (a clock ahead) is counted in "
            "failure_count, the shares and last_seen but falls outside the trend axis; "
            "meta.outside_window counts what the shown groups held there"
        ),
        "edges": (
            f"Jaccard index of affected-test sets among the {EDGE_NODES} largest groups, "
            f">= {EDGE_THRESHOLD}, the {MAX_EDGES} strongest; no positions"
            if include_edges else "not requested (include=edges)"
        ),
        "ai": "deterministic grouping; no model is involved",
    }


def _empty_rows() -> list:
    return []


async def build_failure_groups(
    db: AsyncSession,
    scope: AnalyticsScope,
    *,
    include: frozenset[str],
    now: Optional[datetime] = None,
) -> dict:
    """The response body for ``scope`` (``definitions`` and the envelope keys
    included; the route lifts them into ``meta``)."""
    now = (now or request_clock()).astimezone(timezone.utc)
    include_edges = INCLUDE_EDGES in include
    if scope.denied:
        rows: list = _empty_rows()
    else:
        sql, params = build_statement(scope, include_edges=include_edges, now=now)
        rows = list((await db.execute(scoped_text(sql, params), params)).fetchall())
    return assemble(rows, days=scope.window_days, now=now, include_edges=include_edges)


#: Keys of :func:`assemble`'s result that belong in the envelope, not the body.
ENVELOPE_KEYS = ("truncated", "truncated_total", "outside_window")


__all__ = [
    "DAILY_TREND_MAX_DAYS",
    "EDGE_NODES",
    "EDGE_THRESHOLD",
    "ENVELOPE_KEYS",
    "INCLUDE_TOKENS",
    "LABEL_LENGTH",
    "MAX_CATEGORIES",
    "MAX_EDGES",
    "MAX_GROUPS",
    "MIN_GROUP_FAILURES",
    "TOP_TESTS",
    "assemble",
    "build_failure_groups",
    "build_statement",
    "definitions",
    "edges_from_pairs",
    "parse_include",
    "request_clock",
    "trend_axis",
    "trend_grain",
]
