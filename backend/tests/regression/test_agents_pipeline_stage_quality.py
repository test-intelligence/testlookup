"""D-153: a DEGRADED pipeline row says why it is degraded.

The ``/agents`` row showed a bare DEGRADED tag: the list response carried no
stage detail, so nothing could say which stage failed or fell back. The list
and detail routes now attach ``stage_quality_detail`` (stages run, and the
failed / fallen-back stages with their ``fallback_reason``) for a degraded
pipeline only, in one query, and query nothing when no row is degraded.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/test")

import pytest

pytest.importorskip("sqlalchemy")

from app.models.postgres import AgentPipelineRun  # noqa: E402
from app.models.schemas import AgentPipelineResponse  # noqa: E402
from app.routers import agents  # noqa: E402


class _Rows:
    def __init__(self, rows: list[SimpleNamespace]):
        self._rows = rows

    def all(self) -> list[SimpleNamespace]:
        return self._rows


def _stage(pid, name, status, *, attempt=1, fallback_used=None, reason=None, category=None):
    return SimpleNamespace(
        pipeline_run_id=pid, stage_name=name, status=status, attempt=attempt,
        fallback_used=fallback_used, fallback_reason=reason, error_category=category,
    )


@pytest.mark.asyncio
async def test_a_degraded_pipeline_gets_its_failed_and_fallen_back_stages():
    degraded = SimpleNamespace(id=uuid.uuid4(), execution_metadata={"stage_quality": "degraded"})
    clean = SimpleNamespace(id=uuid.uuid4(), execution_metadata={"stage_quality": "normal"})
    pid = degraded.id
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Rows([
        _stage(pid, "ingestion", "completed"),
        _stage(pid, "root_cause_analysis", "failed", category="timeout"),     # attempt 1 ...
        _stage(pid, "analysis", "completed", fallback_used=True, reason="2/4 tests fell back from requested engine"),
        _stage(pid, "summary", "skipped"),
        _stage(pid, "root_cause_analysis", "failed", attempt=2, reason="deadline_exceeded", category="timeout"),
    ]))

    await agents._attach_stage_quality(db, [degraded, clean])

    db.execute.assert_awaited_once()
    assert clean.stage_quality_detail is None
    assert degraded.stage_quality_detail == {
        "stages_run": 3,                       # summary was skipped; the RCA retry counts once
        "issues": [
            {"stage_name": "root_cause_analysis", "status": "failed",
             "fallback_reason": "deadline_exceeded", "error_category": "timeout"},
            {"stage_name": "analysis", "status": "completed",
             "fallback_reason": "2/4 tests fell back from requested engine", "error_category": None},
        ],
    }


@pytest.mark.asyncio
async def test_no_degraded_pipeline_queries_nothing():
    db = AsyncMock()
    rows = [SimpleNamespace(id=uuid.uuid4(), execution_metadata=None),
            SimpleNamespace(id=uuid.uuid4(), execution_metadata={"stage_quality": "normal"})]

    await agents._attach_stage_quality(db, rows)

    db.execute.assert_not_awaited()
    assert [r.stage_quality_detail for r in rows] == [None, None]


def test_the_response_carries_the_detail():
    row = AgentPipelineRun(
        id=uuid.uuid4(), test_run_id=uuid.uuid4(), workflow_type="deep", status="completed",
        created_at=datetime(2026, 11, 21, tzinfo=timezone.utc), attempt=2, max_attempts=5,
        execution_metadata={"stage_quality": "degraded"},
    )
    row.stage_quality_detail = {
        "stages_run": 4,
        "issues": [{"stage_name": "summary", "status": "failed", "fallback_reason": None, "error_category": "timeout"}],
    }

    payload = AgentPipelineResponse.model_validate(row).model_dump(mode="json")

    assert payload["attempt"] == 2 and payload["created_at"].startswith("2026-11-21")
    assert payload["stage_quality_detail"] == {
        "stages_run": 4,
        "issues": [{"stage_name": "summary", "status": "failed", "fallback_reason": None, "error_category": "timeout"}],
    }


def test_list_and_detail_routes_attach_the_stage_quality():
    import inspect

    assert "await _attach_stage_quality(db, pipelines)" in inspect.getsource(agents.list_pipelines)
    assert "await _attach_stage_quality(db, [pipeline])" in inspect.getsource(agents.get_pipeline)
