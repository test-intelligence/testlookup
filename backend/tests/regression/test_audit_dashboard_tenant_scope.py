"""Regression: `/audit-dashboard/events` leaked other tenants' audit trails.

The router's docstring promised *"Tenant isolation: non-admin users only see
events for their projects."* Nothing implemented it. Three separate defects::

    if project_id is None:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None and len(accessible) == 0:
            return {"total": 0, "items": []}
    return await query_unified_audit(db, project_id=project_id, ...)

1. **Unscoped fall-through.** The short-circuit fired only for a caller with
   ZERO memberships. A member of even one project fell through with
   ``project_id=None`` into a completely unscoped query.
2. **No check on a named project.** A caller supplying a ``project_id`` they
   cannot access was never verified — ``project_id`` was treated as a filter,
   never as a permission.
3. **Instance-wide sources always included.** ``SettingsAuditLog`` and
   ``IdentityEvent`` have no ``project_id`` column, so their sub-queries were
   never filtered by anything. Every QA_LEAD received the whole instance's
   settings-change and SSO/SCIM history.

**Confirmed live.** A QA_LEAD made a member of exactly one throwaway project
(control: ``GET /projects/<seeded>`` → 403, one project visible)::

    GET /audit-dashboard/events?days=365&page_size=50      (no project_id)
    total=78  rows=50  by_source={access: 12, settings: 10, test_management: 28}
    rows carrying ANOTHER project's id: 28
      test_management sync_deleted 3dfc96cf-37e6-4ea0-884d-9a82480479a7

    GET /audit-dashboard/events?project_id=<a project they cannot access>
    200 (not 403) — 16 settings rows

An earlier pass filed this as "needs confirmation" and understated it, because
the probe account had **zero** memberships and so hit the early-return. The
zero-membership case was the *one* path that behaved. Adding a single
membership exposed the cross-project leak.

Fix: ``resolve_project_scope`` in the router (403 for a named project the
caller cannot access) and a real ``allowed_project_ids`` scope inside
``query_unified_audit``. The two sources with no project column are omitted
for a restricted caller rather than leaked — they cannot be attributed to a
project, and the alternative is handing every QA_LEAD the instance's settings
history.

``/audit-dashboard/export`` is unchanged: it already requires ADMIN, who is
unrestricted by design.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException  # noqa: E402

from app.routers import audit_dashboard as audit_router  # noqa: E402

pytestmark = pytest.mark.regression


_MINE = uuid.uuid4()
_THEIRS = uuid.uuid4()


def _user(role="QA_LEAD"):
    user = MagicMock()
    user.id = uuid.uuid4()
    user.role = role
    user.username = "probe"
    return user


class _QueryRecorder:
    """Captures the kwargs the router hands to ``query_unified_audit``."""

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, db, **kwargs):
        self.calls.append(kwargs)
        return {"total": 0, "items": []}


async def _call(project_id, accessible):
    recorder = _QueryRecorder()
    with patch(
        "app.services.audit_dashboard_service.query_unified_audit", recorder
    ), patch(
        "app.core.deps.get_accessible_project_ids", AsyncMock(return_value=accessible)
    ):
        result = await audit_router.list_audit_events(
            project_id=project_id,
            category=None,
            actor_id=None,
            days=30,
            page=1,
            page_size=50,
            current_user=_user(),
            db=AsyncMock(),
        )
    return result, recorder


class TestTheQueryIsActuallyScoped:
    @pytest.mark.asyncio
    async def test_a_member_of_one_project_does_not_get_an_unscoped_query(self):
        """The measured leak: 28 rows from a project they are not in."""
        _, recorder = await _call(project_id=None, accessible={_MINE})
        assert recorder.calls, "the query was never reached"
        scope = recorder.calls[0].get("allowed_project_ids")
        assert scope == {_MINE}, (
            "query_unified_audit was called without the caller's membership set, "
            f"so it returns every project's audit rows (got {scope!r})"
        )

    @pytest.mark.asyncio
    async def test_admin_stays_unrestricted(self):
        _, recorder = await _call(project_id=None, accessible=None)
        assert recorder.calls[0].get("allowed_project_ids") is None

    @pytest.mark.asyncio
    async def test_zero_membership_still_short_circuits(self):
        """The one path that already behaved — keep it that way."""
        result, recorder = await _call(project_id=None, accessible=set())
        assert result == {"total": 0, "items": []}
        assert recorder.calls == []


class TestANamedProjectIsAPermissionNotAFilter:
    @pytest.mark.asyncio
    async def test_naming_an_inaccessible_project_is_denied(self):
        with pytest.raises(HTTPException) as excinfo:
            await _call(project_id=_THEIRS, accessible={_MINE})
        assert excinfo.value.status_code == 403

    @pytest.mark.asyncio
    async def test_naming_an_accessible_project_still_works(self):
        _, recorder = await _call(project_id=_MINE, accessible={_MINE})
        assert recorder.calls[0]["project_id"] == _MINE


class TestInstanceWideSourcesAreNotHandedToTenants:
    """``SettingsAuditLog`` / ``IdentityEvent`` have no project column."""

    def _service_source(self) -> str:
        import inspect

        from app.services import audit_dashboard_service

        return inspect.getsource(audit_dashboard_service.query_unified_audit)

    def test_settings_is_gated_on_being_unrestricted(self):
        src = self._service_source()
        assert 'category == "settings") and not restricted' in src, (
            "the settings audit stream is still returned to restricted callers; "
            "it has no project_id column so it cannot be scoped"
        )

    def test_identity_is_gated_on_being_unrestricted(self):
        src = self._service_source()
        assert 'category == "identity") and not restricted' in src

    def test_the_scope_helper_is_applied_to_project_scoped_tables(self):
        """All three project-scoped sub-queries must go through ``_scope``."""
        src = self._service_source()
        assert src.count("_scope(") >= 4, (  # 1 def + 3 call sites
            "a project-scoped sub-query is bypassing the membership filter"
        )

    def test_an_empty_membership_set_matches_nothing(self):
        """Belt and braces: empty set must not degrade to 'no filter'."""
        src = self._service_source()
        assert "sa_false()" in src, (
            "an empty allowed_project_ids must produce a false predicate, not "
            "an unfiltered query"
        )
