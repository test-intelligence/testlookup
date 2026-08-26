"""Regression: granular step text must be searchable, project-scoped (Phase 3).

A failing step name or its assertion message must make the owning test findable
via /search. Steps live in ``test_steps`` anchored to the project-scoped
``canonical_test_cases`` identity (one snapshot per (project, fingerprint)),
linked from the per-run row via ``TestCase.canonical_test_case_id``.

Two requirements verified here:

1. **Searchability** — the keyword filter (the offline-default, no-embedding
   path) matches BOTH ``test_steps.name`` AND ``test_steps.assertion_message``.
   The semantic indexer's embedded document also carries the step text.

2. **No cross-project leak** — a step belonging to project A cannot surface a
   row under project B's filter.

The step operand used to be a correlated ``EXISTS``. That put a non-
``test_cases`` operand inside the search OR, which stopped Postgres building a
bitmap index scan and made every search sequentially scan ``test_cases``. It is
now an uncorrelated ``= ANY(<array subquery>)`` over the same two columns, so
requirement 1 is unchanged in substance.

Requirement 2 now holds by a different mechanism, and the assertions below
follow it: ``canonical_test_cases`` rows are themselves per-(project,
fingerprint), so a canonical id collected from project A's steps can never
equal the anchor of a project-B test case — and the outer query is still pinned
to ``test_runs.project_id``. Verified end-to-end against real Postgres with a
two-project fixture (project A = 1 hit, project B = 0, unscoped = 1); that lives
in ``tests/integration/test_search_step_scope_postgres.py``.

Style mirrors ``test_semantic_search_fingerprint_scope.py``: aiosqlite isn't
installed locally and the repo's Postgres-specific types (UUID/JSONB/TSVECTOR)
make a live-DB unit test impractical, so we compile the generated SQL and assert
on its structure (the cross-tenant leak class is a SQL-shape property). The
behaviour runs end-to-end against real Postgres in CI integration.
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

from app.models.postgres import TestCase, TestRun  # noqa: E402
from app.services.search_service import build_search_filters  # noqa: E402
from app.services.semantic_search import _doc_text  # noqa: E402


def _compile_keyword_sql(query_text: str, project_id: str) -> str:
    """Compile the keyword search query (the same SELECT shape
    ``search_test_cases_query`` builds) to literal SQL for inspection."""
    filters = build_search_filters(query_text, project_id, None, None, None)
    stmt = (
        select(TestCase.id)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(*filters)
    )
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_keyword_search_matches_step_name_and_assertion_message():
    """The step-text term is matched against both ``test_steps.name`` and
    ``test_steps.assertion_message`` — a failing assertion is findable."""
    sql = _compile_keyword_sql("expected 200 but got 500", str(uuid.uuid4()))

    # The granular-step subquery is present and reads from test_steps.
    assert "FROM test_steps" in sql
    # Both step columns participate in the match.
    assert "test_steps.name ILIKE" in sql
    assert "test_steps.assertion_message ILIKE" in sql
    # The search term reaches the step predicate.
    assert "expected 200 but got 500" in sql


def test_step_match_goes_through_the_project_scoped_canonical_anchor():
    """No cross-project leak: a step can only reach a test case through the
    canonical anchor, which is itself per-(project, fingerprint), and the whole
    query stays bound by the requested project's ``test_runs.project_id``."""
    project_a = str(uuid.uuid4())
    sql = _compile_keyword_sql("flaky step", project_a)

    # The step ids are collected as canonical_test_case_id and matched against
    # the per-run row's own anchor. A canonical id belongs to exactly one
    # project, so ids gathered from another project's steps cannot match here.
    assert "array_agg(DISTINCT test_steps.canonical_test_case_id)" in sql
    assert "test_cases.canonical_test_case_id = ANY" in sql
    # The outer query is pinned to the requested project — the second,
    # independent barrier.
    assert f"test_runs.project_id = '{project_a}'" in sql


def test_short_terms_keep_the_correlated_exists_form():
    """Patterns below ``MIN_INDEXED_TERM_LEN`` deliberately keep the original
    correlated form: pg_trgm pads them into boundary trigrams whose posting
    lists cover nearly the table, so the index-driven shape is measurably the
    WORSE plan there (``as``: 40ms -> 143ms end-to-end). Both paths must keep
    searching step text."""
    sql = _compile_keyword_sql("ab", str(uuid.uuid4()))

    assert "EXISTS" in sql
    assert "test_steps.name ILIKE" in sql
    assert "test_steps.assertion_message ILIKE" in sql
    assert (
        "test_steps.canonical_test_case_id = test_cases.canonical_test_case_id"
        in sql
    )


def test_two_projects_compile_to_distinct_project_filters():
    """The same query for two different projects pins to two different
    ``project_id`` literals — proving results can't bleed between tenants."""
    project_a = str(uuid.uuid4())
    project_b = str(uuid.uuid4())

    sql_a = _compile_keyword_sql("assertion boom", project_a)
    sql_b = _compile_keyword_sql("assertion boom", project_b)

    assert f"test_runs.project_id = '{project_a}'" in sql_a
    assert f"test_runs.project_id = '{project_b}'" in sql_b
    # A's bound project must NOT appear in B's compiled SQL.
    assert project_a not in sql_b
    assert project_b not in sql_a


def test_semantic_index_document_includes_step_text():
    """The embedded document (semantic/ChromaDB path) carries the test's step
    text so a failing step/assertion is findable in the semantic index too."""
    doc = _doc_text(
        "test_checkout",
        "Checkout",
        "AssertionError: totals differ",
        "click checkout | verify total expected 42 but was 0",
    )
    assert "test_checkout" in doc
    assert "Checkout" in doc
    assert "AssertionError: totals differ" in doc
    # Step name + assertion text flow into the embedded document.
    assert "click checkout" in doc
    assert "verify total expected 42 but was 0" in doc


def test_semantic_index_document_bounds_step_text():
    """Step text is bounded so a pathological/huge step tree can't balloon the
    embedded document."""
    huge = "x" * 5000
    doc = _doc_text("t", None, None, huge)
    # The step segment is truncated to its cap (1000 chars).
    step_segment = doc.split(" | ")[-1]
    assert len(step_segment) <= 1000
