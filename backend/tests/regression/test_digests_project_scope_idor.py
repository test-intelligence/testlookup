"""Regression: the digests router took ``project_id`` from the caller unchecked.

Two endpoints in ``routers/digests.py`` accepted a ``project_id`` supplied by
the client and never verified membership:

``GET /api/v1/digests/preview?project_id=<uuid>``
    The access check ran **only when project_id was absent**::

        if not project_id:
            accessible = await get_accessible_project_ids(db, current_user)
            if accessible is not None:
                return DigestContentResponse(period=period, ...)   # empty
        digest = await generate_digest(db, project_id, period)     # unguarded

    Supplying a project_id skipped the check entirely — the guard fired only in
    the case where there was nothing to guard.

``POST /api/v1/digests/subscriptions``
    No check at all. ``payload.project_id`` went straight onto the row.

**Confirmed against the live deployment** with a QA_ENGINEER holding no
membership in the target project. Control first, to prove genuine
non-membership::

    GET /api/v1/projects/<pid>   -> 403
    GET /api/v1/projects         -> 0 projects visible

Then::

    GET /api/v1/digests/preview?project_id=<pid>
    {"project_name": "Checkout Service", "total_runs": 5,
     "avg_pass_rate": 81.1, "new_regressions": 5,
     "latest_run_total_tests": 12, "action_items": [...]}

    POST /api/v1/digests/subscriptions {"project_id": "<pid>", ...}
    201 -> is_active: true, schedule: DAILY, report_attachment: true,
           next_delivery_at: <tomorrow>

The subscription is the more serious of the two: the delivery task in
``worker/tasks.py`` reads ``project_id`` off the claimed row and calls
``generate_digest`` with it, looking the user up only to obtain an email
address. Nothing re-checks membership at send time, so the row is a standing
instruction to mail another tenant's digest — with the full HTML analysis
report attached — on a schedule.

**Why the architectural ratchet did not catch this.**
``tests/test_architectural_authorization.py`` matches routers whose *path*
declares ``{project_id}`` and requires the corresponding guard. Here the id
arrives as a query parameter and a request-body field, so the whole class is
outside what that ratchet inspects. Grepping for the guard would also have
looked fine — ``get_accessible_project_ids`` *is* imported and called in
``preview``, just in the branch that cannot leak.

Fix: both endpoints resolve the caller's scope with ``resolve_project_scope``,
which raises 403 for a non-admin naming a project they are not a member of and
leaves ADMIN unrestricted.

``saved_view_id`` is deliberately not covered: it is accepted by the same
payload but never read by ``digest_content_service`` or the delivery task, so
it carries no data. If that changes it needs the same treatment.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException  # noqa: E402

from app.routers import digests as digests_router  # noqa: E402

pytestmark = pytest.mark.regression


_MINE = uuid.uuid4()
_THEIRS = uuid.uuid4()

_LEAKED = {
    "project_name": "Checkout Service",
    "period": "weekly",
    "generated_at": "2026-08-08T00:10:59+00:00",
    "total_runs": 5,
    "avg_pass_rate": 81.1,
    "new_regressions": 5,
    "latest_run_total_tests": 12,
}


def _user(role="QA_ENGINEER"):
    user = MagicMock()
    user.id = uuid.uuid4()
    user.role = role
    user.username = "zzprobedigest"
    return user


class _Recorder:
    """Stands in for ``generate_digest``; records what it was asked for."""

    def __init__(self):
        self.calls: list = []

    async def __call__(self, db, project_id, period, *args, **kwargs):
        self.calls.append(project_id)
        return dict(_LEAKED)


async def _preview(project_id, accessible):
    """Drive the real endpoint with membership stubbed.

    ``accessible=None`` models ADMIN (unrestricted); a set models a non-admin's
    membership.
    """
    recorder = _Recorder()
    with patch.object(
        digests_router, "generate_digest", recorder, create=True
    ), patch(
        "app.services.digest_content_service.generate_digest", recorder
    ), patch(
        "app.core.deps.get_accessible_project_ids", AsyncMock(return_value=accessible)
    ):
        result = await digests_router.preview_digest(
            project_id=project_id, period="weekly", db=AsyncMock(), current_user=_user()
        )
    return result, recorder


class TestPreviewRefusesAnotherTenantsProject:
    @pytest.mark.asyncio
    async def test_non_member_naming_a_project_is_denied(self):
        """The measured leak: a project_id the caller cannot see."""
        with pytest.raises(HTTPException) as excinfo:
            await _preview(_THEIRS, {_MINE})
        assert excinfo.value.status_code == 403, (
            "a non-member supplied another tenant's project_id and the digest "
            "was generated for it — project name, run count, pass rate and "
            "regression count all disclosed"
        )

    @pytest.mark.asyncio
    async def test_generate_is_never_reached_for_a_denied_project(self):
        """Denial must happen before any content is built, not after."""
        recorder = _Recorder()
        with patch(
            "app.services.digest_content_service.generate_digest", recorder
        ), patch(
            "app.core.deps.get_accessible_project_ids", AsyncMock(return_value={_MINE})
        ):
            with pytest.raises(HTTPException):
                await digests_router.preview_digest(
                    project_id=_THEIRS,
                    period="weekly",
                    db=AsyncMock(),
                    current_user=_user(),
                )
        assert recorder.calls == [], (
            f"digest content was generated for {recorder.calls} despite the denial"
        )

    @pytest.mark.asyncio
    async def test_member_of_the_project_still_works(self):
        """The fix must not break the legitimate case."""
        result, recorder = await _preview(_MINE, {_MINE})
        assert recorder.calls == [_MINE]
        assert result.project_name == "Checkout Service"

    @pytest.mark.asyncio
    async def test_admin_is_unrestricted(self):
        """ADMIN resolves to ``accessible is None`` and keeps full reach."""
        result, recorder = await _preview(_THEIRS, None)
        assert recorder.calls == [_THEIRS]
        assert result.total_runs == 5


class TestSubscriptionCannotBindAnotherTenantsProject:
    @pytest.mark.asyncio
    async def test_non_member_cannot_subscribe(self):
        """The standing instruction to mail someone else's digest, daily."""
        payload = MagicMock()
        payload.project_id = _THEIRS
        payload.saved_view_id = None
        payload.name = "zz probe cross-project sub"
        payload.schedule = "DAILY"
        payload.channel = "email"
        payload.send_when_unchanged = True
        payload.report_attachment = True

        db = AsyncMock()
        db.add = MagicMock()

        with patch(
            "app.core.deps.get_accessible_project_ids", AsyncMock(return_value={_MINE})
        ):
            with pytest.raises(HTTPException) as excinfo:
                await digests_router.create_subscription(
                    payload=payload, db=db, current_user=_user()
                )

        assert excinfo.value.status_code == 403
        assert not db.add.called, (
            "a subscription row bound to another tenant's project was persisted; "
            "the delivery task reads project_id straight off the row and never "
            "re-checks membership"
        )

    @pytest.mark.asyncio
    async def test_member_can_still_subscribe(self):
        payload = MagicMock()
        payload.project_id = _MINE
        payload.saved_view_id = None
        payload.name = "legitimate"
        payload.schedule = "DAILY"
        payload.channel = "email"
        payload.send_when_unchanged = True
        payload.report_attachment = False

        db = AsyncMock()
        db.add = MagicMock()

        with patch(
            "app.core.deps.get_accessible_project_ids", AsyncMock(return_value={_MINE})
        ):
            await digests_router.create_subscription(
                payload=payload, db=db, current_user=_user()
            )
        assert db.add.called, "a member was blocked from subscribing to their own project"

    @pytest.mark.asyncio
    async def test_a_non_admin_cannot_subscribe_to_every_project(self):
        """A NULL project_id is not "no project to check" — it is *all* projects.

        This test previously asserted the opposite, under the name
        ``test_a_global_subscription_needs_no_project`` and the docstring "no
        project means no project to check". That is wrong:
        ``generate_digest`` applies no project filter at all when
        ``project_id`` is None, so the row is a standing instruction to mail
        every project on the install — and its own fixture name, "all my
        projects", shows the intent was the user's projects, not the
        workspace's. The test held the hole open.
        """
        payload = MagicMock()
        payload.project_id = None
        payload.saved_view_id = None
        payload.name = "all my projects"
        payload.schedule = "WEEKLY"
        payload.channel = "email"
        payload.send_when_unchanged = True
        payload.report_attachment = False

        db = AsyncMock()
        db.add = MagicMock()

        with patch(
            "app.core.deps.get_accessible_project_ids", AsyncMock(return_value={_MINE})
        ):
            with pytest.raises(HTTPException) as excinfo:
                await digests_router.create_subscription(
                    payload=payload, db=db, current_user=_user()
                )

        assert excinfo.value.status_code == 403
        assert not db.add.called, (
            "a workspace-wide digest row was persisted for a non-admin; the "
            "delivery task never re-checks membership, so it would mail every "
            "project on the install on a schedule"
        )

    @pytest.mark.asyncio
    async def test_an_admin_may_still_subscribe_to_every_project(self):
        """The capability is restricted, not removed."""
        payload = MagicMock()
        payload.project_id = None
        payload.saved_view_id = None
        payload.name = "whole install"
        payload.schedule = "WEEKLY"
        payload.channel = "email"
        payload.send_when_unchanged = True
        payload.report_attachment = False

        db = AsyncMock()
        db.add = MagicMock()

        with patch(
            "app.core.deps.get_accessible_project_ids", AsyncMock(return_value=None)
        ):
            await digests_router.create_subscription(
                payload=payload, db=db, current_user=_user(role="ADMIN")
            )
        assert db.add.called, "an admin was blocked from a workspace-wide digest"
