"""Regression guard: the suite-filtered metrics query must COMPILE.

The defect (F-080 follow-on)
----------------------------
`/api/v1/metrics/summary?suite_name=<x>` returned **HTTP 500** for every suite,
on the live deployment, for a week after the F-080 fix (#588, 2026-08-14)::

    sqlalchemy.exc.InvalidRequestError: Select statement '...' returned no FROM
    clauses due to auto-correlation; specify correlate(<tables>) to control
    correlation manually.

`_period_stats` built its EXISTS clauses as a bare ``exists().where(...)``. A
bare ``exists()`` has no FROM of its own, so SQLAlchemy auto-correlates *every*
table the clause mentions — here both ``TestCase`` and ``TestRun`` — to the
enclosing query, leaving the subquery with nothing to select from.

The history matters, because this path has now failed three ways:

* before 2026-05-15 — INNER JOIN on ``test_cases`` returned **0** for
  live-stream runs and blanked the dashboard
* 2026-05-15 to 2026-08-14 — run-level aggregate sums returned the **whole
  run's** totals under every suite (60/60/60 where truth was 25/20/15)
* 2026-08-14 to 2026-08-21 — **HTTP 500**

An in-code comment right above the return already documents a *separate* 500 on
this same endpoint from 2026-08-08 (a missing ``total_executions`` key). Picking
a suite on the Overview page has taken the dashboard down more than once.

Why this guard compiles rather than mocks
-----------------------------------------
A mocked session never compiles a statement, so it cannot see this class of
bug at all — the failure is in SQL construction, not in the DB. This guard
compiles the real statement and asserts it has a FROM clause. No database
required, and it would have caught the 500 before deploy.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

pytestmark = pytest.mark.regression


def _compile(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_the_mechanism_needs_the_whole_query_shape():
    """A bare ``exists()`` is not universally broken — the shape matters.

    My first version of this guard asserted that
    ``select(count(TestRun.id)).where(~exists().where(TestCase.x == TestRun.y))``
    fails to compile. It does not: SQLAlchemy resolves that fine. The failure
    needs the production shape — a bare EXISTS over TestCase+TestRun living in
    the shared ``conditions`` list, applied to a query whose only FROM is
    TestRun, alongside a second such EXISTS. Reverting just one of the two is
    not enough to reproduce it either; that was measured.

    So the real guard is
    ``test_period_stats_suite_branch_compiles_every_statement`` below, which
    drives the actual function. This test exists to record that the simple
    reproduction does NOT work, so nobody re-adds it believing it proves
    something.
    """
    from sqlalchemy import exists

    from app.models.postgres import TestCase, TestRun

    simple = select(func.count(TestRun.id)).where(
        ~exists().where(TestCase.test_run_id == TestRun.id)
    )
    sql = _compile(simple)          # compiles happily
    assert "EXISTS" in sql.upper(), sql


def test_the_corrected_exists_compiles_with_a_from_clause():
    """The fixed shape: explicit FROM, correlate only the outer table."""
    from app.models.postgres import TestCase, TestRun

    good = select(func.count(TestRun.id)).where(
        ~(
            select(TestCase.id)
            .where(TestCase.test_run_id == TestRun.id)
            .correlate(TestRun)
            .exists()
        )
    )
    sql = _compile(good)
    assert "FROM test_cases" in sql, (
        "the EXISTS subquery has no FROM of its own, which is exactly the "
        f"auto-correlation bug:\n{sql}"
    )


@pytest.mark.asyncio
async def test_period_stats_suite_branch_compiles_every_statement():
    """Drive the real function and compile everything it builds.

    This is the check that would have caught the live 500. A fake session
    compiles each statement instead of executing it, so the guard needs no
    database but still exercises the actual query construction.
    """
    import uuid

    from app.services import metrics_service

    compiled: list[str] = []

    class _Row:
        total_runs = 0
        avg_duration_ms = 0
        passed = 0
        failed = 0
        broken = 0
        total = 0

    class _Result:
        def one(self):
            return _Row()

    class _CompilingSession:
        async def execute(self, stmt):
            # Compiling is the point: a construction bug raises HERE, which is
            # precisely what a mocked ``db.execute`` would have swallowed.
            compiled.append(_compile(stmt))
            return _Result()

    out = await metrics_service._period_stats(
        _CompilingSession(),
        uuid.uuid4(),
        __import__("datetime").datetime(2026, 1, 1),
        __import__("datetime").datetime(2026, 12, 31),
        "api",
    )

    assert compiled, "no statements were compiled - the guard proved nothing"
    for sql in compiled:
        assert "FROM" in sql.upper(), f"statement has no FROM clause:\n{sql}"
    # The contract the caller depends on (a missing key here 500'd the
    # dashboard once already, on 2026-08-08).
    for key in ("total_runs", "total_executions", "pass_rate", "avg_duration_ms"):
        assert key in out, (
            f"the suite branch dropped {key!r}; the caller reads it "
            "unconditionally and a KeyError here is an HTTP 500."
        )
