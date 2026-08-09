"""``scope=team`` guarded cross-*user* escalation but not cross-*tenant*.

``/api/v1/me/assigned-failures`` drops the per-assignee filter when
``scope=team``, and honours that scope for QA_LEAD and ADMIN only. The source
comment states the intent plainly::

    # Team scope is only honoured for QA_LEAD / ADMIN. Anyone else
    # silently falls back to ``mine`` so the URL can't be tampered with
    # to leak cross-user data.

It does exactly that — and nothing else. Dropping the assignee filter without
adding a project filter leaves the query scoped by **nothing**, so a QA_LEAD
sees every project on the instance, whether or not they are a member of any.

**Confirmed against the live deployment** with a QA_LEAD holding zero
memberships. Control first::

    GET /api/v1/projects                                     -> 0 visible

Then, naming another tenant's project::

    GET /me/assigned-failures?project_id=<theirs>&scope=team -> total 11
        test_refund_flow (api), test_discount_stacking (regression)

And, naming no project at all — instance-wide::

    GET /me/assigned-failures?scope=team&days=90&size=100    -> total 72
        across 2 distinct projects, neither of which the caller belongs to
    GET /me/assigned-failures/count?scope=team               -> {"count": 72}

This is the reach that made F-035 (``POST /search/reindex``) serious: a normal
tenant role, not an admin, operating instance-wide.

``scope=mine`` is unaffected and always was — it filters on
``assigned_to_user_id == current_user.id``, which is self-scoping by
construction. The fix must not disturb it, and the tests below pin that.

Fix: resolve the caller's scope for both handlers. A named project is verified
(403 for a non-member); an unnamed one restricts ``team`` to the caller's
membership set instead of the whole instance. ADMIN stays unrestricted.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.routers import my_failures as mod  # noqa: E402

pytestmark = pytest.mark.regression

SOURCE = inspect.getsource(mod)


def _handler_source(name: str) -> str:
    """Body of one handler, up to the next decorator."""
    m = re.search(rf"async def {name}\(.*?(?=\n@router\.|\Z)", SOURCE, re.S)
    assert m, f"handler {name} not found — was it renamed?"
    return m.group(0)


HANDLERS = ("list_my_assigned_failures", "my_assigned_failures_count")


@pytest.mark.parametrize("handler", HANDLERS)
def test_team_scope_is_bounded_by_membership(handler: str):
    """Both handlers must consult the caller's project scope.

    Without this, dropping the assignee filter for ``team`` leaves the query
    bounded by nothing at all — measured live at 72 rows across every project
    on the instance, for a QA_LEAD belonging to none of them.
    """
    body = _handler_source(handler)
    assert "resolve_project_scope" in body, (
        f"{handler} does not resolve the caller's project scope, so scope=team "
        f"reaches every project on the instance (measured: 72 rows, 2 projects, "
        f"zero memberships)"
    )


@pytest.mark.parametrize("handler", HANDLERS)
def test_the_membership_set_is_actually_applied(handler: str):
    """Resolving scope is useless if the result is never used in the query.

    Guards the obvious wrong fix: calling the helper for its 403 side-effect
    and then ignoring the returned membership set, which would still leak
    instance-wide whenever no project is named.
    """
    body = _handler_source(handler)
    assert re.search(r"allowed[^\n]*\bin_\(|\bin_\([^\n]*allowed", body), (
        f"{handler} resolves scope but never filters by the returned "
        f"membership set — the unnamed-project case still spans the instance"
    )


@pytest.mark.parametrize("handler", HANDLERS)
def test_mine_scope_still_filters_by_assignee(handler: str):
    """The fix must not disturb the path that was already correct."""
    body = _handler_source(handler)
    assert "assigned_to_user_id == current_user.id" in body, (
        f"{handler} lost its per-assignee filter — scope=mine must stay "
        f"self-scoped"
    )


def test_team_scope_is_still_role_gated():
    """The original cross-user guard must survive the tenancy fix."""
    assert re.search(
        r'scope == "team" and current_user\.role not in', SOURCE
    ), "the QA_LEAD/ADMIN role gate on team scope was removed"


class TestBehaviour:
    """The structural checks above pin the shape; this pins the outcome."""

    @pytest.mark.asyncio
    async def test_qa_lead_naming_another_tenants_project_is_denied(self):
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch

        from fastapi import HTTPException

        mine, theirs = uuid.uuid4(), uuid.uuid4()
        user = MagicMock()
        user.id = uuid.uuid4()
        user.role = "QA_LEAD"

        with patch(
            "app.core.deps.get_accessible_project_ids",
            AsyncMock(return_value={mine}),
        ):
            with pytest.raises(HTTPException) as excinfo:
                await mod.my_assigned_failures_count(
                    project_id=str(theirs), days=30, scope="team",
                    db=AsyncMock(), current_user=user,
                )
        assert excinfo.value.status_code == 403, (
            "a QA_LEAD who belongs to no project counted another tenant's "
            "unresolved failures (measured live: 11 for a named project)"
        )

    @pytest.mark.asyncio
    async def test_mine_scope_needs_no_membership_lookup(self):
        """The sidebar polls this. ``mine`` is self-scoped, so it must not pay
        a membership query — an existing test pins the query count, and this
        pins the reason."""
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch

        user = MagicMock()
        user.id = uuid.uuid4()
        user.role = "VIEWER"

        # The Result must be a plain MagicMock: children of an AsyncMock are
        # themselves AsyncMocks, so `.scalar()` would hand back a coroutine.
        result = MagicMock()
        result.scalar.return_value = 0
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result)
        resolver = AsyncMock(return_value=(None, set()))

        with patch("app.core.deps.resolve_project_scope", resolver):
            await mod.my_assigned_failures_count(
                project_id=None, days=30, scope="mine",
                db=db, current_user=user,
            )
        resolver.assert_not_awaited()
