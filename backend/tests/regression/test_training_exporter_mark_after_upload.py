"""Regression: training exporter marked feedback exported BEFORE the upload.

Bug pinned (review/training-exporter, 2026-06-02): ``_export_classifier`` set
``AIFeedback.exported=True`` and committed inside the read session, then
uploaded the JSONL. ``exported.is_(False)`` is the "feedback ready to export"
stat (feedback_service / training_tasks), so an upload failure reset the stat
to zero with no artifact written for the run. Fix: upload first, mark exported
only after the JSONL is durably written.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.services.training import exporter as exp  # noqa: E402


class _R:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, results, sink):
        self._results = list(results)
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, *a, **k):
        self._sink.append(str(stmt))
        return self._results.pop(0) if self._results else _R([])

    async def commit(self):
        self._sink.append("COMMIT")


def _row():
    return SimpleNamespace(
        test_name="t", error_message="boom", failure_category="PRODUCT_BUG",
        root_cause_summary="rc", confidence_score=90,
    )


def _read_results():
    # source1 (defects) → 1 row; source2, source3 → empty; ids-select → 1 id
    return [_R([_row()]), _R([]), _R([]), _R([(uuid.uuid4(),)])]


def _marked(sql_log) -> bool:
    return any("UPDATE ai_feedback" in s and "exported" in s for s in sql_log)


@pytest.mark.asyncio
async def test_feedback_not_marked_exported_when_upload_fails():
    sql_log: list[str] = []
    sessions = iter([_FakeSession(_read_results(), sql_log)])
    exporter = exp.TrainingDataExporter()

    with patch.object(exp, "AsyncSessionLocal", lambda: next(sessions)), \
         patch.object(exporter, "_write_jsonl",
                      AsyncMock(side_effect=RuntimeError("minio down"))):
        with pytest.raises(RuntimeError):
            await exporter._export_classifier()

    # Upload failed → feedback must NOT have been marked exported.
    assert not _marked(sql_log)


@pytest.mark.asyncio
async def test_feedback_marked_exported_only_after_successful_upload():
    sql_log: list[str] = []
    # read session + mark session
    sessions = iter([
        _FakeSession(_read_results(), sql_log),
        _FakeSession([_R([])], sql_log),
    ])
    exporter = exp.TrainingDataExporter()

    write = AsyncMock(return_value=1)
    with patch.object(exp, "AsyncSessionLocal", lambda: next(sessions)), \
         patch.object(exporter, "_write_jsonl", write):
        result = await exporter._export_classifier()

    assert result == 1
    write.assert_awaited_once()
    # The mark-exported UPDATE ran. Ordering is structural: _write_jsonl is
    # awaited before the mark session opens, and the failure-path test proves
    # the mark never runs when the upload raises.
    assert _marked(sql_log)
