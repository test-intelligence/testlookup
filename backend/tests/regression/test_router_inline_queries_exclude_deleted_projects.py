"""Router-level inline queries must exclude soft-deleted projects too.

Two handlers build their query **inline** rather than delegating to a service,
so neither the original single-function sweep nor the widened service sweep
could see them. Both scoped only inside a conditional branch that an ADMIN
never enters:

    if project_id:            ...   # a pinned project
    else / elif accessible:   ...   # a non-admin's membership set

Measured live:

=============================  =========================  ==================
endpoint                       deleted-project rows       live rows
=============================  =========================  ==================
``GET /runs/failed-ids``       **1,133** FAILED runs           17
``GET /agents/pipelines``      **861** pipeline runs           13
=============================  =========================  ==================

``failed-ids`` returned its full cap of **1,000 ids** from that pool, and its
own ``limit`` exists *"to prevent runaway fan-outs"* — so the fan-out was
almost entirely work against projects nobody can open. ``pipelines`` defaults
to ``limit=20``, so the 13 real rows were crowded out completely.

**Twelfth and thirteenth surfaces in this family.** The progression is the
point: instances 1-9 were found one at a time; #555 came from widening the
sweep to the router-computes / service-skips shape; these two came from
widening it again to routers that never call a service at all. Each widening
found what the previous model of the defect could not express.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.routers import agents, runs  # noqa: E402

pytestmark = pytest.mark.regression

CASES = {
    "list_failed_run_ids": runs.list_failed_run_ids,
    "list_pipelines": agents.list_pipelines,
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_query_excludes_deleted_projects(name: str):
    src = inspect.getsource(CASES[name])
    assert "Project.is_active" in src, (
        f"{name} returns rows from soft-deleted projects — measured live at "
        f"1,133/17 (failed-ids) and 861/13 (pipelines)"
    )


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_exclusion_is_outside_the_conditional_branches(name: str):
    """The pinned-project and membership branches are the ones that cannot
    leak. An ADMIN enters neither, which is precisely the case that
    over-returned, so the filter must sit before them."""
    src = inspect.getsource(CASES[name])
    guarded = re.search(
        r"if\s+(project_id|accessible)[^\n]*:\s*\n(?:[^\n]*\n){0,3}?[^\n]*Project\.is_active",
        src,
    )
    assert not guarded, (
        f"{name} applies the live-project filter inside a conditional branch"
    )


def test_failed_ids_keeps_its_tenant_isolation():
    """The life-cycle filter is additional — losing either branch would trade
    an over-listing for a cross-tenant leak."""
    src = inspect.getsource(runs.list_failed_run_ids)
    assert "_require_accessible_project" in src
    assert "TestRun.project_id.in_(accessible)" in src
    assert "if not accessible:" in src, "the fail-closed empty branch is gone"


def test_pipelines_keeps_its_tenant_isolation():
    src = inspect.getsource(agents.list_pipelines)
    assert "TestRun.project_id == project_id" in src
    assert "TestRun.project_id.in_(accessible)" in src


def test_pipelines_join_is_unconditional():
    """The join used to exist only inside the scoped branch; the filter cannot
    reference TestRun without it."""
    src = inspect.getsource(agents.list_pipelines)
    assert re.search(
        r"q\.join\(TestRun, AgentPipelineRun\.test_run_id == TestRun\.id\)\.where", src
    )
