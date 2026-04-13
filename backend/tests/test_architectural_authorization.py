"""
Architectural authorization test — ratchets out scope-check drift.

Every API route that takes a scoped path parameter (``project_id``,
``run_id``, ``session_id``, ``link_id``, …) must either:

  * include an approved guard in its dependency chain, **or**
  * be listed as a known exemption in ``KNOWN_EXEMPT``.

The exemption list is a **backlog**, not a license: CI fails if an entry
is added without review, and also fails if an entry becomes stale (the
route no longer exists or has been refactored onto a guard). Over time
``KNOWN_EXEMPT`` should only shrink.

Guards live in ``app.core.deps``:

  * ``require_project_access`` — reads ``{project_id}`` and checks
    ``ProjectMember`` for the current user (ADMIN bypasses).
  * ``require_run_access`` — reads ``{run_id}``, resolves to a project,
    checks membership.
  * ``require_session_access`` — reads ``{session_id}``, returns a loaded
    :class:`ChatSession` owned by the caller (creator-only + ADMIN).
  * ``require_link_access`` — reads ``{link_id}``, returns a loaded
    :class:`ReportShareLink` the caller may revoke (creator, project
    members, or ADMIN).

A route protected by one of the above covers every nested ID beneath it
(members, clusters, findings, etc.), so we match by the *outermost*
scoped param in the path.
"""
from __future__ import annotations

from fastapi.routing import APIRoute

from app.bootstrap import PROTECTED_ROUTERS, PUBLIC_ROUTERS

# Path params that have an approved guard today.
GUARDED_PARAMS: dict[str, str] = {
    "project_id": "require_project_access",
    "run_id": "require_run_access",
}

# Path params that still have **no guard** — routes using these as a
# primary scope must be listed as exempt until a guard is added.
UNGUARDED_SCOPED_PARAMS: frozenset[str] = frozenset({
    "session_id",
    "link_id",
    "release_id",
    "key_id",
    "rule_id",
    "source_id",
    "batch_id",
})

# ── Known backlog of unprotected scoped routes ──────────────────────────────
#
# Each entry is (METHOD, path_template). **Adding** entries requires team
# sign-off; **removing** them is always welcome.
#
# When you add a guard to a route, delete its entry from this set. CI will
# fail via ``stale_exempt`` if you forget.
#
# Grouped by router module — fix one module at a time.
KNOWN_EXEMPT: frozenset[tuple[str, str]] = frozenset({
    # ── stream SSE — token arrives as query param, guard runs inline ────
    # The endpoint decodes the token, loads the user, and checks
    # ProjectMember directly. The architectural ratchet can't see that
    # inline check, so this entry stays until the SSE auth flow is
    # refactored onto a proper dependency.
    ("GET",    "/api/v1/stream/sse/{project_id}"),
})


# ── Helpers ─────────────────────────────────────────────────────────────────


def _walk_deps(dependant) -> list[str]:
    """Return the fully-qualified name of every callable in a route's
    dependency tree (recursive)."""
    out: list[str] = []
    stack = [dependant]
    while stack:
        dep = stack.pop()
        call = dep.call
        if call is not None:
            out.append(getattr(call, "__qualname__", None) or type(call).__name__)
        stack.extend(dep.dependencies)
    return out


def _collect_api_routes():
    """Every ``APIRoute`` mounted under the public or protected router groups."""
    for router in list(PROTECTED_ROUTERS) + list(PUBLIC_ROUTERS):
        for route in router.routes:
            if isinstance(route, APIRoute):
                yield route


def _scoped_params_in_path(path: str) -> set[str]:
    return {
        p
        for p in (set(GUARDED_PARAMS) | UNGUARDED_SCOPED_PARAMS)
        if "{%s}" % p in path
    }


def _route_is_protected(route: APIRoute) -> bool:
    """A scoped route is considered protected if its outermost scope is
    covered by the matching guard."""
    path = route.path
    params = _scoped_params_in_path(path)
    if not params:
        return True  # no scoped param at all — nothing to guard

    dep_names = _walk_deps(route.dependant)

    # Outermost project scope covers everything nested under it.
    if "{project_id}" in path and any("require_project_access" in n for n in dep_names):
        return True
    # Run scope covers nested cluster/finding/test IDs under a run.
    if "{run_id}" in path and any("require_run_access" in n for n in dep_names):
        return True
    # Release → project.
    if "{release_id}" in path and any("require_release_access" in n for n in dep_names):
        return True
    # Knowledge source → project.
    if "{source_id}" in path and any("require_knowledge_source_access" in n for n in dep_names):
        return True
    # RAG generation batch → project.
    if "{batch_id}" in path and any("require_generation_batch_access" in n for n in dep_names):
        return True
    # Chat session (creator-only).
    if "{session_id}" in path and any("require_session_access" in n for n in dep_names):
        return True
    # Live test-execution session → project.
    if "{session_id}" in path and any("require_live_session_access" in n for n in dep_names):
        return True
    # Report share link (creator / project member / admin).
    if "{link_id}" in path and any("require_link_access" in n for n in dep_names):
        return True
    # API key — owner/admin check.
    if "{key_id}" in path and any("require_api_key_owner" in n for n in dep_names):
        return True

    return False


def _list_unprotected_routes() -> list[tuple[str, str]]:
    unprotected: set[tuple[str, str]] = set()
    for route in _collect_api_routes():
        if not route.path.startswith("/api/v1"):
            continue
        if _route_is_protected(route):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            unprotected.add((method, route.path))
    return sorted(unprotected)


# ── The test ────────────────────────────────────────────────────────────────


def test_every_scoped_route_has_guard_or_is_exempt() -> None:
    """Ratchet: new unprotected scoped routes fail CI.

    Two failure modes:

    1. **Unexpected drift** — a route takes a scoped param but neither
       carries an approved guard nor appears in ``KNOWN_EXEMPT``. Add the
       guard, do not extend the exempt list.
    2. **Stale exemption** — a route listed in ``KNOWN_EXEMPT`` is now
       protected (or has been removed). Delete the entry.
    """
    unprotected = set(_list_unprotected_routes())

    unexpected = sorted(unprotected - KNOWN_EXEMPT)
    stale = sorted(KNOWN_EXEMPT - unprotected)

    errors: list[str] = []
    if unexpected:
        bullets = "\n  ".join(f"{m:6s} {p}" for m, p in unexpected)
        errors.append(
            "New unprotected scoped routes detected — add an authorization "
            "guard in core/deps.py rather than extending KNOWN_EXEMPT:\n  "
            + bullets
        )
    if stale:
        bullets = "\n  ".join(f"{m:6s} {p}" for m, p in stale)
        errors.append(
            "Stale KNOWN_EXEMPT entries — these routes are now protected "
            "(or have been removed). Delete them from KNOWN_EXEMPT:\n  "
            + bullets
        )

    assert not errors, "\n\n".join(errors)


def test_known_exempt_is_only_a_backlog() -> None:
    """Sanity check: the backlog list should shrink, never grow silently.

    This test just asserts the exemption set is not unbounded — if it ever
    exceeds a reasonable cap, someone has been adding without cleaning up.
    """
    cap = 3
    assert len(KNOWN_EXEMPT) <= cap, (
        f"KNOWN_EXEMPT has {len(KNOWN_EXEMPT)} entries (cap={cap}). "
        "Remove entries by adding guards rather than raising the cap."
    )
