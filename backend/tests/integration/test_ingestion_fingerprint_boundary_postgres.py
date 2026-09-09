"""Release-matrix proof for asyncpg's large-ingestion bind boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import uuid
import warnings

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        if os.getenv("TESTLOOKUP_REQUIRE_H06_BOUNDARY", "").lower() == "true":
            pytest.fail("H06 release proof requires TESTLOOKUP_POSTGRES_TEST_DSN")
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.mark.parametrize("requested", [32_767, 50_000])
async def test_large_distinct_batch_persists_exact_terminal_counts(
    requested: int,
) -> None:
    """Exercise SQLAlchemy -> asyncpg -> PostgreSQL at both audit boundaries."""
    from app.models.postgres import (
        LaunchStatus,
        Project,
        TestCase,
        TestCaseHistory,
        TestRun,
    )
    from app.services import ingestion_pipeline as pipeline
    from app.services.ingestion import _update_run_aggregates, make_test_fingerprint

    token = uuid.uuid4().hex
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    results = [
        {
            "test_name": f"boundary_{index}",
            "class_name": "AsyncpgBoundary",
            "suite_name": "release-matrix",
            # A failed final row proves that the complete tail was persisted and
            # participates in terminal status computation.
            "status": "failed" if index == requested - 1 else "passed",
        }
        for index in range(requested)
    ]
    fingerprints = {
        make_test_fingerprint(result["test_name"], result["class_name"])
        for result in results
    }
    assert len(fingerprints) == requested

    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    assert engine.dialect.name == "postgresql"
    assert engine.dialect.driver == "asyncpg"
    factory = async_sessionmaker(engine, expire_on_commit=False)
    started = time.monotonic()
    try:
        async with factory() as db:
            db.add(
                Project(
                    id=project_id,
                    name=f"H06 asyncpg boundary {token}",
                    slug=f"h06-asyncpg-{token}",
                )
            )
            run = TestRun(
                id=run_id,
                project_id=project_id,
                build_number=f"h06-{requested}-{token}",
                jenkins_job="release-matrix",
                status=LaunchStatus.IN_PROGRESS,
            )
            db.add(run)
            await db.flush()

            accepted = await pipeline.ingest_test_results(db, run, results)
            await db.flush()
            await _update_run_aggregates(db, run_id)
            await db.commit()

        async with factory() as verification_db:
            persisted_run = await verification_db.get(TestRun, run_id)
            case_count = await verification_db.scalar(
                select(func.count(TestCase.id)).where(TestCase.test_run_id == run_id)
            )
            distinct_count = await verification_db.scalar(
                select(func.count(func.distinct(TestCase.test_fingerprint))).where(
                    TestCase.test_run_id == run_id
                )
            )
            history_count = await verification_db.scalar(
                select(func.count(TestCaseHistory.id)).where(
                    TestCaseHistory.test_run_id == run_id
                )
            )
            server_version = await verification_db.scalar(select(func.version()))

        assert accepted == requested
        assert case_count == requested
        assert distinct_count == requested
        assert history_count == requested
        assert persisted_run is not None
        assert persisted_run.ingestion_attempted_tests == requested
        assert persisted_run.ingestion_rejected_tests == 0
        assert persisted_run.ingestion_complete is True
        assert persisted_run.total_tests == requested
        assert persisted_run.passed_tests == requested - 1
        assert persisted_run.failed_tests == 1
        assert persisted_run.skipped_tests == 0
        assert persisted_run.broken_tests == 0
        assert persisted_run.unknown_tests == 0
        assert persisted_run.status == LaunchStatus.FAILED
        assert persisted_run.end_time is not None
        assert persisted_run.pass_rate == round((requested - 1) / requested * 100, 2)
        elapsed_seconds = round(time.monotonic() - started, 2)
        terminal_status = getattr(persisted_run.status, "value", persisted_run.status)
        evidence_path = os.getenv("TESTLOOKUP_H06_EVIDENCE_PATH", "").strip()
        if evidence_path:
            path = Path(evidence_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as evidence:
                evidence.write(
                    json.dumps(
                        {
                            "source_sha": os.getenv("GITHUB_SHA", "local"),
                            "requested": requested,
                            "accepted": accepted,
                            "test_cases": case_count,
                            "distinct_fingerprints": distinct_count,
                            "history_rows": history_count,
                            "terminal_status": terminal_status,
                            "ingestion_complete": persisted_run.ingestion_complete,
                            "elapsed_seconds": elapsed_seconds,
                            "database": server_version,
                            "driver": engine.dialect.driver,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
        print(
            f"H06 PostgreSQL boundary persisted {requested} results in "
            f"{elapsed_seconds:.2f}s"
        )
    finally:
        primary_error = sys.exception()
        try:
            async with factory() as cleanup_db:
                await cleanup_db.execute(
                    delete(Project).where(Project.id == project_id)
                )
                await cleanup_db.commit()
        except Exception as cleanup_error:
            if primary_error is None:
                raise
            warnings.warn(
                f"H06 cleanup failed after the primary test failure: {cleanup_error!r}",
                RuntimeWarning,
                stacklevel=1,
            )
        finally:
            await engine.dispose()
