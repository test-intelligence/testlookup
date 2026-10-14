"""HON-05: each top-failing row names the run that holds its own last failure.

/failures opened every row's Suspects panel on the project's newest FAILED
run (``useRuns({size: 1, status: 'FAILED'})``), so a test whose last failure
was in an older run was shown the commit range of a run it did not fail in.
``top_failing_tests`` now returns ``last_failed_run_id``: the run of the row
whose ``created_at`` is the test's ``last_failed``.

Real Postgres because the column is an ``ARRAY_AGG(... ORDER BY ...)[1]``
aggregate; a stand-in result cannot prove which row it picks. Same fixture
pattern as ``test_failures_headline_counts_postgres.py``: requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head, skips otherwise, and rolls
everything back.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


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


async def _project_with_runs(
    session, outcomes: dict[str, list[str]], *, token: str, start: datetime,
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """One run per position, an hour apart; ``outcomes[name][i]`` is that
    test's status in run i. Fingerprints are ``<token>-<name>``."""
    from app.models.postgres import LaunchStatus, Project, TestCase, TestRun, TestStatus

    project = Project(id=uuid.uuid4(), name=f"lastrun {uuid.uuid4().hex[:10]}",
                      slug=f"lastrun-{uuid.uuid4().hex[:10]}")
    session.add(project)
    await session.flush()
    run_ids: list[uuid.UUID] = []
    for index in range(max(len(s) for s in outcomes.values())):
        at = start + timedelta(hours=index)
        run = TestRun(
            id=uuid.uuid4(),
            project_id=project.id,
            build_number=f"lastrun-{index}",
            status=LaunchStatus.FAILED,
            start_time=at,
            created_at=at,
        )
        session.add(run)
        await session.flush()
        run_ids.append(run.id)
        for name, statuses in outcomes.items():
            if index >= len(statuses):
                continue
            session.add(TestCase(
                id=uuid.uuid4(),
                test_run_id=run.id,
                test_name=name,
                class_name="LastRun",
                suite_name="LastRun",
                test_fingerprint=f"{token}-{name}",
                status=TestStatus(statuses[index]),
                created_at=at,
            ))
    await session.flush()
    return project.id, run_ids


async def test_each_row_names_the_run_of_its_own_last_failure(session) -> None:
    from app.services import analytics_service

    token = uuid.uuid4().hex[:12]
    start = datetime.now(timezone.utc) - timedelta(days=3)
    project_id, runs = await _project_with_runs(
        session,
        {
            # Fails in the two older runs, passes in the newest.
            "fixed_last_run": ["FAILED", "BROKEN", "PASSED"],
            # Fails only in the newest run.
            "broke_last_run": ["PASSED", "PASSED", "FAILED"],
        },
        token=token,
        start=start,
    )

    out = await analytics_service.top_failing_tests(session, str(project_id), 30, 15)
    by_name = {i["test_name"]: i for i in out["items"]}

    # Not the newest failed run of the project (runs[2]): its own, runs[1].
    assert by_name["fixed_last_run"]["last_failed_run_id"] == str(runs[1])
    assert by_name["broke_last_run"]["last_failed_run_id"] == str(runs[2])
    # A JSON-ready string, the same run as the row's last_failed time, and
    # that run's build to name it by.
    assert isinstance(by_name["fixed_last_run"]["last_failed_run_id"], str)
    assert by_name["fixed_last_run"]["last_failed"] == start + timedelta(hours=1)
    assert by_name["fixed_last_run"]["last_failed_build"] == "lastrun-1"
    assert by_name["broke_last_run"]["last_failed_build"] == "lastrun-2"


async def test_another_projects_newer_failure_does_not_leak_into_the_row(session) -> None:
    """The fingerprint is not salted per project: a foreign project failing the
    same test later must not lend its run id to this project's row."""
    from app.services import analytics_service

    token = uuid.uuid4().hex[:12]
    start = datetime.now(timezone.utc) - timedelta(days=3)
    mine, my_runs = await _project_with_runs(
        session, {"shared": ["FAILED", "PASSED"]}, token=token, start=start,
    )
    _theirs, their_runs = await _project_with_runs(
        session, {"shared": ["PASSED", "PASSED", "FAILED"]}, token=token, start=start,
    )

    out = await analytics_service.top_failing_tests(session, str(mine), 30, 15)

    assert [i["last_failed_run_id"] for i in out["items"]] == [str(my_runs[0])]
    assert str(their_runs[2]) not in {i["last_failed_run_id"] for i in out["items"]}
