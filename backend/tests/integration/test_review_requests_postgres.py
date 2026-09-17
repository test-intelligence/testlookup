"""Real PostgreSQL proof for E8.1 (migration 0175).

A mocked session cannot see a CHECK constraint or a partial unique index, and
the review gate's guarantees rest on both: a rejection always has a reason, and
a subject never has two live requests. CI runs ``alembic upgrade head`` first.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

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


async def _insert_bound(
    engine, project_id, test_run_id, pipeline_id, *, evidence="a" * 64
):
    review_id = uuid.uuid4()
    async with engine.begin() as db:
        await db.execute(
            text(
                "INSERT INTO review_requests "
                "(id, project_id, kind, subject_type, subject_id, pipeline_run_id, "
                "test_run_id, workflow_type, state, evidence_bundle_sha256, "
                "ai_disclaimer_version) VALUES "
                "(:id, :project, 'report', 'pipeline_run', :subject, :pipeline, "
                ":test_run, 'offline', 'pending_review', :evidence, 'test')"
            ),
            {
                "id": review_id,
                "project": project_id,
                "subject": str(pipeline_id),
                "pipeline": pipeline_id,
                "test_run": test_run_id,
                "evidence": evidence,
            },
        )
    return review_id


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


async def test_accept_reject_race_has_one_settlement_and_matching_pipeline(ctx):
    from app.core.deps import CREDENTIAL_KIND_JWT, _bind_credential_kind
    from app.routers.reviews import _load_for_update
    from app.services.review_request_service import ReviewDecisionRefused, settle_review

    engine, test_run_id, project_id, new_pipeline = ctx
    pipeline_id = await new_pipeline()
    review_id = await _insert_bound(engine, project_id, test_run_id, pipeline_id)
    async with engine.begin() as db:
        reviewer_id = (await db.execute(text("SELECT id FROM users ORDER BY created_at LIMIT 1"))).scalar_one()
    reviewer = _bind_credential_kind(
        SimpleNamespace(id=reviewer_id, is_synthetic=False), CREDENTIAL_KIND_JWT
    )
    session = async_sessionmaker(engine, expire_on_commit=False)

    async def decide(decision: str):
        async with session() as db:
            review = await _load_for_update(db, review_id)
            try:
                await settle_review(
                    db,
                    review=review,
                    reviewer=reviewer,
                    decision=decision,
                    reason_code="missing_evidence" if decision == "rejected" else None,
                )
            except ReviewDecisionRefused as exc:
                await db.rollback()
                return exc.code
            await db.commit()
            return decision

    outcomes = await asyncio.gather(decide("accepted"), decide("rejected"))
    assert sorted(outcomes) in (
        ["accepted", "review_not_pending"],
        ["rejected", "review_not_pending"],
    )
    async with engine.begin() as db:
        review_state, reason = (
            await db.execute(
                text("SELECT state, reason_code FROM review_requests WHERE id=:id"),
                {"id": review_id},
            )
        ).one()
        pipeline_state, error = (
            await db.execute(
                text("SELECT status, error FROM agent_pipeline_runs WHERE id=:id"),
                {"id": pipeline_id},
            )
        ).one()
    if review_state == "accepted":
        assert (pipeline_state, error, reason) == ("passed", None, None)
    else:
        assert review_state == "rejected"
        assert pipeline_state == "failed"
        assert error == "review_rejected: missing_evidence"
        assert reason == "missing_evidence"


async def test_accept_wins_over_concurrent_newer_run_supersession(ctx):
    from app.core.deps import CREDENTIAL_KIND_JWT, _bind_credential_kind
    from app.routers.reviews import _load_for_update
    from app.services.review_request_service import create_run_review_request, settle_review

    engine, test_run_id, project_id, new_pipeline = ctx
    older_pipeline = await new_pipeline()
    newer_pipeline = await new_pipeline()
    old_review_id = await _insert_bound(
        engine, project_id, test_run_id, older_pipeline, evidence="a" * 64
    )
    async with engine.begin() as db:
        reviewer_id = (await db.execute(text("SELECT id FROM users ORDER BY created_at LIMIT 1"))).scalar_one()
    reviewer = _bind_credential_kind(
        SimpleNamespace(id=reviewer_id, is_synthetic=False), CREDENTIAL_KIND_JWT
    )
    session = async_sessionmaker(engine, expire_on_commit=False)

    async with session() as accepting:
        old_review = await _load_for_update(accepting, old_review_id)
        await settle_review(
            accepting, review=old_review, reviewer=reviewer, decision="accepted"
        )

        async def create_newer():
            async with session() as creating:
                request = await create_run_review_request(
                    creating,
                    run=SimpleNamespace(
                        id=newer_pipeline,
                        test_run_id=test_run_id,
                        workflow_type="offline",
                        execution_metadata={},
                    ),
                    project_id=project_id,
                    report_stage_names=["summary"],
                    evidence_bundle_sha256="b" * 64,
                )
                await creating.commit()
                return request.id

        creator = asyncio.create_task(create_newer())
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(creator), timeout=0.2)
        await accepting.commit()
        new_review_id = await asyncio.wait_for(creator, timeout=5)

    async with engine.begin() as db:
        states = dict(
            (await db.execute(
                text("SELECT id, state FROM review_requests WHERE id IN (:old, :new)"),
                {"old": old_review_id, "new": new_review_id},
            )).all()
        )
        old_pipeline_state = (
            await db.execute(
                text("SELECT status FROM agent_pipeline_runs WHERE id=:id"),
                {"id": older_pipeline},
            )
        ).scalar_one()
    assert states == {old_review_id: "accepted", new_review_id: "pending_review"}
    assert old_pipeline_state == "passed"


async def test_distinct_investigator_subjects_stay_pending_in_postgres(ctx):
    from app.services.review_request_service import create_run_review_request

    engine, test_run_id, project_id, new_pipeline = ctx
    first_pipeline = await new_pipeline("investigation")
    second_pipeline = await new_pipeline("investigation")
    session = async_sessionmaker(engine, expire_on_commit=False)

    async with session() as db:
        first = await create_run_review_request(
            db,
            run=SimpleNamespace(
                id=first_pipeline,
                test_run_id=test_run_id,
                workflow_type="investigation",
                execution_metadata={},
            ),
            project_id=project_id,
            report_stage_names=["investigator_synthesis"],
            evidence_bundle_sha256="a" * 64,
        )
        second = await create_run_review_request(
            db,
            run=SimpleNamespace(
                id=second_pipeline,
                test_run_id=test_run_id,
                workflow_type="investigation",
                execution_metadata={},
            ),
            project_id=project_id,
            report_stage_names=["investigator_synthesis"],
            evidence_bundle_sha256="b" * 64,
        )
        await db.commit()

    async with engine.begin() as db:
        states = dict(
            (await db.execute(
                text("SELECT id, state FROM review_requests WHERE id IN (:first, :second)"),
                {"first": first.id, "second": second.id},
            )).all()
        )
    assert states == {first.id: "pending_review", second.id: "pending_review"}


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
