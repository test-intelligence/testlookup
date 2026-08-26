"""The search filter must keep matching on step text — or migration 0138 is dead weight.

``0138`` adds GIN trigram indexes on ``test_steps.name`` and
``test_steps.assertion_message`` because keyword search ORs a correlated EXISTS
over those two columns, and without the indexes every search sequentially
scanned the whole step table (46.2 ms on a 60,360-step corpus, versus 0.8 ms
after).

``tests/integration/test_search_step_index_postgres.py`` pins the other half —
that the indexes exist and the planner picks one. Neither of those notices if
the *search* stops consulting steps: the indexes would still exist, still be
maintained on every ingest (they cost ~20% on the step-snapshot write path),
and buy nothing. This test pins the reason they are there.

Deliberately asserts against the compiled SQL rather than the ORM object graph:
what matters is the predicate Postgres receives.
"""
from __future__ import annotations

import pytest

pytest.importorskip("app.services.search_service")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

from app.models.postgres import TestCase  # noqa: E402
from app.services.search_service import build_search_filters  # noqa: E402

pytestmark = pytest.mark.regression


def _compiled_filter_sql(term: str = "flaky-needle") -> str:
    filters = build_search_filters(term, None, None, None, None)
    stmt = select(TestCase.id).where(*filters)
    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).lower()


def test_search_matches_step_name_and_assertion_message():
    sql = _compiled_filter_sql()

    assert "test_steps" in sql, (
        "keyword search no longer consults test_steps. If that is intended, "
        "migration 0138's GIN indexes are now pure write cost on the ingest "
        "path and should be dropped in a follow-up migration."
    )
    assert "test_steps.name" in sql, "step name is no longer searched"
    assert "test_steps.assertion_message" in sql, (
        "step assertion_message is no longer searched"
    )


def test_step_predicate_is_an_ilike_so_a_trigram_index_can_serve_it():
    """A gin_trgm_ops index serves ILIKE/LIKE. If the predicate is ever changed
    to equality, full-text or a function call, the trigram indexes stop being
    usable and the scan comes back — silently, because results stay correct."""
    sql = _compiled_filter_sql()

    step_clause = sql[sql.index("test_steps") :]
    assert "ilike" in step_clause, (
        f"the step predicate is no longer an ILIKE, so gin_trgm_ops cannot "
        f"serve it:{chr(10)}{step_clause[:400]}"
    )
