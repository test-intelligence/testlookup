"""The release decision write is fenced, and a substituted verdict says so.

D-147: a reaped attempt 1 and the resumed attempt 2 both reached
``ReleaseRiskAgent._persist_decision``, which opened its own session and never
looked at the pipeline lease, so whichever finished last owned the stored
reasoning. The reaper rotates the fencing token; the write now checks it.

S-B-4: when scoring raises, the stage stores a substitute CONDITIONAL_GO at
risk 50 in the same shape as a scored decision. The snapshot now carries
``verdict_driver = "scorer_failed"`` so the gate page can tell them apart.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents import release_risk_agent
from app.agents.release_risk_agent import ReleaseRiskAgent
from app.services.pipeline_lease import LeaseLost


class _Result:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    """Answers the fencing-token read first, then the decision-row read."""

    def __init__(self, current_token: str | None, record) -> None:
        self.current_token = current_token
        self.record = record
        self.statements: list[str] = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if "agent_pipeline_runs" in sql:
            return _Result(self.current_token)
        return _Result(self.record)

    def add(self, _row) -> None:  # pragma: no cover - a new row is a failure here
        raise AssertionError("a fenced-out attempt must not insert a decision")

    async def commit(self) -> None:
        self.committed = True


def _stored_record():
    return SimpleNamespace(
        human_override=None,
        recommendation="NO_GO",
        risk_score=62,
        blocking_issues=["Checkout regression in payments"],
        conditions_for_go=[],
        reasoning="The model's explanation from the live attempt.",
        dimension_scores={"user_impact": 70.0},
        composite_risk=62.0,
        score_model_version=3,
        policy_id=None,
        policy_evaluation=None,
        input_snapshot={"assembled_at": "live-attempt"},
        pipeline_run_id=None,
    )


def _late_decision() -> dict:
    return {
        "recommendation": "CONDITIONAL_GO",
        "risk_score": 40,
        "blocking_issues": [],
        "conditions_for_go": [],
        "reasoning": "LLM reasoning unavailable",
        "dimension_scores": {"user_impact": 30.0},
        "composite_risk": 40.0,
        "score_model_version": 3,
        "policy_id": None,
        "policy_evaluation": None,
    }


def _agent() -> ReleaseRiskAgent:
    return ReleaseRiskAgent.__new__(ReleaseRiskAgent)


@pytest.mark.asyncio
async def test_a_write_with_a_rotated_token_is_refused(monkeypatch) -> None:
    record = _stored_record()
    before = dict(vars(record))
    session = _Session(current_token="token-of-attempt-2", record=record)
    monkeypatch.setattr(release_risk_agent, "AsyncSessionLocal", lambda: session)
    trigger = AsyncMock()
    monkeypatch.setattr(
        "app.services.agent_investigation_service.maybe_auto_trigger", trigger
    )

    with pytest.raises(LeaseLost):
        await _agent()._persist_decision(
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            _late_decision(),
            input_snapshot={"assembled_at": "reaped-attempt"},
            fencing_token="token-of-attempt-1",
        )

    assert vars(record) == before, "the reaped attempt overwrote the decision"
    assert session.committed is False
    assert not any("release_decisions" in sql for sql in session.statements), (
        "the decision row was read for update after the lease was already lost"
    )
    trigger.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_write_with_the_current_token_goes_through(monkeypatch) -> None:
    record = _stored_record()
    session = _Session(current_token="token-of-attempt-2", record=record)
    monkeypatch.setattr(release_risk_agent, "AsyncSessionLocal", lambda: session)

    await _agent()._persist_decision(
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        _late_decision(),
        input_snapshot={"assembled_at": "attempt-2"},
        fencing_token="token-of-attempt-2",
    )

    assert session.committed is True
    assert record.risk_score == 40
    assert record.input_snapshot == {"assembled_at": "attempt-2"}


def _quiet_lifecycle(monkeypatch) -> AsyncMock:
    monkeypatch.setattr(ReleaseRiskAgent, "mark_stage_running", AsyncMock())
    monkeypatch.setattr(ReleaseRiskAgent, "mark_stage_done", AsyncMock())
    monkeypatch.setattr(ReleaseRiskAgent, "broadcast_progress", AsyncMock())
    monkeypatch.setattr(ReleaseRiskAgent, "log_decision", AsyncMock())
    monkeypatch.setattr(
        ReleaseRiskAgent, "_assemble_input_snapshot", AsyncMock(return_value={"pass_rate": 90.0})
    )
    persist = AsyncMock()
    monkeypatch.setattr(ReleaseRiskAgent, "_persist_decision", persist)
    return persist


@pytest.mark.asyncio
async def test_the_stage_hands_its_attempt_token_to_the_write(monkeypatch) -> None:
    persist = _quiet_lifecycle(monkeypatch)
    monkeypatch.setattr(
        ReleaseRiskAgent, "_evaluate",
        AsyncMock(side_effect=RuntimeError("scorer exploded")),
    )

    await ReleaseRiskAgent().run({
        "pipeline_run_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "test_run_id": str(uuid.uuid4()),
        "_fencing_token": "token-of-this-attempt",
    })

    assert persist.await_args.kwargs["fencing_token"] == "token-of-this-attempt"


@pytest.mark.asyncio
async def test_a_scorer_failure_is_marked_on_the_stored_snapshot(monkeypatch) -> None:
    persist = _quiet_lifecycle(monkeypatch)
    monkeypatch.setattr(
        ReleaseRiskAgent, "_evaluate",
        AsyncMock(side_effect=RuntimeError("scorer exploded")),
    )

    await ReleaseRiskAgent().run({
        "pipeline_run_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "test_run_id": str(uuid.uuid4()),
    })

    snapshot = persist.await_args.kwargs["input_snapshot"]
    assert snapshot["verdict_driver"] == "scorer_failed"
    decision = persist.await_args.args[2]
    assert decision["input_snapshot"]["verdict_driver"] == "scorer_failed"
    assert decision["risk_score"] == 50


@pytest.mark.asyncio
async def test_a_scored_decision_carries_no_scorer_failed_marker(monkeypatch) -> None:
    persist = _quiet_lifecycle(monkeypatch)
    monkeypatch.setattr(
        ReleaseRiskAgent, "_evaluate",
        AsyncMock(return_value={
            "recommendation": "GO",
            "risk_score": 12,
            "dimension_scores": {"user_impact": 10.0},
            "blocking_issues": [],
            "conditions_for_go": [],
            "reasoning": "Clean run.",
        }),
    )

    await ReleaseRiskAgent().run({
        "pipeline_run_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "test_run_id": str(uuid.uuid4()),
    })

    assert "verdict_driver" not in persist.await_args.kwargs["input_snapshot"]
