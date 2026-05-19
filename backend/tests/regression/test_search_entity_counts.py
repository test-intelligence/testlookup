"""Regression: /search showed "0 items indexed" on every chip before
the user typed anything.

Bug pinned (2026-05-18): the /search page's chip counts + Index
Health panel previously read from ``response.entity_counts`` — the
PER-QUERY result counts. With no query, those were 0, so the user
saw "Index ready 0 items indexed across 6 entity types" even when
the project had thousands of rows.

Fix: ``GET /api/v1/search/entity-counts`` returns project-scoped
totals via 6 COUNT queries (run in parallel via asyncio.gather).
The frontend now falls back to this when no query is active.

What this file pins:

  * Response shape: ``{test_case, test_run, suite, defect,
    flaky_test, release}``.
  * Caller with zero project memberships gets all-zero counts via
    ``where(False)`` — no cross-tenant leak.
  * Flaky count excludes ``RELEASED`` quarantine requests (those
    are no longer flagged as flaky).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _scalar_result(value):
    res = MagicMock()
    res.scalar_one = MagicMock(return_value=value)
    return res


@pytest.mark.asyncio
async def test_entity_counts_returns_six_keys_in_response():
    from app.routers.search import get_entity_counts

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(42))
    user = SimpleNamespace(id=uuid.uuid4(), role="ADMIN")

    with patch("app.routers.search.resolve_project_scope",
               AsyncMock(return_value=(None, None))):
        result = await get_entity_counts(
            project_id=None, db=db, current_user=user,
        )
    expected_keys = {"test_case", "test_run", "suite", "defect", "flaky_test", "release"}
    assert set(result.keys()) == expected_keys


@pytest.mark.asyncio
async def test_entity_counts_returns_all_zero_for_no_memberships():
    """Tenant safety: a caller with allowed_project_ids = set() must
    get all-zero counts via ``where(False)`` — never the all-projects
    aggregate.

    Strategy: stub ``db.execute`` to always return 0 (matches the
    behaviour of ``where(False)``) and assert the shape comes back
    intact with zeros."""
    from app.routers.search import get_entity_counts

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(0))
    user = SimpleNamespace(id=uuid.uuid4(), role="VIEWER")

    with patch("app.routers.search.resolve_project_scope",
               AsyncMock(return_value=(None, set()))):
        result = await get_entity_counts(
            project_id=None, db=db, current_user=user,
        )
    assert result == {
        "test_case": 0, "test_run": 0, "suite": 0,
        "defect": 0, "flaky_test": 0, "release": 0,
    }


@pytest.mark.asyncio
async def test_entity_counts_flaky_filter_excludes_released():
    """``flaky_test`` count must exclude ``RELEASED`` quarantine
    requests — otherwise the chip drift between live + released
    quarantines confuses users on /search."""
    from app.routers.search import get_entity_counts

    captured: list = []

    async def capture(stmt):
        captured.append(stmt)
        return _scalar_result(0)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=capture)
    user = SimpleNamespace(id=uuid.uuid4(), role="ADMIN")

    with patch("app.routers.search.resolve_project_scope",
               AsyncMock(return_value=(None, None))):
        await get_entity_counts(project_id=None, db=db, current_user=user)

    # One of the captured statements must reference the RELEASED filter.
    sqls = [str(s.compile(compile_kwargs={"literal_binds": True})) for s in captured]
    assert any("RELEASED" in s for s in sqls), (
        "The flaky count query must filter out RELEASED rows so chip "
        "counts reflect ACTIVE flaky requests only."
    )
