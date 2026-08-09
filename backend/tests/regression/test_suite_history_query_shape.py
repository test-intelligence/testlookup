"""`compute_suite_history` must not re-derive what it already knows.

This query was 369 ms of a 513 ms endpoint — the single largest cost in
``GET /api/v1/test-management/suites`` and invisible to four earlier hypotheses,
because it lives in a service the router calls rather than in the router's own
SQL.

Two changes, both verified against the live database to return byte-identical
rows (``EXCEPT`` in both directions, 0 rows either way):

1. ``UNION`` -> ``UNION ALL``. The two branches are **disjoint by construction**:
   path A emits ``(run, primary_suite_name)``; path B emits
   ``(run, tc.suite_name)`` only where it ``IS DISTINCT FROM`` primary, and
   already carries its own ``SELECT DISTINCT``. A pair from A can never equal a
   pair from B, so the dedup sort removes nothing — measured on live data, it
   eliminated **0 of 3824 rows**. 349.7 ms -> 290.4 ms.

2. Carry ``from_primary`` instead of a correlated ``EXISTS``. The join needed to
   know "is this row the run-level suite?", and asked the database per row via
   a subquery over ``test_runs``. ``run_effective`` already knows — it is the
   branch the row came from. 290.4 ms -> 52.9 ms.

Combined: **349.7 ms -> 49.6 ms**, measured on the real generated SQL.

These assertions pin the *shape*, because the shape is what the cost depends on:
the same rows can be produced either way, so a correctness test passes on the
slow version. Suite counts themselves are covered by
``test_suite_history_service.py`` and ``test_test_management_suites_unique_count.py``
— this file guards the performance property those cannot see.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("app.services.suite_history_service")

from app.services import suite_history_service  # noqa: E402

pytestmark = pytest.mark.regression

SOURCE = inspect.getsource(suite_history_service)


def _sql_only(text: str) -> str:
    """Strip SQL comments and Python docstrings.

    Structural assertions on this repo have been fooled by prose before: a
    comment explaining a defect contains the words that describe it, so a naive
    substring search passes against code that still has the bug.
    """
    # Do NOT strip triple-quoted strings: the SQL lives inside text("""...""")
    # blocks, so removing them removes everything under test. That mistake made
    # this file fail identically before and after the fix — a test that cannot
    # distinguish the two states is worthless in both directions.
    text = re.sub(r"^\s*--.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*#.*$", "", text, flags=re.MULTILINE)
    return text


BODY = _sql_only(SOURCE)


def test_the_fixture_still_sees_the_queries():
    """Guards the rest — a bad strip would make every assertion vacuous."""
    assert BODY.count("WITH run_effective AS (") == 2, "expected both suite-history CTEs"


class TestNoRedundantDedup:
    def test_union_all_not_union(self):
        bare = re.findall(r"^\s*UNION\s*$", BODY, flags=re.MULTILINE)
        assert not bare, (
            "a bare UNION is back in suite_history_service. The two branches are "
            "disjoint by construction, so the dedup sort removes nothing "
            "(measured: 0 of 3824 rows) and costs ~60ms"
        )

    def test_both_ctes_use_union_all(self):
        assert BODY.count("UNION ALL") >= 2


class TestNoCorrelatedExists:
    def test_the_subquery_is_gone(self):
        assert "SELECT 1 FROM test_runs tr2" not in BODY, (
            "the correlated EXISTS is back. It re-derives per row what "
            "run_effective already knows from the branch the row came from — "
            "measured at 290ms vs 53ms"
        )

    def test_both_branches_carry_the_flag(self):
        assert BODY.count("TRUE AS from_primary") == 2, "path A must flag itself"
        assert BODY.count("FALSE AS from_primary") == 2, "path B must flag itself"

    def test_the_join_uses_the_flag(self):
        # Both aliases: the second query joins a `filtered` CTE (SELECT * from
        # run_effective, so the flag propagates).
        assert "re.from_primary" in BODY
        assert "f.from_primary" in BODY

    def test_null_suite_cases_still_attach_only_to_the_run_level_bucket(self):
        """The semantic the flag replaces: a test_case with no suite_name of its
        own belongs to the run-level suite, and to nothing else. If the guard
        were inverted, NULL-suite cases would attach to every per-row bucket and
        inflate every count."""
        joins = re.findall(
            r"NULLIF\(TRIM\(tc\.suite_name\), ''\) IS NULL\s*\n\s*AND (\w+)\.from_primary",
            BODY,
        )
        assert len(joins) == 2, f"expected both joins to gate on from_primary, found {joins}"
