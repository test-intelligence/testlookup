"""Regression: both search endpoints declare that they ignore the release filter.

Search is deliberately NOT release-scoped. It is a discovery tool, and scoping
it would return nothing for a test that exists but last ran in another release
— which reads as "that test does not exist", so a user hunting for a test they
know they wrote would conclude the product had lost it.

That is the right behaviour and it is invisible. The release picker sits in the
TopBar on every route, so a reader with 2.4.0 selected reasonably assumes it
applied here too. S4b's answer was that a surface ignoring a visible filter has
to DECLARE it, which `/search` did.

`/search/global` did not — and that is the endpoint behind the default "All"
chip, so it is the path most searches actually take. Two endpoints, identical
behaviour, and only one of them honest.

The declaration is now a single module constant used by both. A second copy
would drift, and the drift would be silent: nothing about a stale sentence in
one payload looks wrong.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression

from app.routers import search as search_mod  # noqa: E402


def test_the_declaration_says_search_is_not_release_scoped():
    decl = search_mod.RELEASE_SCOPE_DECLARATION
    assert decl["release"] == "not_applicable"
    assert "release" in decl["note"].lower()


def test_the_note_is_addressed_to_a_reader_not_to_a_developer():
    """It is rendered verbatim in the UI badge's tooltip.

    A note that said "release_id is not applied" would be accurate and useless
    to the person who needs it — someone wondering why their test is missing.
    """
    note = search_mod.RELEASE_SCOPE_DECLARATION["note"]
    assert "search" in note.lower()
    assert len(note) > 40, "too short to explain anything"
    # No identifier-speak leaking into user-facing copy.
    assert "release_id" not in note
    assert "not_applicable" not in note


def test_both_endpoints_use_the_same_declaration():
    """The whole point of the constant.

    Asserted on the SOURCE because the two endpoints have different shapes —
    one builds a dict literal, the other decorates a service result — so there
    is no single runtime object to compare. What matters is that neither one
    re-types the sentence.
    """
    import inspect

    src = inspect.getsource(search_mod)

    # The literal sentence appears exactly once: in the constant.
    assert src.count("Search spans every release in the project by design") == 1, (
        "the note is written out more than once — the copies will drift, and a "
        "stale sentence in one payload looks like nothing is wrong"
    )

    for fn in (search_mod.search_test_cases, search_mod.global_search_endpoint):
        assert "RELEASE_SCOPE_DECLARATION" in inspect.getsource(fn), (
            f"{fn.__name__} does not declare its release scope, so a reader "
            "with a release selected takes its results as that release's"
        )


def test_each_endpoint_gets_its_own_copy_of_the_dict():
    """`dict(...)` at each use site, not the shared object.

    Handing the same mutable dict to two responses means anything that later
    annotates one response — a middleware, a serializer, a test — mutates the
    constant and therefore every future response from both endpoints.
    """
    import inspect

    for fn in (search_mod.search_test_cases, search_mod.global_search_endpoint):
        src = inspect.getsource(fn)
        assert "dict(RELEASE_SCOPE_DECLARATION)" in src, (
            f"{fn.__name__} returns the shared constant itself; a single "
            "mutation would leak into every subsequent response"
        )


def test_global_search_attaches_scope_without_swallowing_the_result():
    """The result must still be the search result.

    Wrapping it (``return {"scope": ..., "data": result}``) would technically
    declare the scope and break every client reading `items`.
    """
    import inspect

    src = inspect.getsource(search_mod.global_search_endpoint)
    assert 'result["scope"]' in src
    assert "return result" in src
