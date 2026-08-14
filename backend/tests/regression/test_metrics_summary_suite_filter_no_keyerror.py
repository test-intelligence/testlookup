"""Every suite-filtered dashboard request returned HTTP 500.

``GET /api/v1/metrics/summary?project_id=…&suite_name=api`` -> **500**. Measured
live on the homelab; ``suite_name`` is a documented query param on the endpoint
and the Overview page's suite filter goes through it, so picking a suite took
the whole dashboard down.

Root cause: ``_period_stats`` has two returns. The suite-scoped early return
(inside ``if suite_name:``) returned three keys; the unscoped return grew a
fourth, ``total_executions``, and the caller reads it unconditionally::

    total_executions = cur["total_executions"]        # KeyError on the suite path

**Self-inflicted, and stated as such**: #492 ("Total executions KPI counted
runs, not executions", 2026-08-08) added the key to the unscoped branch and to
the caller but not to the suite branch. It has been broken on the deployment
since that date. It survived because nothing exercised ``_period_stats`` with a
suite name — the endpoint's own tests never combine ``project_id`` with
``suite_name``, and no exploratory pass had crossed those two filters either.

The first test below guards the CLASS: the two returns must stay key-for-key
identical, so the next key added to one branch cannot silently skip the other.
That is the actual defect — a two-branch function whose branches drifted — not
the single missing key.
"""
from __future__ import annotations

import ast
import inspect

import pytest

pytest.importorskip("sqlalchemy")

from app.services import metrics_service  # noqa: E402

pytestmark = pytest.mark.regression


def _return_key_sets(fn):
    """Both ``return {...}`` key sets, in source order."""
    tree = ast.parse(inspect.getsource(fn))
    returns = sorted(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)
        ),
        # ast.walk is breadth-first and does NOT preserve source order; sorting
        # by lineno is what makes "the suite branch" mean the suite branch.
        key=lambda n: n.lineno,
    )
    return [
        {k.value for k in r.value.keys if isinstance(k, ast.Constant)} for r in returns
    ]


def test_both_period_stats_branches_return_the_same_keys():
    """The class guard. A key added to one branch must be added to the other."""
    sets = _return_key_sets(metrics_service._period_stats)
    assert len(sets) == 2, "expected exactly two dict returns in _period_stats"
    suite_scoped, unscoped = sets
    assert suite_scoped == unscoped, (
        f"the suite-scoped branch is missing {sorted(unscoped - suite_scoped)} "
        f"and has extra {sorted(suite_scoped - unscoped)}; the caller reads "
        f"these keys unconditionally, so the suite path raises KeyError -> 500"
    )


def test_the_caller_cannot_read_a_key_neither_branch_returns():
    """Catches the inverse mistake: adding a read without adding the key."""
    sets = _return_key_sets(metrics_service._period_stats)
    available = set.intersection(*sets)

    caller = ast.parse(inspect.getsource(metrics_service.get_dashboard_summary))
    reads = {
        n.slice.value
        for n in ast.walk(caller)
        if isinstance(n, ast.Subscript)
        and isinstance(n.value, ast.Name)
        and n.value.id in ("cur", "prev")
        and isinstance(n.slice, ast.Constant)
    }
    assert reads, "the caller no longer subscripts cur/prev — update this guard"
    assert reads <= available, (
        f"get_dashboard_summary reads {sorted(reads - available)} which "
        f"_period_stats does not always return"
    )


def test_total_executions_is_the_key_that_was_missing():
    """Pins the specific instance so the fix cannot be reverted quietly."""
    suite_scoped, _ = _return_key_sets(metrics_service._period_stats)
    assert "total_executions" in suite_scoped


def test_suite_branch_sources_executions_from_summed_totals():
    """``total_executions`` must be executions, never a run count — the
    conflation #492 existed to fix (the KPI read 5 where Coverage said 60).

    Updated for F-080: the suite branch no longer sums whole-run aggregate
    columns, because those cannot be suite-scoped. It now sums per-test rows
    for the suite plus the run-level totals of runs that have no per-test rows
    yet. Both branches must still publish an EXECUTION count.
    """
    src = inspect.getsource(metrics_service._period_stats)
    # Unscoped branch: still reads the summed run-level total.
    assert '"total_executions": int(getattr(row, "sum_total", 0) or 0)' in src
    # Suite branch: the composed per-test + pending total.
    assert '"total_executions": sum_total' in src
    assert "sum_total = int(case_row.total or 0) + int(pending_row.total or 0)" in src
    # And it must never regress to a run count.
    assert '"total_executions": row.total_runs' not in src
    assert '"total_executions": run_row.total_runs' not in src
