"""A run's duration must be computed, not left NULL.

Regression for TL-2026-09-18-01-005.

``_update_run_aggregates`` recomputed six status counts, the pass rate and the
suite attribution — over the very ``TestCase`` rows that carry ``duration_ms``
— and never summed them. ``TestRun.duration_ms`` had no writer anywhere in
``app/``, on either ingest path, so every ingested run carried a NULL duration.

It stayed invisible because the seeded demo data sets the column directly. The
app's own sample runs had durations; real uploads did not.

The user-visible symptom is on ``/runs``: ``RunsPage`` counts a run as carrying
metadata only when ``(duration_ms ?? 0) > 0`` alongside ``branch`` and
``release_name``, and warns when coverage drops below 80%. The product told
users their uploads were missing metadata that the product had declined to
compute.

Confirmed end to end against a running stack before and after the fix, by
ingesting ``samples/junit/auth-suite.xml`` through ``POST /api/v1/ingest/file``:
the run's ``duration_ms`` went from ``None`` to ``12456`` — exactly the
``time="12.456"`` the suite declares for itself, and exactly the sum of its six
cases.

PostgreSQL rather than SQLite because ``test_runs`` carries JSONB columns that
the SQLite dialect cannot render, and because the two existing
``_update_run_aggregates`` tests live here for the same reason. This file is
named in the ``postgres-integration`` job's file list in ``.github/workflows/ci.yml``;
a test nothing runs is decoration.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def _seed(factory, durations: list[int | None]) -> uuid.UUID:
    """One project, one run, one PASSED case per entry in ``durations``."""
    from app.models.postgres import LaunchStatus, Project, TestCase, TestRun, TestStatus

    token = uuid.uuid4().hex
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    async with factory() as db:
        db.add(Project(id=project_id, name=f"dur {token}", slug=f"dur-{token}"))
        db.add(
            TestRun(
                id=run_id,
                project_id=project_id,
                build_number=f"dur-{token[:8]}",
                status=LaunchStatus.IN_PROGRESS,
            )
        )
        await db.flush()
        for index, duration in enumerate(durations):
            db.add(
                TestCase(
                    id=uuid.uuid4(),
                    test_run_id=run_id,
                    test_name=f"case_{index}",
                    class_name="DurationSuite",
                    suite_name="DurationSuite",
                    test_fingerprint=f"{token[:8]}{index:04d}",
                    status=TestStatus.PASSED,
                    duration_ms=duration,
                )
            )
        await db.commit()
    return run_id


async def _aggregate_and_read(factory, run_id: uuid.UUID, *, times: int = 1):
    from app.models.postgres import TestRun
    from app.services.ingestion import _update_run_aggregates

    for _ in range(times):
        async with factory() as db:
            await _update_run_aggregates(db, run_id)
            await db.commit()
    async with factory() as db:
        return await db.get(TestRun, run_id)


async def _cleanup(factory, run_id: uuid.UUID) -> None:
    from app.models.postgres import Project, TestCase, TestRun

    async with factory() as db:
        run = await db.get(TestRun, run_id)
        project_id = run.project_id if run else None
        await db.execute(delete(TestCase).where(TestCase.test_run_id == run_id))
        await db.execute(delete(TestRun).where(TestRun.id == run_id))
        if project_id is not None:
            await db.execute(delete(Project).where(Project.id == project_id))
        await db.commit()


@pytest.fixture
async def factory():
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_run_duration_is_the_sum_of_its_cases(factory) -> None:
    """The exact shape of ``samples/junit/auth-suite.xml``, whose suite declares
    ``time="12.456"`` across six cases."""
    run_id = await _seed(factory, [1234, 892, 2100, 3210, 4120, 900])
    try:
        run = await _aggregate_and_read(factory, run_id)
        assert run.duration_ms == 12456
        # The counts the function already computed must not regress.
        assert run.total_tests == 6
    finally:
        await _cleanup(factory, run_id)


async def test_an_unmeasured_run_stays_null_rather_than_zero(factory) -> None:
    """A run whose cases carry no duration has not been measured as 0ms.

    ``RunsPage`` treats 0 and null alike for its coverage warning, but they are
    different claims: 0 asserts an instantaneous run. Absence is not a
    measurement of zero.
    """
    run_id = await _seed(factory, [None, None])
    try:
        assert (await _aggregate_and_read(factory, run_id)).duration_ms is None
    finally:
        await _cleanup(factory, run_id)


async def test_partially_timed_cases_sum_the_timed_ones(factory) -> None:
    """A parser that timed some cases and not others still yields a duration."""
    run_id = await _seed(factory, [500, None, 250])
    try:
        assert (await _aggregate_and_read(factory, run_id)).duration_ms == 750
    finally:
        await _cleanup(factory, run_id)


async def test_recomputing_does_not_drift(factory) -> None:
    """``_update_run_aggregates`` runs more than once per run (upload, then
    finalize). The duration must be a fresh SUM each time, not accumulated."""
    run_id = await _seed(factory, [100, 200])
    try:
        assert (await _aggregate_and_read(factory, run_id, times=3)).duration_ms == 300
    finally:
        await _cleanup(factory, run_id)
