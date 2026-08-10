"""Coverage must not name one project for a row that spans several.

``coverage_stats`` groups suite rows by suite name alone, so in All-Projects
scope a single row can aggregate several projects — ``api``, ``smoke`` and
``regression`` are the most collidable suite names there are. The row was then
labelled with::

    MAX(p.name) AS project_name

which states one project as fact. Measured on the live deployment, the ``api``
row summed two projects (5 unique tests + 10) and attributed all 15 to
whichever name sorted highest.

**Scope, honestly stated.** This is LOW severity and the first version of the
finding was overstated. No consumer reads the field today: ``CoverageSuite`` in
``frontend/src/types/analytics.ts`` has no ``project_name``, the MCP tool
``get_coverage_report`` takes a *required* ``project_id``, and
``analysis_report_service`` scopes its call too. A live probe of the rendered
page showed suite rows with no project attribution at all. So this fixes a
payload that lies to the *next* consumer, not a visible defect.

The aggregation is deliberately left alone. Merging a suite across projects is
a defensible org-wide roll-up, and regrouping by ``(project_id, suite)`` would
change what the All-Projects view means and break the UI's one-row-per-suite
assumption. The fix answers honestly within the existing shape: name the
project only when there is exactly one, and publish ``project_count`` so a
consumer can tell a roll-up from a single-project row.
"""
from __future__ import annotations

import re

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression

import inspect  # noqa: E402

from app.services import analytics_service  # noqa: E402

SOURCE = inspect.getsource(analytics_service.coverage_stats)


def test_project_name_is_not_a_bare_max():
    """``MAX(p.name)`` on its own picks a winner and calls it the truth."""
    assert not re.search(r"MAX\(p\.name\)\s+AS\s+project_name", SOURCE), (
        "coverage names a single project for rows that may span several — a "
        "merged row reports whichever project name sorts highest as fact"
    )


def test_project_name_is_conditional_on_a_single_project():
    assert re.search(
        r"CASE WHEN COUNT\(DISTINCT tr\.project_id\) = 1 THEN MAX\(p\.name\)",
        SOURCE,
    ), (
        "project_name must be emitted only when the row belongs to exactly "
        "one project, and NULL otherwise"
    )


def test_project_count_is_published():
    """Without it, a consumer cannot distinguish a roll-up from a row whose
    project name simply happens to be missing."""
    assert re.search(
        r"COUNT\(DISTINCT tr\.project_id\)\s+AS project_count", SOURCE
    ), "the row must carry how many projects it aggregates"


def test_the_aggregation_was_not_restructured():
    """Guards the over-reach: regrouping by (project, suite) would change what
    the All-Projects view means and break the UI's one-row-per-suite keying.
    The fix is to stop lying, not to re-cut the data."""
    group_by = re.search(r"GROUP BY ([^\n]+)", SOURCE)
    assert group_by, "GROUP BY clause not found — query restructured?"
    assert "project_id" not in group_by.group(1), (
        "suite rows were regrouped by project — that is a contract change the "
        "UI does not expect, not the minimal honest fix"
    )
