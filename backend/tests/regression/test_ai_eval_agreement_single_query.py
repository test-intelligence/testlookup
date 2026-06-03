"""Regression pin for the Phase AUTO perf fix (auto/perf-20260603-1830).

``ai_eval_service.compute_agreement_rate`` issued three sequential COUNT queries
over the same ``created_at >= cutoff`` window (total, correct,
partially_correct). They are now one aggregate query with conditional counts
(`count(case((rating == X, 1)))`). Round trips drop 3 → 1; output is identical.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.regression


class _Res:
    def __init__(self, row):
        self._row = row

    def one(self):
        return self._row


@pytest.mark.asyncio
async def test_agreement_rate_uses_one_query_and_preserves_output():
    from app.services import ai_eval_service as svc

    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Res(SimpleNamespace(total=100, correct=80, partial=10)))
    )
    result = await svc.compute_agreement_rate(db, days=30)

    assert db.execute.await_count == 1  # was 3 separate COUNTs
    assert result["total_feedback"] == 100
    assert result["correct"] == 80
    assert result["partially_correct"] == 10
    assert result["incorrect"] == 10  # 100 - 80 - 10
    assert result["agreement_rate"] == round((80 + 10 * 0.5) / 100, 4)  # 0.85
    assert result["period_days"] == 30


@pytest.mark.asyncio
async def test_agreement_rate_zero_feedback_is_none():
    from app.services import ai_eval_service as svc

    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Res(SimpleNamespace(total=0, correct=0, partial=0)))
    )
    result = await svc.compute_agreement_rate(db, days=7)

    assert db.execute.await_count == 1
    assert result["total_feedback"] == 0
    assert result["incorrect"] == 0
    assert result["agreement_rate"] is None
