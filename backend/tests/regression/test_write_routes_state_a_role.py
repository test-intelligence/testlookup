"""A signed-in route that writes states a role, or is on a reviewed list.

The security audit of 2026-11-18 found a group of write routes (test plans,
strategies, suite-review verdicts, the duplicate sweep, live-run recovery)
that checked only that the caller was signed in and a member of the project,
so the read-only VIEWER role wrote through them. Each was a route added
without ``require_role``; nothing made that fail.

The rule here: every route on a method other than GET/HEAD/OPTIONS that
authenticates a user must carry ``require_role(...)`` or
``require_project_role(...)`` in its dependency tree, unless it is listed in
``NO_ROLE_MINIMUM`` below. The list is the set of such routes when this test
was written: the caller's own things (profile, password, notification
preferences, saved views, chat, data-subject requests), read-shaped POSTs
(previews, validation, tickets), and a few handlers that check the role
themselves. It may only shrink. A new write route that any signed-in user may
call needs a line here, which is a decision someone reviews.

A stated role also brings the project half with it: on a write the caller
must hold that role in the project the request names (see
``test_project_writes_need_the_role_in_that_project.py``).
"""
from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

pytestmark = pytest.mark.regression

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

#: (method, path) of write routes that authenticate a user and state no role.
NO_ROLE_MINIMUM: frozenset[tuple[str, str]] = frozenset({
    ("POST", "/api/v1/agents/invocations/{invocation_id}/events/ticket"),
    ("POST", "/api/v1/auth/change-password"),
    ("POST", "/api/v1/auth/first-time-reset"),
    ("POST", "/api/v1/auth/logout"),
    ("PATCH", "/api/v1/auth/me"),
    ("POST", "/api/v1/auth/me/dismissals"),
    ("POST", "/api/v1/auth/mfa/disable"),
    ("POST", "/api/v1/auth/mfa/recovery-codes"),
    ("POST", "/api/v1/chat/sessions"),
    ("DELETE", "/api/v1/chat/sessions/{session_id}"),
    ("POST", "/api/v1/chat/sessions/{session_id}/messages"),
    ("POST", "/api/v1/chat/sessions/{session_id}/messages/stream"),
    ("POST", "/api/v1/compliance-packs/{pack_id}/verify"),
    ("POST", "/api/v1/digests/subscriptions"),
    ("DELETE", "/api/v1/digests/subscriptions/{sub_id}"),
    ("PATCH", "/api/v1/digests/subscriptions/{sub_id}"),
    ("POST", "/api/v1/digests/subscriptions/{sub_id}/pause"),
    ("POST", "/api/v1/digests/subscriptions/{sub_id}/resume"),
    ("POST", "/api/v1/feedback/{analysis_id}"),
    ("PUT", "/api/v1/feedback/{analysis_id}"),
    ("POST", "/api/v1/github/app/setup-callback"),
    ("PUT", "/api/v1/me/assigned-failures/{test_case_id}/reassign"),
    ("PUT", "/api/v1/me/assigned-failures/{test_case_id}/triage"),
    ("POST", "/api/v1/me/data-export"),
    ("POST", "/api/v1/me/erasure-request"),
    ("POST", "/api/v1/me/erasure-request/withdraw"),
    ("POST", "/api/v1/notifications/history/read-all"),
    ("POST", "/api/v1/notifications/history/{log_id}/read"),
    ("POST", "/api/v1/notifications/preferences"),
    ("DELETE", "/api/v1/notifications/preferences/{pref_id}"),
    ("PUT", "/api/v1/notifications/preferences/{pref_id}"),
    ("POST", "/api/v1/notifications/test"),
    ("POST", "/api/v1/onboarding/track"),
    ("POST", "/api/v1/onboarding/{project_id}/complete"),
    ("POST", "/api/v1/onboarding/{project_id}/detect"),
    ("POST", "/api/v1/project-data-jobs/{data_job_id}/download-ticket"),
    ("POST", "/api/v1/projects/{project_id}/attribution-rules/preview"),
    ("POST", "/api/v1/projects/{project_id}/ownership/source-roots/preview"),
    ("POST", "/api/v1/projects/{project_id}/workflows/{workflow_id}/validate"),
    ("DELETE", "/api/v1/reports/share-links/{link_id}"),
    ("POST", "/api/v1/reports/summary/exports"),
    ("POST", "/api/v1/reports/summary/exports/{export_id}/retry"),
    ("POST", "/api/v1/runs/{run_id}/decision-reports/{report_id}/feedback"),
    ("POST", "/api/v1/runs/{run_id}/intelligence/refresh"),
    ("POST", "/api/v1/saved-views"),
    ("DELETE", "/api/v1/saved-views/{view_id}"),
    ("PATCH", "/api/v1/saved-views/{view_id}"),
    ("DELETE", "/api/v1/search/recent"),
    ("POST", "/api/v1/search/reindex"),
})


def _dependency_names(dependant) -> list[str]:
    out, pending = [], [dependant]
    while pending:
        dep = pending.pop()
        if dep.call is not None:
            out.append(getattr(dep.call, "__qualname__", "") or "")
        pending.extend(dep.dependencies)
    return out


def _writes_without_a_role(routes) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for route in routes:
        if not isinstance(route, APIRoute):
            continue
        names = _dependency_names(route.dependant)
        if any(name.startswith(("require_role.", "require_project_role.")) for name in names):
            continue
        if not any(name in ("get_current_active_user", "get_current_user") for name in names):
            continue  # no user session: API-key ingest, public and webhook routes have their own tests
        found.update((method, route.path) for method in route.methods if method not in SAFE_METHODS)
    return found


def test_every_write_route_states_a_role_or_is_reviewed():
    from app.main import app

    found = _writes_without_a_role(app.routes)
    new = sorted(found - NO_ROLE_MINIMUM, key=lambda item: (item[1], item[0]))
    stale = sorted(NO_ROLE_MINIMUM - found, key=lambda item: (item[1], item[0]))

    assert not new, (
        "These write routes authenticate a user but state no role, so a VIEWER can call them. "
        "Add Depends(require_role(UserRole.X)) to the route. Only if every signed-in user "
        "really may call it (the caller's own data, or a read-shaped POST), add it to "
        "NO_ROLE_MINIMUM with that reason in the review:\n  "
        + "\n  ".join(f"{method} {path}" for method, path in new)
    )
    assert not stale, (
        "These routes now state a role or are gone. Delete them from NO_ROLE_MINIMUM "
        "(the list only shrinks):\n  " + "\n  ".join(f"{method} {path}" for method, path in stale)
    )


def test_the_reviewed_list_only_shrinks():
    assert len(NO_ROLE_MINIMUM) <= 49, "NO_ROLE_MINIMUM grew. Give the new route a role instead."


def test_the_scan_sees_a_route_without_a_role_and_passes_one_with_it():
    from fastapi import Depends, FastAPI

    from app.core.deps import get_current_active_user, require_role
    from app.models.postgres import UserRole

    app = FastAPI()

    @app.post("/open")
    async def open_write(_user=Depends(get_current_active_user)):
        return {}

    @app.post("/gated")
    async def gated_write(_user=Depends(require_role(UserRole.TESTER))):
        return {}

    @app.get("/read")
    async def read(_user=Depends(get_current_active_user)):
        return {}

    @app.post("/public")
    async def public():
        return {}

    assert _writes_without_a_role(app.routes) == {("POST", "/open")}
