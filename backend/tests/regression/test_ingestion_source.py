"""Regression: TestRun.ingestion_source tracks how a run entered TestLookup.

Manual-upload feature (PRD: Manual Test Report Upload, MRU-1/MRU-2). The
``ingestion_source`` column (migration 0091) lets the UI badge manually
uploaded runs and lets dashboards distinguish CI-driven runs from operator
uploads. This pins:

  1. ``create_run_from_payload`` threads the caller-supplied source onto the
     new TestRun (the file-upload task passes ``"upload"``).
  2. It defaults to ``"unknown"`` when a caller doesn't specify one.
  3. The run-read schema (``TestRunSummary``) exposes the field so the
     frontend badge can read it.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from app.models.postgres import IngestionSource, LaunchStatus  # noqa: E402
# Aliased so pytest doesn't try to collect the Pydantic model as a test class.
from app.models.schemas import TestRunSummary as RunSummarySchema  # noqa: E402
from app.services.ingestion_pipeline import create_run_from_payload  # noqa: E402


class _Result:
    def __init__(self, scalar=None, first=None):
        self._scalar = scalar
        self._first = first

    def scalar_one_or_none(self):
        return self._scalar

    def first(self):
        return self._first


def _mock_db(project):
    db = AsyncMock()
    # 1st execute → project lookup; 2nd → existing-run check (None = create new)
    db.execute = AsyncMock(side_effect=[_Result(project), _Result(None)])
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.mark.asyncio
async def test_create_run_threads_upload_source():
    project = SimpleNamespace(id=uuid.uuid4())
    db = _mock_db(project)

    run = await create_run_from_payload(
        db,
        project_id=str(project.id),
        build_number="upload-123",
        ingestion_source="upload",
    )

    assert run.ingestion_source == "upload"
    db.add.assert_called_once_with(run)


@pytest.mark.asyncio
async def test_create_run_defaults_source_to_unknown():
    project = SimpleNamespace(id=uuid.uuid4())
    db = _mock_db(project)

    run = await create_run_from_payload(
        db, project_id=str(project.id), build_number="b1",
    )

    assert run.ingestion_source == IngestionSource.UNKNOWN.value == "unknown"


@pytest.mark.asyncio
async def test_reuse_existing_returns_run_without_clobbering_source():
    """The SDK/CI batch path (reuse_existing=True) reuses a run on a build
    collision and must NOT overwrite its ingestion_source (no 'live' -> 'sdk'
    blending, no misleading badge)."""
    project = SimpleNamespace(id=uuid.uuid4())
    existing = SimpleNamespace(id=uuid.uuid4(), ingestion_source='live', build_number='b1')
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_Result(scalar=project), _Result(scalar=existing)])
    db.flush = AsyncMock()
    db.add = MagicMock()

    run = await create_run_from_payload(
        db, project_id=str(project.id), build_number='b1',
        ingestion_source='sdk', reuse_existing=True,
    )

    assert run is existing
    assert run.ingestion_source == 'live'  # preserved, not clobbered
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_upload_never_merges_and_suffixes_on_collision():
    """The manual-upload path (reuse_existing=False) must NEVER merge into an
    existing run. On a build-label collision it creates a fresh run with a
    suffixed label so the returned id is authoritative and aggregates aren't
    blended."""
    project = SimpleNamespace(id=uuid.uuid4())
    new_id = str(uuid.uuid4())
    db = AsyncMock()
    # project lookup → _unique_build_number: 'b1' taken, then 'b1-2' free.
    db.execute = AsyncMock(side_effect=[
        _Result(scalar=project),
        _Result(first=('row',)),   # 'b1' is taken
        _Result(first=None),       # 'b1-2' is free
    ])
    db.flush = AsyncMock()
    db.add = MagicMock()

    run = await create_run_from_payload(
        db, project_id=str(project.id), build_number='b1', run_id=new_id,
        ingestion_source='upload', reuse_existing=False,
    )

    assert run.build_number == 'b1-2'
    assert str(run.id) == new_id          # endpoint-supplied id is authoritative
    assert run.ingestion_source == 'upload'
    db.add.assert_called_once_with(run)


@pytest.mark.asyncio
async def test_upload_keeps_label_when_free():
    project = SimpleNamespace(id=uuid.uuid4())
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_Result(scalar=project), _Result(first=None)])
    db.flush = AsyncMock()
    db.add = MagicMock()

    run = await create_run_from_payload(
        db, project_id=str(project.id), build_number='upload-xyz',
        ingestion_source='upload', reuse_existing=False,
    )

    assert run.build_number == 'upload-xyz'


def test_summary_schema_exposes_ingestion_source():
    row = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        build_number="upload-123",
        jenkins_job=None,
        trigger_source="api",
        ingestion_source="upload",
        branch=None,
        status=LaunchStatus.PASSED,
        total_tests=3,
        passed_tests=3,
        failed_tests=0,
        skipped_tests=0,
        broken_tests=0,
        pass_rate=100.0,
        duration_ms=10,
        ocp_pod_name=None,
        ocp_namespace=None,
        primary_suite_name="Smoke",
        suite_names=["Smoke"],
        start_time=None,
        end_time=None,
        created_at=__import__("datetime").datetime.now(),
    )

    out = RunSummarySchema.model_validate(row)
    assert out.ingestion_source == "upload"
