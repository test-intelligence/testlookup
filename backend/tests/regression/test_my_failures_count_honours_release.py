"""Regression: the count endpoint answers the same question as the list.

`/me/assigned-failures/count` exists, by its own docstring, to answer the list
endpoint's question cheaply — "the list endpoint already returns
``unresolved_total`` for pages it serves, but the sidebar polls independently".
It copies the list's filters for exactly that reason, including the
soft-deleted-projects one, where it says:

    ...and, like the list, skips soft-deleted projects. If these two ever
    disagree the badge advertises work the page cannot show.

When the epic put `release_id` on the list it did not reach here — a separate
handler with its own filter list. So a client could scope the list by release
and get an unscoped count back from the endpoint built to mirror it.

**What this is NOT.** An earlier draft of this file justified the change by
saying the sidebar badge would otherwise disagree with a release-scoped page.
That is wrong, and worth recording: the sidebar renders
`useMyFailuresCountUnscoped`, which sends neither `project_id` nor `release_id`
because it is an "all my work" indicator spanning every project. It was never
mirroring the page. The real argument is narrower and still holds — the two
endpoints are a documented pair, and a pair that accepts different filters is
one a client author has to discover by experiment.
"""
from __future__ import annotations

import inspect

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression

from app.routers import my_failures as router_mod  # noqa: E402

LIST_SRC = inspect.getsource(router_mod.list_my_assigned_failures)
COUNT_SRC = inspect.getsource(router_mod.my_assigned_failures_count)


def test_the_count_endpoint_accepts_a_release():
    sig = inspect.signature(router_mod.my_assigned_failures_count)
    assert "release_id" in sig.parameters, (
        "the sidebar badge cannot be told which release the page is showing, so "
        "it counts project-wide work beside a release-scoped table"
    )


def test_the_count_applies_the_release_the_same_way_the_list_does():
    """Same helper, not a re-implementation.

    A hand-rolled predicate here would be a second definition of "in this
    release" — and the Unattributed sentinel is a literal string rather than a
    uuid, so a re-implementation gets it wrong in a way that returns rows
    instead of failing.
    """
    assert "release_predicate(release_id)" in COUNT_SRC
    assert "release_predicate(release_id)" in LIST_SRC


def test_the_count_resolves_the_release_before_trusting_it():
    """`resolve_release_query_scope` is the access check.

    Without it the badge would count rows from a release in a project the
    caller cannot read — a number is a small leak, but it is still a leak, and
    it is the one this endpoint could produce without ever rendering a row.
    """
    assert "resolve_release_query_scope" in COUNT_SRC, (
        "the count takes a release id from the query string and applies it "
        "unverified"
    )


def test_the_release_goes_into_the_same_filter_list_as_every_other_predicate():
    """Not a separate `if release_id:` branch bolted onto the statement.

    The filters list is what makes the count's scoping legible next to the
    list's. A predicate applied somewhere else is the shape that drifts.
    """
    filters_block = COUNT_SRC.split("stmt =")[0]
    assert "filters.extend(release_predicate(release_id))" in filters_block, (
        "the release predicate is applied outside the shared filters list"
    )


def test_omitting_the_release_leaves_the_count_query_unchanged():
    """NFR1 for this endpoint.

    `release_predicate(None)` returns an empty list, so a caller that sends no
    release produces exactly the SQL it produced before — which is what keeps
    the sidebar's polling path off the release index it does not need.
    """
    from app.core.release_filter import release_predicate

    assert release_predicate(None) == []


@pytest.mark.parametrize("endpoint", ["list_my_assigned_failures", "my_assigned_failures_count"])
def test_both_endpoints_describe_the_parameter_the_same_way(endpoint):
    """The two are read together in the OpenAPI schema.

    Two different descriptions for one concept is how a client author concludes
    they mean different things.
    """
    src = inspect.getsource(getattr(router_mod, endpoint))
    assert "Only failures from runs in this release." in src
