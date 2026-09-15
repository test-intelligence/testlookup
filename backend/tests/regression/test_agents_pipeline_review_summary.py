"""T20/K3: pipeline cards receive review state and settlement time."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/test")

import pytest

pytest.importorskip("sqlalchemy")

from app.models.postgres import AgentPipelineRun  # noqa: E402
from app.models.schemas import AgentPipelineResponse  # noqa: E402
from app.routers import agents  # noqa: E402


class _Rows:
    def __init__(self, rows: list[SimpleNamespace]):
        self._rows = rows

    def all(self) -> list[SimpleNamespace]:
        return self._rows


def _pipeline(pipeline_id: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=pipeline_id or uuid.uuid4())


@pytest.mark.asyncio
async def test_review_summaries_are_batched_and_map_reviewed_at_to_settled_at():
    accepted_id = uuid.uuid4()
    pending_id = uuid.uuid4()
    settled_at = datetime(2026, 9, 12, 11, 15, tzinfo=timezone.utc)
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=_Rows(
            [
                SimpleNamespace(
                    pipeline_run_id=accepted_id,
                    state="accepted",
                    reviewed_at=settled_at,
                ),
                SimpleNamespace(
                    pipeline_run_id=pending_id,
                    state="pending_review",
                    reviewed_at=None,
                ),
            ]
        )
    )
    accepted = _pipeline(accepted_id)
    pending = _pipeline(pending_id)
    no_review = _pipeline()

    await agents._attach_review_summaries(db, [accepted, pending, no_review])

    db.execute.assert_awaited_once()
    assert accepted.review_summary == {
        "state": "accepted",
        "settled_at": settled_at,
    }
    assert pending.review_summary == {
        "state": "pending_review",
        "settled_at": None,
    }
    assert no_review.review_summary is None


@pytest.mark.asyncio
async def test_review_summary_query_only_selects_live_pipeline_report_subjects():
    db = AsyncMock(return_value=None)
    db.execute = AsyncMock(return_value=_Rows([]))

    await agents._attach_review_summaries(db, [_pipeline()])

    statement = db.execute.await_args.args[0]
    params = list(statement.compile().params.values())
    assert "report" in params
    assert "pipeline_run" in params
    assert "superseded" in params


@pytest.mark.asyncio
async def test_empty_pipeline_list_does_not_query_reviews():
    db = AsyncMock()
    await agents._attach_review_summaries(db, [])
    db.execute.assert_not_awaited()


def test_pipeline_response_serializes_review_summary_without_reviewer_identity():
    row = AgentPipelineRun(
        id=uuid.uuid4(),
        test_run_id=uuid.uuid4(),
        workflow_type="offline",
        status="passed",
        created_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
    )
    row.review_summary = {
        "state": "accepted",
        "settled_at": datetime(2026, 9, 12, 11, 15, tzinfo=timezone.utc),
        "reviewed_by": str(uuid.uuid4()),
    }

    payload = AgentPipelineResponse.model_validate(row).model_dump(mode="json")

    assert payload["review_summary"] == {
        "state": "accepted",
        "settled_at": "2026-09-12T11:15:00Z",
    }


def test_list_and_detail_routes_attach_the_same_review_summary():
    """The shared response model must not expose an empty field on one route."""
    import inspect

    assert "await _attach_review_summaries(db, pipelines)" in inspect.getsource(
        agents.list_pipelines
    )
    assert "await _attach_review_summaries(db, [pipeline])" in inspect.getsource(
        agents.get_pipeline
    )
