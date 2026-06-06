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
    def __init__(self, scalar):
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar


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
