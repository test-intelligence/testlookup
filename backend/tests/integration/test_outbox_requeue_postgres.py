"""Failed run-outbox intents can be put back (re-audit N23, against real PostgreSQL).

The outbox marks an intent ``failed`` after eight publish failures and never
retries it. Until N23 every finished run's ``agent_pipeline`` intent ended that
way, with ``broker_TypeError``, so fixing the payload alone would not have
analysed one of those runs. These drive the requeue through its real query --
filters, order, lock -- and the relay's claim that has to pick a requeued row up.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` with the database migrated to head.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.postgres import Project, RunDownstreamOutbox, TestRun as DbTestRun
from app.services.run_downstream_outbox import (
    claim_downstream_dispatches,
    requeue_failed_downstream_operations,
)

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def seeded():
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    base = datetime.now(timezone.utc) - timedelta(days=3)

    def _row(label: str, operation: str, status: str, last_error, minutes: int):
        return RunDownstreamOutbox(
            id=uuid.uuid4(),
            run_id=run_id,
            project_id=project_id,
            operation=operation,
            input_version=f"v-{label}",
            # The pre-N23 shape: the publisher translates run_id on its way out.
            payload={
                "run_id": str(run_id),
                "project_id": str(project_id),
                "build_number": "b-1",
                "workflow_type": "offline",
            },
            queue="ai_analysis",
            priority=6,
            status=status,
            attempts=8,
            dispatch_failures=8 if last_error == "broker_TypeError" else 0,
            execution_attempts=3 if last_error == "execution_attempts_exhausted" else 0,
            dispatch_token=uuid.uuid4(),
            last_error=last_error,
            created_at=base + timedelta(minutes=minutes),
        )

    rows = {
        "stuck_old": _row("stuck-old", "agent_pipeline", "failed", "broker_TypeError", 1),
        "stuck_new": _row("stuck-new", "agent_pipeline", "failed", "broker_TypeError", 2),
        "exhausted": _row("exhausted", "agent_pipeline", "failed", "execution_attempts_exhausted", 3),
        "done": _row("done", "agent_pipeline", "completed", None, 4),
        "other_op": _row("other-op", "run_notifications", "failed", "broker_TypeError", 5),
    }
    async with sessions() as db:
        db.add_all([
            Project(id=project_id, name=f"requeue-{project_id.hex}", slug=f"requeue-{project_id.hex}"),
            DbTestRun(
                id=run_id,
                project_id=project_id,
                build_number=f"requeue-{run_id.hex}",
                status="PASSED",
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                skipped_tests=0,
                broken_tests=0,
                unknown_tests=0,
            ),
        ])
        await db.flush()
        db.add_all(list(rows.values()))
        await db.commit()
    try:
        yield sessions, {key: row.id for key, row in rows.items()}, run_id
    finally:
        async with sessions() as db:
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.commit()
        await engine.dispose()


async def _states(sessions, ids: dict) -> dict:
    async with sessions() as db:
        found = (await db.execute(
            select(RunDownstreamOutbox).where(RunDownstreamOutbox.id.in_(list(ids.values())))
        )).scalars().all()
    by_id = {row.id: row for row in found}
    return {key: by_id[value] for key, value in ids.items()}


async def test_a_dry_run_lists_the_matches_and_changes_nothing(seeded):
    sessions, ids, run_id = seeded
    async with sessions() as db:
        listed = await requeue_failed_downstream_operations(
            db, operation="agent_pipeline", last_error="broker_TypeError", run_id=run_id,
        )
        await db.commit()

    assert [row["id"] for row in listed] == [str(ids["stuck_old"]), str(ids["stuck_new"])], (
        "a dry run should list exactly the matching failed rows, oldest first"
    )
    assert all(row["last_error"] == "broker_TypeError" for row in listed)
    states = await _states(sessions, ids)
    assert {key: row.status for key, row in states.items()} == {
        "stuck_old": "failed",
        "stuck_new": "failed",
        "exhausted": "failed",
        "done": "completed",
        "other_op": "failed",
    }, "a dry run changed a row"


async def test_a_requeue_puts_back_only_the_rows_it_matched(seeded):
    sessions, ids, run_id = seeded
    async with sessions() as db:
        requeued = await requeue_failed_downstream_operations(
            db, operation="agent_pipeline", last_error="broker_TypeError", run_id=run_id,
            dry_run=False,
        )
        await db.commit()

    assert {row["id"] for row in requeued} == {str(ids["stuck_old"]), str(ids["stuck_new"])}
    assert all(row["dispatch_failures"] == 8 for row in requeued), "the rows were reported after the reset"
    states = await _states(sessions, ids)
    for key in ("stuck_old", "stuck_new"):
        row = states[key]
        assert (row.status, row.attempts, row.dispatch_failures, row.execution_attempts) == (
            "pending", 0, 0, 0,
        ), f"{key} was not fully reset: {row.status} {row.dispatch_failures}"
        assert row.last_error is None
        assert row.next_attempt_at is None and row.lease_expires_at is None
        assert row.dispatch_token is None, "a requeued row kept its failed publication's token"
    assert states["exhausted"].status == "failed", "a row that failed another way was requeued"
    assert states["done"].status == "completed"
    assert states["other_op"].status == "failed", "another operation's row was requeued"


async def test_the_relay_claims_what_was_requeued_oldest_first(seeded):
    """A failed row is invisible to the relay; a requeued one is due at once."""
    sessions, ids, run_id = seeded
    async with sessions() as db:
        await requeue_failed_downstream_operations(
            db, operation="agent_pipeline", last_error="broker_TypeError", run_id=run_id,
            limit=1, dry_run=False,
        )
        await db.commit()

    async with sessions() as db:
        claimed = await claim_downstream_dispatches(db, limit=500)
        mine = [(row.id, row.status) for row in claimed if row.run_id == run_id]
        await db.rollback()

    assert mine == [(ids["stuck_old"], "sending")], (
        f"expected the oldest requeued row, and only it, to be claimed: {mine}"
    )


async def test_an_unknown_operation_is_refused(seeded):
    sessions, _, _ = seeded
    async with sessions() as db:
        with pytest.raises(ValueError, match="unknown downstream operation"):
            await requeue_failed_downstream_operations(db, operation="drop_tables")
