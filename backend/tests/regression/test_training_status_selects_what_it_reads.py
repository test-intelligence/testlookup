"""GET /training/status read a count its query never selected.

The API sweep (homelab, 2026-10-10) found ``GET /api/v1/training/status``
answering 500 for every role: ``get_training_status`` read ``totals.labelled``
from an aggregate that selected only ``total`` and ``unexported``. The count had
been folded into ``get_feedback_stats``' aggregate instead (F-10). Both unit
tests passed, because their fake row carried ``labelled`` whatever the query
asked for.

This fake answers only the columns the statement actually selects, so a read
of anything else fails here as it failed in production.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

SETTINGS = SimpleNamespace(
    FINETUNE_ENABLED=False,
    FINETUNE_CLASSIFIER_MIN_EXAMPLES=500,
    FINETUNE_REASONING_MIN_EXAMPLES=200,
    FINETUNE_EMBED_MIN_PAIRS=100,
    FINETUNE_INCREMENTAL_TRIGGER=50,
    ML_MIN_TRAINING_SAMPLES=50,
)


class _SelectedColumnsOnly:
    """A db whose result row has exactly the columns the query selected."""

    def __init__(self, value: int = 3):
        self.value = value
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        names = [column.name for column in stmt.selected_columns]
        row = SimpleNamespace(**{name: self.value for name in names})
        return SimpleNamespace(one=lambda: row)


@pytest.mark.asyncio
async def test_the_training_status_query_selects_every_count_it_reads():
    from app.services import feedback_service as svc

    db = _SelectedColumnsOnly(value=3)
    with patch("app.services.model_registry.ModelRegistry.get_all_status", AsyncMock(return_value={})):
        out = await svc.get_training_status(db, SETTINGS)

    [stmt] = db.statements  # still one aggregate, not a second round-trip
    assert {c.name for c in stmt.selected_columns} >= {"total", "unexported", "labelled"}
    assert out["ml_activation"]["labelled_corrections"] == 3
    assert out["feedback"] == {"total": 3, "unexported": 3}
