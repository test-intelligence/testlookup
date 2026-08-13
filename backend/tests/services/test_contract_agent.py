from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.agents.contract_agent import ContractAgent
import app.agents.contract_agent as contract_agent_module
from app.models.agent_contracts import ContractAgentOutput
from app.tools import validate_api_contract as contract_tool_module


@pytest.mark.asyncio
async def test_contract_agent_requires_authoritative_scope(monkeypatch):
    invoke = AsyncMock(side_effect=AssertionError("tool must not run without scope"))
    monkeypatch.setattr(contract_agent_module, "validate_api_contract", type("FakeTool", (), {"ainvoke": invoke})())

    result = await ContractAgent().validate_cluster(["tc-1"], project_id="p1")

    assert result["status"] == "not_enough_evidence"
    assert result["evidence_refs"] == []
    invoke.assert_not_called()


@pytest.mark.asyncio
async def test_contract_agent_returns_bounded_sanitized_findings(monkeypatch):
    invoke = AsyncMock(return_value=json.dumps({
        "endpoint": "https://service.invalid/users",
        "evidence_available": True,
        "violations": [{
            "field_path": "user.email",
            "violation_type": "missing_field",
            "expected": "string",
            "actual": "password=hunter2 https://user:secret@host.invalid",
            "severity": "critical",
        }],
    }))
    monkeypatch.setattr(contract_agent_module, "validate_api_contract", type("FakeTool", (), {"ainvoke": invoke})())

    result = await ContractAgent().validate_cluster(
        ["tc-1"], project_id="p1", run_id="r1", pipeline_run_id="pl1"
    )

    assert result["status"] == "complete"
    assert result["critical_count"] == 1
    assert result["evidence_refs"][0]["source"] == "validate_api_contract"
    assert "hunter2" not in json.dumps(result)
    ContractAgentOutput.model_validate({"contract": {"agent_name": "contract_validation"}, **result})
    invoke.assert_awaited_once()
    params = json.loads(invoke.await_args.args[0]["params_json"])
    assert params["project_id"] == "p1"
    assert params["test_run_id"] == "r1"
    assert params["pipeline_run_id"] == "pl1"


@pytest.mark.asyncio
async def test_contract_agent_is_honest_when_scoped_payload_is_missing(monkeypatch):
    invoke = AsyncMock(return_value=json.dumps({
        "violations": [],
        "evidence_available": False,
    }))
    monkeypatch.setattr(contract_agent_module, "validate_api_contract", type("FakeTool", (), {"ainvoke": invoke})())

    result = await ContractAgent().validate_cluster(
        ["tc-1"], project_id="p1", run_id="r1", pipeline_run_id="pl1"
    )

    assert result["status"] == "not_enough_evidence"
    assert result["violation_count"] == 0
    assert result["evidence_refs"] == []


@pytest.mark.asyncio
async def test_contract_tool_rejects_missing_scope_before_mongo(monkeypatch):
    called = False

    def fail_db():
        nonlocal called
        called = True
        raise AssertionError("Mongo must not be touched without scope")

    monkeypatch.setattr(contract_tool_module, "get_mongo_db", fail_db)
    result = json.loads(await contract_tool_module.validate_api_contract.ainvoke({
        "params_json": json.dumps({"test_case_id": "tc-1"}),
    }))

    assert result["evidence_available"] is False
    assert called is False


def test_contract_agent_output_is_bounded():
    output = ContractAgentOutput.model_validate({
        "contract": {"agent_name": "contract_validation"},
        "violations": [{"field_path": "x"}] * 200,
        "evidence_refs": [{"source": "validate_api_contract"}] * 200,
    })
    assert len(output.violations) == 200
    assert len(output.evidence_refs) == 200
@pytest.mark.asyncio
async def test_contract_agent_does_not_publish_partial_success(monkeypatch):
    invoke = AsyncMock(side_effect=[RuntimeError("provider unavailable"), json.dumps({"evidence_available": True, "violations": []})])
    monkeypatch.setattr(contract_agent_module, "validate_api_contract", type("FakeTool", (), {"ainvoke": invoke})())

    result = await ContractAgent().validate_cluster(
        ["tc-1", "tc-2"], project_id="p1", run_id="r1", pipeline_run_id="pl1"
    )

    assert result["status"] == "failed"
    assert "incomplete" in result["summary"]