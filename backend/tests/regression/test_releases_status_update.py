"""Regression: marking a release as "released" 400'd when phases
weren't all marked completed.

Bug pinned (2026-05-18): the user marked the release status to
"released" and got a 400 with "Cannot mark as released — N phase(s)
not completed". The bug surfaced when the release had auto-generated
phases that were in "skipped" state — but the check predates the
"skipped" terminal state and only allowed "completed".

Fix: ``release_service.update_release`` validates phases against the
set ``{"completed", "skipped"}`` so skipped phases satisfy the gate.

What this file pins:

  * Transitioning to "released" with all phases completed = OK.
  * Transitioning to "released" with all phases skipped = OK.
  * Transitioning to "released" with a mix of completed + skipped = OK.
  * Transitioning to "released" with a single "pending" phase = 400.
  * Releases with zero phases can still be marked released
    (zero-phase releases are valid; the check only fires when
    phases exist).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _release(status="planning", is_active=False):
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status,
        name="r1",
        released_at=None,
        version=None,
        # Migration 0150: marking a release terminal hands the active flag to a
        # successor. These tests pin PHASE validation, so they use a
        # non-active release to keep rotation out of the way — rotation has
        # its own coverage in test_active_release_invariant.py.
        is_active=is_active,
        project_id=uuid.uuid4(),
    )


def _phases(*states):
    return [SimpleNamespace(id=uuid.uuid4(), name=f"p{i}", status=s)
            for i, s in enumerate(states)]


def _exec_result_for_phases(phases):
    res = MagicMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=phases)
    res.scalars = MagicMock(return_value=scalars)
    return res


def _release_lookup(release):
    """Patch get_release_or_404 to return the release."""
    async def _lookup(db, release_id):
        return release
    return _lookup


@pytest.mark.asyncio
async def test_release_to_released_allows_skipped_phases(monkeypatch):
    """A phase in 'skipped' state must satisfy the release gate.
    Pre-fix the check only allowed 'completed' so users couldn't
    finalise a release with optional phases marked skipped."""
    from app.services import release_service

    release = _release(status="ready")
    phases = _phases("completed", "skipped", "completed")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_exec_result_for_phases(phases))

    monkeypatch.setattr(release_service, "get_release_or_404", _release_lookup(release))

    body = SimpleNamespace(model_dump=lambda exclude_none: {"status": "released"})
    result = await release_service.update_release(db, str(release.id), body)
    assert result.status == "released"
    assert result.released_at is not None


@pytest.mark.asyncio
async def test_release_to_released_400s_on_pending_phase(monkeypatch):
    """Genuine incomplete state must still 400. Otherwise the gate
    is useless — anyone can mark released with phases still active."""
    from app.services import release_service
    from fastapi import HTTPException

    release = _release(status="ready")
    phases = _phases("completed", "in_progress", "completed")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_exec_result_for_phases(phases))

    monkeypatch.setattr(release_service, "get_release_or_404", _release_lookup(release))

    body = SimpleNamespace(model_dump=lambda exclude_none: {"status": "released"})
    with pytest.raises(HTTPException) as exc:
        await release_service.update_release(db, str(release.id), body)
    assert exc.value.status_code == 400
    assert "phase" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_release_to_released_ok_with_zero_phases(monkeypatch):
    """A release with no phases must be markable as released —
    zero phases is a valid state (e.g. hotfix-style releases)."""
    from app.services import release_service

    release = _release(status="ready")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_exec_result_for_phases([]))

    monkeypatch.setattr(release_service, "get_release_or_404", _release_lookup(release))

    body = SimpleNamespace(model_dump=lambda exclude_none: {"status": "released"})
    result = await release_service.update_release(db, str(release.id), body)
    assert result.status == "released"
