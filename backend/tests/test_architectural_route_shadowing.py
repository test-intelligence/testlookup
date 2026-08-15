"""No route may be unreachable because an earlier route shadows it.

FastAPI matches routes in **registration order**. A literal path segment
declared after a same-shape parameter route is therefore dead: the parameter
route wins, the literal is fed to the parameter's validator, and every request
gets a 422 that names a field the caller never sent.

Two live instances on the reference deployment, both confirmed by calling them:

``POST /api/v1/feedback/jira-webhook``::

    {"detail":[{"type":"uuid_parsing","loc":["path","analysis_id"],
      "msg":"Input should be a valid UUID, invalid character: found `j` at 1",
      "input":"jira-webhook"}]}

``GET /api/v1/test-management/cases/stale``::

    {"detail":[{"type":"uuid_parsing","loc":["path","case_id"],
      "msg":"Input should be a valid UUID, invalid character: found `s` at 1",
      "input":"stale"}]}

The first is a **webhook an external system posts to** — Jira had been getting a
422 for every delivery, and nothing in the product would ever have reported it,
because from the application's point of view nothing failed.

The two arose differently, which is why the check is structural rather than a
convention about one file. ``jira-webhook`` was shadowed *within a single
router* (declared at line 268, below its parameter route at line 127).
``cases/stale`` was shadowed *across routers*, by registration order in
``bootstrap.py``. A reviewer looking at either file alone would see nothing
wrong.

This test walks the assembled application's own routing table, so it is exact:
it asks what FastAPI will actually match, not what the source looks like.
"""
from __future__ import annotations

import pytest


def _segments(path: str) -> list[str]:
    return [s for s in path.strip("/").split("/")]


def _is_param(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


def _shadows(earlier: str, later: str) -> bool:
    """Would ``earlier`` swallow a request intended for ``later``?

    True only when they have the same segment count, every literal segment
    agrees, and at least one of ``earlier``'s segments is a parameter sitting
    where ``later`` has a literal — which is precisely the unreachable case.
    """
    a, b = _segments(earlier), _segments(later)
    if len(a) != len(b):
        return False
    swallowed = False
    for seg_a, seg_b in zip(a, b):
        if _is_param(seg_a):
            if _is_param(seg_b):
                continue
            swallowed = True
            continue
        if seg_a != seg_b:
            return False
    return swallowed


def _collect_routes():
    from app.main import app

    routes = []
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path and methods:
            routes.append((path, set(methods)))
    return routes


def _find_shadowed():
    routes = _collect_routes()
    found = []
    for index, (path, methods) in enumerate(routes):
        if any(_is_param(s) for s in _segments(path)):
            # Only a fully-literal path can be rendered unreachable this way.
            continue
        for earlier_path, earlier_methods in routes[:index]:
            if not (methods & earlier_methods):
                continue
            if _shadows(earlier_path, path):
                found.append((path, sorted(methods), earlier_path))
                break
    return found


def test_no_route_is_shadowed_by_an_earlier_parameter_route():
    """The regression itself.

    A failure here means the named endpoint is **dead in production** and
    answers 422 to every caller. Fix by declaring the literal route before the
    parameter route (same file), or by registering its router earlier
    (``bootstrap.py``) — not by renaming the endpoint.
    """
    shadowed = _find_shadowed()
    assert not shadowed, "unreachable routes:\n" + "\n".join(
        f"  {','.join(m)} {p}  ← shadowed by earlier {q}" for p, m, q in shadowed
    )


def test_the_two_endpoints_that_were_dead_are_reachable():
    """Named explicitly so a re-ordering that revives the bug is unambiguous
    about what it broke, rather than just failing the generic check."""
    routes = _collect_routes()
    shadowed_paths = {p for p, _m, _q in _find_shadowed()}
    for path in (
        "/api/v1/feedback/jira-webhook",
        "/api/v1/test-management/cases/stale",
    ):
        assert any(p == path for p, _ in routes), f"{path} is not registered at all"
        assert path not in shadowed_paths, f"{path} is unreachable again"


# ── The detector must actually detect ────────────────────────────────────────
#
# A structural check that quietly matches nothing is the failure mode that made
# the original bug survive: it reports OK because it could not see, not because
# nothing was wrong.

@pytest.mark.parametrize(
    "earlier,later,expected",
    [
        # The two real cases.
        ("/api/v1/feedback/{analysis_id}", "/api/v1/feedback/jira-webhook", True),
        ("/api/v1/tm/cases/{case_id}", "/api/v1/tm/cases/stale", True),
        # Different length — no conflict.
        ("/a/{id}", "/a/b/c", False),
        # Literal mismatch earlier in the path — no conflict.
        ("/a/{id}/x", "/b/lit/x", False),
        # Trailing literal differs — no conflict.
        ("/a/{id}/citations", "/a/export/excel", False),
        # Identical literals — a duplicate, not a shadow of this kind.
        ("/a/b", "/a/b", False),
        # Parameter shadowing a parameter is not an unreachable literal.
        ("/a/{x}", "/a/{y}", False),
        # Shadowing deeper in the path still counts.
        ("/a/b/{id}", "/a/b/latest", True),
    ],
)
def test_the_shadow_detector_is_not_vacuous(earlier, later, expected):
    assert _shadows(earlier, later) is expected


def test_the_route_table_is_actually_populated():
    """If the app exposed no routes this whole file would pass by doing
    nothing — the same fail-open shape the check exists to prevent."""
    routes = _collect_routes()
    assert len(routes) > 100, f"only {len(routes)} routes collected"
