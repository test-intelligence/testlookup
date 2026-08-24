"""Regression pin for the Phase AUTO perf fix (auto/perf-20260603-2300).

``feedback_service.get_feedback_stats`` ran 3 COUNTs on AIFeedback (group-by
ratings, total, unexported) and ``get_training_status`` ran 2 (unexported,
total). The total + unexported pair is now one aggregate query with a
conditional ``count(...).filter(exported.is_(False))``: get_feedback_stats 3→2,
get_training_status 2→1. Output identical (FILTER (exported IS FALSE) matches
the prior WHERE exactly).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.regression


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _One:
    def __init__(self, row):
        self._row = row

    def one(self):
        return self._row


@pytest.mark.asyncio
async def test_get_feedback_stats_collapses_total_and_unexported():
    from app.services import feedback_service as svc

    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        _Rows([("correct", 5), ("incorrect", 2)]),          # group-by ratings
        _One(SimpleNamespace(total=7, unexported=3)),         # total + unexported (one query)
    ]))

    out = await svc.get_feedback_stats(db)

    assert db.execute.await_count == 2  # was 3
    assert out["total_feedback"] == 7
    assert out["unexported"] == 3
    assert out["by_rating"] == {"correct": 5, "incorrect": 2}


@pytest.mark.asyncio
async def test_get_training_status_collapses_counts():
    from app.services import feedback_service as svc

    db = SimpleNamespace(execute=AsyncMock(
        # `labelled` joined this aggregate when the ML activation gate was
        # added (F-10) -- folded into the SAME query precisely so the
        # single-call invariant below still holds.
        return_value=_One(SimpleNamespace(total=10, unexported=4, labelled=0))
    ))
    settings = SimpleNamespace(
        FINETUNE_ENABLED=True,
        FINETUNE_CLASSIFIER_MIN_EXAMPLES=200,
        FINETUNE_REASONING_MIN_EXAMPLES=100,
        FINETUNE_EMBED_MIN_PAIRS=50,
        FINETUNE_INCREMENTAL_TRIGGER=25,
    )

    with patch("app.services.model_registry.ModelRegistry.get_all_status",
               AsyncMock(return_value={})):
        out = await svc.get_training_status(db, settings)

    assert db.execute.await_count == 1  # was 2
    assert out["feedback"] == {"total": 10, "unexported": 4}
    assert out["finetune_enabled"] is True
