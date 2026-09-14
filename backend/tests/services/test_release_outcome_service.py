from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.postgres import ReleaseOutcome
from app.services import release_outcome_service as svc


@pytest.mark.asyncio
async def test_record_outcome_is_append_only_and_strips_the_reason() -> None:
    db = AsyncMock()
    db.add = MagicMock()
    release = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    actor_id = uuid.uuid4()

    row = await svc.record_outcome(
        db,
        release=release,
        outcome_kind="incident",
        reason="  checkout errors after deploy  ",
        marked_by_user_id=actor_id,
    )

    assert isinstance(row, ReleaseOutcome)
    assert row.release_id == release.id
    assert row.project_id == release.project_id
    assert row.outcome_kind == "incident"
    assert row.reason == "checkout errors after deploy"
    assert row.marked_by_user_id == actor_id
    db.add.assert_called_once_with(row)
    db.flush.assert_awaited_once()
    assert not hasattr(db, "commit") or db.commit.await_count == 0


@pytest.mark.asyncio
async def test_g5_window_query_is_project_scoped_and_half_open() -> None:
    db = AsyncMock()
    scalar_result = MagicMock()
    scalar_result.scalars.return_value.all.return_value = []
    db.execute.return_value = scalar_result
    project_id = uuid.uuid4()
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 8, tzinfo=timezone.utc)

    assert await svc.list_project_outcomes_between(
        db,
        project_id=project_id,
        start=start,
        end=end,
    ) == []

    statement = str(db.execute.await_args.args[0])
    assert "release_outcomes.project_id" in statement
    assert "release_outcomes.marked_at >=" in statement
    assert "release_outcomes.marked_at <" in statement
