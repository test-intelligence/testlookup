"""Suites and quarantine lists must not return soft-deleted projects.

Both apply their project filter only when the caller supplies one. The routers
pass ``project_ids=None`` for an ADMIN (no membership confinement), so the
unscoped lists applied **no project filter at all**. Measured live:

=====================  =========================  ==================
list                   deleted-project rows       total returned
=====================  =========================  ==================
``/api/v1/suites``     **93** (of 103) — 90%      103
``/api/v1/quarantine`` **3** (of 3) — **100%**    3
=====================  =========================  ==================

Every quarantine proposal in the queue was for a test in a project nobody can
open.

**Tenth and eleventh surfaces in this family**, and both were found by widening
the class sweep after F-072. The first sweep looked for ``if project_id:``
guarding a filter in one function; this shape is split across two files — the
*router* computes ``project_ids = None if accessible is None else
list(accessible)`` and the *service* skips filtering when it receives ``None``.
Nothing in a single-function scan matches that.

`quarantine/stats` was checked in the same pass and returns zeros through a
different code path; `webhooks` has no rows. Neither is claimed as covered.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.services import flaky_quarantine_service, test_suite_service  # noqa: E402

pytestmark = pytest.mark.regression

CASES = {
    "list_test_suites": (test_suite_service.list_test_suites, "TestSuite"),
    "list_requests": (flaky_quarantine_service.list_requests, "FlakyQuarantineRequest"),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_list_excludes_deleted_projects(name: str):
    fn, _ = CASES[name]
    src = inspect.getsource(fn)
    assert "is_active" in src, (
        f"{name} returns rows from soft-deleted projects — measured live at "
        f"93/103 suites and 3/3 quarantine requests"
    )


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_exclusion_is_unconditional(name: str):
    """It must not sit inside ``if project_ids is not None:`` — that branch is
    the membership-confined one, which cannot leak. The ADMIN path passes
    ``None`` and is exactly the case that over-returned."""
    fn, _ = CASES[name]
    src = inspect.getsource(fn)
    guarded = re.search(
        r"if project_ids is not None:\s*\n(?:[^\n]*\n){0,4}?[^\n]*is_active", src
    )
    assert not guarded, f"{name} applies the filter only on the scoped branch"


@pytest.mark.parametrize("name", sorted(CASES))
def test_membership_confinement_is_preserved(name: str):
    """The life-cycle filter is additional. Losing the membership restriction
    or its fail-closed empty branch would trade an over-listing for a
    cross-tenant leak."""
    fn, model = CASES[name]
    src = inspect.getsource(fn)
    assert f"{model}.project_id.in_(project_ids)" in src
    assert "if not project_ids:" in src


def test_quarantine_status_filters_still_apply():
    """The quarantine list also filters by lifecycle state; the new clause
    must not have displaced it."""
    src = inspect.getsource(flaky_quarantine_service.list_requests)
    assert "status == status_filter" in src
    assert "_LIVE_STATES" in src
