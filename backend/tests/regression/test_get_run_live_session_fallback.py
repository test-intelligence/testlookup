"""Regression: ``GET /api/v1/runs/{id}`` 404'd for in-flight live sessions.

Bug pinned: clicking a row on /live during the first ~30s of a new
live session navigated to ``/runs/<uuid>`` which 404'd because the
``test_runs`` row doesn't exist yet — the Phase 4.5 drainer creates
it on first non-empty drain (every 30s) and ``persist_live_session``
creates it on session-complete.

Fix landed: 2026-05-19. ``get_run_with_release`` falls back to a
synthesized TestRun built from the still-active ``LiveSession`` so
the UI gets a meaningful in-progress response.

What this file pins:

  * No TestRun + no LiveSession → returns None (router 404s, unchanged).
  * No TestRun + matching LiveSession → returns a TestRun-shaped dict
    with status=IN_PROGRESS, trigger_source="live_stream", and the
    project + build attached from the LiveSession.
  * Existing TestRun is preferred over the fallback (the fallback
    never overrides authoritative data).
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


def _make_live_session(run_id: uuid.UUID, project_id: uuid.UUID):
    """Minimum fields the synthesizer reads."""
    return SimpleNamespace(
        id=run_id,
        project_id=project_id,
        build_number="b-2029",
        suite_name="Realistic TestNG client examples",
        total_tests=10,
        started_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_get_run_returns_none_when_neither_table_has_the_id():
    """The router maps None → 404; nothing about that contract should change."""
    from app.services.runs_service import get_run_with_release

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result(None),  # TestRun lookup
        _scalar_result(None),  # LiveSession lookup
    ])
    result = await get_run_with_release(db, uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_get_run_falls_back_to_live_session_when_test_run_missing():
    """The user-visible bug: in-flight live session → must return IN_PROGRESS payload."""
    from app.services.runs_service import get_run_with_release

    run_id = uuid.uuid4()
    project_id = uuid.uuid4()
    live = _make_live_session(run_id, project_id)

    db = AsyncMock()
    # ``execute`` is called in this exact order: TestRun lookup,
    # LiveSession lookup, then fetch_release_map / project_map /
    # run_seq_map round-trips. Patch the helpers so we only need
    # to stub the two scalar lookups.
    db.execute = AsyncMock(side_effect=[
        _scalar_result(None),     # TestRun → None triggers fallback
        _scalar_result(live),     # LiveSession found
    ])

    with patch("app.services.runs_service.fetch_release_map",
               AsyncMock(return_value={})), \
         patch("app.services.runs_service.fetch_project_name_map",
               AsyncMock(return_value={str(project_id): "GoogleSearch"})), \
         patch("app.services.runs_service.fetch_run_seq_map",
               AsyncMock(return_value={str(run_id): 7})):
        result = await get_run_with_release(db, run_id)

    assert result is not None, "live-session fallback must produce a payload"
    assert str(result["id"]) == str(run_id)
    assert result["status"] in ("IN_PROGRESS", "in_progress")
    assert result["trigger_source"] == "live_stream"
    assert result["build_number"] == "b-2029"
    assert result.get("release_name") is None
    assert result["project_name"] == "GoogleSearch"
    assert result["run_seq"] == 7
    # Aggregates start at zero — the drainer will overwrite them once
    # it fires. A non-zero default would confuse the UI into showing
    # fake pass/fail counts.
    assert int(result.get("passed_tests") or 0) == 0
    assert int(result.get("failed_tests") or 0) == 0


@pytest.mark.asyncio
async def test_get_run_prefers_real_test_run_over_live_session():
    """If both rows exist, the real TestRun wins. The fallback is read-only
    and never shadows authoritative data."""
    from app.services.runs_service import get_run_with_release
    from app.models.postgres import TestRun

    run_id = uuid.uuid4()
    project_id = uuid.uuid4()
    real_run = TestRun(
        id=run_id,
        project_id=project_id,
        build_number="b-real",
        trigger_source="ci",
        status="COMPLETED",
        total_tests=42,
        passed_tests=40,
        failed_tests=2,
        skipped_tests=0,
        broken_tests=0,
    )

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result(real_run),  # First lookup hits — no fallback fires.
    ])

    with patch("app.services.runs_service.fetch_release_map",
               AsyncMock(return_value={})), \
         patch("app.services.runs_service.fetch_project_name_map",
               AsyncMock(return_value={})), \
         patch("app.services.runs_service.fetch_run_seq_map",
               AsyncMock(return_value={})):
        result = await get_run_with_release(db, run_id)

    assert result is not None
    # No second db.execute call would have fired (LiveSession lookup is skipped).
    # Asserting indirectly via the build_number that only the real row carries.
    assert result["build_number"] == "b-real"
    assert result["trigger_source"] == "ci"
