from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.models.postgres import Release, ReleaseOutcome
from app.routers import releases
from app.services import release_outcome_service, release_service
from app.services.activity.events import ACTIVITY_EVENTS, render_summary


def test_outcome_request_is_strict_and_requires_a_real_reason() -> None:
    with pytest.raises(ValidationError):
        releases.ReleaseOutcomeIn(outcome="incident", reason="   ")
    with pytest.raises(ValidationError):
        releases.ReleaseOutcomeIn(outcome="success", reason="looks fine")
    with pytest.raises(ValidationError):
        releases.ReleaseOutcomeIn(outcome="rollback", reason="valid", extra=True)


@pytest.mark.asyncio
async def test_mark_outcome_persists_actor_and_activity_in_one_transaction(monkeypatch) -> None:
    project_id = uuid.uuid4()
    release_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    release = SimpleNamespace(id=release_id, project_id=project_id, name="2.14.0")
    actor = SimpleNamespace(
        id=actor_id,
        full_name="Release Owner",
        username="release-owner",
        email="owner@example.com",
        is_service_account=False,
    )
    outcome = ReleaseOutcome(
        id=uuid.uuid4(),
        release_id=release_id,
        project_id=project_id,
        outcome_kind="rollback",
        reason="Error rate exceeded the rollback threshold",
        marked_by_user_id=actor_id,
        marked_at=datetime.now(timezone.utc),
    )
    db = AsyncMock()
    monkeypatch.setattr(
        releases.release_service,
        "get_release_or_404",
        AsyncMock(return_value=release),
    )
    record_outcome = AsyncMock(return_value=outcome)
    monkeypatch.setattr(release_outcome_service, "record_outcome", record_outcome)
    activity = AsyncMock()
    monkeypatch.setattr(releases, "record_activity", activity)

    response = await releases.mark_release_outcome(
        str(release_id),
        releases.ReleaseOutcomeIn(
            outcome="rollback",
            reason="Error rate exceeded the rollback threshold",
        ),
        db,
        actor,
        actor,
    )

    assert response["outcome_kind"] == "rollback"
    record_outcome.assert_awaited_once_with(
        db,
        release=release,
        outcome_kind="rollback",
        reason="Error rate exceeded the rollback threshold",
        marked_by_user_id=actor_id,
    )
    assert activity.await_args.kwargs["event_type"] == "release.outcome_marked"
    assert activity.await_args.kwargs["release_id"] == release_id
    assert activity.await_args.kwargs["context"] == {
        "outcome": "rollback",
        "reason": "Error rate exceeded the rollback threshold",
    }
    db.commit.assert_awaited_once()


def test_release_outcome_activity_event_renders_both_manual_actions() -> None:
    assert "release.outcome_marked" in ACTIVITY_EVENTS
    for outcome in ("incident", "rollback"):
        summary = render_summary(
            ACTIVITY_EVENTS["release.outcome_marked"],
            entity_label="2.14.0",
            actor_name="Release Owner",
            context={"outcome": outcome},
        )
        assert summary == f"Release 2.14.0 marked as {outcome}"


@pytest.mark.asyncio
async def test_release_detail_includes_newest_production_outcomes() -> None:
    project_id = uuid.uuid4()
    release_id = uuid.uuid4()
    release = Release(
        id=release_id,
        project_id=project_id,
        name="2.14.0",
        status="released",
    )
    release.phases = []
    release.test_run_links = []
    older = ReleaseOutcome(
        id=uuid.uuid4(),
        release_id=release_id,
        project_id=project_id,
        outcome_kind="incident",
        reason="Latency breached the production SLO",
        marked_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
    )
    newer = ReleaseOutcome(
        id=uuid.uuid4(),
        release_id=release_id,
        project_id=project_id,
        outcome_kind="rollback",
        reason="Rolled back after the latency incident",
        marked_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
    )

    release_result = SimpleNamespace(scalar_one_or_none=lambda: release)
    outcome_scalars = SimpleNamespace(all=lambda: [newer, older])
    outcome_result = SimpleNamespace(scalars=lambda: outcome_scalars)
    aggregate = SimpleNamespace(
        _mapping={
            "total_runs": 0,
            "total_tests": None,
            "total_passed": None,
            "total_failed": None,
            "avg_pass_rate": None,
        }
    )
    aggregate_result = SimpleNamespace(one=lambda: aggregate)
    db = AsyncMock()
    db.execute.side_effect = [release_result, outcome_result, aggregate_result]

    detail = await release_service.get_release_details(db, str(release_id))

    assert [row["outcome_kind"] for row in detail["outcomes"]] == [
        "rollback",
        "incident",
    ]
    outcome_statement = str(db.execute.await_args_list[1].args[0])
    assert "release_outcomes.release_id" in outcome_statement
    assert "release_outcomes.project_id" in outcome_statement
    assert "ORDER BY release_outcomes.marked_at DESC" in outcome_statement
    assert "LIMIT" in outcome_statement
