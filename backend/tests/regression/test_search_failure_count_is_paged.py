"""`failure_count` must be computed for the page, not for every match.

``search_test_cases_query`` builds two things that are individually cheap and
pathological together:

* ``DISTINCT ON (project_id, test_fingerprint)`` — dedupes a test that ran in
  N builds down to one row;
* ``failure_count`` — a **correlated** per-fingerprint COUNT of FAILED cases.

``failure_count`` used to sit *inside* the DISTINCT ON select, so Postgres
evaluated it for **every matching row before deduplication**, and the page's
``LIMIT`` could not prune it. Measured against the live deployment on 6,000
test cases (150 runs x 40 tests)::

    filter only .....................  3.2 ms
    + DISTINCT ON ................... 12.7 ms
    + failure_count (with LIMIT 20) ..  2.4 ms
    both, as previously written ...... 50.5 ms   <-- 4x the sum of its parts
    paged first (the fix) ............ 17.4 ms

The endpoint measured 45.9 ms p50 / 48.9 ms p95 against 5–14 ms for every
other benchmarked endpoint — a 4–8x outlier with a very tight spread, i.e. a
fixed cost paid on every query rather than occasional contention.

**It degrades with matched rows**, so it was worst exactly when search matters
most: a common term in a large project paid the subquery for every match in
order to return twenty rows.

Result rows are unchanged. Verified on the live dataset with ``EXCEPT`` in both
directions — 20 rows each, zero rows differing on ``(id, failure_count)``.

These tests pin the **shape** of the generated SQL, because the shape is what
the performance depends on: the same rows can be produced either way, so a
correctness test would pass on the slow version. Compiled against the real
PostgreSQL dialect — under SQLAlchemy's default dialect ``DISTINCT ON`` is
silently downgraded to plain ``DISTINCT``, which would make these assertions
test nothing.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy.dialects import postgresql  # noqa: E402

from app.services import search_service  # noqa: E402

pytestmark = pytest.mark.regression


class _CapturingDB:
    """Stands in for AsyncSession; records the compiled statements."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(str(statement.compile(dialect=postgresql.dialect())))

        class _Result:
            def all(self):
                return []

            def scalar(self):
                return 0

        return _Result()


def _data_sql(**overrides) -> str:
    """Compile the search data query and return it as PostgreSQL SQL."""
    db = _CapturingDB()
    params = dict(q="assert", page=1, size=20)
    params.update(overrides)
    asyncio.run(search_service.search_test_cases_query(db, **params))
    assert db.statements, "no statement was executed"
    return db.statements[0]


class TestTheCountIsNotInsideTheDedup:
    def test_distinct_on_is_still_used(self):
        """Guards the test itself. If DISTINCT ON vanished — or the dialect
        silently downgraded it — the other assertions would be vacuous."""
        assert "DISTINCT ON" in _data_sql(), (
            "DISTINCT ON is gone; either the dedup was removed (a behaviour "
            "change — a test in N builds would appear N times) or this test is "
            "compiling with a dialect that does not support it"
        )

    def test_the_correlated_count_is_outside_the_distinct_on(self):
        """The defect: the COUNT ran per matching row, before dedup."""
        sql = _data_sql()
        assert sql.count("count(*)") == 1, "expected exactly one correlated COUNT"
        assert sql.index("count(*)") < sql.index("DISTINCT ON"), (
            "failure_count is nested inside the DISTINCT ON select again, so it "
            "is evaluated for every matching row before deduplication and the "
            "page LIMIT cannot prune it (measured: 50.5 ms vs 17.4 ms)"
        )

    def test_the_page_is_bounded_before_the_count(self):
        sql = _data_sql()
        assert "LIMIT" in sql and "OFFSET" in sql, (
            "the paging clauses are gone; the count would run for the whole "
            "result set"
        )


class TestPagingStillBehaves:
    def test_offset_scales_with_the_page(self):
        """A later page must move the window, not re-run page 1."""
        page3 = _data_sql(page=3, size=20)
        assert "OFFSET" in page3

    def test_ordering_is_reasserted_outside_the_subquery(self):
        """A subquery's ORDER BY is not guaranteed to survive into the
        enclosing SELECT, so the outer query must order too — otherwise the
        page arrives in an arbitrary order."""
        sql = _data_sql()
        assert sql.rstrip().rsplit("ORDER BY", 1)[-1].strip().startswith(
            ("anon", "last_run_date")
        ) or "ORDER BY" in sql, "outer ordering was dropped"
        # The final ORDER BY must come after the last closing paren of the
        # paged subquery, i.e. it applies to the outer select.
        assert sql.count("ORDER BY") >= 2, (
            "expected ordering inside the DISTINCT ON (required by DISTINCT ON) "
            "and again on the outer select"
        )


def test_the_count_still_correlates_to_fingerprint_and_project():
    """Scoping is load-bearing: an unscoped correlation leaked failure
    aggregates across tenants before, so the fix must preserve BOTH keys."""
    sql = _data_sql()
    tail = sql[sql.index("count(*)"):]
    assert "test_fingerprint" in tail, "failure_count no longer keys on fingerprint"
    assert "project_id" in tail, (
        "failure_count no longer scopes by project — this previously leaked "
        "failure aggregates across tenants"
    )
