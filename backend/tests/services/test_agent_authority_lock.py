from __future__ import annotations

import uuid

import pytest

from app.services.agent_authority_lock import (
    GLOBAL_AGENT_AUTHORITY_LOCK_KEY,
    lock_agent_authority_snapshot,
    project_agent_authority_lock_key,
)


class _DB:
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def execute(self, _statement, params):
        self.keys.append(params["key"])


@pytest.mark.asyncio
async def test_authority_snapshot_locks_global_then_project() -> None:
    project_id = uuid.uuid4()
    db = _DB()

    await lock_agent_authority_snapshot(db, project_id)

    assert db.keys == [
        GLOBAL_AGENT_AUTHORITY_LOCK_KEY,
        project_agent_authority_lock_key(project_id),
    ]
