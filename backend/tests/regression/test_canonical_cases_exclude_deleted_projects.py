"""The canonical test-case list must not return cases from deleted projects.

``list_canonical_test_cases`` applied a project filter only when the caller
supplied one. The router passes ``project_ids=None`` for an ADMIN (no
membership confinement), so the unscoped list applied **no project filter at
all**. Measured live:

===============================  =========  ========
projects                         cases      projects
===============================  =========  ========
``is_active = false`` (deleted)  **1,205**  36
``is_active = true``                   32   2
API, unscoped                    **1,237**  38
===============================  =========  ========

**97% of the Test Management canonical-case list** was tests belonging to
projects the user cannot open, filter by, or navigate to.

Ninth surface in this family (#535 runs, #538 dashboard, #539 analytics, #541
ROI, #547 trends, #549 defect KPI, #550 releases, #551 live page). Found by
giving `canonical-test-cases/:canonicalId` its first coverage — the *detail*
page's edge cases were all correct (200 / 404 / 422); the **list** behind it
was not.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.services import test_suite_service  # noqa: E402

pytestmark = pytest.mark.regression

SOURCE = inspect.getsource(test_suite_service.list_canonical_test_cases)


def test_the_query_excludes_deleted_projects():
    assert "is_active" in SOURCE, (
        "the canonical-case list returns cases from soft-deleted projects — "
        "measured live at 1,205 of 1,237 rows"
    )


def test_the_exclusion_is_unconditional():
    """It must not sit inside ``if project_ids is not None:`` — that branch is
    the membership-confined one, which cannot leak. The ADMIN path passes
    ``None`` and is exactly the case that over-returned."""
    guarded = re.search(
        r"if project_ids is not None:\s*\n(?:[^\n]*\n){0,3}?[^\n]*is_active", SOURCE
    )
    assert not guarded, (
        "the live-project filter sits inside the project_ids branch, so the "
        "ADMIN (project_ids=None) path still returns deleted projects"
    )


def test_the_membership_restriction_is_preserved():
    """The life-cycle filter is additional. Losing the membership confinement
    while adding it would trade an over-listing for a cross-tenant leak."""
    assert "CanonicalTestCase.project_id.in_(project_ids)" in SOURCE
    assert "if not project_ids:" in SOURCE, (
        "the empty-membership fail-closed branch was removed"
    )


def test_other_filters_still_apply():
    """suite_id and status are independent of this change."""
    assert "CanonicalTestCase.test_suite_id == suite_id" in SOURCE
    assert "CanonicalTestCase.status == status_filter" in SOURCE
