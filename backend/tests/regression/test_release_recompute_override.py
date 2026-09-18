"""Agent recomputation must preserve a serialized human release override."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import postgresql


class _Result:
    def __init__(self, record) -> None:
        self.record = record

    def scalar_one_or_none(self):
        return self.record


class _Session:
    def __init__(self, record) -> None:
        self.record = record
        self.statement = None
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, statement):
        self.statement = statement
        return _Result(self.record)

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_recompute_locks_and_preserves_human_override(monkeypatch) -> None:
    from app.agents import release_risk_agent

    record = SimpleNamespace(
        human_override="QA accepted the documented residual risk",
        recommendation="GO",
        risk_score=80,
        original_recommendation="NO_GO",
        original_risk_score=80,
        override_audit=[{"reason": "QA accepted the documented residual risk"}],
    )
    session = _Session(record)
    monkeypatch.setattr(release_risk_agent, "AsyncSessionLocal", lambda: session)

    with patch(
        "app.services.release_decision_webhook.emit_release_decided",
        AsyncMock(),
    ):
        agent = release_risk_agent.ReleaseRiskAgent.__new__(
            release_risk_agent.ReleaseRiskAgent
        )
        await agent._persist_decision(
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            {
                "recommendation": "CONDITIONAL_GO",
                "risk_score": 45,
                "blocking_issues": [],
                "conditions_for_go": ["Recheck after deployment"],
                "reasoning": "Fresh agent evidence is less severe.",
                "dimension_scores": {"user_impact": 20.0},
                "composite_risk": 45.0,
                "score_model_version": 1,
                "policy_id": None,
                "policy_evaluation": None,
            },
            {"pass_rate": 95.0},
        )

    assert session.committed is True
    assert session.statement is not None
    sql = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql
    assert record.recommendation == "GO"
    assert record.risk_score == 45
    assert record.original_recommendation == "NO_GO"
    assert record.original_risk_score == 80
    assert record.override_audit == [
        {"reason": "QA accepted the documented residual risk"}
    ]
    assert record.reasoning == "Fresh agent evidence is less severe."
    assert record.input_snapshot == {"pass_rate": 95.0}
