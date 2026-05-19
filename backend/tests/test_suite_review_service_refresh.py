"""Regression test for the suite-review PUT 500 (2026-05-16).

The router serializes the returned ``SuiteRunReview`` via
``_review_to_response`` before the request commits. The model's
``updated_at`` / ``created_at`` columns are populated by
``server_default=func.now()`` / ``onupdate=func.now()``. After
``db.flush()`` SQLAlchemy marks those columns expired so the next read
must hit the DB. On an async session, the implicit sync lazy-load raises
``sqlalchemy.exc.MissingGreenlet`` and the request 500s — that was the
exact failure reported for ``PUT /suite-reviews/by-run/{run}/{suite}``.

The fix in ``suite_review_service`` is to call ``db.refresh(row)`` after
the flush so the server-computed timestamps are loaded synchronously
while we're still inside the async context. These tests pin that
behaviour: both the create path (``get_or_create_review`` first call) and
the update path must refresh.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _scalar_result(value):
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=value)
    return res


@pytest.mark.asyncio
async def test_get_or_create_review_refreshes_after_creating_new_row():
    """First call (no existing row) must refresh so server defaults load."""
    from app.services import suite_review_service as svc

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(None))  # no existing
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    project_id = uuid.uuid4()
    run_id = uuid.uuid4()

    row = await svc.get_or_create_review(db, project_id, "checkout-api", run_id)

    db.add.assert_called_once()
    db.flush.assert_awaited_once()
    db.refresh.assert_awaited_once_with(row)


@pytest.mark.asyncio
async def test_get_or_create_review_does_not_refresh_when_row_exists():
    """Returning an already-loaded row needs no refresh — no expired cols."""
    from app.services import suite_review_service as svc

    existing = SimpleNamespace(id=uuid.uuid4(), state="pending")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(existing))
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    row = await svc.get_or_create_review(db, uuid.uuid4(), "x", uuid.uuid4())

    assert row is existing
    db.add.assert_not_called()
    db.flush.assert_not_awaited()
    db.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_review_refreshes_after_flush():
    """Update path must refresh so the recomputed ``updated_at`` is loaded.

    Without this refresh, Pydantic's read of ``review.updated_at`` in the
    router's response serializer triggers a sync lazy-load that fails
    with ``MissingGreenlet`` on the async session.
    """
    from app.services import suite_review_service as svc

    review = SimpleNamespace(
        id=uuid.uuid4(),
        state="pending",
        note=None,
        reviewer_user_id=None,
        reviewed_at=None,
        suite_name="notifications",
        test_run_id=uuid.uuid4(),
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(review))
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    reviewer = uuid.uuid4()
    updated = await svc.update_review(db, review.id, "review_later", "n", reviewer)

    assert updated is review
    assert review.state == "review_later"
    assert review.reviewer_user_id == reviewer
    db.flush.assert_awaited_once()
    db.refresh.assert_awaited_once_with(review)


@pytest.mark.asyncio
async def test_update_review_rejects_unknown_state():
    """Sanity: invalid state still raises before any DB writes."""
    from app.services import suite_review_service as svc

    db = AsyncMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    with pytest.raises(ValueError):
        await svc.update_review(db, uuid.uuid4(), "totally_invalid", None, uuid.uuid4())

    db.flush.assert_not_awaited()
    db.refresh.assert_not_awaited()
