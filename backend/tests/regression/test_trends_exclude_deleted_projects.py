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

#: Functions in this module that aggregate across projects. Not only
#: ``test_runs`` sums — the defect counters were the sixth surface in this
#: family precisely because the first version of this guard looked for
#: ``SUM(tr.*_tests)`` and a ``COUNT(defects.id)`` does not match that shape.
AGGREGATORS = (
    "get_trend_data",
    "_period_stats",
    "count_open_critical_defects",
)


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


def test_no_cross_project_aggregate_was_missed():
    """The whole point: this family recurred because siblings went unswept.

    The first version of this scan looked only for ``SUM(tr.*_tests)``. The
    defect counters — ``COUNT(defects.id)`` — did not match, and shipped the
    same bug: the Overview KPI read 4 active defects (all on soft-deleted
    projects) while the Defects page read 0.

    So the scan now covers any function aggregating **either** table.
    """
    patterns = (
        r"SUM\(tr\.\w+_tests\)",
        r"func\.sum\(TestRun\.\w+_tests\)",
        r"func\.count\(Defect\.\w+\)",
        r"func\.count\(TestRun\.\w+\)",
    )
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
        if any(re.search(p, src) for p in patterns) and "is_active" not in src:
            missed.append(name)
    assert not missed, (
        f"these functions aggregate across projects without excluding "
        f"soft-deleted ones, and are not covered by AGGREGATORS: {missed}"
    )


def test_the_defect_counters_exclude_deleted_projects():
    """The measured case: KPI 4, Defects page 0, all four on deleted projects."""
    src = inspect.getsource(metrics_service.get_dashboard_summary)
    assert "Project.is_active" in src, (
        "the Overview active-defects KPI counts defects on soft-deleted "
        "projects — measured live at 4 where the Defects page showed 0"
    )
