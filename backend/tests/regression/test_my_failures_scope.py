"""Regression: ``/my-failures`` ``?scope=mine|team`` toggle.

Bug pinned: admin viewing ``/my-failures`` saw almost nothing because
auto-assignment routes failures to the synthetic default-QA-Lead user
(see ``services/default_qa_lead_service.py``). The fix adds an opt-in
``team`` scope that QA_LEAD/ADMIN callers can use to see every
unresolved failure across the project. Lower roles silently downgrade
to ``mine`` so the URL can't be tampered with to leak cross-user data.

Fix landed: 2026-05-19, commit b5b5cab "bug fixes and enhancements to
test suite and failures".

What this file pins:

  * ``scope=mine`` (default) → SQL filters by
    ``TestCase.assigned_to_user_id == current_user.id``.
  * ``scope=team`` as QA_LEAD/ADMIN → assignee filter is dropped.
  * ``scope=team`` as a non-lead → silently downgraded to ``mine``
    (no 403; the URL is harmless to lower roles).

Strategy: capture the ``Select`` statement passed to ``db.execute``
and inspect its compiled SQL for the assignee predicate. That ties
the regression to the actual WHERE clause emitted, not just to the
endpoint's return value.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.postgres import UserRole


# ── helpers ────────────────────────────────────────────────────────────────


def _count_result(value: int):
    res = MagicMock()
    res.scalar = MagicMock(return_value=value)
    return res


def _emits_assignee_filter(stmt) -> bool:
    """Render the captured ``Select`` and check for the assignee column.

    SQLAlchemy ``Select`` has no public "list of WHERE clauses" view that's
    stable across versions; the compiled-SQL substring check is the most
    durable assertion shape. ``literal_binds=False`` keeps the predicate
    parameterised but the column reference is in the text.
    """
    compiled = stmt.compile(compile_kwargs={"literal_binds": False})
    return "test_cases.assigned_to_user_id" in str(compiled)


def _user(role: str = UserRole.QA_ENGINEER.value):
    return SimpleNamespace(id=uuid.uuid4(), role=role)


# ── count endpoint — cheapest path to assert the filter ────────────────────


@pytest.mark.asyncio
async def test_count_mine_filters_by_assignee_for_any_role():
    """Default scope = mine → assignee predicate must appear."""
    from app.routers.my_failures import my_assigned_failures_count

    captured: list = []

    async def capture(stmt):
        captured.append(stmt)
        return _count_result(0)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=capture)
    user = _user(role=UserRole.VIEWER.value)

    await my_assigned_failures_count(
        project_id=None, days=30, scope="mine", db=db, current_user=user,
    )
    assert captured, "count endpoint must run exactly one query"
    assert _emits_assignee_filter(captured[0]), (
        "scope=mine should keep the assignee predicate so callers only "
        "see their own work."
    )


@pytest.mark.asyncio
async def test_count_team_as_qa_lead_drops_assignee_filter():
    """QA_LEAD asks for team scope → assignee predicate must be absent."""
    from app.routers.my_failures import my_assigned_failures_count

    captured: list = []

    async def capture(stmt):
        captured.append(stmt)
        return _count_result(0)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=capture)
    user = _user(role=UserRole.QA_LEAD.value)

    await my_assigned_failures_count(
        project_id=None, days=30, scope="team", db=db, current_user=user,
    )
    assert not _emits_assignee_filter(captured[0]), (
        "scope=team for QA_LEAD must drop the assignee predicate so the "
        "lead can see every unresolved failure on the project."
    )


@pytest.mark.asyncio
async def test_count_team_as_admin_drops_assignee_filter():
    from app.routers.my_failures import my_assigned_failures_count

    captured: list = []

    async def capture(stmt):
        captured.append(stmt)
        return _count_result(0)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=capture)
    user = _user(role=UserRole.ADMIN.value)

    await my_assigned_failures_count(
        project_id=None, days=30, scope="team", db=db, current_user=user,
    )
    assert not _emits_assignee_filter(captured[0])


@pytest.mark.asyncio
async def test_count_team_as_viewer_is_silently_downgraded_to_mine():
    """A VIEWER who hand-edits the URL must NOT leak team data."""
    from app.routers.my_failures import my_assigned_failures_count

    captured: list = []

    async def capture(stmt):
        captured.append(stmt)
        return _count_result(0)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=capture)
    user = _user(role=UserRole.VIEWER.value)

    await my_assigned_failures_count(
        project_id=None, days=30, scope="team", db=db, current_user=user,
    )
    assert _emits_assignee_filter(captured[0]), (
        "VIEWER asking for team scope must be silently downgraded — the "
        "assignee predicate stays in place."
    )


@pytest.mark.asyncio
async def test_count_team_as_qa_engineer_is_silently_downgraded():
    """Same rule for QA_ENGINEER — only QA_LEAD/ADMIN get team scope."""
    from app.routers.my_failures import my_assigned_failures_count

    captured: list = []

    async def capture(stmt):
        captured.append(stmt)
        return _count_result(0)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=capture)
    user = _user(role=UserRole.QA_ENGINEER.value)

    await my_assigned_failures_count(
        project_id=None, days=30, scope="team", db=db, current_user=user,
    )
    assert _emits_assignee_filter(captured[0])


# ── list endpoint — same downgrade rule on the path that the page hits ─────


@pytest.mark.asyncio
async def test_list_team_scope_path_returns_empty_envelope_cleanly():
    """The list endpoint short-circuits on a zero count. As long as no
    exception escapes for ``scope=team`` we know the downgrade branch
    parses correctly even when the user has no failures."""
    from app.routers.my_failures import list_my_assigned_failures

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_count_result(0))
    user = _user(role=UserRole.QA_LEAD.value)

    result = await list_my_assigned_failures(
        project_id=None,
        days=30,
        # Explicit: a DIRECT call gets FastAPI's `Query(None)` OBJECT as the
        # default, not None, and the release resolver rejects it as a malformed
        # UUID. FastAPI only substitutes the real value per request.
        release_id=None,
        page=1,
        size=25,
        scope="team",
        db=db,
        current_user=user,
    )
    assert result.total == 0
    assert result.items == []


@pytest.mark.asyncio
async def test_list_mine_does_not_query_when_count_zero():
    """No second query when no failures — saves a round trip for the
    common 'caught up' state. Matches the original short-circuit."""
    from app.routers.my_failures import list_my_assigned_failures

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_count_result(0))
    user = _user(role=UserRole.QA_ENGINEER.value)

    await list_my_assigned_failures(
        project_id=None,
        days=30,
        # Explicit: a DIRECT call gets FastAPI's `Query(None)` OBJECT as the
        # default, not None, and the release resolver rejects it as a malformed
        # UUID. FastAPI only substitutes the real value per request.
        release_id=None,
        page=1,
        size=25,
        scope="mine",
        db=db,
        current_user=user,
    )
    assert db.execute.await_count == 1
