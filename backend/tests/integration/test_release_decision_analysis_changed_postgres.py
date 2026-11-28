"""D-97 on real PostgreSQL: ``analysis_changed_since`` finds a human category
change saved after a stored release decision, and nothing else.

One statement, ``GREATEST`` over two scalar subqueries (the analysis versions a
correction or bulk reclassify stages, and the correction's ``ai_feedback``
row), so it is run here against the real tables: the run scoping through
``test_cases``, the trigger and rating filters, the strict "after" comparison
and GREATEST skipping a NULL half.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.integration.ai_history_seed import (
    drop_project,
    dsn,
    seed_analysis,
    seed_pipeline,
    seed_project,
    seed_run,
    seed_user,
)

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

DECIDED = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
async def factory():
    eng = create_async_engine(dsn(), pool_size=2, max_overflow=0)
    yield async_sessionmaker(eng, expire_on_commit=False)
    await eng.dispose()


@pytest.fixture
async def world(factory):
    """Two runs of one project: the decision's run and a neighbour."""
    from types import SimpleNamespace

    async with factory() as db:
        project = await seed_project(db, "d97-pg")
        run, cases = await seed_run(db, project, 2)
        other_run, other_cases = await seed_run(db, project, 1)
        analyses = [await seed_analysis(db, c) for c in cases]
        other_analysis = await seed_analysis(db, other_cases[0])
        user = await seed_user(db, "dora")
        await db.commit()
    yield SimpleNamespace(
        project=project, run=run, cases=cases, analyses=analyses,
        other_run=other_run, other_cases=other_cases, other_analysis=other_analysis, user=user,
    )
    await drop_project(factory, project, [user])


def _feedback(world, *, case=0, at, rating="incorrect", category="PRODUCT_BUG", other=False):
    from app.models.postgres import AIFeedback

    return AIFeedback(
        analysis_id=world.other_analysis if other else world.analyses[case],
        test_case_id=world.other_cases[0] if other else world.cases[case],
        user_id=world.user, rating=rating, corrected_category=category,
        source="manual", exported=False, created_at=at,
    )


def _version(world, *, case=0, n, trigger, at, other=False):
    from app.models.postgres import AIAnalysisVersion

    return AIAnalysisVersion(
        test_case_id=world.other_cases[0] if other else world.cases[case],
        test_run_id=world.other_run if other else world.run,
        project_id=world.project, test_fingerprint="fp", version=n, trigger=trigger,
        content_sha256="0" * 64, failure_category="PRODUCT_BUG", created_at=at,
    )


async def _probe(factory, world, pipeline=None):
    from app.services.release_council_service import analysis_changed_since

    async with factory() as db:
        return await analysis_changed_since(db, world.run, DECIDED, pipeline)


async def _pipeline_with_root_cause(factory, world, started_at):
    from app.models.postgres import AgentStageResult

    async with factory() as db:
        pipeline = await seed_pipeline(db, world.run)
        db.add(AgentStageResult(
            pipeline_run_id=pipeline, task_key="root_cause_analysis",
            stage_name="root_cause_analysis", status="completed", started_at=started_at,
        ))
        # Another stage of the same pipeline, earlier still: not the read time.
        db.add(AgentStageResult(
            pipeline_run_id=pipeline, task_key="ingestion",
            stage_name="ingestion", status="completed", started_at=started_at - timedelta(hours=1),
        ))
        await db.commit()
    return pipeline


async def test_a_correction_saved_while_the_run_was_in_flight_is_found(factory, world):
    """Review F3: root cause read the analyses at 08:50, the decision was
    assembled at 09:00; a correction at 08:55 is not in the decision. With the
    pipeline it is reported; with only the decision time it is not."""
    in_flight = DECIDED - timedelta(minutes=5)
    pipeline = await _pipeline_with_root_cause(factory, world, DECIDED - timedelta(minutes=10))
    async with factory() as db:
        db.add(_version(world, n=1, trigger="human_correction", at=in_flight))
        # Before the root-cause stage started: already in the decision.
        db.add(_feedback(world, case=1, at=DECIDED - timedelta(minutes=20)))
        await db.commit()
    assert await _probe(factory, world, pipeline) == in_flight
    assert await _probe(factory, world) is None


async def test_a_pipeline_without_a_root_cause_row_falls_back_to_the_decision_time(factory, world):
    async with factory() as db:
        pipeline = await seed_pipeline(db, world.run)
        db.add(_feedback(world, at=DECIDED - timedelta(minutes=5)))
        await db.commit()
    assert await _probe(factory, world, pipeline) is None
    async with factory() as db:
        db.add(_feedback(world, case=1, at=DECIDED + timedelta(minutes=5)))
        await db.commit()
    assert await _probe(factory, world, pipeline) == DECIDED + timedelta(minutes=5)


async def test_nothing_after_the_decision_is_none(factory, world):
    async with factory() as db:
        db.add(_feedback(world, at=DECIDED - timedelta(hours=1)))
        db.add(_version(world, n=1, trigger="human_correction", at=DECIDED - timedelta(minutes=5)))
        # Exactly at the decision time is not after it.
        db.add(_version(world, case=1, n=1, trigger="human_correction", at=DECIDED))
        await db.commit()
    assert await _probe(factory, world) is None


async def test_noise_is_ignored(factory, world):
    """A pipeline re-analysis, a rating that is not a correction, a correction
    without a category, and a correction in another run: none of them."""
    later = DECIDED + timedelta(hours=2)
    async with factory() as db:
        db.add(_version(world, n=1, trigger="pipeline", at=later))
        db.add(_version(world, case=1, n=1, trigger="manual_analyze", at=later))
        db.add(_feedback(world, at=later, rating="correct", category=None))
        db.add(_feedback(world, case=1, at=later, category=None))
        db.add(_feedback(world, at=later, other=True))
        db.add(_version(world, n=1, trigger="human_correction", at=later, other=True))
        await db.commit()
    assert await _probe(factory, world) is None


async def test_a_correction_row_alone_is_found(factory, world):
    """The history write failed (it never fails a correction): the
    ``ai_feedback`` row is enough."""
    at = DECIDED + timedelta(hours=1)
    async with factory() as db:
        db.add(_feedback(world, at=at))
        await db.commit()
    assert await _probe(factory, world) == at


async def test_a_version_alone_is_found_and_the_newest_wins(factory, world):
    """An edited correction or a bulk reclassify stages a version and no new
    ``ai_feedback`` row; with both kinds present the newest time is returned."""
    async with factory() as db:
        db.add(_version(world, n=1, trigger="bulk_reclassify", at=DECIDED + timedelta(hours=1)))
        db.add(_version(world, case=1, n=1, trigger="human_correction", at=DECIDED + timedelta(hours=4)))
        db.add(_feedback(world, at=DECIDED + timedelta(hours=2)))
        await db.commit()
    assert await _probe(factory, world) == DECIDED + timedelta(hours=4)
