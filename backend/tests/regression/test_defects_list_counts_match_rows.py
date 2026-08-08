"""Regression: `/analytics/defects` counted defects it refused to list.

``analytics_service.list_defects`` runs two queries that disagree by
construction::

    # rows
    FROM defects d
    JOIN test_cases tc ON tc.id = d.test_case_id      <-- INNER JOIN
    ...

    # total
    SELECT COUNT(*) FROM defects d
    WHERE 1=1 {project_filter} {status_filter}        <-- no join at all

Any defect whose ``test_case_id`` is NULL is dropped from the rows and still
counted in the total. **Confirmed live** on a project with five defects::

    GET /api/v1/analytics/defects?project_id=<pid>&days=90
    {"items": [], "total": 5, "page": 1, "size": 20, "pages": 1}

An empty list, a total of five, and a page count derived from the total.

**A NULL ``test_case_id`` is normal, not exotic.** Two independent parts of
the system produce it:

* ``analytics_service._find_recent_test_case_id`` — its own docstring says
  *"Returns None when no match is found — the caller stores the defect with a
  NULL test_case_id rather than failing the intake."* So the ordinary defect
  intake path creates these rows whenever the failure signature does not match
  a current test case.
* The model declares ``ForeignKey("test_cases.id", ondelete="SET NULL")``. The
  schema deliberately lets defects outlive their test cases — which is exactly
  what the retention purge (US-11.4) does. A defect whose test case has been
  purged becomes invisible while still inflating the count.

The partial unique index on the table (``resolution_status = 'OPEN' AND
test_case_id IS NOT NULL``) is further evidence the NULL case was designed for.

Fix: ``LEFT JOIN test_cases``. The joined columns (``test_name``,
``suite_name``) simply come back NULL, which the ``dict(row._mapping)``
response mapping already handles. Making the *count* match the inner join was
the other option and is worse — it would hide legitimate defects consistently
rather than showing them.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.services import analytics_service  # noqa: E402

pytestmark = pytest.mark.regression


def _source() -> str:
    return inspect.getsource(analytics_service.list_defects)


def _sql_blocks() -> list[str]:
    """The triple-quoted SQL bodies inside ``list_defects``."""
    return re.findall(r'"""(.*?)"""', _source(), re.S)


def _rows_sql() -> str:
    block = next((b for b in _sql_blocks() if "FROM defects" in b and "SELECT COUNT(" not in b), None)
    assert block, "could not locate the row-fetching SQL in list_defects()"
    return block


def _count_sql() -> str:
    block = next((b for b in _sql_blocks() if "SELECT COUNT(" in b), None)
    assert block, "could not locate the count SQL in list_defects()"
    return block


def _strip_sql_comments(sql: str) -> str:
    """Remove ``--`` comments before any structural parsing.

    Prose explaining a join reads like a join. The first version of this test
    matched the word "dropped" out of the sentence "An inner join dropped
    those rows" in the fix's own comment, and reported a phantom join on a
    table called ``dropped``. Assertions about code must never read the
    commentary about that code.
    """
    return re.sub(r"--[^\n]*", "", sql)


def _row_eliminating_joins(sql: str) -> set[str]:
    """Tables joined in a way that can DROP rows (i.e. not LEFT/RIGHT/FULL).

    An inner join to a table is a filter; a LEFT JOIN is not. Only the former
    can make the row query disagree with an unjoined COUNT.
    """
    sql = _strip_sql_comments(sql)
    joins = set()
    for match in re.finditer(r"(\b(?:LEFT|RIGHT|FULL|INNER|CROSS)?\s*(?:OUTER\s+)?)JOIN\s+(\w+)", sql, re.I):
        kind = (match.group(1) or "").strip().upper()
        table = match.group(2).lower()
        if kind in ("", "INNER", "CROSS"):
            joins.add(table)
    return joins


class TestTheTwoQueriesCannotDisagree:
    def test_rows_query_does_not_inner_join_test_cases(self):
        """The exact defect: an inner join on a nullable FK."""
        joins = _row_eliminating_joins(_rows_sql())
        assert "test_cases" not in joins, (
            "list_defects INNER JOINs test_cases, so every defect with a NULL "
            "test_case_id is dropped from items while still being counted in "
            "total — measured live as items=[] with total=5"
        )

    def test_no_row_eliminating_join_is_absent_from_the_count(self):
        """The general invariant, not just this one table.

        Any join that can drop rows must appear in both queries, or the total
        will not describe the list.
        """
        rows_filters = _row_eliminating_joins(_rows_sql())
        count_filters = _row_eliminating_joins(_count_sql())
        assert rows_filters <= count_filters, (
            f"the row query filters on {sorted(rows_filters - count_filters)} but "
            "the count query does not, so total and items describe different sets"
        )

    def test_test_cases_is_still_joined_at_all(self):
        """The fix must keep test_name/suite_name available, not drop the join."""
        assert re.search(r"LEFT\s+JOIN\s+test_cases", _strip_sql_comments(_rows_sql()), re.I), (
            "test_cases is no longer LEFT JOINed, so test_name/suite_name would "
            "disappear from every row"
        )


class TestTheNullCaseIsRealNotHypothetical:
    """Anchors *why* this matters, so a future reader does not 'simplify' the
    LEFT JOIN back to an inner one."""

    def test_intake_deliberately_stores_a_null_test_case_id(self):
        helper = inspect.getsource(analytics_service._find_recent_test_case_id)
        assert "NULL test_case_id" in helper or "None" in helper, (
            "the intake path no longer documents storing a NULL test_case_id — "
            "re-check whether the LEFT JOIN is still required"
        )

    def test_the_fk_still_nulls_on_delete(self):
        from app.models import postgres as models

        defect_src = inspect.getsource(models.Defect)
        assert 'ondelete="SET NULL"' in defect_src, (
            "Defect.test_case_id no longer SET NULLs on delete; if it now "
            "cascades, revisit whether orphaned defects can still exist"
        )
