"""
Architectural authorization test — ratchets out scope-check drift.

Every API route that takes a scoped path parameter (``project_id``,
``run_id``, ``case_id``, ``canonical_id``, ``session_id``, ``link_id``, …)
must either:

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
    # Test-management resources have router-local guards rather than guards
    # in ``core.deps``. They still belong in the path scan: leaving either
    # name out makes every new lifecycle/promotion route pass vacuously as
    # "no scoped param at all".
    "case_id",
    "canonical_id",
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
    # ── stream session GET/DELETE — dual-auth (JWT or X-API-Key) ────────
    # The standard ``require_live_session_access`` depends on
    # ``get_current_active_user`` which is JWT-only and breaks SDK
    # callers that authenticate with ``X-API-Key``. The membership check
    # was moved inline into ``stream_service.get_session`` /
    # ``close_session`` and honours either auth path via the
    # ``bound_project_id`` derived from ``get_api_key_context``. Stays
    # exempt until the guard is refactored onto ``get_current_user_or_api_key``.
    ("GET",    "/api/v1/stream/sessions/{session_id}"),
    ("DELETE", "/api/v1/stream/sessions/{session_id}"),
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
    # Authored test case -> project. This guard is router-local because the
    # shared test-management module also returns the loaded case to handlers.
    if "{case_id}" in path:
        evidence = _authorization_evidence(route)
        if (
            any("require_case_access" in n for n in dep_names)
            or "require_case_access" in evidence
            or "_require_case_project_access" in evidence
            or "resolve_project_scope" in evidence
        ):
            return True
    # Canonical automation case -> project. Existing suite routes perform the
    # check inline through ``_enforce_project_access``; newer lifecycle routes
    # may use a dependency named ``require_canonical_case_access``. Inspect the
    # handler/callee evidence as well as dependencies so both safe forms count.
    if "{canonical_id}" in path:
        evidence = _authorization_evidence(route)
        if (
            any("require_canonical_case_access" in n for n in dep_names)
            or "_enforce_project_access" in evidence
            or "require_canonical_case_access" in evidence
        ):
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


def test_test_case_resource_ids_are_scanned_and_not_exempted() -> None:
    """Lifecycle routes must never regress to the old vacuous-pass shape."""
    assert {"case_id", "canonical_id"} <= UNGUARDED_SCOPED_PARAMS
    assert not any(
        "{case_id}" in path or "{canonical_id}" in path
        for _method, path in KNOWN_EXEMPT
    )


def test_existing_test_case_routes_have_real_access_evidence() -> None:
    scoped = [
        route
        for route in _collect_api_routes()
        if "{case_id}" in route.path or "{canonical_id}" in route.path
    ]
    assert scoped, "expected authored/canonical test-case routes to be mounted"
    unprotected = [route.path for route in scoped if not _route_is_protected(route)]
    assert unprotected == [], (
        "test-case routes without project-derived access evidence: "
        f"{sorted(set(unprotected))}"
    )


# ══════════════════════════════════════════════════════════════════════════
# Scoped ids that arrive OUTSIDE the path
# ══════════════════════════════════════════════════════════════════════════
#
# Everything above matches ``{param}`` in the path template. That left a blind
# spot the size of nine confirmed cross-tenant holes: ``_route_is_protected``
# returns ``True`` when it finds no scoped **path** param, so a route taking
# ``project_id``/``run_id`` as a **query param or body field** was auto-declared
# safe and the gate stayed green.
#
# ``POST /api/v1/agents/defect-command`` was the worst of them: ``cluster_id``,
# ``run_id`` and ``project_id`` all arrived as query params behind a bare
# ``require_role(QA_ENGINEER)``. It read another tenant's failure clusters,
# generated a defect from their failure text, and persisted it into whatever
# project the caller named.
#
# The trap that makes this class hard to fix: ``require_run_access()`` reads
# ``run_id`` from ``request.path_params`` and returns the caller UNCHANGED when
# it is absent. Attaching it to a query-param route is a silent no-op that looks
# exactly like protection. The check has to run inside the handler (or a service
# it calls), which is why the evidence below is gathered from source rather than
# from the dependency tree alone.

import ast  # noqa: E402
import inspect  # noqa: E402

#: Ids that identify a tenant-owned object, wherever they arrive.
SCOPED_IDS: frozenset[str] = frozenset({
    "project_id", "run_id", "test_run_id", "release_id", "defect_id",
    "analysis_id", "cluster_id", "plan_id", "strategy_id", "case_id",
    "canonical_id",
    "session_id", "link_id", "key_id", "source_id", "batch_id", "rule_id",
    "report_id", "suite_name", "project_key",
    # Plurals and prefixed forms. Their absence is not hypothetical: it hid
    # ``/agents/pipelines/bulk-trigger`` (``run_ids``, up to 2000 per call)
    # and ``/integrations/jira`` (``test_case_id``) from this scan while both
    # were live cross-tenant holes, found only by triaging the backlog by hand.
    "run_ids", "test_run_ids", "project_ids", "test_case_id", "test_case_ids",
    "defect_ids", "case_ids", "canonical_ids", "cluster_ids",
})

#: Any of these appearing in a handler — or in a function it calls — is accepted
#: as evidence that the caller's access to the named object was verified.
#: Membership calls, ownership filters and project-bound API-key contexts all
#: count: they are different shapes of the same guarantee.
_SCOPE_EVIDENCE: tuple[str, ...] = (
    # canonical membership resolution
    "resolve_project_scope", "get_accessible_project_ids",
    # dependency guards
    "require_project_access", "require_run_access", "require_release_access",
    "require_session_access", "require_live_session_access", "require_link_access",
    "require_api_key_owner", "require_knowledge_source_access",
    "require_generation_batch_access", "require_plan_access", "require_case_access",
    "require_canonical_case_access",
    "require_attempt_access", "require_investigation_access",
    # local helpers defined in routers/services
    "_assert_project_access", "_check_project_access", "_enforce_project_access",
    "_require_accessible_project", "_require_case_project_access",
    "_require_pipeline_access", "_require_valid_project_id",
    "_authorize_run_and_project", "_require_analysis_access",
    "assert_user_is_qa_lead_on_project",
    # project-bound API key: the server derives project_id from the key itself
    "get_streaming_api_key_context", "StreamingApiKeyContext",
    "get_api_key_context", "_api_key_bound_project", "bound_project_id",
    # ownership filter — scoping to the caller's own rows
    "current_user.id", "current_user.username",
)

#: Routes carrying a scoped id outside the path where this scan finds no check.
#: Each was read by hand when the section was added; the note says what was
#: found. Split into two groups so the second cannot quietly become permanent.
NONPATH_KNOWN_EXEMPT: frozenset[tuple[str, str]] = frozenset({
    # ── Reviewed and genuinely fine ──────────────────────────────────────
    # ADMIN-only. A caller who is already an administrator of the whole
    # deployment naming a project_id is filtering, not escalating.
    ("GET",  "/api/v1/audit-dashboard/export"),
    ("GET",  "/api/v1/onboarding/events"),
    # The project is derived server-side from ``X-Session-Token`` via
    # ``resolve_project_id_for_session`` -- the caller cannot name one.
    ("POST", "/api/v1/stream/events/batch"),
    # Returns one boolean (is this flag on for me?) and is deliberately open
    # to any authenticated user so the SPA can render without ADMIN. Documented
    # as such on the handler.
    ("GET",  "/api/v1/feature-flags/{key}/status"),

    # The three entries that sat here as "suspected, needs triage" were all
    # triaged and all three were real cross-tenant holes. They are fixed, not
    # exempted. Triaging them also surfaced a fourth
    # (``/agents/pipelines/bulk-trigger``) that this scan had missed because
    # ``run_ids`` was not in SCOPED_IDS -- see the note there.
})


def _non_path_scoped_ids(route: APIRoute) -> set[str]:
    """Scoped ids the route accepts as a query param or body field."""
    found: set[str] = set()
    for field in list(route.dependant.query_params) + list(route.dependant.body_params):
        if field.name in SCOPED_IDS:
            found.add(field.name)
        model_fields = getattr(getattr(field, "type_", None), "model_fields", None)
        if model_fields:
            found |= {n for n in model_fields if n in SCOPED_IDS}
    return found


def _called_targets(func) -> list:
    """``(qualifier, name)`` for every call in the handler, to follow one level down.

    A handler that delegates to ``create_test_plan(db, payload, current_user)``
    — or to ``svc.list_sources(...)`` where ``svc`` is an imported *module* —
    is protected if that callee resolves the scope. Reading only the handler
    body reports both as unprotected, which is how a guard earns a reputation
    for false alarms and then gets ignored. Both shapes are resolved: a bare
    name against the router module, and ``alias.name`` against whatever
    ``alias`` is bound to there.
    """
    try:
        tree = ast.parse(inspect.getsource(func).lstrip())
    except (OSError, TypeError, SyntaxError, IndentationError):
        return []
    targets: list = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name):
            targets.append((None, fn.id))
        elif isinstance(fn, ast.Attribute):
            qualifier = fn.value.id if isinstance(fn.value, ast.Name) else None
            targets.append((qualifier, fn.attr))
    return targets


def _authorization_evidence(route: APIRoute) -> str:
    """Dependency names + handler source + the source of what the handler calls."""
    parts = list(_walk_deps(route.dependant))
    endpoint = route.endpoint
    try:
        parts.append(inspect.getsource(endpoint))
    except (OSError, TypeError):
        pass

    module = inspect.getmodule(endpoint)
    if module is not None:
        for qualifier, name in _called_targets(endpoint):
            owner = module if qualifier is None else getattr(module, qualifier, None)
            if owner is None:
                continue
            target = getattr(owner, name, None)
            if target is None or not callable(target):
                continue
            try:
                parts.append(inspect.getsource(target))
            except (OSError, TypeError):
                continue
    return "\n".join(parts)


def _list_unscoped_nonpath_routes() -> list[tuple[str, str]]:
    offenders: set[tuple[str, str]] = set()
    for route in _collect_api_routes():
        if not route.path.startswith("/api/v1"):
            continue
        # Handled by the path-param ratchet above.
        if _scoped_params_in_path(route.path):
            continue
        if not _non_path_scoped_ids(route):
            continue
        if any(marker in _authorization_evidence(route) for marker in _SCOPE_EVIDENCE):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            offenders.add((method, route.path))
    return sorted(offenders)


def test_the_nonpath_scan_actually_inspects_routes() -> None:
    """A scan that matched nothing would pass forever — the original failure."""
    carriers = [
        r for r in _collect_api_routes()
        if r.path.startswith("/api/v1")
        and not _scoped_params_in_path(r.path)
        and _non_path_scoped_ids(r)
    ]
    assert len(carriers) >= 40, (
        "expected many routes taking a scoped id outside the path, found "
        f"{len(carriers)} — the extractor is probably broken"
    )


def test_a_scoped_id_outside_the_path_is_still_checked() -> None:
    """The blind spot that hid nine cross-tenant holes.

    A route may satisfy this with any shape of check — a dependency guard, a
    ``resolve_project_scope`` call in the handler or a service it calls, an
    ownership filter, or a project-bound API key. What it may not do is accept
    a tenant-owned id and never look at who is asking.
    """
    offenders = set(_list_unscoped_nonpath_routes())

    unexpected = sorted(offenders - NONPATH_KNOWN_EXEMPT)
    stale = sorted(NONPATH_KNOWN_EXEMPT - offenders)

    errors: list[str] = []
    if unexpected:
        errors.append(
            "A route accepts a tenant-owned id as a query param or body field "
            "and never verifies the caller may touch it. NOTE: attaching "
            "require_run_access() will NOT fix this — it reads path_params and "
            "returns the caller unchanged when the id is not in the path. "
            "Resolve the owning project inside the handler instead:\n  "
            + "\n  ".join(f"{m:6s} {p}" for m, p in unexpected)
        )
    if stale:
        errors.append(
            "These NONPATH_KNOWN_EXEMPT entries now check their scope (or the "
            "route is gone). Delete them — the backlog only shrinks:\n  "
            + "\n  ".join(f"{m:6s} {p}" for m, p in stale)
        )
    assert not errors, "\n\n".join(errors)


def test_the_nonpath_backlog_only_shrinks() -> None:
    """Every remaining entry has been read and is genuinely fine. The cap keeps
    it that way: a new entry means someone chose exemption over a check."""
    cap = 4
    assert len(NONPATH_KNOWN_EXEMPT) <= cap, (
        f"NONPATH_KNOWN_EXEMPT has {len(NONPATH_KNOWN_EXEMPT)} entries "
        f"(cap={cap}). Add the missing check rather than raising the cap."
    )
