"""Dashboard metrics must not aggregate over deleted projects.

``_period_stats`` adds a project condition **only when one is supplied**::

    conditions = [TestRun.created_at >= start, TestRun.created_at < end]
    if project_id:
        conditions.append(TestRun.project_id == project_id)

Unscoped — the dashboard's own "all projects" view — nothing restricted it, so
every soft-deleted project was counted. Measured on the live deployment:

===============================  ==========
surface                          executions
===============================  ==========
``metrics/summary`` (unscoped)   **44,315**
DB, live projects                     192
DB, deleted projects               44,123
===============================  ==========

``192 + 44,123 = 44,315`` exactly. **99.6% of the headline number came from
projects the user cannot see, open, or navigate to.**

**This was found by re-verifying an earlier fix, and it is partly self-inflicted.**
#535 excluded deleted projects from ``GET /api/v1/runs``. This aggregation was
left alone, so the runs list reported 68 runs while the dashboard headline
reported 44,315 executions. Before that change the two surfaces were at least
*consistently* wrong; afterwards they disagreed — the cross-surface
disagreement class this repo has hit repeatedly (F-013, F-014, F-038, F-039).

(Most of those deleted projects were throwaway probes created during
exploration, so the ratio is deployment-specific. One deleted project is enough
for the headline to overstate reality.)

**Scope of the fix, deliberately narrow.** A survey found 80 of 82
TestRun-aggregating functions carry no ``is_active`` filter — but nearly all are
*project-scoped*: the caller has already resolved a single project, so the
filter would be redundant. Only cross-project aggregation is affected. This
pins the one measured, and the survey is recorded in the ledger rather than
turned into a blanket edit nobody verified.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.services import metrics_service  # noqa: E402

pytestmark = pytest.mark.regression

SOURCE = inspect.getsource(metrics_service._period_stats)


def test_the_unscoped_aggregate_excludes_deleted_projects():
    assert "is_active" in SOURCE, (
        "dashboard metrics aggregate over soft-deleted projects — measured "
        "live at 44,315 executions where only 192 belong to live projects"
    )


def test_the_exclusion_is_unconditional():
    """It must not sit behind ``if project_id:`` — that is the branch that
    cannot leak. The unscoped call is precisely the one that over-counts."""
    m = re.search(r"if project_id:\s*\n(\s+)conditions\.append\([^\n]*\n", SOURCE)
    assert m, "the project_id branch was restructured — re-check the exclusion"
    guarded_block = m.group(0)
    assert "is_active" not in guarded_block, (
        "the live-project exclusion sits inside `if project_id:` — it then "
        "cannot fire for the unscoped dashboard, which is the case that "
        "over-counts"
    )


def test_a_scoped_call_still_filters_by_its_project():
    """The fix must not displace the existing per-project condition."""
    assert "TestRun.project_id == project_id" in SOURCE
