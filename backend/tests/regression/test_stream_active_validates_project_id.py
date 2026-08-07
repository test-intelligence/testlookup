"""``/stream/active`` must validate ``project_id`` for EVERY role.

Found by exploratory testing against the live homelab (2026-08-07), while
resolving an earlier inconclusive observation. As an ADMIN::

    GET /api/v1/stream/active?project_id=all         -> 200 {"sessions": [], "count": 0}
    GET /api/v1/stream/active?project_id=not-a-uuid  -> 200 {"sessions": [], "count": 0}

I initially could not tell whether that empty result meant "filter applied,
nothing found" (there were no active sessions) or "filter silently ignored".
Reading the handler settled it: the validation was unreachable for admins.

    accessible = await get_accessible_project_ids(db, current_user)
    if project_id:
        if accessible is not None:        # <- None for an ADMIN
            try:
                if uuid.UUID(project_id) not in accessible: ... 403
            except ValueError:            ... 400 "Invalid project_id"
        return await stream_service.list_active_sessions(db, project_id, ...)

``get_accessible_project_ids`` returns ``None`` for an ADMIN, so the whole block
was skipped and a raw string reached the query. Non-admins got a clean 400 for
the identical input; admins got a silently empty live dashboard.

This is the SAME role-dependent shape as the ``/metrics`` 500 fixed earlier in
this session — the check that guards the value is nested inside the check that
only non-admins trigger. Only the symptom differs: this query degrades to "no
match" rather than raising, and an empty page with no explanation is the harder
failure to diagnose, not the easier one. ``ALL_PROJECTS_ID`` ("all") is a
frontend-only sentinel, so one stale link or missed SPA guard produces it.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.routers import stream  # noqa: E402


def _handler_src() -> str:
    """Handler source with comments and docstrings stripped.

    Third time this session that a structural assertion was fooled by prose: the
    fix's own comment quotes ``accessible is not None`` while explaining the bug,
    so a naive ``src.find(...)`` matched the *comment* — which sits before the
    real code — and the ordering assertion passed for the wrong reason. Strip
    anything that is not executable before reasoning about structure.
    """
    src = inspect.getsource(stream.list_active_sessions)
    out, in_doc = [], False
    for line in src.split("\n"):
        stripped = line.strip()
        if stripped.startswith(('"""', "'''")):
            # naive toggle is fine: this handler has at most one docstring
            in_doc = not in_doc and stripped.count('"""') % 2 == 1
            continue
        if in_doc or stripped.startswith("#"):
            continue
        out.append(line.split("  # ")[0])
    return "\n".join(out)


class TestValidationIsNotNestedUnderTheRoleCheck:
    def test_uuid_parse_happens_before_the_accessible_branch(self):
        """The ordering IS the fix — nesting is what made it admin-only."""
        src = _handler_src()
        parse_at = src.find("uuid.UUID(project_id)")
        role_at = src.find("accessible is not None")
        assert parse_at != -1, "handler no longer parses project_id as a UUID"
        assert role_at != -1, "handler no longer branches on `accessible`"
        assert parse_at < role_at, (
            "the UUID parse still sits after/inside the `accessible is not None` "
            "branch, so ADMINs skip it and a raw string reaches the query"
        )

    def test_the_400_is_raised_before_the_role_branch(self):
        """The 400 must be reachable without passing the role check.

        Ordering, not indentation: the parse legitimately sits inside a ``try:``
        and is therefore *naturally* deeper than the role check, so an indent
        comparison says nothing useful. What matters is that the malformed-input
        rejection happens BEFORE the branch that only non-admins enter.
        """
        src = _handler_src()
        bad_request_at = src.find("HTTP_400_BAD_REQUEST")
        role_at = src.find("accessible is not None")
        assert bad_request_at != -1, "handler no longer raises 400 on bad input"
        assert role_at != -1, "handler no longer branches on `accessible`"
        assert bad_request_at < role_at, (
            "the 400 for a malformed project_id is only reachable after the "
            "`accessible is not None` branch, so an ADMIN never gets it — they "
            "get a silently empty session list instead"
        )


class TestContract:
    def test_malformed_project_id_yields_400_not_an_empty_page(self):
        src = _handler_src()
        assert "HTTP_400_BAD_REQUEST" in src
        assert re.search(r"detail=.*[Ii]nvalid project_id", src), (
            "a malformed project_id must be reported, not silently swallowed "
            "into an empty session list"
        )

    def test_forbidden_is_still_returned_for_a_real_but_inaccessible_project(self):
        """The fix must not trade a 403 away for a 400."""
        src = _handler_src()
        assert "HTTP_403_FORBIDDEN" in src
        assert "not in accessible" in src

    def test_admins_are_still_unscoped_when_no_project_id_is_given(self):
        """Passing no project_id must keep the admin's cross-project view."""
        src = _handler_src()
        assert "project_id=None" in src, (
            "the no-project_id branch must still list across projects"
        )


def test_sibling_handlers_are_not_obviously_exposed():
    """Cheap breadth check on the same file.

    Not exhaustive and not a substitute for reading each handler — it just flags
    the specific `if accessible is not None:` -> nested `uuid.UUID(...)` shape
    that caused this bug, so a copy-paste of it is noticed.
    """
    src = inspect.getsource(stream)
    collapsed = re.sub(r"\s+", " ", src)
    bad = "if accessible is not None: try: if uuid.UUID("
    assert bad not in collapsed, (
        "another handler nests its UUID validation inside the non-admin branch; "
        "admins would skip it there too"
    )
