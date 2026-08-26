"""The statement-cache key must change whenever the query's structure changes.

``search_test_cases_query`` reuses one statement per shape. The shape tuple IS
the cache key, so a structural dimension missing from it means two
differently-built queries share a statement, and whichever ran first wins.

The integration tests catch a value baked into the statement. They do not catch
a *dimension* quietly dropped from the key while the tuple keeps its arity --
that mutation only broke them by changing the tuple's length, which is luck, not
coverage. This pins each dimension by name.
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("app.services.search_service")

from app.services.search_service import (  # noqa: E402
    MIN_INDEXED_TERM_LEN,
    search_shape,
)

pytestmark = pytest.mark.regression

PROJECT = str(uuid.uuid4())
OTHER = [uuid.uuid4()]

BASE = dict(q="timeout", project_id=None, status=None, days=None, allowed=None)


def _shape(**overrides):
    args = {**BASE, **overrides}
    return search_shape(
        args["q"], args["project_id"], args["status"], args["days"], args["allowed"]
    )


@pytest.mark.parametrize(
    "label, overrides",
    [
        ("term drops below the indexed threshold", {"q": "a" * (MIN_INDEXED_TERM_LEN - 1)}),
        ("project pin added", {"project_id": PROJECT}),
        ("allow-list added", {"allowed": OTHER}),
        ("empty allow-list (no memberships)", {"allowed": []}),
        ("status filter added", {"status": "FAILED"}),
        ("date window added", {"days": 7}),
    ],
)
def test_each_structural_input_changes_the_shape(label, overrides):
    assert _shape(**overrides) != _shape(), (
        label + " does not change the cache key, so that query would reuse a "
        "statement built for a different structure"
    )


def test_values_alone_do_not_change_the_shape():
    """The counterpart: two different projects MUST share a shape.

    If they did not, the cache would hold one entry per tenant and grow without
    bound -- and the cross-tenant test in
    ``tests/integration/test_search_statement_cache_postgres.py`` would stop
    exercising a shared entry, quietly ceasing to test what it claims to.
    """
    a = _shape(project_id=str(uuid.uuid4()))
    b = _shape(project_id=str(uuid.uuid4()))
    assert a == b, "two pinned projects should share one statement shape"

    long_a = _shape(q="timeout")
    long_b = _shape(q="connection reset by peer")
    assert long_a == long_b, "two long search terms should share one shape"


def test_empty_and_populated_allow_lists_are_different_shapes():
    """"No memberships" compiles to a constant-false predicate; a populated list
    compiles to an expanding bind parameter. Sharing one statement between them
    would hand a user with no access the statement built for someone with it."""
    assert _shape(allowed=[]) != _shape(allowed=OTHER)


def test_the_shape_space_stays_small():
    """The cache is unbounded only if the shape space is. 2 term forms x 4
    scopes x status x days = 32; a change that makes this grow with values
    turns the cache into a memory leak."""
    seen = set()
    for q in ("ab", "timeout"):
        for scope in (None, PROJECT):
            for allowed in (None, [], OTHER):
                for status in (None, "FAILED"):
                    for days in (None, 7):
                        seen.add(search_shape(q, scope, status, days, allowed))
    assert len(seen) <= 32, "shape space grew to " + str(len(seen))
