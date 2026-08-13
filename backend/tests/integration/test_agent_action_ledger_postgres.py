"""Opt-in PostgreSQL race/rollback coverage for action governance.

The fast suite uses fake sessions. These checks require
``TESTLOOKUP_POSTGRES_TEST_DSN`` and should target only a disposable/approved
database. They create bounded, uniquely keyed rows and clean them afterward.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def test_action_idempotency_and_outbox_are_atomic_under_race():
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    project_key = f"pg-action-{uuid.uuid4().hex}"
    action_key = f"pg-action-idem-{uuid.uuid4().hex}"
    outbox_key = f"pg-action-outbox-{uuid.uuid4().hex}"
    action_ids: list[uuid.UUID] = []
    try:
        async with engine.begin() as db:
            project_id = (
                await db.execute(text("SELECT id FROM projects ORDER BY created_at LIMIT 1"))
            ).scalar_one()

        async def insert_action() -> str:
            action_id = uuid.uuid4()
            async with engine.begin() as db:
                try:
                    await db.execute(
                        text(
                            "INSERT INTO agent_action_ledger "
                            "(id, project_id, action_type, target_type, target_id, status, "
                            "approval_required, idempotency_key, request_sha256, request_payload) "
                            "VALUES (:id, :project, 'release_gate', 'test_run', :target, "
                            "'pending_review', true, :key, :hash, '{}'::jsonb)"
                        ),
                        {
                            "id": action_id,
                            "project": project_id,
                            "target": project_key,
                            "key": action_key,
                            "hash": "a" * 64,
                        },
                    )
                    action_ids.append(action_id)
                    return "created"
                except Exception as exc:  # asyncpg exposes a typed unique error
                    if "duplicate key" not in str(exc).lower():
                        raise
                    return "conflict"

        assert sorted(await asyncio.gather(insert_action(), insert_action())) == [
            "conflict",
            "created",
        ]
        action_id = action_ids[0]

        async with engine.begin() as db:
            await db.execute(
                text(
                    "INSERT INTO agent_action_dispatch_outbox "
                    "(id, action_id, project_id, idempotency_key, status, attempts) "
                    "VALUES (:id, :action, :project, :key, 'pending', 0)"
                ),
                {"id": uuid.uuid4(), "action": action_id, "project": project_id, "key": outbox_key},
            )

        # A duplicate delivery intent must fail without leaving the transaction
        # unusable; the following unrelated insert proves rollback recovery.
        async with engine.connect() as db:
            tx = await db.begin()
            with pytest.raises(Exception, match="duplicate key"):
                await db.execute(
                    text(
                        "INSERT INTO agent_action_dispatch_outbox "
                        "(id, action_id, project_id, idempotency_key, status, attempts) "
                        "VALUES (:id, :action, :project, :key, 'pending', 0)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "action": action_id,
                        "project": project_id,
                        "key": outbox_key,
                    },
                )
            await tx.rollback()
            await db.execute(
                text(
                    "UPDATE agent_action_dispatch_outbox SET last_error='rollback-ok' "
                    "WHERE action_id=:action"
                ),
                {"action": action_id},
            )
            await db.commit()
    finally:
        async with engine.begin() as db:
            await db.execute(
                text("DELETE FROM agent_action_dispatch_outbox WHERE idempotency_key=:key"),
                {"key": outbox_key},
            )
            await db.execute(
                text("DELETE FROM agent_action_ledger WHERE idempotency_key=:key"),
                {"key": action_key},
            )
        await engine.dispose()
