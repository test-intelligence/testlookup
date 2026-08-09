"""Regression: the chat router took ``project_id`` from the caller unchecked.

This is **F-033 verbatim, in a router the post-F-033 sweep missed**. The digests
fix corrected two endpoints with this exact shape; ``routers/chat.py`` carried
a third copy of it, character for character::

    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            return []
    return await chat_service.get_run_summaries(db, project_id, days)

The guard fires **only when there is nothing to guard**. Supplying a
``project_id`` skips it entirely, and ``chat_service.get_run_summaries`` applies
no scoping of its own — it filters Mongo by whatever id it is handed.

**Confirmed against the live deployment** with a VIEWER holding no membership
anywhere. Control first, to prove genuine non-membership::

    GET /api/v1/projects                         -> 0 projects visible
    GET /api/v1/chat/run-summaries               -> 0 (the guarded branch works)

Then::

    GET /api/v1/chat/run-summaries?project_id=<theirs>   -> 5 records
      build 105 | "Build **105** completed - 2 tests failed. Pass rate: 83.3%
                   (10/12 tests)"
      + test_run_id, markdown_report, anomaly_count, is_regression

Two sibling paths take an unchecked ``project_id`` in a **request body**, which
is worse than a leaky read because the id is then used to fetch data:

``POST /api/v1/chat/sessions``
    No check at all — ``payload.project_id`` goes straight onto the row.
    Confirmed live: the same zero-membership VIEWER created a session bound to
    another tenant's project and got 201 back with that ``project_id`` echoed.

``POST /api/v1/chat/sessions/{id}/messages``
    ``send_message`` passes ``session.project_id or payload.project_id`` to the
    ConversationAgent, which is what retrieves project data to answer with. So
    a per-message ``project_id`` re-points an otherwise legitimate session at
    another tenant. Session *ownership* is correctly guarded by
    ``require_session_access`` (creator-only); the project inside it was not.

    Code-verified only. The live send was not executed, so the end-to-end
    answer-content leak is **not** claimed as measured — the unguarded path is
    read from the source, and the session-binding half of it is measured above.

**Why the architectural ratchet did not catch this.**
``test_architectural_authorization.py`` matches routers whose *path* declares
``{project_id}``. Here the id arrives as a query parameter and as request-body
fields, so the class sits outside what that ratchet inspects — and grepping for
the guard looks fine, because ``get_accessible_project_ids`` **is** imported and
called, just in the branch that cannot leak. That is precisely what let one
copy of this bug survive the sweep that fixed the others.

Fix: all three resolve the caller's scope with ``resolve_project_scope``, which
403s a non-admin naming a project they are not a member of and leaves ADMIN
unrestricted. ``run-summaries`` additionally filters by the caller's membership
set when no project is named, so a non-admin gets *their* summaries instead of
the blanket empty list the old guard returned.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException  # noqa: E402

from app.routers import chat as chat_router  # noqa: E402

pytestmark = pytest.mark.regression


_MINE = uuid.uuid4()
_THEIRS = uuid.uuid4()

#: Shape of one leaked record, as measured on the live deployment.
_LEAKED = {
    "test_run_id": "5cb0f8ac-7ecb-4465-9ee5-13fcc115df4a",
    "project_id": str(_THEIRS),
    "build_number": "105",
    "executive_summary": "Build **105** completed - 2 tests failed. Pass rate: 83.3%",
    "anomaly_count": 0,
    "is_regression": False,
}


def _user(role="VIEWER"):
    user = MagicMock()
    user.id = uuid.uuid4()
    user.role = role
    user.username = "zz_chat_probe"
    return user


class _Recorder:
    """Stands in for the service; records the project it was asked for."""

    def __init__(self):
        self.calls: list = []

    async def __call__(self, db, project_id, days, *args, **kwargs):
        self.calls.append(project_id)
        return [dict(_LEAKED)]


async def _run_summaries(project_id, accessible, role="VIEWER"):
    """Drive the real endpoint with membership stubbed.

    ``accessible=None`` models ADMIN (unrestricted); a set models a non-admin's
    memberships; an empty set models a user who belongs to nothing.
    """
    recorder = _Recorder()
    with patch.object(
        chat_router.chat_service, "get_run_summaries", recorder
    ), patch(
        "app.core.deps.get_accessible_project_ids", AsyncMock(return_value=accessible)
    ):
        result = await chat_router.get_run_summaries(
            project_id=str(project_id) if project_id else None,
            days=5,
            db=AsyncMock(),
            current_user=_user(role),
        )
    return result, recorder


class TestRunSummariesRefusesAnotherTenantsProject:
    @pytest.mark.asyncio
    async def test_non_member_naming_a_project_is_denied(self):
        """The measured leak: a project_id the caller cannot see."""
        with pytest.raises(HTTPException) as excinfo:
            await _run_summaries(_THEIRS, {_MINE})
        assert excinfo.value.status_code == 403, (
            "a non-member supplied another tenant's project_id and received "
            "that project's run summaries — build numbers, pass rates, failure "
            "counts and executive summaries all disclosed"
        )

    @pytest.mark.asyncio
    async def test_user_with_no_memberships_is_denied(self):
        """The exact live repro: a VIEWER belonging to nothing."""
        with pytest.raises(HTTPException) as excinfo:
            await _run_summaries(_THEIRS, set())
        assert excinfo.value.status_code == 403

    @pytest.mark.asyncio
    async def test_the_service_is_never_reached_when_access_is_denied(self):
        """Denial must happen before the query, not be filtered afterwards."""
        recorder = _Recorder()
        with patch.object(
            chat_router.chat_service, "get_run_summaries", recorder
        ), patch(
            "app.core.deps.get_accessible_project_ids",
            AsyncMock(return_value={_MINE}),
        ):
            with pytest.raises(HTTPException):
                await chat_router.get_run_summaries(
                    project_id=str(_THEIRS), days=5,
                    db=AsyncMock(), current_user=_user(),
                )
        assert recorder.calls == [], (
            "the service was queried for a project the caller cannot access"
        )

    @pytest.mark.asyncio
    async def test_a_member_still_gets_their_own_project(self):
        """The guard must not break the legitimate path."""
        result, recorder = await _run_summaries(_MINE, {_MINE})
        assert len(result) == 1
        assert recorder.calls == [str(_MINE)]

    @pytest.mark.asyncio
    async def test_admin_is_unrestricted(self):
        result, recorder = await _run_summaries(_THEIRS, None, role="ADMIN")
        assert len(result) == 1, "ADMIN must keep cross-project visibility"


class TestSessionCreationRefusesAnotherTenantsProject:
    """``payload.project_id`` went straight onto the row with no check.

    Worse than the read: ``send_message`` hands the session's project to the
    ConversationAgent, so the row is a standing handle on another tenant's data.
    """

    @pytest.mark.asyncio
    async def test_non_member_cannot_bind_a_session_to_their_project(self):
        payload = MagicMock()
        payload.project_id = _THEIRS
        payload.title = "zz probe session"

        created = AsyncMock()
        with patch.object(
            chat_router.chat_service, "create_session", created
        ), patch(
            "app.core.deps.get_accessible_project_ids",
            AsyncMock(return_value={_MINE}),
        ):
            with pytest.raises(HTTPException) as excinfo:
                await chat_router.create_session(
                    payload=payload, db=AsyncMock(), current_user=_user()
                )
        assert excinfo.value.status_code == 403, (
            "a zero-membership VIEWER created a chat session bound to another "
            "tenant's project (confirmed live: 201 with that project_id echoed)"
        )
        created.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_session_with_no_project_is_still_allowed(self):
        """Project-less sessions are legitimate — general chat."""
        payload = MagicMock()
        payload.project_id = None
        payload.title = None

        with patch.object(
            chat_router.chat_service, "create_session", AsyncMock(return_value=MagicMock())
        ), patch(
            "app.core.deps.get_accessible_project_ids",
            AsyncMock(return_value={_MINE}),
        ):
            db = AsyncMock()
            await chat_router.create_session(
                payload=payload, db=db, current_user=_user()
            )


class TestPerMessageProjectOverride:
    """A per-message ``project_id`` must not re-point a legitimate session."""

    @pytest.mark.asyncio
    async def test_message_cannot_name_an_inaccessible_project(self):
        session = MagicMock()
        session.project_id = None
        payload = MagicMock()
        payload.message = "How many tests failed in the latest run?"
        payload.project_id = str(_THEIRS)

        sent = AsyncMock()
        with patch.object(
            chat_router.chat_service, "send_message", sent
        ), patch(
            "app.core.deps.get_accessible_project_ids",
            AsyncMock(return_value={_MINE}),
        ):
            with pytest.raises(HTTPException) as excinfo:
                await chat_router.send_message(
                    payload=payload, session=session,
                    db=AsyncMock(), current_user=_user(),
                )
        assert excinfo.value.status_code == 403
        sent.assert_not_awaited(), (
            "the ConversationAgent was handed a project the caller cannot access"
        )
