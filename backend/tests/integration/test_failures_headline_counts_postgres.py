"""/failures counts every flaky and repeat-failing test, not its top-N list.

The page read the length of each list as the count. The flaky list's default
limit is 20 and top-failing's is 15, so a project with 30 flaky tests read
"20 tests intermittent" and "Flaky tests 20", and "Repeat failures" never
passed 15 (the UX redesign's browser E2E pass, 2026-10-08). Both endpoints
now return the whole count beside their list: ``total`` for flaky-tests;
``total`` and ``repeat_total`` for top-failing, from window counts taken
before LIMIT.

Same fixture pattern as ``test_viz_seed_postgres.py``: requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head, skips
otherwise. Nothing is committed: the session is rolled back.

Runs in CI as part of the ``postgres-integration`` job's file list in
``.github/workflows/ci.yml``.
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


async def _project_with_history(session, outcomes: dict[str, list[str]]) -> uuid.UUID:
    """One run per position; ``outcomes[name][i]`` is that test's status in run i."""
    from app.models.postgres import (
        LaunchStatus,
        Project,
        TestCase,
        TestCaseHistory,
        TestRun,
        TestStatus,
    )

    token = uuid.uuid4().hex[:12]
    project = Project(id=uuid.uuid4(), name=f"counts {token}", slug=f"counts-{token}")
    session.add(project)
    await session.flush()
    runs = max(len(statuses) for statuses in outcomes.values())
    start = datetime.now(timezone.utc) - timedelta(days=3)
    for index in range(runs):
        at = start + timedelta(hours=index)
        run = TestRun(
            id=uuid.uuid4(),
            project_id=project.id,
            build_number=f"b-{token[:6]}-{index}",
            status=LaunchStatus.FAILED,
            start_time=at,
            created_at=at,
        )
        session.add(run)
        await session.flush()
        for name, statuses in outcomes.items():
            if index >= len(statuses):
                continue
            case = TestCase(
                id=uuid.uuid4(),
                test_run_id=run.id,
                test_name=name,
                class_name="Counts",
                suite_name="Counts",
                test_fingerprint=f"{token}-{name}",
                status=TestStatus(statuses[index]),
                created_at=at,
            )
            session.add(case)
            await session.flush()
            session.add(
                TestCaseHistory(
                    id=uuid.uuid4(),
                    test_case_id=case.id,
                    test_run_id=run.id,
                    test_fingerprint=case.test_fingerprint,
                    status=case.status,
                    created_at=at,
                )
            )
    await session.flush()
    return project.id


async def test_top_failing_counts_every_failing_and_repeat_failing_test(session) -> None:
    from app.services import analytics_service

    project_id = await _project_with_history(
        session,
        {
            "three": ["FAILED", "FAILED", "FAILED"],
            "two": ["FAILED", "FAILED", "PASSED"],
            "two_more": ["FAILED", "PASSED", "BROKEN"],
            "once": ["FAILED", "PASSED", "PASSED"],
            "never": ["PASSED", "PASSED", "PASSED"],
        },
    )

    out = await analytics_service.top_failing_tests(session, str(project_id), 30, 1)

    assert [i["test_name"] for i in out["items"]] == ["three"]
    assert out["total"] == 4  # every test that failed at all
    assert out["repeat_total"] == 3  # failed twice or more
    # The window columns do not leak into the rows.
    assert "failing_total" not in out["items"][0]
    assert "repeat_total" not in out["items"][0]


async def test_flaky_total_is_the_whole_count_past_the_list(session) -> None:
    from app.services import analytics_service

    # P-F-P-F-P: oscillating, two or more flips, inside the ratio band.
    flaky = ["PASSED", "FAILED", "PASSED", "FAILED", "PASSED"]
    project_id = await _project_with_history(
        session,
        {"flake_a": flaky, "flake_b": flaky, "flake_c": flaky, "steady": ["PASSED"] * 5},
    )

    out = await analytics_service.flaky_tests(session, str(project_id), 30, 2)

    assert len(out["items"]) == 2
    assert out["total"] == 3
