"""The dev seed dates each test case at its run; migration 0199 repairs the rest.

``scripts/seed_dev_data.py`` created every ``TestCase`` without ``created_at``,
so thirty days of seeded results were all dated the minute the seed ran. The
windows on ``test_cases.created_at`` (top failing, failure categories) then
counted all thirty days as "the last 7", while the flaky list (windowed on the
run's start, through ``test_case_history``) did not: one /failures row read
"7" failures over "failed 3 of 7 executions" (the UX redesign's browser E2E
pass, 2026-10-08; every seeded install, the homelab demo included).

The same pass found the seed never ran the canonical sync, so /suites listed
none of its six suites; ``_seed_suite_catalog`` runs it now.

Same fixture pattern as ``test_viz_seed_postgres.py``: requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head, skips
otherwise. Nothing is committed: each test rolls its session back, so no other
project, the base seed's included, is ever touched.

Runs in CI as part of the ``postgres-integration`` job's file list in
``.github/workflows/ci.yml``.
"""

from __future__ import annotations

import importlib.util
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

MIGRATION_0199 = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "0199_date_seeded_test_cases_at_their_run.py"
)


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def session():
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        try:
            yield db
        finally:
            await db.rollback()
    await engine.dispose()


def _load_0199():
    spec = importlib.util.spec_from_file_location("migration_0199", MIGRATION_0199)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_the_seed_dates_each_test_case_at_its_run(session, monkeypatch) -> None:
    from app.models.postgres import Project
    from scripts import seed_dev_data

    # A fresh generator: the module's RNG is shared state.
    monkeypatch.setattr(seed_dev_data, "RNG", random.Random(42))
    token = uuid.uuid4().hex[:12]
    project = Project(
        id=uuid.uuid4(),
        name=f"seed times {token}",
        slug=f"seed-times-{token}",
        description=f"seed times {token}",
    )
    session.add(project)
    await session.flush()

    runs = await seed_dev_data._seed_test_runs(session, project, None, num_days=3)

    assert len(runs) == 3
    starts = {
        row.test_run_id: row.created_at
        for row in (
            await session.execute(
                text(
                    "SELECT tc.test_run_id, tc.created_at FROM test_cases tc "
                    "JOIN test_runs tr ON tr.id = tc.test_run_id WHERE tr.project_id = :p"
                ),
                {"p": project.id},
            )
        ).all()
    }
    assert set(starts) == {run.id for run in runs}
    misdated = (
        await session.execute(
            text(
                "SELECT count(*) FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id "
                "WHERE tr.project_id = :p AND tc.created_at <> tr.start_time"
            ),
            {"p": project.id},
        )
    ).scalar_one()
    assert misdated == 0
    # Three days apart, not one instant: the oldest run's cases are days old.
    assert min(starts.values()) < datetime.now(timezone.utc) - timedelta(days=1)


async def test_the_seed_registers_its_suites_in_the_catalog(session, monkeypatch) -> None:
    """The seed never ran the canonical sync, so /suites listed none of its six
    suites (browser E2E pass, 2026-10-08). It now runs it, oldest run first."""
    from app.models.postgres import Project
    from scripts import seed_dev_data

    monkeypatch.setattr(seed_dev_data, "RNG", random.Random(42))
    token = uuid.uuid4().hex[:12]
    project = Project(
        id=uuid.uuid4(), name=f"seed catalog {token}", slug=f"seed-catalog-{token}",
        description=f"seed catalog {token}",
    )
    session.add(project)
    await session.flush()
    runs = await seed_dev_data._seed_test_runs(session, project, None, num_days=3)

    await seed_dev_data._seed_suite_catalog(session, project, runs)

    suites = set(
        (
            await session.execute(
                text("SELECT name FROM test_suites WHERE project_id = :p"), {"p": project.id}
            )
        ).scalars()
    )
    assert {s["name"] for s in seed_dev_data.SUITES} <= suites
    unlinked = (
        await session.execute(
            text(
                "SELECT count(*) FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id "
                "WHERE tr.project_id = :p AND tc.canonical_test_case_id IS NULL"
            ),
            {"p": project.id},
        )
    ).scalar_one()
    assert unlinked == 0
    # Each canonical test was last seen in the newest run.
    newest = max(runs, key=lambda r: r.start_time).id
    stale = (
        await session.execute(
            text(
                "SELECT count(*) FROM canonical_test_cases "
                "WHERE project_id = :p AND last_seen_run_id <> :r"
            ),
            {"p": project.id, "r": newest},
        )
    ).scalar_one()
    assert stale == 0


async def _project_with_run(session, *, seeded: bool, job_suffix: str) -> tuple[uuid.UUID, uuid.UUID]:
    from app.models.postgres import LaunchStatus, Project, TestCase, TestRun, TestStatus

    token = uuid.uuid4().hex[:12]
    slug = f"seed-fix-{token}"
    marker = "seed_dev_data_v1" if seeded else "a real project"
    project = Project(id=uuid.uuid4(), name=f"p {token}", slug=slug, description=f"demo · {marker}")
    session.add(project)
    start = datetime.now(timezone.utc) - timedelta(days=20)
    run = TestRun(
        id=uuid.uuid4(),
        project_id=project.id,
        build_number=f"b-{token[:8]}",
        status=LaunchStatus.FAILED,
        jenkins_job=f"{slug.replace('-', '_')}-{job_suffix}",
        start_time=start,
        end_time=start + timedelta(minutes=5),
        created_at=start,
    )
    session.add(run)
    await session.flush()
    case_id = uuid.uuid4()
    # No created_at: the insert time, as the seed wrote it.
    session.add(
        TestCase(
            id=case_id,
            test_run_id=run.id,
            test_name="test_x",
            class_name="Suite",
            suite_name="Suite",
            test_fingerprint=f"fp{token}",
            status=TestStatus.FAILED,
        )
    )
    await session.flush()
    return case_id, run.id


async def test_migration_0199_redates_only_the_dev_seeds_rows(session) -> None:
    module = _load_0199()
    assert (module.revision, module.down_revision) == ("0199", "0198")

    seeded, seeded_run = await _project_with_run(session, seeded=True, job_suffix="regression-pipeline")
    other_job, _ = await _project_with_run(session, seeded=True, job_suffix="qa-pipeline")
    real, _ = await _project_with_run(session, seeded=False, job_suffix="regression-pipeline")

    async def created(case_id):
        return (
            await session.execute(text("SELECT created_at FROM test_cases WHERE id = :i"), {"i": case_id})
        ).scalar_one()

    before = {case: await created(case) for case in (seeded, other_job, real)}

    await session.execute(text(module.SQL))
    run_start = (
        await session.execute(text("SELECT start_time FROM test_runs WHERE id = :r"), {"r": seeded_run})
    ).scalar_one()
    assert await created(seeded) == run_start
    # The viz seed's jobs and real projects keep their own times.
    assert await created(other_job) == before[other_job]
    assert await created(real) == before[real]

    # Idempotent: a second pass changes nothing.
    await session.execute(text(module.SQL))
    assert await created(seeded) == run_start
