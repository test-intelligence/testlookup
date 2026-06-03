"""Regression pin for the Phase AUTO perf fix (auto/perf-20260603-1837).

``release_service.update_phase`` computed ``all_done`` by fetching every phase
row for the release and scanning in Python (`all(p.status in (...))`). It now
issues a single COUNT of NOT-done phases (`status NOT IN ('completed','skipped')
OR status IS NULL`) — one aggregate instead of an O(N) row fetch. ``all_done``
is True iff that count is 0 (zero phases → 0 → True, matching ``all([])``).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.regression


class _Res:
    def __init__(self, scalar):
        self._scalar = scalar

    def scalar(self):
        return self._scalar


def _body():
    # exclude_none flag accepted; returns a status-only update (no rename → no
    # sibling query, so the only db.execute is the all_done COUNT).
    return SimpleNamespace(model_dump=lambda exclude_none=False: {"status": "completed"})


@pytest.mark.asyncio
async def test_update_phase_all_done_true_when_no_incomplete(monkeypatch):
    from app.services import release_service as svc

    monkeypatch.setattr(
        svc, "get_phase_or_404",
        AsyncMock(return_value=SimpleNamespace(status="pending", name="P1")),
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_Res(0)))  # 0 incomplete

    phase, all_done = await svc.update_phase(
        db, str(uuid.uuid4()), str(uuid.uuid4()), _body()
    )

    assert all_done is True
    assert db.execute.await_count == 1   # single COUNT, not a fetch-all + scan
    assert phase.status == "completed"   # the update was applied


@pytest.mark.asyncio
async def test_update_phase_all_done_false_when_incomplete_remain(monkeypatch):
    from app.services import release_service as svc

    monkeypatch.setattr(
        svc, "get_phase_or_404",
        AsyncMock(return_value=SimpleNamespace(status="pending", name="P1")),
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_Res(3)))  # 3 incomplete

    _, all_done = await svc.update_phase(
        db, str(uuid.uuid4()), str(uuid.uuid4()), _body()
    )

    assert all_done is False
    assert db.execute.await_count == 1
