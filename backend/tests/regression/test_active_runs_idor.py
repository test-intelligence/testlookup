"""Regression: GET /api/v1/agents/active-runs leaked every tenant's live runs (S2).

Audit item S2 (2026-06-03): the list endpoint had only
``Depends(get_current_active_user)`` and returned
``RedisLiveRunState.get_all_active()`` verbatim — unscoped. Any authenticated
user (including a VIEWER in one project) saw every other project's active live
runs: slug, build number, pass/fail counts, timing, project_id. The
``/active-runs/{run_id}`` sibling already gated on ``require_run_access``; the
list did not.

Fix: filter the active-run list by ``get_accessible_project_ids`` (ADMIN →
None → unrestricted) using the ``project_id`` already stored on each Redis
state row — in-memory, no DB round trip. Rows with no resolvable project_id
are dropped for non-admins (can't prove ownership → don't leak).

These tests drive the real endpoint function with the Redis layer + seq
enrichment stubbed, asserting the scope filter is applied.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.routers import agents as agents_router  # noqa: E402

pytestmark = pytest.mark.regression


_PID_A = uuid.uuid4()
_PID_B = uuid.uuid4()


def _rows():
    return [
        {"run_id": "run-a", "project_id": str(_PID_A), "build_number": "1"},
        {"run_id": "run-b", "project_id": str(_PID_B), "build_number": "2"},
        {"run_id": "run-orphan", "project_id": "", "build_number": "3"},
    ]


async def _call(accessible):
    """Invoke the endpoint with Redis + seq enrichment stubbed and a fixed
    ``get_accessible_project_ids`` result."""
    with patch.object(
        agents_router, "get_accessible_project_ids",
        AsyncMock(return_value=accessible),
    ), patch(
        "app.streams.live_run_state.RedisLiveRunState.get_all_active",
        AsyncMock(return_value=_rows()),
    ), patch(
        "app.services.stream_service.canonical_test_run_uuid",
        lambda slug: uuid.uuid4(),
    ), patch(
        "app.services.runs_service.fetch_run_seq_map",
        AsyncMock(return_value={}),
    ):
        result = await agents_router.get_active_live_runs(
            db=AsyncMock(), current_user=object(),
        )
    return [r["run_id"] for r in result["active_runs"]]


@pytest.mark.asyncio
async def test_non_admin_sees_only_accessible_project_runs():
    # Caller can access project A only → must NOT see run-b or the orphan.
    seen = await _call(accessible={_PID_A})
    assert seen == ["run-a"]


@pytest.mark.asyncio
async def test_admin_sees_all_runs():
    # ADMIN → get_accessible_project_ids returns None → unrestricted.
    seen = await _call(accessible=None)
    assert set(seen) == {"run-a", "run-b", "run-orphan"}


@pytest.mark.asyncio
async def test_member_of_both_projects_sees_both_but_not_orphan():
    seen = await _call(accessible={_PID_A, _PID_B})
    assert set(seen) == {"run-a", "run-b"}


@pytest.mark.asyncio
async def test_no_memberships_sees_nothing():
    seen = await _call(accessible=set())
    assert seen == []
