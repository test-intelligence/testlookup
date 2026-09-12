"""Real PostgreSQL proof for E8.1 (migration 0175).

A mocked session cannot see a CHECK constraint or a partial unique index, and
the review gate's guarantees rest on both: a rejection always has a reason, and
a subject never has two live requests. CI runs ``alembic upgrade head`` first.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def ctx():
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    async with engine.begin() as db:
        row = (await db.execute(text("SELECT id, project_id FROM test_runs ORDER BY created_at LIMIT 1"))).first()
    if row is None:
        await engine.dispose()
        pytest.skip("database has no test run fixture")
    pipelines: list[uuid.UUID] = []

    async def new_pipeline(workflow="offline"):
        pid = uuid.uuid4()
        async with engine.begin() as db:
            await db.execute(
                text("INSERT INTO agent_pipeline_runs (id, test_run_id, workflow_type, status, started_at, completed_at) "
                     "VALUES (:id, :run, :wf, 'completed', now(), now())"),
                {"id": pid, "run": row[0], "wf": workflow},
            )
        pipelines.append(pid)
        return pid

    yield engine, row[0], row[1], new_pipeline
    async with engine.begin() as db:
        for pid in pipelines:
            await db.execute(text("DELETE FROM review_requests WHERE subject_id = :s"), {"s": str(pid)})
            await db.execute(text("DELETE FROM agent_pipeline_runs WHERE id = :id"), {"id": pid})
    await engine.dispose()


async def _insert(engine, project_id, subject_id, **cols):
    values = {
        "id": uuid.uuid4(), "project_id": project_id, "subject_id": str(subject_id),
        "state": "pending_review", "reason_code": None, "reviewed_at": None,
    }
    values.update(cols)
    async with engine.begin() as db:
        await db.execute(
            text("INSERT INTO review_requests (id, project_id, kind, subject_type, subject_id, state, "
                 "reason_code, reviewed_at, ai_disclaimer_version) VALUES (:id, :project_id, 'report', "
                 "'pipeline_run', :subject_id, :state, :reason_code, :reviewed_at, 'test')"),
            values,
        )
    return values["id"]


async def test_a_rejection_without_a_reason_is_refused(ctx):
    engine, _run, project_id, new_pipeline = ctx
    subject = await new_pipeline()
    with pytest.raises(IntegrityError, match="ck_review_requests_rejection_has_reason"):
        await _insert(engine, project_id, subject, state="rejected", reviewed_at=None)


async def test_an_unknown_state_or_reason_is_refused(ctx):
    engine, _run, project_id, new_pipeline = ctx
    subject = await new_pipeline()
    with pytest.raises(IntegrityError, match="ck_review_requests_state"):
        await _insert(engine, project_id, subject, state="approved")
    with pytest.raises(IntegrityError, match="ck_review_requests_reason_code"):
        await _insert(engine, project_id, subject, reason_code="looked_wrong")


async def test_a_subject_can_never_have_two_live_requests(ctx):
    engine, _run, project_id, new_pipeline = ctx
    subject = await new_pipeline()
    await _insert(engine, project_id, subject)
    with pytest.raises(IntegrityError, match="uq_review_requests_live_subject"):
        await _insert(engine, project_id, subject)


async def test_superseded_history_does_not_count_as_live(ctx):
    engine, _run, project_id, new_pipeline = ctx
    subject = await new_pipeline()
    await _insert(engine, project_id, subject, state="superseded")
    await _insert(engine, project_id, subject, state="superseded")
    await _insert(engine, project_id, subject)  # one live beside two historical rows


async def test_the_service_supersedes_through_the_real_index(ctx):
    """Replacing a settled review inserts a live row for a subject that already
    has one; that only works if the old row is superseded first."""
    from types import SimpleNamespace

    from app.services.review_request_service import create_run_review_request

    engine, test_run_id, project_id, new_pipeline = ctx
    pid = await new_pipeline()
    run = SimpleNamespace(id=pid, test_run_id=test_run_id, workflow_type="offline")
    session = async_sessionmaker(engine, expire_on_commit=False)

    async with session() as db:
        first = await create_run_review_request(
            db, run=run, project_id=project_id, report_stage_names=["summary"],
            evidence_bundle_sha256="a" * 64,
        )
        first.state = "accepted"
        first.reviewed_at = text("now()")
        await db.commit()

    async with session() as db:
        replacement = await create_run_review_request(
            db, run=run, project_id=project_id, report_stage_names=["summary"],
            evidence_bundle_sha256="b" * 64,
        )
        await db.commit()

    async with engine.begin() as db:
        rows = (await db.execute(
            text("SELECT id, state, superseded_by FROM review_requests WHERE subject_id = :s ORDER BY created_at"),
            {"s": str(pid)},
        )).all()
    states = {r[0]: (r[1], r[2]) for r in rows}
    assert states[first.id] == ("superseded", replacement.id)
    assert states[replacement.id] == ("pending_review", None)


async def test_the_new_columns_default_to_off(ctx):
    engine, _run, project_id, _new_pipeline = ctx
    async with engine.begin() as db:
        default_off = (await db.execute(
            text("SELECT allow_unreviewed_distribution FROM projects WHERE id = :p"), {"p": project_id}
        )).scalar_one()
        synthetic_real_users = (await db.execute(
            text("SELECT count(*) FROM users WHERE is_synthetic AND lower(email) NOT LIKE '%@qa-lead.testlookup.local'")
        )).scalar_one()
    assert default_off is False
    assert synthetic_real_users == 0, "the backfill marked a real account synthetic"
