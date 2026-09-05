"""Regression: the last two read surfaces, and why they differ.

`/my-failures` and the emailed trends report were scoped like every other
windowed read. These two could not be, and copying S4b's two answers is the
point of this file.

**The flaky-coach leaderboard is INTERSECTED, not rescoped.** It is a
rolling-window statistic keyed on (project, fingerprint) whose intermittency
signal needs several observations of the same test; a three-day hotfix rarely
provides them. Recomputing per release would multiply the work while answering
"no data" more often than it answered. So the release selects WHICH ranked tests
are shown and each keeps its full-window score — which makes the result
mixed-scope, and that has to be stated rather than left to inference.

**Search is deliberately NOT scoped at all.** It is a discovery tool. Scoping it
would return nothing for a test that exists but last ran in another release,
which reads as "that test does not exist" — a user hunting for a test they wrote
would conclude the product had lost it. A surface that ignores a filter the
header is showing has to declare that, or the user reasonably assumes it
applied.
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression

from app.models.schemas import FlakyCoachScope  # noqa: E402

PROJECT = uuid.uuid4()
RELEASE = "3f9a5f7e-1c2b-4d6a-9e8f-0a1b2c3d4e5f"


# ── The leaderboard intersects ───────────────────────────────────────────────


def test_the_scope_block_defaults_to_project():
    """Absent release means the payload keeps its old meaning."""
    scope = FlakyCoachScope()
    assert scope.membership == "project"
    assert scope.score == "project_window"
    assert scope.release_id is None


def test_the_scope_block_separates_membership_from_score():
    """The two halves are different, and conflating them is the whole risk.

    A filtered list looks entirely release-scoped. Saying `score` stays
    project-window is what stops a reader taking an impact number as "how flaky
    during 2.4.0".
    """
    scope = FlakyCoachScope(membership="release", release_id=RELEASE, note="…")
    assert scope.membership == "release"
    assert scope.score == "project_window", (
        "the score must NOT claim to be release-scoped — it is not recomputed"
    )


def test_the_response_carries_the_scope_only_as_an_addition():
    """Additive: a consumer that never reads `scope` is unaffected."""
    from app.models.schemas import FlakyCoachResponse

    assert FlakyCoachResponse(project_id=str(PROJECT)).scope is None


def test_the_leaderboard_intersects_rather_than_rescoping():
    """Read from the code, because the distinction is the design.

    A rescope would recompute the statistic per release; an intersection filters
    an already-ranked list. If this ever becomes a rescope, the evidence floor
    stops being met for most releases and the leaderboard mostly empties.
    """
    import inspect

    from app.services import test_health_coach_service as svc

    src = inspect.getsource(svc.get_flaky_coach)
    assert "_fingerprints_in_release" in src, (
        "the release no longer selects which ranked tests are shown"
    )

    # The filter is applied to the ALREADY-BUILT list, which is what makes it
    # an intersection. Asserted structurally rather than by hunting for a
    # substring: the first version of this test looked for
    # `release_id=release_id` as a rescope signature and matched the scope
    # block, which passes exactly that — legitimately.
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(src))
    rebinds_entries = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", "") == "entries" for t in n.targets)
        and isinstance(n.value, ast.ListComp)
    ]
    assert rebinds_entries, (
        "the release is not filtering the ranked list — if it moved into the "
        "ranking query that is a rescope, and this statistic cannot support one"
    )


def test_the_intersection_is_project_scoped_on_both_sides():
    """``test_fingerprint`` is unique only within a project, so an unscoped
    lookup would let another project's run admit a test into this leaderboard.
    """
    import inspect

    from app.services import test_health_coach_service as svc

    src = inspect.getsource(svc._fingerprints_in_release)
    assert "TestRun.project_id == project_id" in src
    assert "release_predicate" in src, (
        "the intersection restates the release rule instead of sharing it"
    )


# ── Search declares that it ignores the filter ───────────────────────────────


def test_search_declares_itself_release_agnostic():
    """The labelling half of S4b, which the design gate found unmet.

    Checked on the response the router builds: a comment explaining the
    decision is not something the UI can render.
    """
    import inspect

    from app.routers import search as router_mod

    src = inspect.getsource(router_mod.search_test_cases)
    assert '"scope"' in src, (
        "search returns no scope block, so a user cannot tell whether the "
        "release filter they can see applied to these results"
    )
    assert '"not_applicable"' in src


def test_search_takes_no_release_parameter():
    """The decision, pinned. If someone later adds one, this fails and they
    have to argue with the reasoning rather than silently scoping a discovery
    tool."""
    import inspect

    from app.routers import search as router_mod

    sig = inspect.signature(router_mod.search_test_cases)
    assert "release_id" not in sig.parameters, (
        "search now takes a release — a test that exists but last ran in "
        "another release would come back as no results, which reads as the "
        "test not existing"
    )
