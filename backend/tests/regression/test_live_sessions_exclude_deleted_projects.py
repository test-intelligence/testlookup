"""The Live page must not list sessions from soft-deleted projects.

``list_active_sessions`` unions **three** sources — the Redis active set,
completed ``LiveSession`` rows, and ``live_stream`` ``TestRun`` rows — and each
was filtered only by the two *conditional* scopes:

    if project_id:            ...   # a pinned project
    elif allowed_project_ids: ...   # a non-admin's memberships

An ADMIN with no project pinned matches neither, so nothing restricted any of
the three. Measured live: of 9 entries returned, **4 belonged to two deleted
probe projects** (``ZZ Java SDK Probe``, ``ZZ SDK Probe (go/js)``).

**Eighth surface in this family** (#535 runs, #538 dashboard, #539 analytics,
#541 ROI, #547 trends, #549 defect KPI, #550 releases). The filter is applied
to all three sources deliberately: filtering one would leave the other two
leaking, and this endpoint's whole job is to merge them.

**What is NOT a bug here**, checked before filing: the endpoint returns
*completed* sessions too. Its docstring says so — *"List active + recent live
sessions"*, with ``days`` controlling the completed cutoff. The name
``/stream/active`` is loose, but the contract is explicit and intentional.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.services import stream_service  # noqa: E402

pytestmark = pytest.mark.regression

SOURCE = inspect.getsource(stream_service.list_active_sessions)


def test_all_three_sources_exclude_deleted_projects():
    """One filter per source: Redis set, LiveSession query, TestRun query."""
    assert SOURCE.count("is_active") >= 3, (
        "list_active_sessions unions three sources (Redis, LiveSession, "
        "TestRun) and each needs the live-project exclusion — filtering only "
        "some leaves the rest leaking; measured live at 4 of 9 entries from "
        "deleted projects"
    )


def test_the_redis_set_is_filtered():
    assert "live_project_ids" in SOURCE, (
        "the Redis active set is unfiltered, so a session on a deleted "
        "project still shows as live"
    )


@pytest.mark.parametrize("model", ["LiveSession", "TestRun"])
def test_each_db_query_is_filtered(model: str):
    assert re.search(
        rf"{model}\.project_id\.in_\(\s*select\(Project\.id\)\.where\(Project\.is_active",
        SOURCE,
    ), f"the {model} query does not exclude soft-deleted projects"


def test_the_conditional_scopes_are_preserved():
    """The life-cycle filter is additional.

    ``project_id`` pins one project; ``allowed_project_ids`` confines a
    non-admin to their memberships. Losing either while adding an is_active
    filter would trade an over-listing for a cross-tenant leak.
    """
    assert "if project_id:" in SOURCE
    assert "allowed_project_ids" in SOURCE


def test_completed_sessions_are_still_returned():
    """Guards against "fixing" the loose endpoint name.

    Returning completed sessions is deliberate — the docstring says "active +
    recent", and ``days`` bounds them. A future reader who trims this to
    active-only would break the Live page's recent-runs list.
    """
    assert 'LiveSession.status == "completed"' in SOURCE
    assert "days" in SOURCE
