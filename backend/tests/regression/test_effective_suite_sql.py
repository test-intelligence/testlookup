"""Regression: a test could surface under the wrong suite, and tests of
live_stream runs were invisible when filtering/searching by the
session-supplied suite label.

Two related 2026-05-19/20 bugs, both rooted in "which suite does this
test belong to?":

  1. (2026-05-20) ``analytics_service._suite_filter_sql`` used a loose
     OR — ``tc.suite_name = :s OR tr.primary_suite_name = :s`` — which
     over-returned: a multi-suite run whose ``primary_suite_name``
     matched leaked EVERY test of that run, so an Order test surfaced
     under the "Smoke" suite. The fix introduces a single *effective
     suite* expression (``_effective_suite_sql``) used for BOTH the
     filter and the GROUP BY so attribution can never drift, and the
     filter became equality on that effective suite, not an OR.

  2. (2026-05-19) Search (``search_service.build_search_filters`` and
     ``global_search_service``) couldn't find tests of live_stream
     runs by their session label, because old TestNG listeners stamp
     the test *class name* on every per-event ``tc.suite_name`` while
     the authoritative label lives on ``tr.primary_suite_name``. The
     fix adds ``TestRun.primary_suite_name`` to the search OR.

See ``effective-suite-query-pattern`` in memory for the full list of
call sites this convention now spans.

These are deliberately string/structure-level pins (the underlying
SQL needs a live Postgres to execute) so they run in the unit suite
and fail loudly if the effective-suite contract is reverted.
"""
from __future__ import annotations

import pytest


# ── analytics_service: the effective-suite expression ──────────────────────


def test_effective_suite_prefers_primary_for_live_stream():
    """The expression must COALESCE a live_stream-only
    ``primary_suite_name`` ahead of the per-event ``tc.suite_name`` so
    live runs bucket under their session label, and fall through to
    ``tc.suite_name`` for file uploads."""
    from app.services.analytics_service import _effective_suite_sql

    sql = _effective_suite_sql()
    norm = " ".join(sql.split())  # collapse whitespace for robust matching

    assert "COALESCE(" in norm
    assert "tr.trigger_source = 'live_stream'" in norm
    # live_stream branch reads the run-level label …
    assert "NULLIF(TRIM(tr.primary_suite_name), '')" in norm
    # … and the fall-through is the per-event suite.
    assert "NULLIF(TRIM(tc.suite_name), '')" in norm
    # primary_suite_name must appear BEFORE tc.suite_name (preference order).
    assert norm.index("primary_suite_name") < norm.index("tc.suite_name")


def test_suite_filter_uses_equality_on_effective_suite_not_loose_or():
    """The bug was a loose OR that over-returned. The filter must now be
    equality on the effective suite, lowercased, bound to
    ``:suite_name``."""
    from app.services.analytics_service import _suite_filter_sql

    flt = _suite_filter_sql()
    norm = " ".join(flt.split())

    assert norm.startswith("AND LOWER(")
    assert "= :suite_name" in norm
    # The exact loose-OR shape that leaked cross-suite rows must be gone.
    assert "OR tr.primary_suite_name" not in norm
    assert "OR tc.suite_name" not in norm


def test_filter_and_grouping_share_one_expression():
    """The whole point of factoring out ``_effective_suite_sql`` is that
    the filter and the GROUP BY can never disagree about a test's
    suite. Pin that the filter is built FROM the shared expression."""
    from app.services.analytics_service import (
        _effective_suite_sql,
        _suite_filter_sql,
    )

    assert _effective_suite_sql() in _suite_filter_sql()


@pytest.mark.asyncio
async def test_suite_detail_matches_on_effective_suite_not_loose_or():
    """``suite_detail`` (/coverage/suite) must use equality on the effective
    suite — the same rule ``coverage_stats`` (/coverage) uses — so the two
    surfaces agree. The loose OR (``tc.suite_name = :s OR
    tr.primary_suite_name = :s``) over-returned every test of a multi-suite
    run whose ``primary_suite_name`` matched; it must be gone from all three
    suite_detail queries (summary/cases/runs)."""
    import uuid

    from app.services import analytics_service as svc
    from app.services.analytics_service import _effective_suite_sql

    class _Row:
        def __init__(self, mapping):
            self._mapping = mapping

    class _Result:
        def __init__(self, *, one_row=None, rows=None):
            self._one = one_row
            self._rows = rows or []

        def one(self):
            return self._one

        def fetchall(self):
            return self._rows

    class _FakeDB:
        def __init__(self):
            self.sqls: list[str] = []

        async def execute(self, stmt, params=None):
            self.sqls.append(str(stmt))
            # First query is the summary (.one()); return a non-empty total so
            # the run-level fallback path is skipped. The rest use .fetchall().
            if len(self.sqls) == 1:
                return _Result(one_row=_Row({"total_executions": 5}))
            return _Result(rows=[])

    db = _FakeDB()
    await svc.suite_detail(
        db, project_id=str(uuid.uuid4()), suite_name="Smoke", days=7,
        allowed_project_ids=None,
    )

    eff = _effective_suite_sql()
    # summary + cases + runs queries (no fallback fired).
    assert len(db.sqls) == 3
    for sql in db.sqls:
        assert eff in sql, "suite_detail query must match on the effective suite"
        # The exact over-returning OR fragment must be gone.
        assert "OR LOWER(TRIM(COALESCE(tr.primary_suite_name" not in sql


# ── search_service: primary_suite_name in the search OR ────────────────────


def test_build_search_filters_matches_run_level_suite_label():
    """A query for the session-supplied label ("API Regression
    Multi-Class") must be able to match tests of live_stream runs whose
    per-event ``tc.suite_name`` is the test class name — so
    ``TestRun.primary_suite_name`` has to be in the search OR alongside
    the existing test_name / suite_name / error_message clauses."""
    from app.services.search_service import build_search_filters

    filters = build_search_filters(
        "API Regression", project_id=None, status=None, days=None,
        allowed_project_ids=None,
    )
    # First clause is the keyword OR.
    or_sql = str(filters[0])
    assert "primary_suite_name" in or_sql
    # The pre-existing match columns must still be present (the fix is
    # additive — it must not drop any existing search surface).
    for col in ("test_name", "suite_name", "error_message"):
        assert col in or_sql


def test_build_search_filters_empty_membership_injects_false_predicate():
    """Pre-existing tenant guard, re-pinned alongside the OR change:
    a caller with zero memberships gets a guaranteed-empty result via an
    ``IN ([])`` predicate rather than leaking cross-tenant rows."""
    from app.services.search_service import build_search_filters

    filters = build_search_filters(
        "anything", project_id=None, status=None, days=None,
        allowed_project_ids=[],
    )
    # OR clause + the empty-membership guard.
    assert len(filters) == 2


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
