"""Regression: /agents pipeline cards must carry run/suite context.

The `/agents` (AI Pipelines) list showed only a workflow type per pipeline —
users couldn't tell which run or suite a pipeline analysed. ``_attach_run_context``
now decorates each ``AgentPipelineRun`` (read path, in place, like
``_apply_effective_status``) with the owning TestRun's ``build_number`` +
``primary_suite_name`` and the shared per-(project, suite) ``run_seq`` ("Run #N").

This pins: the attach maps each pipeline to its run, leaves legacy rows (no
TestRun) None instead of raising, and fires no query for an empty list.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/test")

import pytest

pytest.importorskip("sqlalchemy")

from app.routers import agents  # noqa: E402


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


def _pipeline(run_id):
    # SimpleNamespace allows the in-place attribute attachment the helper does.
    return SimpleNamespace(test_run_id=run_id)


@pytest.mark.asyncio
async def test_attaches_build_suite_and_run_seq_from_owning_run():
    run_id = uuid.uuid4()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result([
        SimpleNamespace(id=run_id, build_number="build-42", primary_suite_name="API Regression"),
    ]))
    p = _pipeline(run_id)

    with patch.object(agents.runs_service, "fetch_run_seq_map",
                      AsyncMock(return_value={str(run_id): 7})):
        await agents._attach_run_context(db, [p])

    assert p.build_number == "build-42"
    assert p.suite_name == "API Regression"   # primary_suite_name → suite_name
    assert p.run_seq == 7


@pytest.mark.asyncio
async def test_legacy_pipeline_without_a_testrun_gets_none_not_an_error():
    run_id = uuid.uuid4()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result([]))   # no TestRun row
    p = _pipeline(run_id)

    with patch.object(agents.runs_service, "fetch_run_seq_map",
                      AsyncMock(return_value={})):
        await agents._attach_run_context(db, [p])

    assert p.build_number is None
    assert p.suite_name is None
    assert p.run_seq is None


@pytest.mark.asyncio
async def test_run_seq_is_independent_of_build_and_suite():
    """A run with no materialised suite still gets a Run #N (and vice versa)."""
    run_id = uuid.uuid4()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result([
        SimpleNamespace(id=run_id, build_number="b9", primary_suite_name=None),
    ]))
    p = _pipeline(run_id)

    with patch.object(agents.runs_service, "fetch_run_seq_map",
                      AsyncMock(return_value={str(run_id): 3})):
        await agents._attach_run_context(db, [p])

    assert p.suite_name is None
    assert p.build_number == "b9"
    assert p.run_seq == 3


@pytest.mark.asyncio
async def test_empty_pipeline_list_fires_no_query():
    db = AsyncMock()
    db.execute = AsyncMock()
    with patch.object(agents.runs_service, "fetch_run_seq_map", AsyncMock()) as seq:
        await agents._attach_run_context(db, [])
    db.execute.assert_not_awaited()
    seq.assert_not_awaited()
