"""Test-case search must stay in a shape the trigram indexes can serve.

Re-audit H9. Keyword search over test cases was one OR across five
predicates, and two of them each defeated the three trigram indexes on
``test_cases`` -- measured on the homelab against 50,510 rows (plans in
migration 0166):

* ``TestRun.primary_suite_name`` sat inside the same OR. A bitmap OR only
  combines indexes on one table, so a branch on another table forced the whole
  OR to be checked row by row. Removing the tags branch alone left the plan
  identical.
* ``CAST(tags AS VARCHAR) ILIKE`` had no index, so even a one-table OR
  collapsed.

The fix splits the run-level branch into its own half of a UNION and casts tags
to TEXT, matching ``ix_test_cases_tags_trgm`` exactly.

Every property here is invisible to a functional test: results are identical
either way, and only the plan changes. So these assert on the SQL Postgres
receives, the same approach as ``test_search_filter_uses_step_columns.py``.
The index itself, and the planner's ability to use it, are pinned against a
real database in ``tests/integration/test_search_tags_index_postgres.py``.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("app.services.global_search_service")

from sqlalchemy.dialects import postgresql  # noqa: E402

from app.services import global_search_service  # noqa: E402

pytestmark = pytest.mark.regression

PROJECT = str(uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
OTHER = str(uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))


def _sql(**kwargs) -> str:
    captured = {}

    class _DB:
        async def execute(self, stmt):
            captured["stmt"] = stmt

            class _R:
                def all(self):
                    return []

            return _R()

    params = {"project_id": None, "period_start": None}
    params.update(kwargs)
    asyncio.run(
        global_search_service._search_test_cases(
            _DB(),
            "zqxjneedle",
            params.pop("project_id"),
            params.pop("period_start"),
            **params,
        )
    )
    return str(
        captured["stmt"].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).lower()


def _where_clauses(sql: str) -> list[str]:
    """Each WHERE clause, up to the ORDER BY that closes its half."""
    return [m.group(1) for m in re.finditer(r"where (.+?) order by", sql, re.S)]


# ── The tags branch can use its index ────────────────────────────────────


def test_tags_are_cast_to_text_to_match_the_index():
    sql = _sql()
    assert "cast(test_cases.tags as text) ilike" in sql, (
        "the tags predicate no longer casts to TEXT, so ix_test_cases_tags_trgm "
        "— built on CAST(tags AS TEXT) — cannot serve it"
    )
    assert "cast(test_cases.tags as varchar)" not in sql, (
        "tags are cast to VARCHAR again. That is a different expression from "
        "the indexed one, so the whole OR drops back to checking every row."
    )


# ── The cross-table branch is not inside the OR ──────────────────────────


def test_the_run_level_branch_is_split_from_the_test_case_columns():
    """The regression that mattered most: plan B was identical to plan A."""
    sql = _sql()
    assert " union " in sql, "the search is one OR again, not a UNION"

    clauses = _where_clauses(sql)
    own_text = [c for c in clauses if "test_cases.test_name ilike" in c]
    assert own_text, "no WHERE clause matches the test-case columns — scan broken"
    for clause in own_text:
        assert "primary_suite_name" not in clause, (
            "test_runs.primary_suite_name is back in the same OR as the "
            "test_cases columns. A bitmap OR cannot combine indexes across "
            "tables, so this forces a row-by-row scan and makes all four "
            "trigram indexes dead weight:\n" + clause
        )

    run_level = [c for c in clauses if "primary_suite_name ilike" in c]
    assert run_level, (
        "the run-level suite name is no longer searched at all — live_stream "
        "tests whose per-event suite_name is a class name stop being findable"
    )


def test_every_text_column_is_still_searched():
    """Splitting the query must not quietly drop a match."""
    sql = _sql()
    for column in (
        "test_cases.test_name ilike",
        "test_cases.suite_name ilike",
        "test_cases.error_message ilike",
        "cast(test_cases.tags as text) ilike",
        "test_runs.primary_suite_name ilike",
    ):
        assert column in sql, f"{column} is no longer part of the search"


# ── Filters apply to BOTH halves ─────────────────────────────────────────


def test_the_tenant_pin_applies_to_both_halves():
    """Ids only come from filtered halves, so the outer query cannot leak."""
    sql = _sql(project_id=PROJECT)
    halves = _where_clauses(sql)
    assert len(halves) == 2
    for clause in halves:
        assert f"test_runs.project_id = '{PROJECT}'" in clause, (
            "one half of the search ignores the project pin, so it returns "
            "another tenant's test cases:\n" + clause
        )


def test_the_accessible_set_applies_to_both_halves():
    sql = _sql(allowed_project_ids={PROJECT, OTHER})
    halves = _where_clauses(sql)
    assert len(halves) == 2
    for clause in halves:
        assert "test_runs.project_id in" in clause, (
            "one half of the search ignores the caller's accessible projects:\n"
            + clause
        )


def test_the_period_filter_applies_to_both_halves():
    since = datetime(2026, 9, 1, tzinfo=timezone.utc)
    sql = _sql(period_start=since)
    for clause in _where_clauses(sql):
        assert "test_cases.created_at >=" in clause, (
            "one half of the search ignores the period filter:\n" + clause
        )


def test_inactive_projects_are_excluded_in_both_halves():
    for clause in _where_clauses(_sql()):
        assert "projects.is_active is true" in clause


def test_each_half_is_bounded_before_the_union():
    """The global top N is inside the union of each half's top N.

    Bounding each half keeps a broad term from materialising every match in
    one branch before the outer sort — two halves plus the outer query.
    """
    sql = _sql(override_limit=25)
    assert sql.count("limit 25") == 3, (
        "a half of the search is no longer limited on its own, so a broad "
        "term materialises every match before sorting"
    )
