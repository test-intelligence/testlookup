"""Creating a saved view must verify the project, as listing them does.

Low severity, and worth being precise about why: ``SavedView.project_id`` is
read only to filter the list and to unset sibling defaults. It grants no data
access, so this is **not** a disclosure — it is write-side pollution.

What makes it worth fixing is the asymmetry. When the read path on this router
was guarded (F-042), the write path was left alone, so a zero-membership VIEWER
could do this against the live deployment::

    POST /api/v1/saved-views {"project_id": "<theirs>", ...}   -> 201
    GET  /api/v1/saved-views?project_id=<theirs>               -> 403

Create it, then be forbidden from reading it back. A guard on one half of a
resource and not the other is the kind of gap that becomes a real finding the
moment someone starts consuming the field — the digests router already carries
a note saying exactly that about ``saved_view_id``.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException  # noqa: E402

from app.routers import saved_views as mod  # noqa: E402

pytestmark = pytest.mark.regression

_MINE = uuid.uuid4()
_THEIRS = uuid.uuid4()


def _payload(project_id):
    p = MagicMock()
    p.project_id = project_id
    p.name = "zz sweep view"
    p.description = None
    p.filters = {"status": "failed"}
    p.is_shared = False
    p.is_default = False
    return p


def _user():
    u = MagicMock()
    u.id = uuid.uuid4()
    u.role = "VIEWER"
    return u


@pytest.mark.asyncio
async def test_non_member_cannot_bind_a_view_to_another_tenants_project():
    db = AsyncMock()
    with patch(
        "app.core.deps.get_accessible_project_ids", AsyncMock(return_value={_MINE})
    ):
        with pytest.raises(HTTPException) as excinfo:
            await mod.create_saved_view(
                payload=_payload(_THEIRS), db=db, current_user=_user()
            )
    assert excinfo.value.status_code == 403, (
        "a zero-membership VIEWER created a saved view bound to another "
        "tenant's project (confirmed live: 201, then 403 reading it back)"
    )
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_a_project_less_view_is_still_allowed():
    """Global saved views are legitimate and must not be blocked."""
    db = AsyncMock()
    with patch(
        "app.core.deps.get_accessible_project_ids", AsyncMock(return_value={_MINE})
    ):
        await mod.create_saved_view(
            payload=_payload(None), db=db, current_user=_user()
        )
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_a_member_can_still_create_in_their_own_project():
    db = AsyncMock()
    with patch(
        "app.core.deps.get_accessible_project_ids", AsyncMock(return_value={_MINE})
    ):
        await mod.create_saved_view(
            payload=_payload(_MINE), db=db, current_user=_user()
        )
    db.add.assert_called_once()
