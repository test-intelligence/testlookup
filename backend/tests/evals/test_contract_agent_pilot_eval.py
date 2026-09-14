"""Deterministic Contract Agent contribution and privacy corpus.

This is a structural readiness gate, not a claim of live API-contract quality.
It proves the specialist contributes only when enabled, preserves authoritative
scope, and sanitizes a representative contract violation before report use.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.agents import contract_agent as contract_agent_module
from app.agents import workflow
from app.agents.contract_agent import ContractAgent
from app.services.agent_eval_samples import pilot_inputs


@pytest.fixture(autouse=True)
def _quiet_stage_lifecycle(monkeypatch):
    """The stage now records itself, which needs Postgres and Mongo.

    This corpus measures evidence contribution and sanitisation, not
    persistence, so the lifecycle is stubbed rather than made fail-open --
    a lifecycle that swallowed its own errors would leave the stage
    unrecorded with nobody the wiser.
    """
    monkeypatch.setattr(ContractAgent, "mark_stage_running", AsyncMock())
    monkeypatch.setattr(ContractAgent, "mark_stage_done", AsyncMock())
    monkeypatch.setattr(ContractAgent, "log_decision", AsyncMock())


_CORPUS = pilot_inputs("contract_validation")[:3]


def _state(*, enabled: bool) -> dict:
    return {
        "contract_agent_enabled": enabled,
        "failed_test_ids": [item["test_case_id"] for item in _CORPUS],
        "project_id": "project-contract-eval",
        "test_run_id": "run-contract-eval",
        "pipeline_run_id": "pipeline-contract-eval",
    }


@pytest.mark.asyncio
async def test_contract_pilot_enabled_contribution_disabled_noop_and_privacy(
    monkeypatch,
):
    invoke = AsyncMock(
        return_value=json.dumps({
            "endpoint": _CORPUS[0]["endpoint"],
            "evidence_available": True,
            "violations": [{
                "field_path": _CORPUS[0]["field"],
                "violation_type": "missing_field",
                "expected": "string",
                "actual": "password=hunter2 email=user@example.com",
                "severity": "critical",
            }],
        })
    )
    monkeypatch.setattr(
        contract_agent_module,
        "validate_api_contract",
        type("FakeContractTool", (), {"ainvoke": invoke})(),
    )

    enabled = await workflow.contract_validation_node(_state(enabled=True))
    disabled = await workflow.contract_validation_node(_state(enabled=False))

    findings = enabled["contract_findings"]
    assert findings["status"] == "complete"
    assert findings["critical_count"] == len(_CORPUS)
    assert findings["evidence_refs"]
    assert disabled["contract_findings"]["status"] == "not_enough_evidence"
    assert disabled["contract_findings"]["violations"] == []
    assert disabled["skipped_stages"] == ["contract_validation"]
    assert invoke.await_count == len(_CORPUS)
    for call in invoke.await_args_list:
        params = json.loads(call.args[0]["params_json"])
        assert params["project_id"] == "project-contract-eval"
        assert params["test_run_id"] == "run-contract-eval"
        assert params["pipeline_run_id"] == "pipeline-contract-eval"

    serialized = json.dumps(enabled, sort_keys=True)
    assert "hunter2" not in serialized
    assert "user@example.com" not in serialized
