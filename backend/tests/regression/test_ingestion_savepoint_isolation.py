"""M10: one rejected result must not poison the rest of an ingest batch.

The production failure was a PostgreSQL transaction rule, not merely missing
logging: after ``flush()`` raises, the outer SQLAlchemy transaction is aborted
until rollback.  Catching the exception and continuing therefore turns every
later row into ``PendingRollbackError``.  These tests pin the partial-acceptance
contract: each row gets a SAVEPOINT, rejection metadata is durable and bounded,
and an incomplete run cannot be reported as passed or clear a release gate.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")


class _EmptyPrefetch:
    def scalars(self):
        return self

    def all(self):
        return []


class _Savepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.asyncio
async def test_valid_invalid_valid_rows_are_isolated_and_rejection_is_durable():
    from app.services import ingestion_pipeline as pipeline

    run = SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4(), framework="junit"
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_EmptyPrefetch()),
        begin_nested=MagicMock(side_effect=_Savepoint),
    )
    results = [
        {"test_name": "first", "status": "passed", "suite_name": "smoke"},
        {"test_name": "bad", "status": "passed", "suite_name": "smoke"},
        {"test_name": "last", "status": "failed", "suite_name": "smoke"},
    ]

    async def _upsert(_db, case, _run, **_kwargs):
        if case["test_name"] == "bad":
            raise RuntimeError("secret database detail must not be persisted")
        return SimpleNamespace(test_fingerprint=case["test_name"])

    with (
        patch.object(pipeline, "_upsert_test_case", side_effect=_upsert),
        patch.object(pipeline, "_count_ingested_cases") as count_metric,
    ):
        accepted = await pipeline.ingest_test_results(db, run, results)

    assert accepted == 2
    assert db.begin_nested.call_count == 3
    assert run.ingestion_attempted_tests == 3
    assert run.ingestion_rejected_tests == 1
    assert run.ingestion_complete is False
    assert len(run.ingestion_rejection_reasons) == 1
    reason = run.ingestion_rejection_reasons[0]
    assert reason["row_index"] == 1
    assert reason["error_type"] == "RuntimeError"
    assert "secret database detail" not in str(reason)
    emitted_counts = count_metric.call_args.args[1]
    assert sum(emitted_counts.values()) == accepted


@pytest.mark.asyncio
async def test_rejection_samples_are_bounded():
    from app.services import ingestion_pipeline as pipeline

    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), framework=None)
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_EmptyPrefetch()),
        begin_nested=MagicMock(side_effect=_Savepoint),
    )
    results = [
        {"test_name": f"bad-{i}", "status": "passed", "suite_name": "smoke"}
        for i in range(100)
    ]

    with patch.object(
        pipeline,
        "_upsert_test_case",
        new=AsyncMock(side_effect=RuntimeError("private driver message")),
    ):
        accepted = await pipeline.ingest_test_results(db, run, results)

    assert accepted == 0
    assert run.ingestion_rejected_tests == 100
    from app.models.postgres import LaunchStatus
    assert run.status == LaunchStatus.STOPPED
    assert len(run.ingestion_rejection_reasons) <= 20
    assert "private driver message" not in str(run.ingestion_rejection_reasons)


def test_incomplete_release_rollup_cannot_return_go():
    from app.services import release_rollup_service as rollup_service

    rollup = rollup_service.ReleaseRollup(
        latest_by_test={
            f"smoke::test-{i}": "PASSED"
            for i in range(rollup_service.MIN_EVIDENCE)
        },
        incomplete_runs={"run-partial": 1},
    )

    verdict, reasons = rollup_service.decide(rollup)

    assert verdict == "NOT_EVALUATED"
    assert any("run-partial" in reason and "rejected 1" in reason for reason in reasons)
    scorecard = rollup_service.summarise(rollup)
    assert scorecard["ingestion_complete"] is False
    assert scorecard["incomplete_runs"] == {"run-partial": 1}


@pytest.mark.asyncio
async def test_incomplete_run_aggregate_status_is_stopped():
    from sqlalchemy.dialects import postgresql

    from app.models.postgres import LaunchStatus
    from app.services import ingestion

    counts = SimpleNamespace(
        total=5,
        passed=5,
        failed=0,
        skipped=0,
        broken=0,
        unknown=0,
        ingestion_complete=False,
    )

    class _Result:
        def one(self):
            return counts

        def all(self):
            return []

        def scalar_one_or_none(self):
            return None

    class _DB:
        def __init__(self):
            self.calls = 0
            self.update = None

        async def execute(self, statement):
            self.calls += 1
            if self.calls == 4:
                self.update = statement
            return _Result()

    db = _DB()
    await ingestion._update_run_aggregates(db, uuid.uuid4())

    params = db.update.compile(dialect=postgresql.dialect()).params
    assert params["status"] == LaunchStatus.STOPPED


@pytest.mark.asyncio
async def test_release_rollup_reads_incomplete_state_from_selected_runs():
    from datetime import datetime, timezone

    from app.services import release_rollup_service as rollup_service

    release_id = uuid.uuid4()
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    run = SimpleNamespace(
        id=run_id,
        project_id=project_id,
        primary_release_id=release_id,
        start_time=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        ingestion_complete=False,
        ingestion_rejected_tests=2,
    )

    class _Rows:
        def __init__(self, rows):
            self.rows = rows

        def scalar_one_or_none(self):
            return self.rows[0] if self.rows else None

        def scalars(self):
            return self

        def all(self):
            return self.rows

    class _DB:
        async def execute(self, statement):
            sql = " ".join(str(statement).split())
            if "FROM releases" in sql:
                return _Rows([project_id])
            if "FROM test_runs" in sql:
                return _Rows([run])
            if "release_test_run_links" in sql:
                return _Rows([(run_id, "explicit_name")])
            if "FROM test_cases" in sql:
                return _Rows([])
            raise AssertionError(sql)

    rollup = await rollup_service.build_rollup(_DB(), release_id)

    assert rollup.incomplete_runs == {str(run_id): 2}


def test_test_run_model_and_schema_expose_ingestion_completeness():
    from app.models.postgres import TestRun
    from app.models.schemas import TestRunSummary

    assert hasattr(TestRun, "ingestion_attempted_tests")
    assert hasattr(TestRun, "ingestion_rejected_tests")
    assert hasattr(TestRun, "ingestion_complete")
    assert hasattr(TestRun, "ingestion_rejection_reasons")
    fields = TestRunSummary.model_fields
    assert {
        "ingestion_attempted_tests",
        "ingestion_rejected_tests",
        "ingestion_complete",
        "ingestion_rejection_reasons",
    } <= set(fields)


def test_migration_0161_adds_ingestion_outcome_columns():
    path = Path(__file__).parents[2] / "migrations" / "versions" / "0161_ingestion_outcome.py"
    spec = importlib.util.spec_from_file_location("migration_0161", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0161"
    assert module.down_revision == "0160"
    source = path.read_text(encoding="utf-8")
    for column in (
        "ingestion_attempted_tests",
        "ingestion_rejected_tests",
        "ingestion_complete",
        "ingestion_rejection_reasons",
    ):
        assert column in source
