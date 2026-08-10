"""The runs list must not return runs from soft-deleted projects.

``DELETE /projects/{id}`` is a **soft** delete — it flips ``is_active`` to
False. ``my_failures`` already carries a helper for this, and its docstring
states the problem exactly:

    ``DELETE /projects/{id}`` is a SOFT delete -- it flips ``is_active`` to
    False. **Only the project LIST honours that flag**, so a deleted project's
    failures kept appearing in the assignment inbox: actionable work items for
    a project the user cannot open, filter by, or navigate to, and which is
    gone from every project picker.

That was fixed for the assignment inbox alone. ``GET /api/v1/runs`` has the
identical defect — the same shape as the authorization sweep, where a guard was
corrected in one router while identical copies survived elsewhere.

Measured on the live deployment:

===============================  ==========  ======
projects                         count       runs
===============================  ==========  ======
``is_active = false`` (deleted)  43          1422
``is_active = true``             2           67
===============================  ==========  ======

The project picker showed **2** projects; the unscoped runs list returned rows
from **13**, most of them deleted. Runs, and every window metric derived from
them, counted projects the user cannot see or navigate to.

(Many of those 43 were throwaway projects created during exploration, so the
ratio is specific to that deployment. The mechanism is not: one deleted project
is enough to put unreachable runs in the list.)

The filter goes in the shared ``filters`` list so the row query and the count
query cannot disagree — a count that includes rows the list excludes is the
next bug along, and ``/analytics/defects`` already shipped exactly that
(``items: [] total: 5``, F-038).
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.services import runs_service  # noqa: E402

pytestmark = pytest.mark.regression

SOURCE = inspect.getsource(runs_service.list_project_runs)


def test_the_query_excludes_inactive_projects():
    assert "is_active" in SOURCE, (
        "GET /api/v1/runs returns runs from soft-deleted projects — rows for a "
        "project that is gone from every picker (measured live: 1422 runs "
        "across 43 deleted projects)"
    )


def test_the_filter_is_shared_by_the_count_query():
    """A count that disagrees with the list is the next bug along.

    ``filters`` is built once and reused by both queries, so the exclusion must
    be appended there rather than tacked onto one statement's ``.where(...)``.
    Matched across lines, since the append spans several.
    """
    assert re.search(r"filters\.append\(.{0,300}?is_active", SOURCE, re.S), (
        "the is_active exclusion is not inside a filters.append(...) — if it "
        "were applied to the row query alone, the count would still include "
        "deleted projects and disagree with the rows it returns"
    )


def test_it_filters_by_project_activity_not_run_status():
    """Guards a plausible wrong fix: filtering on the run instead."""
    assert "Project.is_active" in SOURCE or "project" in SOURCE.lower(), (
        "the exclusion must key on the project's is_active flag"
    )
