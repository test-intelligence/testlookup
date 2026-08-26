from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import any_, distinct, func, or_, select, text
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


def build_search_filters(
    q: str,
    project_id: str | None,
    status: str | None,
    days: int | None,
    allowed_project_ids: Iterable[uuid.UUID] | None = None,
):
    pattern = like_contains(q)
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
    if len(q.strip()) >= MIN_INDEXED_TERM_LEN:
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
    if project_id:
        # Single-project pin (access already verified by the router).
        filters.append(TestRun.project_id == project_id)
    elif allowed_project_ids is not None:
        # Non-admin fan-out across the user's accessible projects. Empty set
        # means "no memberships" → we inject a guaranteed-false predicate so
        # the query returns zero rows without a database round-trip.
        allowed = list(allowed_project_ids)
        if not allowed:
            filters.append(TestRun.project_id.in_([]))
        else:
            filters.append(TestRun.project_id.in_(allowed))
    if status:
        filters.append(TestCase.status == status.upper())
    if days:
        filters.append(TestCase.created_at >= datetime.now(timezone.utc) - timedelta(days=days))
    return filters


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
    # Correlated subquery for failure_count — scoped to the *same project* as
    # the matched test case. The previous implementation used an unscoped join
    # on ``test_fingerprint`` which leaked failure aggregates across tenants.
    history_case = aliased(TestCase, name="history_case")
    history_run = aliased(TestRun, name="history_run")

    filters = build_search_filters(q, project_id, status, days, allowed_project_ids)

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
        .offset((page - 1) * size)
        .limit(size)
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
    rows = (await db.execute(query)).all()
    total = (await db.execute(count_query)).scalar() or 0
    items = []
    for row in rows:
        item = dict(row._mapping)
        item["match_reasons"] = ["Keyword match on test name or error message"]
        item["source_mode_used"] = "keyword"
        item["relevance_score"] = 1.0
        items.append(item)
    return items, total, -(-total // size)
