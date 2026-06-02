"""Regression: build_dataset_from_feedback silently ignored min_confidence.

Bug pinned (review/ai-eval-service, 2026-06-02): the function accepted a
``min_confidence`` argument but never applied it to the query — a caller asking
for "only high-confidence feedback" silently got everything. Fix: apply
``AIAnalysis.confidence_score >= min_confidence`` when a non-zero floor is given.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from app.services import ai_eval_service as svc  # noqa: E402


class _Res:
    def all(self):
        return []


class _CapturingDB:
    def __init__(self):
        self.sql: list[str] = []

    async def execute(self, stmt, *a, **k):
        self.sql.append(str(stmt))
        return _Res()


@pytest.mark.asyncio
async def test_min_confidence_applies_confidence_floor():
    db = _CapturingDB()
    await svc.build_dataset_from_feedback(db, min_confidence=70)
    # The WHERE clause must carry a confidence_score floor (the SELECT list
    # mentions confidence_score too, so assert on the ">=" comparison).
    assert "confidence_score >=" in db.sql[0]


@pytest.mark.asyncio
async def test_no_confidence_floor_by_default():
    db = _CapturingDB()
    await svc.build_dataset_from_feedback(db)  # min_confidence=0 → no floor
    assert "confidence_score >=" not in db.sql[0]
