"""``live_consumer._finalise_run_in_db`` against real Postgres: a live run ends once, graded and counted.

When the live stream sees ``run_complete`` it finalises the run row: terminal
status from the shared grader, the counts, a pass rate that leaves skips out,
``end_time``, and an analytics epoch bump after the commit. It guards on ``IN_PROGRESS`` and
swallows every exception, so until now the only tests were source checks
(``tests/regression/test_unknown_status_not_erased_from_run.py``). Here, on rows
written by ``create_run_from_payload``:

* a run with passes, a failure, skips and an unknown result ends FAILED with
  every count, ``pass_rate == 75.0`` (3 of 4 executed), ``end_time`` set and
  one analytics epoch bump for its project (recorded, not sent: the real bump
  gives up after 0.25 s, which would make this a timing test);
* a second ``run_complete`` changes nothing;
* a run whose only non-passing result is unknown is not graded PASSED, and an
  all-skipped run has no pass rate (never a measured 0 %);
* a stream run id that is not a UUID raises nothing out of the consumer and
  touches no run (today's behaviour: the failed bind is logged and dropped).

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head; skips only without
it. Writes one throwaway project and removes it (the cleanup raises if a row is
left).
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def _cleanup(sessions, project_id: uuid.UUID) -> None:
    """Delete the project (its runs cascade); prove nothing is left."""
    async with sessions.begin() as db:
        await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": project_id})
    async with sessions() as db:
        left = {
            "projects": (
                await db.execute(
                    text("SELECT count(*) FROM projects WHERE id = :p"),
                    {"p": project_id},
                )
            ).scalar_one(),
            "test_runs": (
                await db.execute(
                    text("SELECT count(*) FROM test_runs WHERE project_id = :p"),
                    {"p": project_id},
                )
            ).scalar_one(),
        }
    leaked = {table: count for table, count in left.items() if count}
    if leaked:
        raise AssertionError(f"live finalise cleanup left rows behind: {leaked}")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    from app.models.postgres import Project

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:10]
    project_id = uuid.uuid4()
    patch = pytest.MonkeyPatch()
    # The consumer opens its own session: point it at this module's engine.
    patch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)
    bumps: list = []

    async def _record_bump(project):
        bumps.append(project)

    patch.setattr("app.services.cache_service.bump_analytics_epoch", _record_bump)
    try:
        async with sessions.begin() as db:
            db.add(
                Project(
                    id=project_id,
                    name=f"livefinal-{tag}",
                    slug=f"livefinal-{tag}",
                    is_active=True,
                    description="throwaway live finalise",
                )
            )
        yield sessions, project_id, tag, bumps
    finally:
        patch.undo()
        await _cleanup(sessions, project_id)
        await engine.dispose()


async def _live_run(world, label: str) -> uuid.UUID:
    from app.services.ingestion_pipeline import create_run_from_payload

    sessions, project_id, tag, _bumps = world
    async with sessions.begin() as db:
        run = await create_run_from_payload(
            db,
            project_id=str(project_id),
            build_number=f"{label}-{tag}",
            ingestion_source="live",
            reuse_existing=False,
        )
        return run.id


async def _read(world, run_id):
    from app.models.postgres import TestRun

    sessions, _project_id, _tag, _bumps = world
    async with sessions() as db:
        return (
            await db.execute(select(TestRun).where(TestRun.id == run_id))
        ).scalar_one()


async def test_a_completed_live_run_is_graded_counted_and_its_epoch_moves(world):
    from app.models.postgres import LaunchStatus
    from app.streams.live_consumer import _finalise_run_in_db

    _sessions, project_id, _tag, bumps = world
    run_id = await _live_run(world, "mixed")
    before = await _read(world, run_id)
    assert before.status == LaunchStatus.IN_PROGRESS and before.end_time is None
    bumps.clear()

    await _finalise_run_in_db(
        str(run_id), {"passed": 3, "failed": 1, "skipped": 2, "unknown": 1}
    )

    run = await _read(world, run_id)
    assert run.status == LaunchStatus.FAILED
    assert (
        run.total_tests,
        run.passed_tests,
        run.failed_tests,
        run.skipped_tests,
        run.broken_tests,
        run.unknown_tests,
    ) == (
        7,
        3,
        1,
        2,
        0,
        1,
    )
    assert run.pass_rate == pytest.approx(75.0), (
        "skips are neither a pass nor a failure"
    )
    assert run.end_time is not None
    assert bumps == [project_id], (
        "the analytics cache was not invalidated for the run's project"
    )

    # A second run_complete (a redelivery) changes nothing.
    await _finalise_run_in_db(str(run_id), {"passed": 9, "failed": 0, "skipped": 0})
    again = await _read(world, run_id)
    assert (
        again.status,
        again.total_tests,
        again.passed_tests,
        again.failed_tests,
        again.pass_rate,
        again.end_time,
    ) == (
        run.status,
        run.total_tests,
        run.passed_tests,
        run.failed_tests,
        run.pass_rate,
        run.end_time,
    ), "a redelivered run_complete rewrote a finished run"
    assert bumps == [project_id], "a redelivered run_complete bumped the epoch again"


async def test_an_unknown_result_is_not_graded_green_and_all_skipped_has_no_rate(world):
    from app.models.postgres import LaunchStatus
    from app.streams.live_consumer import _finalise_run_in_db

    unknown_run = await _live_run(world, "unknown")
    await _finalise_run_in_db(str(unknown_run), {"passed": 2, "unknown": 1})
    unknown = await _read(world, unknown_run)
    assert unknown.status != LaunchStatus.PASSED, (
        "an uninterpretable result went out green"
    )
    assert unknown.status == LaunchStatus.STOPPED
    assert (unknown.total_tests, unknown.unknown_tests, unknown.pass_rate) == (
        3,
        1,
        pytest.approx(100.0),
    )

    skipped_run = await _live_run(world, "skipped")
    await _finalise_run_in_db(str(skipped_run), {"skipped": 3})
    skipped = await _read(world, skipped_run)
    assert skipped.pass_rate is None, (
        "a run that executed nothing has no pass rate, not 0 %"
    )
    assert (skipped.status, skipped.total_tests, skipped.skipped_tests) == (
        LaunchStatus.STOPPED,
        3,
        3,
    )


async def test_a_stream_run_id_that_is_not_a_uuid_raises_nothing_and_touches_no_run(
    world,
):
    from app.models.postgres import LaunchStatus
    from app.streams.live_consumer import _finalise_run_in_db

    bystander = await _live_run(world, "bystander")
    # A LiveSession's run id can be a slug; this path binds it as a UUID.
    await _finalise_run_in_db("live-session-slug-test-only", {"passed": 1})
    assert (await _read(world, bystander)).status == LaunchStatus.IN_PROGRESS
