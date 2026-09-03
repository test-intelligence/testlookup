"""Regression: the release axis on run-backed reads (S4a).

Three properties, and the first is what lets this whole read path ship
incrementally.

**NFR1 — omitting ``release_id`` changes nothing.** Not "returns similar
results": the SQL must be byte-identical to what it was before the release axis
existed, and no extra query may be issued. Every endpoint here is on a hot path
that the web app already calls, so a regression is not a wrong number, it is a
slower or subtly different dashboard for every user who never asked for a
release.

**The predicate must not defeat the index.** The obvious null-tolerant form
``AND (:release_id IS NULL OR tr.primary_release_id = :release_id)`` reads
better and stops the planner using
``ix_test_runs_project_release_created`` — on every call, including the
overwhelming majority that pass no release.

**The guard is per-id, not per-route.** The architectural authorization ratchet
checks evidence per ROUTE and stops at the first scoped parameter it can
satisfy, so a route already carrying ``project_id`` passes it with
``release_id`` entirely unchecked.
"""
from __future__ import annotations

import inspect

import pytest

from app.services import analytics_service as svc

# Every analytics function that filters test_runs directly. The precomputed
# aggregate endpoints (/flaky-scores, /systemic-clusters) are deliberately NOT
# here — they read per-project derived tables with no run dimension, so no
# predicate on test_runs can reach them. That is S4b, and until it lands those
# surfaces must be labelled project-scoped rather than silently answering
# project-wide under a release filter.
RUN_BACKED = (
    "flaky_tests",
    "failure_categories",
    "top_failing_tests",
    "coverage_stats",
    "suite_detail",
)


def _code_only(fn) -> str:
    """Source with the docstring removed.

    These helpers document the wrong shapes by name — "``(:p IS NULL OR ...)``
    is the form that loses the index", "not a join to
    ``release_test_run_links``" — so a naive substring check over the raw
    source matches the explanation and fails on correct code. Worse, if the
    prose were ever removed the same assertion would start passing for the
    wrong reason. Assert against the executable body only.
    """
    src = inspect.getsource(fn)
    doc = fn.__doc__
    return src.replace(doc, "", 1) if doc else src


# ── NFR1 ─────────────────────────────────────────────────────────────────────


def test_the_fragment_is_empty_and_costs_nothing_when_no_release_is_asked_for():
    """The literal mechanism behind NFR1.

    An empty fragment interpolates to nothing, so the rendered SQL is
    character-for-character what it was before S4a — and ``params`` gains no
    key, so no bind is added either.
    """
    params: dict = {}
    assert svc._add_release_param(params, None) == ""
    assert params == {}, "an absent release must not add a bind parameter"


def test_the_fragment_binds_rather_than_interpolates_the_value():
    """A release id reaching the SQL by string interpolation would be an
    injection point on a user-supplied query parameter.
    """
    params: dict = {}
    frag = svc._add_release_param(params, "11111111-2222-3333-4444-555555555555")
    assert ":release_id" in frag
    assert "11111111" not in frag, "the value must be bound, never interpolated"
    assert params["release_id"] == "11111111-2222-3333-4444-555555555555"


@pytest.mark.parametrize("fn_name", RUN_BACKED)
def test_every_run_backed_function_takes_an_optional_release(fn_name):
    """Optional with a None default — never required.

    A required parameter would break every existing caller, which is the
    opposite of shipping this incrementally.
    """
    sig = inspect.signature(getattr(svc, fn_name))
    assert "release_id" in sig.parameters
    assert sig.parameters["release_id"].default is None


@pytest.mark.parametrize("fn_name", RUN_BACKED)
def test_every_run_backed_function_interpolates_the_fragment(fn_name):
    """A parameter accepted and then ignored is worse than not accepting it:
    the caller believes the filter applied.
    """
    src = inspect.getsource(getattr(svc, fn_name))
    assert "_add_release_param(params, release_id)" in src
    assert "{release_filter}" in src


# ── The index trap ───────────────────────────────────────────────────────────


def test_the_predicate_is_conditional_not_null_tolerant():
    """``(:p IS NULL OR col = :p)`` is the shape that loses the index.

    The planner cannot know at plan time which branch applies, so it stops
    using ix_test_runs_project_release_created and scans — for every call,
    including all the ones that pass no release at all. The cost lands on
    users who never asked for the feature.
    """
    body = _code_only(svc._add_release_param)
    assert "IS NULL OR" not in body
    # And the empty-string early return is what makes it conditional.
    assert 'return ""' in body


def test_the_predicate_reads_the_denormalized_column_not_the_link_table():
    """Joining release_test_run_links would multiply rows for any run linked to
    more than one release, silently inflating every COUNT and AVG in this
    module. The denormalized column (migration 0152) is one row per run.
    """
    body = _code_only(svc._add_release_param)
    assert "primary_release_id" in body
    assert "release_test_run_links" not in body


def test_suite_detail_scopes_every_one_of_its_subqueries():
    """suite_detail has five filter sites across subqueries, including the
    old-SDK fallback path.

    Scoping only some of them would make a single response internally
    inconsistent — a suite total filtered to a release beside a per-test
    breakdown that was not — which reads as a data bug, not a missing filter.
    """
    src = inspect.getsource(svc.suite_detail)
    assert src.count("{release_filter}") == 5, (
        f"expected all 5 subquery sites scoped, found {src.count('{release_filter}')}"
    )


# ── The guard ────────────────────────────────────────────────────────────────


def test_the_query_guard_is_a_distinct_function_from_the_path_guard():
    """``require_release_access`` reads a PATH param as a Depends.

    The release axis is an optional QUERY param on routes that already carry
    their own scoped id, and the ratchet checks evidence per-route — it stops
    at the first scoped parameter it can satisfy. So these routes would pass
    the ratchet with the release unchecked, and nothing would report it.
    """
    from app.core import deps

    assert hasattr(deps, "resolve_release_query_scope")
    src = inspect.getsource(deps.resolve_release_query_scope)
    # Must short-circuit before touching the database when nothing was asked.
    early = src[: src.index("from app.models.postgres import Release")]
    assert "if release_id is None:" in early
    assert "return None" in early


@pytest.mark.asyncio
async def test_the_guard_issues_no_query_when_no_release_is_requested():
    """Part of NFR1: the absent path must cost nothing, not merely return None.

    A guard that looked the release up before checking for None would add a
    round trip to every call of five hot endpoints.
    """
    from unittest.mock import AsyncMock

    from app.core.deps import resolve_release_query_scope

    db = AsyncMock()
    got = await resolve_release_query_scope(db, None, object())
    assert got is None
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_malformed_release_id_is_rejected_before_the_database():
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from app.core.deps import resolve_release_query_scope

    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await resolve_release_query_scope(db, "not-a-uuid", object())
    assert exc.value.status_code == 422
    db.execute.assert_not_awaited()


@pytest.mark.parametrize("fn_name", RUN_BACKED)
def test_every_route_guards_the_release_before_using_it(fn_name):
    """The parameter must be validated on the way in, not trusted.

    Without this a caller could scope any of these endpoints to a release in a
    project they cannot read — and because the filter narrows rather than
    widens, the response would look unremarkable.
    """
    from app.routers import analytics

    src = inspect.getsource(getattr(analytics, fn_name))
    assert "resolve_release_query_scope(db, release_id, current_user)" in src
    guard_at = src.index("resolve_release_query_scope")
    passed_at = src.index("release_id=release_id")
    assert guard_at < passed_at, (
        "the release must be validated before it reaches the service layer"
    )


# ── Scope boundary ───────────────────────────────────────────────────────────


def test_systemic_clusters_still_takes_no_release():
    """The derived-table boundary, checked on the ROUTER where these live.

    The original version of this test looked them up on ``analytics_service``,
    where neither exists — so ``getattr`` returned None, the loop skipped, and
    it passed while asserting nothing. Found when S4b changed the answer for
    one of the two and the test did not notice.

    ``systemic_clusters`` reads a per-project derived table with no run
    dimension. Accepting a release_id would mean the caller believes a filter
    applied while getting project-wide clusters.
    """
    from app.routers import analytics

    assert "release_id" not in inspect.signature(analytics.systemic_clusters).parameters, (
        "systemic-clusters reads a derived table with no run dimension — a "
        "release_id here would be accepted and silently ignored"
    )
