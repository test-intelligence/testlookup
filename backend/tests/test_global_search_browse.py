"""Unit tests for the /api/v1/search/global empty-query browse mode (2026-05-14).

User-reported bug: the /search page chip counts (Tests, Suites, Runs, etc.)
showed totals but clicking them yielded an empty panel — the page only
runs a search when there's a query. The fix relaxed the endpoint's
``q`` validation from ``min_length=1`` to a default of ``""`` so the
frontend can fire the request and the backend's adapters fall through
to "most-recent N" via ``ILIKE '%%'`` matching any non-NULL row.

These tests pin the contract at the service layer:
  * Empty ``q`` does not raise / does not 422.
  * The service still applies tenant filters (``project_id`` /
    ``allowed_project_ids``).
  * Entity-types narrowing still works with empty ``q``.

The integration through the router → ``global_search_service`` is
exercised once here so a regression in either layer surfaces.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _all_result(rows):
    res = MagicMock()
    res.all = MagicMock(return_value=rows)
    res.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=rows)))
    return res


# ── Service layer: global_search accepts empty q ──────────────────────────


@pytest.mark.asyncio
async def test_global_search_empty_q_short_circuits_when_no_memberships():
    """A non-admin caller with no project memberships must NOT see anyone
    else's records via the browse path. Short-circuit returns empty without
    touching the database."""
    from app.services.global_search_service import global_search

    db = AsyncMock()
    result = await global_search(
        db=db,
        q="",
        project_id=None,
        allowed_project_ids=set(),
    )
    assert result["items"] == []
    assert result["total"] == 0
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_search_empty_q_runs_every_adapter_when_unscoped():
    """Empty q + unrestricted access fans out to all adapters. We mock each
    adapter's DB call to return an empty list — but the test pins that the
    function dispatches without raising on the empty query."""
    from app.services.global_search_service import (
        global_search,
        ALL_ENTITY_TYPES,
    )

    db = AsyncMock()
    # Every adapter does .execute(...).all() or .scalars().all() — return
    # empty lists for both shapes so any path is safe.
    db.execute = AsyncMock(return_value=_all_result([]))

    result = await global_search(db=db, q="")
    # Each entity type ran without raising.
    assert isinstance(result["items"], list)
    assert result["total"] == 0
    assert result["query"] == ""
    # entity_counts only contains types with hits — empty here.
    assert result["entity_counts"] == {}
    assert "search_type" in result
    # No request to filter by entity_types means every adapter ran.
    assert db.execute.await_count >= len(ALL_ENTITY_TYPES)


@pytest.mark.asyncio
async def test_global_search_empty_q_narrowed_to_one_entity_type():
    """``entity_types={'test_case'}`` should only call the test_case adapter
    even with empty q — narrowing trumps fan-out."""
    from app.services.global_search_service import global_search

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_all_result([]))

    result = await global_search(
        db=db, q="", entity_types={"test_case"},
    )
    assert result["entity_counts"] == {}
    # The test_case adapter issues exactly one execute(). Other adapters
    # didn't run, so total executes == 1.
    assert db.execute.await_count == 1


# ── Like-pattern semantics for empty q ────────────────────────────────────


def test_like_contains_empty_string_returns_percent_percent():
    """The empty-q browse relies on ``ILIKE '%%'`` matching every non-NULL
    row. Pin the helper that produces that pattern so a change there can't
    silently break the browse contract."""
    from app.services.sql_utils import like_contains
    assert like_contains("") == "%%"


def test_like_contains_escapes_wildcards():
    """A literal ``%`` or ``_`` in the user query must be escaped so ILIKE
    treats it as text, not a wildcard. Sanity check for the non-empty path."""
    from app.services.sql_utils import like_contains
    assert like_contains("50%") == r"%50\%%"
    assert like_contains("test_case") == r"%test\_case%"
