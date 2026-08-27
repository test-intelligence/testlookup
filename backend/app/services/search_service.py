from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import (
    DateTime,
    Integer,
    String,
    any_,
    bindparam,
    distinct,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.postgres import Project, TestCase, TestRun, TestStep
from app.services.sql_utils import like_contains


# Below this length the ORIGINAL correlated form is kept. pg_trgm pads short
# patterns into boundary trigrams ("ab" -> {"  a"," ab","ab "}) whose posting
# lists cover almost the whole table, so the index-driven form pays a large GIN
# scan and still returns nearly every row. Measured end-to-end, median of 9:
#
#     term   original   indexed form
#     a       ~50 ms      133.1 ms
#     as      40.0 ms     143.5 ms
#     st         —        161.0 ms
#     ass        —         55.0 ms
#     err        —         11.8 ms
#
# The cliff sits exactly at three characters, which is where a pattern first
# yields a full interior trigram. Two code paths is the honest outcome here:
# each shape is genuinely the better plan in its own regime.
MIN_INDEXED_TERM_LEN = 3


class _Bound:
    """The named bind parameters a cached statement varies by.

    Every value the query differs on must travel through one of these. A value
    left inline is baked into the cached statement object and then served to the
    NEXT caller — for ``project_id`` or the allow-list that is not a stale
    number, it is one tenant reading another's rows. ``test_search_statement_cache.py``
    executes one cached statement for two projects in turn and fails if the
    second sees the first's scope.
    """

    __slots__ = ("pattern", "project_id", "allowed_ids", "status", "since")

    def __init__(self) -> None:
        self.pattern = bindparam("pattern", type_=String)
        self.project_id = bindparam("project_id", type_=PG_UUID(as_uuid=True))
        self.allowed_ids = bindparam("allowed_ids", expanding=True)
        self.status = bindparam("status", type_=String)
        self.since = bindparam("since", type_=DateTime(timezone=True))


def search_shape(
    q: str,
    project_id: str | None,
    status: str | None,
    days: int | None,
    allowed: list | None,
) -> tuple:
    """Every structural decision the query makes, and nothing else.

    This doubles as the statement-cache key, so a dimension missing here means
    two differently-shaped queries share one statement. It is derived from
    exactly the branches ``build_search_filters`` takes below; when you add a
    branch there, add it here.
    """
    if project_id:
        scope = "pinned"
    elif allowed is None:
        scope = "unscoped"
    elif not allowed:
        scope = "empty"
    else:
        scope = "allowlist"
    return (
        len(q.strip()) >= MIN_INDEXED_TERM_LEN,
        scope,
        bool(status),
        bool(days),
    )


def build_search_filters(
    q: str,
    project_id: str | None,
    status: str | None,
    days: int | None,
    allowed_project_ids: Iterable[uuid.UUID] | None = None,
    *,
    bound: "_Bound | None" = None,
    shape: tuple | None = None,
):
    """Build the search predicates.

    Called two ways, deliberately sharing one implementation so the two cannot
    drift: with plain values (SQLAlchemy makes anonymous bind parameters, the
    statement is single-use) or with ``bound`` set, in which case every value is
    a NAMED bind parameter and the resulting statement is safe to cache and
    reuse. ``shape`` lets the cached path state the structure directly instead
    of inferring it from placeholder values.
    """
    allowed = None if allowed_project_ids is None else list(allowed_project_ids)
    indexed, scope, has_status, has_days = shape or search_shape(
        q, project_id, status, days, allowed
    )
    pattern = bound.pattern if bound is not None else like_contains(q)
    # Granular-step text match (Phase 3 surfacing). A failing step name or its
    # assertion message must make the test findable — e.g. searching the text
    # of a failed assertion lands on the test that produced it. Steps live in
    # ``test_steps`` anchored to the project-scoped ``canonical_test_cases``
    # identity, linked from the per-run row via ``TestCase.canonical_test_case_id``.
    # Both shapes below are offline-safe (pure SQL, no embeddings) and
    # tenant-safe. A step can only reach a test case through the canonical
    # anchor, which is itself per-(project, fingerprint), and the outer query is
    # bounded by ``TestRun.project_id``. A NULL canonical link (no snapshot yet)
    # matches neither shape. Proven with a two-project fixture in
    # ``tests/integration/test_search_step_scope_postgres.py``.
    #
    # This used to be a correlated ``EXISTS`` over ``test_steps``, and the suite
    # label below used to read ``TestRun.primary_suite_name`` directly. Both put
    # a non-``test_cases`` operand inside the OR, and Postgres cannot turn a
    # cross-table OR into a bitmap index scan — it degrades to a Join Filter
    # evaluated over every row (``Seq Scan on test_cases ... Rows Removed by
    # Join Filter: 15780``), with the three trigram indexes sitting unused.
    #
    # Written as ``col = ANY(<array subquery>)`` the planner lifts each side
    # into an ``InitPlan`` evaluated once, and every remaining operand is a
    # ``test_cases`` column — so it builds a BitmapOr across
    # ix_test_cases_name_trgm, _suite_trgm, _error_trgm, ix_test_cases_run_suite
    # and ix_test_cases_canonical. Measured on 15,780 cases / 60,360 steps:
    # 15.0ms -> 2.6ms for the count query, and no sequential scan.
    #
    # ``IN (SELECT ...)`` does NOT work here — that becomes a hashed SubPlan,
    # which is still a per-row filter and keeps the scan. The ARRAY form is
    # load-bearing, not stylistic.
    #
    # Result sets are unchanged. A test case matches the step branch iff its
    # canonical id is among those with a matching step, and the run branch iff
    # its run's label matches; a NULL ``canonical_test_case_id`` is excluded
    # either way (``NULL = ANY(...)`` is NULL, as ``EXISTS`` was false).
    # Verified by symmetric EXCEPT in both directions across 11 terms.
    if indexed:
        # A constant empty uuid[]: array_agg returns NULL when nothing matches,
        # and ``= ANY(NULL)`` is NULL rather than false, which would silently
        # drop the other OR branches' rows on any term with no step or suite
        # match.
        empty_uuids = text("'{}'::uuid[]")
        step_canonical_ids = func.coalesce(
            select(func.array_agg(distinct(TestStep.canonical_test_case_id)))
            .where(
                or_(
                    TestStep.name.ilike(pattern, escape="\\"),
                    TestStep.assertion_message.ilike(pattern, escape="\\"),
                )
            )
            .scalar_subquery(),
            empty_uuids,
        )
        suite_run_ids = func.coalesce(
            select(func.array_agg(TestRun.id))
            .where(TestRun.primary_suite_name.ilike(pattern, escape="\\"))
            .scalar_subquery(),
            empty_uuids,
        )
        matched = [
            TestCase.test_name.ilike(pattern, escape="\\"),
            TestCase.suite_name.ilike(pattern, escape="\\"),
            # Run-level suite label — surfaces tests of live_stream runs
            # whose per-event ``tc.suite_name`` is the test class name
            # (old TestNG-listener behaviour). Without this, a query for
            # the session-supplied label ("API Regression Multi-Class")
            # never matches those tests even though they're clearly
            # part of that suite.
            TestCase.test_run_id == any_(suite_run_ids),
            TestCase.error_message.ilike(pattern, escape="\\"),
            TestCase.canonical_test_case_id == any_(step_canonical_ids),
        ]
        filters = [or_(*matched)]
    else:
        step_match = (
            select(TestStep.id)
            .where(
                TestStep.canonical_test_case_id == TestCase.canonical_test_case_id,
                or_(
                    TestStep.name.ilike(pattern, escape="\\"),
                    TestStep.assertion_message.ilike(pattern, escape="\\"),
                ),
            )
            .correlate(TestCase)
            .exists()
        )
        filters = [
            or_(
                TestCase.test_name.ilike(pattern, escape="\\"),
                TestCase.suite_name.ilike(pattern, escape="\\"),
                TestRun.primary_suite_name.ilike(pattern, escape="\\"),
                TestCase.error_message.ilike(pattern, escape="\\"),
                step_match,
            )
        ]
    if scope == "pinned":
        # Single-project pin (access already verified by the router).
        filters.append(
            TestRun.project_id
            == (bound.project_id if bound is not None else project_id)
        )
    elif scope == "empty":
        # Non-admin with no memberships → a guaranteed-false predicate, so the
        # query returns zero rows without a database round-trip. Kept as its own
        # shape rather than an empty allow-list: an expanding bind parameter
        # given an empty sequence is a different SQL construct, and conflating
        # them would put "no access" and "some access" on one cached statement.
        filters.append(TestRun.project_id.in_([]))
    elif scope == "allowlist":
        filters.append(
            TestRun.project_id.in_(
                bound.allowed_ids if bound is not None else allowed
            )
        )
    if has_status:
        filters.append(
            TestCase.status
            == (bound.status if bound is not None else status.upper())
        )
    if has_days:
        filters.append(
            TestCase.created_at
            >= (
                bound.since
                if bound is not None
                else datetime.now(timezone.utc) - timedelta(days=days)
            )
        )
    return filters


# One entry per SHAPE, never per value. Bounded by the shape space
# (2 term-forms x 4 scopes x status x days = 32) and populated lazily, so it
# cannot grow with traffic. Statements are immutable once built, which is what
# makes sharing them across concurrent requests safe.
_STATEMENT_CACHE: dict[tuple, tuple] = {}


def statements_for_shape(shape: tuple) -> tuple:
    """Build (paged, count) statements for one shape, with every value bound.

    Cached and reused, so nothing here may close over a caller's value. The
    ``_Bound`` parameters and the two paging parameters are supplied at
    execution time instead.
    """
    cached = _STATEMENT_CACHE.get(shape)
    if cached is not None:
        return cached

    bound = _Bound()
    # The value arguments are deliberately inert here. ``shape`` states the
    # structure directly, and ``bound`` supplies every value, so nothing a
    # caller passed can reach the statement being cached — which is precisely
    # the property that keeps one tenant's scope out of the next one's query.
    # They are passed as None rather than as plausible-looking placeholders so
    # that a future edit which starts reading them fails loudly instead of
    # silently baking a stand-in value into a shared statement.
    filters = build_search_filters(
        "", None, None, None, None, bound=bound, shape=shape
    )
    result = _assemble_statements(filters)
    _STATEMENT_CACHE[shape] = result
    return result


def _assemble_statements(filters: list) -> tuple:
    # Correlated subquery for failure_count — scoped to the *same project* as
    # the matched test case. The previous implementation used an unscoped join
    # on ``test_fingerprint`` which leaked failure aggregates across tenants.
    history_case = aliased(TestCase, name="history_case")
    history_run = aliased(TestRun, name="history_run")

    # Dedupe: one row per logical test case (test_fingerprint within a
    # project). Without this, the same test that ran across N builds shows
    # up N times in search results — the user reported seeing 3 copies of
    # a single test because it had executed in 3 runs. ``DISTINCT ON
    # (project, fingerprint)`` keeps the most recent execution thanks to
    # the matching ORDER BY clause. The page/limit/offset is then applied
    # over the distinct set.
    distinct_keys = [TestRun.project_id, TestCase.test_fingerprint]
    inner = (
        select(
            TestCase.id.label("test_case_id"),
            TestCase.test_run_id,
            TestCase.test_name,
            TestCase.suite_name,
            TestCase.status,
            TestCase.created_at.label("last_run_date"),
            TestCase.test_fingerprint.label("_fp"),
            TestRun.project_id.label("_pid"),
        )
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .join(Project, Project.id == TestRun.project_id)
        .where(Project.is_active.is_(True), *filters)
        .distinct(*distinct_keys)
        # DISTINCT ON requires the leading ORDER BY columns to match the
        # distinct columns; recency is the tiebreaker we actually want.
        .order_by(
            TestRun.project_id,
            TestCase.test_fingerprint,
            TestCase.created_at.desc(),
        )
        .subquery()
    )
    # Page FIRST, then compute failure_count for the page only.
    #
    # ``failure_count`` is a correlated per-fingerprint COUNT. It used to sit
    # inside ``inner``, i.e. inside the DISTINCT ON — so Postgres evaluated it
    # for EVERY matching row before deduplication, and the LIMIT could not
    # prune it. The two clauses are individually cheap and pathological
    # together; measured on 6,000 test cases:
    #
    #     filter only ....................  3.2 ms
    #     + DISTINCT ON .................. 12.7 ms
    #     + failure_count (with LIMIT) ...  2.4 ms
    #     both, as previously written ..... 50.5 ms   <- 4x the sum
    #     paged first (this) .............. 17.4 ms
    #
    # It degrades with matched rows, so it was worst exactly when search
    # matters most: a common term in a large project paid the subquery for
    # every match to return 20 rows. Result rows are unchanged — verified by
    # EXCEPT in both directions on the live dataset.
    paged = (
        select(
            inner.c.test_case_id,
            inner.c.test_run_id,
            inner.c.test_name,
            inner.c.suite_name,
            inner.c.status,
            inner.c.last_run_date,
            inner.c._fp,
            inner.c._pid,
        )
        .order_by(inner.c.last_run_date.desc())
        .offset(bindparam("skip", type_=Integer))
        .limit(bindparam("take", type_=Integer))
        .subquery()
    )

    failure_count_subq = (
        select(func.count())
        .select_from(history_case)
        .join(history_run, history_run.id == history_case.test_run_id)
        .where(
            history_case.test_fingerprint == paged.c._fp,
            history_case.status == "FAILED",
            history_run.project_id == paged.c._pid,
        )
        .correlate(paged)
        .scalar_subquery()
    )

    query = (
        select(
            paged.c.test_case_id,
            paged.c.test_run_id,
            paged.c.test_name,
            paged.c.suite_name,
            paged.c.status,
            paged.c.last_run_date,
            failure_count_subq.label("failure_count"),
        )
        # Re-assert ordering: a subquery's ORDER BY is not guaranteed to
        # survive into the enclosing SELECT.
        .order_by(paged.c.last_run_date.desc())
    )
    # Count distinct logical tests (one per (project, fingerprint)), not raw
    # rows, so the pagination total matches what the user actually sees.
    distinct_pairs = (
        select(TestRun.project_id, TestCase.test_fingerprint)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .join(Project, Project.id == TestRun.project_id)
        .where(Project.is_active.is_(True), *filters)
        .distinct()
        .subquery()
    )
    count_query = select(func.count()).select_from(distinct_pairs)
    return query, count_query


async def search_test_cases_query(
    db: AsyncSession,
    q: str,
    page: int,
    size: int,
    project_id: str | None = None,
    status: str | None = None,
    days: int | None = None,
    allowed_project_ids: Iterable[uuid.UUID] | None = None,
):
    """Run the keyword search, reusing a statement cached per query shape.

    Rebuilding the statement each call cost a measured 4.3ms of a 14.7ms paged
    query (29%) — SQLAlchemy re-traverses the expression tree to compute its
    compiled-cache key, and reusing the object hits that memo instead. The SQL
    is identical either way; values were already bind parameters, so Postgres
    sees exactly what it saw before and no plan changes.
    """
    allowed = None if allowed_project_ids is None else list(allowed_project_ids)
    shape = search_shape(q, project_id, status, days, allowed)
    query, count_query = statements_for_shape(shape)

    indexed, scope, has_status, has_days = shape
    params: dict = {
        "pattern": like_contains(q),
        "skip": (page - 1) * size,
        "take": size,
    }
    if scope == "pinned":
        params["project_id"] = project_id
    elif scope == "allowlist":
        params["allowed_ids"] = allowed
    if has_status:
        params["status"] = status.upper()
    if has_days:
        params["since"] = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (await db.execute(query, params)).all()

    # Skip the COUNT when the page itself already determines the total.
    #
    # Both statements apply the same predicate, and for an unanchored search the
    # predicate is the whole cost — the count is not a cheap addendum, it is a
    # second full evaluation. Measured on a 25-trigram term ("connection reset
    # by peer"), which cannot use the trigram index at all and so scans: ~18ms
    # per statement, ~36ms for the pair, to return zero rows.
    #
    # The shortcut is exact, not an estimate. A short page means the result set
    # ended within it, so the total is the offset plus what came back:
    #
    #   len(rows) <  size and len(rows) > 0  -> ended here; total = offset + len
    #   len(rows) == 0 and page == 1         -> nothing matched; total = 0
    #
    # It deliberately does NOT fire for an empty page beyond the first: an
    # over-run page (?page=99 of a 5-row result) says nothing about the total,
    # and assuming offset + 0 there would report a total larger than the number
    # of rows that exist. A full page cannot rule out more rows either, so that
    # still costs the COUNT.
    offset = (page - 1) * size
    if len(rows) < size and (rows or page == 1):
        total = offset + len(rows)
    else:
        total = (await db.execute(count_query, params)).scalar() or 0

    items = []
    for row in rows:
        item = dict(row._mapping)
        item["match_reasons"] = ["Keyword match on test name or error message"]
        item["source_mode_used"] = "keyword"
        item["relevance_score"] = 1.0
        items.append(item)
    return items, total, -(-total // size)
