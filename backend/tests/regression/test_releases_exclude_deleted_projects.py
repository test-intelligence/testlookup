"""The releases list must not return releases from soft-deleted projects.

``list_releases`` applied its project filter only when one was supplied. Measured
live against the deployment:

===============================  ==========  ========
projects                         releases    projects
===============================  ==========  ========
``is_active = false`` (deleted)  **36**      36
``is_active = true``                  2       2
API, unscoped                    **38**      38
===============================  ==========  ========

So **36 of 38 rows** on the Releases page belonged to projects the user cannot
open, filter by, or navigate to.

**Seventh surface in this family** (#535 runs, #538 dashboard, #539 analytics,
#541 ROI, #547 trends, #549 defect KPI, this). The first six were each found by
someone noticing a wrong number; this one was found by sweeping for the
**class** — any query whose project filter is conditional on ``project_id`` —
which is what the previous six should have prompted sooner.

*Measurement note*: a first pass grouped the API response by ``project_name``
and looked ambiguous, because several deleted probe projects share similar
names and one **live** project shares a name with a deleted one. Resolving by
``project_id`` against ``is_active`` was necessary to state the split
correctly.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.services import release_service  # noqa: E402

pytestmark = pytest.mark.regression

SOURCE = inspect.getsource(release_service.list_releases)


def test_the_query_excludes_deleted_projects():
    assert "is_active" in SOURCE, (
        "the releases list returns releases from soft-deleted projects — "
        "measured live at 36 of 38 rows"
    )


def test_the_exclusion_is_unconditional():
    """It must not sit inside ``if project_id:`` — that is the branch that
    cannot leak, and putting it there is what this whole family of bugs is."""
    guarded = re.search(
        r"if project_id:\s*\n(?:\s+)stmt = stmt\.where\([^\n]*is_active", SOURCE
    )
    assert not guarded, (
        "the live-project filter sits inside the project_id branch, so the "
        "unscoped list still returns deleted projects"
    )


def test_tenant_isolation_is_preserved():
    """The life-cycle filter is additional.

    ``accessible_project_ids`` is the non-admin membership confinement; losing
    it while adding an is_active filter would trade an over-listing for a
    cross-tenant leak.
    """
    assert "accessible_project_ids" in SOURCE
    assert "Release.project_id.in_(list(accessible_project_ids))" in SOURCE


def test_the_project_pin_still_applies():
    assert "Release.project_id == uuid.UUID(project_id)" in SOURCE
