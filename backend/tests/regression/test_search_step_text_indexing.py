"""Regression: granular step text must be searchable, project-scoped (Phase 3).

A failing step name or its assertion message must make the owning test findable
via /search. Steps live in ``test_steps`` anchored to the project-scoped
``canonical_test_cases`` identity (one snapshot per (project, fingerprint)),
linked from the per-run row via ``TestCase.canonical_test_case_id``.

Two requirements verified here:

1. **Searchability** — the keyword filter (the offline-default, no-embedding
   path) matches BOTH ``test_steps.name`` AND ``test_steps.assertion_message``
   via a correlated ``EXISTS``. The semantic indexer's embedded document also
   carries the concatenated step text.

2. **No cross-project leak** — the step ``EXISTS`` correlates on the test's OWN
   canonical anchor (itself project-scoped), and the overall keyword query is
   bound by ``TestRun.project_id`` for the requested project. A step belonging
   to project A therefore cannot surface a row under project B's filter.

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

    # The granular-step EXISTS subquery is present and reads from test_steps.
    assert "EXISTS" in sql
    assert "FROM test_steps" in sql
    # Both step columns participate in the match.
    assert "test_steps.name ILIKE" in sql
    assert "test_steps.assertion_message ILIKE" in sql
    # The search term reaches the step predicate.
    assert "expected 200 but got 500" in sql


def test_step_exists_correlates_on_own_canonical_anchor_and_project_scoped():
    """No cross-project leak: the step EXISTS joins on the test's OWN canonical
    anchor (project-scoped), and the whole query is bound by the requested
    project's ``test_runs.project_id``."""
    project_a = str(uuid.uuid4())
    sql = _compile_keyword_sql("flaky step", project_a)

    # Correlate on the per-run row's own canonical link, NOT an unscoped join —
    # the canonical identity is itself per-(project, fingerprint).
    assert (
        "test_steps.canonical_test_case_id = test_cases.canonical_test_case_id"
        in sql
    )
    # The outer query is pinned to the requested project, so a step belonging to
    # another project's canonical cannot surface a row here.
    assert f"test_runs.project_id = '{project_a}'" in sql


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
