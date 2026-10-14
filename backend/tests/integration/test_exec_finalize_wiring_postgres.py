"""EXEC-01/02/05 wired end to end: an upload's report timings reach finalize_run's new steps.

The parsers (EXEC-01), the suite-execution writer (EXEC-02) and the duration check (EXEC-05) each have their own
tests; this one drives the REAL ``finalize_run`` on a run ingested from a TestNG report with its parsed
``ReportTimings`` and checks what only the wiring can produce:

* ``suite_executions`` holds one row per suite of the run, taken from the report (``source='report'``);
* the run's own wall clock / start / finish come from the report, and ``duration_ms`` still means summed test time;
* the duration step ran after the suite rows existed and stored ``duration_findings`` honestly: a first run of a
  project has no history, so it is ``measured: false`` with the reason, never a zero.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head; skips otherwise. Listed in CI's protected
PostgreSQL step.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "timing"


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def test_finalize_records_suite_executions_and_duration_findings_from_the_report(monkeypatch):
    from app.models.postgres import LaunchStatus, Project, SuiteExecution, TestRun
    from app.services import ingestion_pipeline as pipeline
    from app.worker.tasks import _parse_file_to_results_with_timings

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=2)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # finalize_run opens its own sessions; point them at the test database.
    monkeypatch.setattr(pipeline, "AsyncSessionLocal", factory)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", factory, raising=False)

    token = uuid.uuid4().hex[:12]
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    content = (FIXTURES / "testng-results-config.xml").read_text("utf-8")
    results, timings = _parse_file_to_results_with_timings(content, "testng", "testng-results.xml", str(run_id))
    assert timings.get("suites"), "the fixture states suite timing; the test needs it"

    try:
        async with factory() as db:
            db.add(Project(id=project_id, name=f"EXEC wiring {token}", slug=f"exec-wiring-{token}"))
            await db.flush()
            run = TestRun(
                id=run_id, project_id=project_id, build_number=f"wiring-{token}",
                jenkins_job="exec-wiring", status=LaunchStatus.IN_PROGRESS,
            )
            db.add(run)
            await db.flush()
            assert await pipeline.ingest_test_results(db, run, results) == len(results)
            await db.commit()

        await pipeline.finalize_run(
            run_id=str(run_id), project_id=str(project_id), build_number=f"wiring-{token}",
            run_ai=False, report_timings=timings,
        )

        async with factory() as db:
            suites = (await db.execute(select(SuiteExecution).where(SuiteExecution.test_run_id == run_id))).scalars().all()
            stored_run = (await db.execute(select(TestRun).where(TestRun.id == run_id))).scalar_one()
    finally:
        async with factory() as cleanup:
            await cleanup.execute(delete(Project).where(Project.id == project_id))
            await cleanup.commit()
        await engine.dispose()

    assert suites, "finalize_run wrote no suite_executions row: the EXEC-02 step is not wired"
    assert {s.source for s in suites} == {"report"}, [(s.suite_name, s.source) for s in suites]
    assert all(s.wall_ms is not None and s.summed_ms is not None for s in suites)

    # The run's clock: the report's own when it states one, else the union of the suites' report spans.
    expected_source = "report" if timings.get("run_started_at") and timings.get("run_finished_at") else "derived"
    assert stored_run.timing_source == expected_source, (stored_run.timing_source, timings.get("run_started_at"))
    assert stored_run.wall_clock_ms is not None and stored_run.exec_started_at is not None
    first_suite_start = min(s.started_at for s in suites if s.started_at is not None)
    assert stored_run.exec_started_at <= first_suite_start
    assert stored_run.duration_ms == sum(int(r.get("duration_ms") or 0) for r in results), (
        "duration_ms must keep meaning summed test time"
    )

    findings = stored_run.duration_findings
    assert findings is not None, "finalize_run did not run the EXEC-05 duration step"
    assert findings.get("measured") is False and findings.get("reason"), findings
