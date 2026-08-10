"""The trend chart must not plot soft-deleted projects.

``get_trend_data`` built its scope as::

    project_filter = "AND tr.project_id = :project_id" if project_id else ""

which is the *exact* shape ``_period_stats`` carried before it was fixed — in
this same module. Unscoped, nothing restricted the query, so the dashboard's
trend line aggregated every deleted project. Measured live at ``days=4``:

=================================  ========
source                             total
=================================  ========
trend series, summed               **44,061**
DB, live projects only                  192
dashboard KPI (already fixed)           192
=================================  ========

The KPI and the chart beside it therefore disagreed by **229x on the same
screen** — the cross-surface disagreement class this repo keeps hitting, this
time between two elements of one page.

**Fifth surface in this family** (``my_failures`` → ``/runs`` #535 → dashboard
summary #538 → analytics #539 → ROI #541 → trends). It survived the earlier
pass because that pass fixed the function the bug was *measured* in and did not
sweep its siblings in the same file. So this test asserts the property for
**every** run-aggregating query in the module, discovered by inspection rather
than named — a sixth function added later is covered without editing this file.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.services import metrics_service  # noqa: E402

pytestmark = pytest.mark.regression

#: Functions in this module that aggregate over ``test_runs`` across projects.
AGGREGATORS = ("get_trend_data", "_period_stats")


@pytest.mark.parametrize("fn_name", AGGREGATORS)
def test_the_aggregate_excludes_deleted_projects(fn_name: str):
    src = inspect.getsource(getattr(metrics_service, fn_name))
    assert "is_active" in src, (
        f"{fn_name} aggregates over soft-deleted projects — measured live, the "
        f"trend series summed 44,061 where only 192 belong to live projects"
    )


@pytest.mark.parametrize("fn_name", AGGREGATORS)
def test_the_exclusion_is_not_conditional_on_a_project_id(fn_name: str):
    """The unscoped call is precisely the one that over-counts.

    Guards the specific regression: a filter written as
    ``"..." if project_id else ""`` puts the restriction on the branch that
    cannot leak.
    """
    src = inspect.getsource(getattr(metrics_service, fn_name))
    conditional = re.findall(
        r"['\"][^'\"]*is_active[^'\"]*['\"]\s*if\s+project_id\s+else\s+['\"]{2}", src
    )
    assert not conditional, (
        f"{fn_name} applies the live-project filter only when a project is "
        f"named; unscoped it still counts deleted projects"
    )


@pytest.mark.parametrize("fn_name", AGGREGATORS)
def test_a_scoped_call_still_pins_its_project(fn_name: str):
    """The life-cycle filter is additional — losing the pin would trade an
    over-count for a cross-project leak."""
    src = inspect.getsource(getattr(metrics_service, fn_name))
    assert "project_id" in src


def test_no_run_aggregating_function_was_missed():
    """The whole point: this family recurred because siblings went unswept.

    Any function in the module that sums ``test_runs`` columns must appear in
    AGGREGATORS, so adding a sixth one fails here until it is covered.
    """
    missed = []
    for name, obj in vars(metrics_service).items():
        if name in AGGREGATORS or not callable(obj):
            continue
        try:
            src = inspect.getsource(obj)
        except (TypeError, OSError):
            continue
        if getattr(obj, "__module__", "") != metrics_service.__name__:
            continue
        aggregates = re.search(r"SUM\(tr\.\w+_tests\)", src) or re.search(
            r"func\.sum\(TestRun\.\w+_tests\)", src
        )
        if aggregates and "is_active" not in src:
            missed.append(name)
    assert not missed, (
        f"these functions aggregate test_runs without excluding deleted "
        f"projects, and are not covered by AGGREGATORS: {missed}"
    )
