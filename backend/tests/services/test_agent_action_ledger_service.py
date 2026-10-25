from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


def _row(**overrides):
    base = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "action_type": "decision_report_action",
        "target_type": "release_review",
        "target_id": "release.blocker.1",
        "request_sha256": "a" * 64,
        "status": "pending_review",
        "pipeline_run_id": None,
        "request_payload": {"title": "Investigate"},
    }
    base.update(overrides)
    return MagicMock(**base)


@pytest.mark.asyncio
async def test_create_proposal_sanitizes_and_reuses_idempotently():
    from app.services.agent_action_ledger_service import create_action_proposal

    db = MagicMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    empty = MagicMock()
    empty.scalar_one_or_none.return_value = None
    db.execute.return_value = empty
    project_id = uuid.uuid4()
    row, created = await create_action_proposal(
        db,
        project_id=project_id,
        action_type="jira_ticket_creation",
        target_type="jira",
        target_id="DEF-1",
        idempotency_key="jira:run-1:def-1",
        request_payload={"summary": "password=hunter2", "safe": "value"},
    )
    assert created is True
    assert row.status == "pending_review"
    stored = db.add.call_args.args[0]
    assert "hunter2" not in str(stored.request_payload)
    assert len(stored.request_sha256) == 64

    existing = MagicMock()
    existing.scalar_one_or_none.return_value = stored
    db.execute.return_value = existing
    reused, created_again = await create_action_proposal(
        db,
        project_id=project_id,
        action_type="jira_ticket_creation",
        target_type="jira",
        target_id="DEF-1",
        idempotency_key="jira:run-1:def-1",
        request_payload={"summary": "password=hunter2", "safe": "value"},
    )
    assert reused is stored
    assert created_again is False


@pytest.mark.asyncio
async def test_idempotency_conflict_and_invalid_transition_fail_closed():
    from app.services.agent_action_ledger_service import create_action_proposal, transition_action

    project_id = uuid.uuid4()
    stored = _row(project_id=project_id)
    db = MagicMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    existing = MagicMock()
    existing.scalar_one_or_none.return_value = stored
    db.execute.return_value = existing
    with pytest.raises(ValueError, match="action_idempotency_conflict"):
        await create_action_proposal(
            db,
            project_id=project_id,
            action_type="jira_ticket_creation",
            target_type="jira",
            target_id="DEF-2",
            idempotency_key="same-key",
            request_payload={"summary": "different"},
        )

    with pytest.raises(ValueError, match="action_transition_invalid"):
        await transition_action(
            db,
            project_id=project_id,
            action_id=stored.id,
            to_status="executed",
        )


@pytest.mark.asyncio
async def test_transition_records_execution_result_and_rollback_payload():
    from app.services.agent_action_ledger_service import transition_action

    row = _row(status="approved")
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db = MagicMock()
    db.execute = AsyncMock()
    db.execute.return_value = result
    executing = await transition_action(
        db,
        project_id=row.project_id,
        action_id=row.id,
        to_status="executing",
    )
    assert executing.execution_started_at is not None
    row.status = "executing"
    completed = await transition_action(
        db,
        project_id=row.project_id,
        action_id=row.id,
        to_status="executed",
        result_payload={"jira_key": "DEF-1", "body": "token=secret"},
    )
    assert completed.execution_completed_at is not None
    assert "secret" not in str(completed.result_payload)


@pytest.mark.asyncio
async def test_report_proposals_are_bounded_and_approval_gated():
    from app.services.agent_action_ledger_service import persist_report_action_proposals

    db = MagicMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    empty = MagicMock()
    empty.scalar_one_or_none.return_value = None
    db.execute.return_value = empty
    count = await persist_report_action_proposals(
        db,
        project_id=uuid.uuid4(),
        test_run_id=uuid.uuid4(),
        pipeline_run_id=uuid.uuid4(),
        proposed_actions=[
            {
                "action_id": "release.blocker.1",
                "idempotency_key": "release:blocker:1",
                "title": "Investigate blocker",
                "owner": "release_owner",
                "rationale": "failure",
                "required_permission": "release_review",
                "risk": "high",
                "evidence": [{"type": "decision_evidence", "id": "abc"}],
            }
        ],
    )
    assert count == 1
    proposal = db.add.call_args.args[0]
    assert proposal.approval_required is True
    assert proposal.status == "pending_review"
    assert proposal.action_type == "decision_report_action"
    assert proposal.request_payload["proposing_agent_id"] == "agent.decision_report.v1"


@pytest.mark.asyncio
async def test_approved_action_gets_one_outbox_and_claim_is_leased():
    from app.services.agent_action_ledger_service import (
        claim_action_dispatches,
        enqueue_action_dispatch,
        mark_action_dispatch_failed,
        mark_action_dispatch_sent,
    )

    action = _row(status="approved", project_id=uuid.uuid4(), idempotency_key="action-1")
    outbox = MagicMock(action_id=action.id, idempotency_key=action.idempotency_key, status="pending", attempts=0)
    db = MagicMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    found = MagicMock()
    found.scalar_one_or_none.return_value = None
    db.execute.return_value = found
    created = await enqueue_action_dispatch(db, action=action)
    assert created.status == "pending"
    assert db.add.call_args.args[0].idempotency_key == "action-1"

    claimed_result = MagicMock()
    claimed_result.scalars.return_value.all.return_value = [outbox]
    db.execute.return_value = claimed_result
    claimed = await claim_action_dispatches(db, limit=1, lease_seconds=30)
    assert claimed == [outbox]
    assert outbox.status == "sending"
    assert outbox.attempts == 1
    assert outbox.lease_expires_at is not None

    row_result = MagicMock()
    row_result.scalar_one_or_none.return_value = outbox
    db.execute.return_value = row_result
    assert await mark_action_dispatch_failed(
        db, outbox_id=outbox.id, error_code="broker_unavailable", permanent=False
    ) is True
    assert outbox.status == "pending"

    outbox.status = "sending"
    assert await mark_action_dispatch_sent(db, outbox_id=outbox.id) is True
    assert outbox.status == "sent"


@pytest.mark.asyncio
async def test_executor_fails_closed_without_registered_external_adapter(monkeypatch):
    # W15 rewrite (contract §0.22 / §6.5): the executor now runs registered
    # adapters, so the fail-closed case is a type with NO adapter -- on the
    # executor test's multi-session world (one MagicMock result cannot answer
    # the new statements).
    from tests.test_agent_action_executor import Harness, _row as executor_row

    harness = Harness(monkeypatch)
    action = executor_row(action_type="jira_ticket_creation", payload={"summary": "file it"})
    world = harness.world(action, real_adapter=True)
    outcome = await harness.run(world)
    assert outcome["status"] == "failed"
    assert outcome["reason"] == "action_executor_not_configured"
    assert action.status == "failed"
    assert action.error_code == "action_executor_not_configured"
    assert len(world.commits) == 1


@pytest.mark.asyncio
async def test_relay_publishes_only_ids_and_acknowledges_outbox(monkeypatch):
    pytest.importorskip("celery")
    import app.services.agent_action_ledger_service as service
    import app.worker.tasks as tasks

    row = MagicMock(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        action_id=uuid.uuid4(),
    )
    db = MagicMock()
    db.commit = AsyncMock()

    class _Session:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    claim = AsyncMock(return_value=[row])
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _Session())
    monkeypatch.setattr(service, "claim_action_dispatches", claim)
    monkeypatch.setattr(service, "mark_action_dispatch_sent", sent)
    task = MagicMock()
    monkeypatch.setattr(tasks, "execute_agent_action", task)

    result = await service.relay_action_dispatch_outbox(limit=1)
    assert result == {"claimed": 1, "sent": 1, "failed": 0}
    task.apply_async.assert_called_once_with(
        args=[str(row.project_id), str(row.action_id)],
        queue="default",
        task_id=f"agent-action-{row.action_id}",
    )
    sent.assert_awaited_once_with(db, outbox_id=row.id)
    # Two commits, in two separate sessions, is the point of the outbox:
    #   1. the CLAIM is committed BEFORE anything is published, so a crash
    #      mid-publish cannot hand the same row to a second relay pass;
    #   2. the send acknowledgement is committed on its own afterwards.
    # This stub hands back the same session object for both, so both land on
    # the same mock. Collapsing these into one commit would reintroduce the
    # double-publish window.
    assert db.commit.await_count == 2


def test_action_relay_is_scheduled_and_executor_is_registered():
    pytest.importorskip("celery")
    from app.worker.celery_app import celery_app

    assert "relay-agent-action-dispatch-outbox" in celery_app.conf.beat_schedule
    assert "app.worker.tasks.execute_agent_action" in celery_app.tasks


# -- E8.6: a mutating call needs an accepted review of the run that proposed it --
#
# W15 rewrite (contract §0.22 / §6.5): these five ran the executor over ONE
# MagicMock result, which cannot answer the executor's new statements, and
# three of their expectations change by design -- the legacy
# ``decision_report_action`` is now ACKNOWLEDGEABLE (an executed no-effect
# action) and the refusal codes are specific (``review_not_accepted``,
# ``act_mode_required``, ``proposer_not_permitted``) instead of the old
# blanket ``policy_denied``. They run on the executor test's world now.


def _decision_payload(**overrides):
    payload = {
        "proposing_agent_id": "agent.decision_report.v1",
        "action_id": "release.blocker.1",
        "title": "Investigate",
        "owner": "release_owner",
        "rationale": "failure",
        "evidence": [],
        "risk": "unknown",
        "required_permission": "release_review",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_executor_denies_an_action_whose_proposing_run_is_not_accepted(monkeypatch):
    from tests.test_agent_action_executor import Harness, _row as executor_row

    harness = Harness(monkeypatch)
    action = executor_row(action_type="decision_report_action", payload=_decision_payload())
    world = harness.world(action, review=None, real_adapter=True)

    outcome = await harness.run(world)

    assert outcome == {"status": "failed", "action_id": str(action.id), "reason": "review_not_accepted"}
    assert action.status == "failed"
    assert action.error_code == "review_not_accepted"
    assert len(world.commits) == 1
    review_stmt = next(
        s for session in world.sessions for s in session.raw_statements if "FROM review_requests" in str(s)
    )
    review_query = str(review_stmt)
    assert "review_requests.pipeline_run_id" in review_query
    assert "review_requests.state" in review_query
    assert "review_requests.project_id" in review_query
    assert "review_requests.kind" in review_query
    assert "review_requests.subject_type" in review_query
    assert "review_requests.subject_id" in review_query
    params = set(review_stmt.compile().params.values())
    assert {
        "accepted",
        "report",
        "pipeline_run",
        str(action.pipeline_run_id),
        action.project_id,
    } <= params


@pytest.mark.asyncio
async def test_executor_proceeds_when_the_proposing_run_was_accepted(monkeypatch):
    from tests.test_agent_action_executor import Harness, _row as executor_row

    harness = Harness(monkeypatch)
    action = executor_row(action_type="decision_report_action", payload=_decision_payload())
    world = harness.world(action, real_adapter=True)

    outcome = await harness.run(world)

    # The legacy type is the no-effect acknowledge now (D-5): executed, nothing changed.
    assert outcome == {"status": "executed", "action_id": str(action.id)}
    assert action.result_payload["acknowledged"] is True


@pytest.mark.asyncio
async def test_executor_denies_an_accepted_action_unless_proposer_mode_is_act(monkeypatch):
    from tests.test_agent_action_executor import Harness, _act_config, _row as executor_row

    harness = Harness(monkeypatch)
    harness.config = _act_config(mode="suggest")
    action = executor_row()                      # an effectful quarantine.apply proposal
    world = harness.world(action)

    outcome = await harness.run(world)

    assert outcome == {"status": "failed", "action_id": str(action.id), "reason": "act_mode_required"}
    assert action.status == "failed"
    assert action.error_code == "act_mode_required"
    assert harness.resolved == [(action.project_id, "investigator")]
    assert len(world.commits) == 1
    assert world.adapter.calls == []


@pytest.mark.asyncio
async def test_executor_denies_pipeline_action_without_proposer_identity(monkeypatch):
    from tests.test_agent_action_executor import Harness, _quarantine_payload, _row as executor_row

    harness = Harness(monkeypatch)
    payload = _quarantine_payload()
    payload.pop("proposing_agent_id")
    action = executor_row(payload=payload)
    world = harness.world(action)

    outcome = await harness.run(world)

    assert outcome["reason"] == "proposer_not_permitted"
    assert harness.resolved == [], "no config can be proven without an identity"
    assert world.adapter.calls == []


@pytest.mark.asyncio
async def test_executor_needs_no_review_for_an_action_with_no_proposing_run(monkeypatch):
    from tests.test_agent_action_executor import Harness, _row as executor_row

    harness = Harness(monkeypatch)
    action = executor_row(action_type="decision_report_action", payload=_decision_payload(), pipeline=False)
    world = harness.world(action, review=None, real_adapter=True)

    outcome = await harness.run(world)

    assert outcome["status"] == "executed"
    assert not any("FROM review_requests" in s for s in world.statements()), (
        "no review lookup without a proposing run"
    )
