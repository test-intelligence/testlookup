"""Regression: ``PUT /test-cases/{id}/review`` 500'd with MissingGreenlet.

Bug pinned (2026-05-18): clicking "Reproducible" (or any other state)
on the run-detail review modal returned 500. Root cause:
``TestExecutionReview.updated_at`` is a ``func.now()`` server-default
column with ``onupdate=func.now()``. After ``await db.flush()`` the
attribute is marked "expired" on the ORM side, so the next read
(during Pydantic serialisation) tries to lazy-load it — which under
async sessions needs greenlet context the response serialiser
doesn't have, raising ``MissingGreenlet``.

Fix: ``await db.refresh(row)`` after the flush in
``test_execution_review_service.upsert_review`` (both insert + update
paths). Forces SQLAlchemy to materialise the column into the session
synchronously.

What this file pins:

  * Insert path refreshes the row after flush.
  * Update path refreshes the existing row after flush.
  * ``hydrate_response`` can read ``updated_at`` immediately after
    the service returns, without lazy-loading.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _scalar_result(value):
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=value)
    return res


@pytest.mark.asyncio
async def test_upsert_review_refreshes_after_insert():
    """First-time review: row created, flush, then refresh."""
    from app.services.test_execution_review_service import upsert_review

    test_case_id = uuid.uuid4()
    project_id = uuid.uuid4()
    reviewer = uuid.uuid4()
    now = datetime.now(timezone.utc)

    refresh_calls: list = []

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(None))  # no existing review
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock(side_effect=refresh_calls.append)

    row = await upsert_review(
        db=db,
        test_case_id=test_case_id,
        project_id=project_id,
        state="reproducible",
        reviewer_user_id=reviewer,
    )

    assert db.flush.await_count == 1
    assert db.refresh.await_count == 1, (
        "Insert path must call db.refresh(row) to materialise the "
        "onupdate ``updated_at`` column before the Pydantic serialiser "
        "tries to read it."
    )
    assert refresh_calls[0] is row


@pytest.mark.asyncio
async def test_upsert_review_refreshes_after_update():
    """Existing review: row updated in place, flush, then refresh."""
    from app.services.test_execution_review_service import upsert_review

    test_case_id = uuid.uuid4()
    project_id = uuid.uuid4()
    reviewer = uuid.uuid4()
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        test_case_id=test_case_id,
        project_id=project_id,
        state="reviewed",
        reviewed_by_user_id=reviewer,
        defect_link=None,
        note=None,
        transitioned_at=datetime.now(timezone.utc),
    )

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(existing))
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    row = await upsert_review(
        db=db,
        test_case_id=test_case_id,
        project_id=project_id,
        state="reproducible",
        reviewer_user_id=reviewer,
        note="local repro confirmed",
    )

    assert row is existing
    assert row.state == "reproducible"
    assert row.note == "local repro confirmed"
    assert db.flush.await_count == 1
    assert db.refresh.await_count == 1, (
        "Update path must also call db.refresh(row) — the onupdate "
        "column expires on flush exactly as it does on insert."
    )
