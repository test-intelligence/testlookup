"""ROI metrics must not count soft-deleted projects.

Every count in ``get_value_metrics`` was guarded by ``if project_id:`` alone —
the same shape that made the dashboard over-count (#538) and the analytics
helper leak (#539). Unscoped, nothing restricted them.

**Confirmed by reproducing the service's real predicate, not by assuming.** An
earlier pass logged this as "needs confirmation" precisely because the number
did not corroborate a naive comparison: the API returned
``flaky_tests_identified: 3`` while the coach table held 5 live rows and 5
deleted ones. Neither matched, because the metric counts only rows whose
``status_history`` actually oscillates (``history_is_intermittent``). Running
that predicate over the live rows resolved it:

===============================================  =====
source                                           count
===============================================  =====
live project (Checkout Service)                      2
soft-deleted project (ZZ Probe, multi-day trends)    1
**total, = the unscoped API response**           **3**
===============================================  =====

It is user-visible: the ROI page renders ``FLAKY TESTS FOUND 3`` in
All-Projects scope. Only the *hero* hours-saved number is gated on
``available``; the component cards render regardless. An ROI figure gets quoted
to stakeholders, so counting projects nobody can open overstates the product's
own value.

The fix routes all 11 counts through two helpers. Both keep the pin when a
project is supplied, so scoped behaviour is unchanged; the life-cycle
restriction is what becomes unconditional. The joins to ``TestRun`` are now
unconditional too — previously they existed only on the scoped path, which is
exactly why the unscoped path counted everything.
"""
from __future__ import annotations

import inspect
import re
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import func as sa_func, select  # noqa: E402

from app.models.postgres import (  # noqa: E402
    Defect,
    FailureCluster,
    FlakyCoachResult,
    TestRun,
)
from app.services import value_metrics_service as vms  # noqa: E402

pytestmark = pytest.mark.regression

SOURCE = inspect.getsource(vms.get_value_metrics)
PROJECT = uuid.uuid4()


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": False}))


class TestTheHelpersRestrictToLiveProjects:
    def test_direct_scope_excludes_deleted_when_unscoped(self):
        """The measured case: no project_id, so nothing used to restrict it."""
        stmt = _sql(
            vms._scope_direct(
                select(sa_func.count(FlakyCoachResult.id)),
                FlakyCoachResult.project_id,
                None,
            )
        )
        assert "is_active" in stmt, (
            "unscoped ROI counts include soft-deleted projects — measured as "
            "flaky_tests_identified=3 where only 2 belong to a live project"
        )

    def test_run_linked_scope_excludes_deleted_when_unscoped(self):
        stmt = _sql(
            vms._scope_via_run(
                select(sa_func.count(FailureCluster.id)),
                FailureCluster.test_run_id == TestRun.id,
                None,
            )
        )
        assert "is_active" in stmt

    def test_the_join_is_unconditional(self):
        """The join used to exist only on the scoped path — which is why the
        unscoped path had nothing to filter on."""
        stmt = _sql(
            vms._scope_via_run(
                select(sa_func.count(FailureCluster.id)),
                FailureCluster.test_run_id == TestRun.id,
                None,
            )
        )
        assert "JOIN" in stmt.upper()


class TestScopedBehaviourIsUnchanged:
    """The life-cycle filter is additional. Dropping the pin while adding it
    would turn an over-count into a cross-project leak."""

    def test_direct_scope_still_pins_the_project(self):
        stmt = _sql(
            vms._scope_direct(
                select(sa_func.count(Defect.id)), Defect.project_id, PROJECT
            )
        )
        assert "project_id = " in stmt
        assert "is_active" in stmt

    def test_run_scope_still_pins_the_project(self):
        stmt = _sql(
            vms._scope_via_run(
                select(sa_func.count(FailureCluster.id)),
                FailureCluster.test_run_id == TestRun.id,
                PROJECT,
            )
        )
        assert "project_id = " in stmt
        assert "is_active" in stmt


class TestEveryCountRoutesThroughTheHelpers:
    """A count added later that reintroduces the bare guard is the bug again."""

    def test_no_bare_conditional_project_filter_remains(self):
        leftovers = re.findall(
            r"if project_id:\s*\n\s+\w+_stmt = \w+_stmt\.(?:join|where)", SOURCE
        )
        assert not leftovers, (
            f"{len(leftovers)} count(s) still apply their project filter only "
            "when scoped, so the unscoped ROI figure counts deleted projects"
        )

    @pytest.mark.parametrize(
        "stmt_name",
        [
            "cluster_stmt",
            "tests_grouped_stmt",
            "dup_stmt",
            "dup_cand_stmt",
            "promoted_stmt",
            "flaky_rows_stmt",
            "quarantine_stmt",
            "blocked_stmt",
            "conditional_stmt",
            "overrides_stmt",
            "intel_stmt",
        ],
    )
    def test_each_count_is_scoped(self, stmt_name: str):
        assert re.search(rf"{stmt_name} = _scope_(?:via_run|direct)\(", SOURCE), (
            f"{stmt_name} does not go through a scoping helper — it will count "
            "soft-deleted projects on the unscoped ROI view"
        )
