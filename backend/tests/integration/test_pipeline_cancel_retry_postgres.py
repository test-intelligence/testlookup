"""Real PostgreSQL proof for E7.4: cancel, the retry race, and ``rerun_of``.

A mocked session cannot see a constraint or a lock (memory: seven green tests
once covered a reset the database refused), and the cancel-vs-retry resolution
is *entirely* a locking argument -- both writers take the row ``FOR UPDATE``
and the claim is that either ordering ends ``failed``. That claim is only worth
anything against a real database, so it is tested here as well as in the unit
suite.

CI runs ``alembic upgrade head`` before this file, so 0174's ``rerun_of``
column and FK are live. Rows are seeded the way
``test_pipeline_state_machine_postgres`` does it.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Response
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


async def _seed(engine, status: str, **cols) -> uuid.UUID:
    pipeline_id = uuid.uuid4()
    async with engine.begin() as db:
        run = (
            await db.execute(text("SELECT id FROM test_runs ORDER BY created_at LIMIT 1"))
        ).first()
        if run is None:
            pytest.skip("database has no test run fixture")
        await db.execute(
            text(
                "INSERT INTO agent_pipeline_runs "
                "(id, test_run_id, workflow_type, status, started_at, attempt, "
                " max_attempts, cancel_requested) "
                "VALUES (:id, :run, 'offline', :status, now(), :attempt, 5, :cancelled)"
            ),
            {
                "id": pipeline_id,
                "run": run[0],
                "status": status,
                "attempt": cols.get("attempt", 1),
                "cancelled": cols.get("cancel_requested", False),
            },
        )
    return pipeline_id


async def _cleanup(engine, *pipeline_ids: uuid.UUID) -> None:
    async with engine.begin() as db:
        for pid in pipeline_ids:
            await db.execute(
                text("DELETE FROM agent_pipeline_runs WHERE id = :id OR rerun_of = :id"),
                {"id": pid},
            )


async def _status(engine, pipeline_id: uuid.UUID) -> tuple:
    async with engine.begin() as db:
        return (
            await db.execute(
                text(
                    "SELECT status, error, next_retry_at, cancel_requested "
                    "FROM agent_pipeline_runs WHERE id = :id"
                ),
                {"id": pipeline_id},
            )
        ).first()


async def _seed_invocation_subject(engine) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    user_id, project_id, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    suffix = uuid.uuid4().hex
    async with engine.begin() as db:
        await db.execute(
            text(
                "INSERT INTO users (id,email,username,hashed_password,role,is_active) "
                "VALUES (:id,:email,:username,'integration','QA_ENGINEER',true)"
            ),
            {"id": user_id, "email": f"m12-{suffix}@example.com", "username": f"m12-{suffix}"},
        )
        await db.execute(
            text("INSERT INTO projects (id,name,slug,is_active) VALUES (:id,'M12 race',:slug,true)"),
            {"id": project_id, "slug": f"m12-race-{suffix}"},
        )
        await db.execute(
            text(
                "INSERT INTO test_runs "
                "(id,project_id,build_number,status,ingestion_source,total_tests,passed_tests,"
                " failed_tests,skipped_tests,broken_tests,unknown_tests) "
                "VALUES (:id,:project,'m12','IN_PROGRESS','unknown',0,0,0,0,0,0)"
            ),
            {"id": run_id, "project": project_id},
        )
    return user_id, project_id, run_id


async def _cleanup_invocation_subject(engine, user_id: uuid.UUID, project_id: uuid.UUID) -> None:
    async with engine.begin() as db:
        await db.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project_id})
        await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


# ── cancel ────────────────────────────────────────────────────────────────────


async def test_cancelling_a_waiting_run_lands_failed_in_the_database():
    from app.services.pipeline_cancellation import request_cancel

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    pipeline_id = await _seed(engine, "retry_wait")
    session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session() as db:
            outcome = await request_cancel(db, pipeline_id, requested_by="qa@example.com")
            await db.commit()
        assert outcome.terminal is True

        status, error, next_retry_at, cancelled = await _status(engine, pipeline_id)
        assert status == "failed", "the CHECK constraint allows only the six states"
        assert error.startswith("cancelled: ")
        assert next_retry_at is None
        assert cancelled is True
    finally:
        await _cleanup(engine, pipeline_id)
        await engine.dispose()


async def test_cancelling_a_running_run_sets_the_flag_without_terminalising():
    from app.services.pipeline_cancellation import request_cancel

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    pipeline_id = await _seed(engine, "running")
    session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session() as db:
            outcome = await request_cancel(db, pipeline_id)
            await db.commit()
        assert outcome.terminal is False

        status, _error, _next, cancelled = await _status(engine, pipeline_id)
        assert status == "running"
        assert cancelled is True, "the worker reads this flag at its next stage"
    finally:
        await _cleanup(engine, pipeline_id)
        await engine.dispose()


# ── the race, against real row locks ──────────────────────────────────────────


async def test_cancel_and_the_retry_scheduler_serialise_on_the_row_lock():
    """Two concurrent sessions, both taking the row FOR UPDATE.

    Whichever commits first, the run must end ``failed`` and must NOT be left
    in ``retry_wait`` with a wake-up time.
    """
    from app.services.pipeline_cancellation import request_cancel
    from app.services.workflow_run_state import PipelineRunStatus, apply_transition

    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    pipeline_id = await _seed(engine, "failed", attempt=1)
    session = async_sessionmaker(engine, expire_on_commit=False)
    started = asyncio.Event()
    try:
        async def _cancel():
            async with session() as db:
                await started.wait()
                await request_cancel(db, pipeline_id, requested_by="qa@example.com")
                await db.commit()

        async def _park():
            """Stands in for _schedule_pipeline_retry's critical section."""
            from sqlalchemy import select

            from app.models.postgres import AgentPipelineRun

            async with session() as db:
                row = (
                    await db.execute(
                        select(AgentPipelineRun)
                        .where(AgentPipelineRun.id == pipeline_id)
                        .with_for_update()
                    )
                ).scalar_one()
                started.set()
                await asyncio.sleep(0.05)  # hold the lock while cancel queues
                if not row.cancel_requested:
                    apply_transition(row, PipelineRunStatus.RETRY_WAIT, error="model_unavailable")
                await db.commit()

        await asyncio.gather(_park(), _cancel())

        status, error, next_retry_at, cancelled = await _status(engine, pipeline_id)
        assert cancelled is True
        assert status == "failed", (
            f"cancel racing a retryable failure must land failed, got {status!r} -- "
            "a retry_wait row would come back to life after the operator was "
            "told it had stopped"
        )
        assert next_retry_at is None
        assert error.startswith("cancelled: ")
    finally:
        await _cleanup(engine, pipeline_id)
        await engine.dispose()


async def test_concurrent_invokes_share_one_durable_invocation(monkeypatch):
    """The TestRun lock closes the active-lookup/insert race with real Postgres."""
    from app.routers import agent_invoke as router
    from app.services import agent_config_resolver
    from app.worker import tasks

    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    user_id, project_id, run_id = await _seed_invocation_subject(engine)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    dispatch = MagicMock()
    monkeypatch.setattr(router, "resolve_project_scope", AsyncMock())
    monkeypatch.setattr(
        router,
        "resolve_for_project",
        AsyncMock(
            side_effect=lambda _db, _project, agent, **_kw: agent_config_resolver.resolve(
                agent, global_ai_config={}
            )
        ),
    )
    monkeypatch.setattr(router, "record_activity", AsyncMock())
    monkeypatch.setattr(router, "ActorRef", SimpleNamespace(from_user=lambda _user: "actor"))
    monkeypatch.setattr(tasks.run_agent_invocation, "apply_async", dispatch)
    body = router.AgentInvokeRequest(
        project_id=project_id,
        input={
            "agent_id": "agent.summary.v1",
            "payload": {"test_run_id": str(run_id)},
        },
    )
    user = SimpleNamespace(id=user_id)

    async def _invoke():
        async with sessions() as db:
            response = Response(status_code=202)
            result = await router.invoke_agent(
                agent_id="agent.summary.v1",
                body=body,
                response=response,
                db=db,
                current_user=user,
                idempotency_key=None,
            )
            return result, response.status_code

    try:
        first, second = await asyncio.gather(_invoke(), _invoke())
        assert first[0]["id"] == second[0]["id"]
        assert sorted((first[1], second[1])) == [200, 202]
        dispatch.assert_called_once()
        async with engine.begin() as db:
            count = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM agent_invocations "
                        "WHERE test_run_id = :run AND agent_id = 'agent.summary.v1'"
                    ),
                    {"run": run_id},
                )
            ).scalar_one()
        assert count == 1
    finally:
        await _cleanup_invocation_subject(engine, user_id, project_id)
        await engine.dispose()


async def test_idempotency_key_is_independent_across_project_and_agent_scopes():
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    user_id, project_a, run_a = await _seed_invocation_subject(engine)
    _user_b, project_b, run_b = await _seed_invocation_subject(engine)
    key = f"m12-{uuid.uuid4()}"

    async def _insert(db, project_id, run_id, agent_id):
        await db.execute(
            text(
                "INSERT INTO agent_invocations "
                "(id,project_id,agent_id,stage_name,test_run_id,pipeline_run_id,workflow_type,"
                " mode,requested_by,idempotency_key,request_sha256) "
                "VALUES (:id,:project,:agent,'summary',:run,:pipeline,'offline','async',"
                " :user,:key,:fingerprint)"
            ),
            {
                "id": uuid.uuid4(),
                "project": project_id,
                "agent": agent_id,
                "run": run_id,
                "pipeline": uuid.uuid4(),
                "user": user_id,
                "key": key,
                "fingerprint": uuid.uuid4().hex,
            },
        )

    try:
        async with engine.begin() as db:
            await _insert(db, project_a, run_a, "agent.summary.v1")
            await _insert(db, project_b, run_b, "agent.summary.v1")
            await _insert(db, project_a, run_a, "agent.flaky_sentinel.v1")
        async with engine.begin() as db:
            count = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM agent_invocations "
                        "WHERE requested_by = :user AND idempotency_key = :key"
                    ),
                    {"user": user_id, "key": key},
                )
            ).scalar_one()
        assert count == 3
        with pytest.raises(IntegrityError):
            async with engine.begin() as db:
                await _insert(db, project_a, run_a, "agent.summary.v1")
    finally:
        await _cleanup_invocation_subject(engine, user_id, project_a)
        await _cleanup_invocation_subject(engine, _user_b, project_b)
        await engine.dispose()


# ── rerun_of (migration 0174) ─────────────────────────────────────────────────


async def test_rerun_of_links_a_successor_and_survives_its_predecessor():
    """``ON DELETE SET NULL``: the successor is a run in its own right."""
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    original = await _seed(engine, "failed", attempt=5)
    successor = await _seed(engine, "running")
    try:
        async with engine.begin() as db:
            await db.execute(
                text("UPDATE agent_pipeline_runs SET rerun_of = :orig WHERE id = :id"),
                {"orig": original, "id": successor},
            )
        async with engine.begin() as db:
            linked = (
                await db.execute(
                    text("SELECT rerun_of FROM agent_pipeline_runs WHERE id = :id"),
                    {"id": successor},
                )
            ).scalar_one()
        assert linked == original

        async with engine.begin() as db:
            await db.execute(
                text("DELETE FROM agent_pipeline_runs WHERE id = :id"), {"id": original}
            )
        async with engine.begin() as db:
            row = (
                await db.execute(
                    text("SELECT status, rerun_of FROM agent_pipeline_runs WHERE id = :id"),
                    {"id": successor},
                )
            ).first()
        assert row is not None, "deleting the predecessor must not cascade"
        assert row[1] is None
    finally:
        await _cleanup(engine, original, successor)
        await engine.dispose()


async def test_rerun_of_must_reference_a_real_run():
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    pipeline_id = await _seed(engine, "running")
    try:
        async with engine.begin() as db:
            with pytest.raises(IntegrityError):
                await db.execute(
                    text("UPDATE agent_pipeline_runs SET rerun_of = :ghost WHERE id = :id"),
                    {"ghost": uuid.uuid4(), "id": pipeline_id},
                )
    finally:
        await _cleanup(engine, pipeline_id)
        await engine.dispose()
