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


@pytest.mark.asyncio
async def test_contract_agent_violations_keep_their_own_endpoint(monkeypatch):
    """W23d P-5: a test that called two endpoints names the one that drifted."""
    sentence = "field `price` missing from GET /api/products/{id}; drift since build 41"
    invoke = AsyncMock(return_value=json.dumps({
        "endpoint": "/api/cart",
        "endpoints": ["/api/cart", "/api/products/{id}"],
        "evidence_available": True,
        "exchanges_checked": 2,
        "violations": [{
            "field_path": "price", "violation_type": "schema_drift", "expected": "present",
            "actual": "absent", "severity": "critical", "method": "GET",
            "endpoint": "/api/products/{id}", "baseline_build": "41", "sentence": sentence,
        }],
    }))
    monkeypatch.setattr(contract_agent_module, "validate_api_contract", type("FakeTool", (), {"ainvoke": invoke})())

    result = await ContractAgent().validate_cluster(
        ["tc-1"], project_id="p1", run_id="r1", pipeline_run_id="pl1"
    )

    (violation,) = result["violations"]
    assert violation["endpoint"] == "/api/products/{id}"
    assert (violation["method"], violation["baseline_build"], violation["sentence"]) == ("GET", "41", sentence)
    assert result["evidence_refs"][0]["endpoint"] == "/api/products/{id}"
    assert result["endpoints_checked"] == ["/api/cart", "/api/products/{id}"]
    assert result["status"] == "complete"
    # W23d: one field on one endpoint reads in the singular (the Specialist evidence row shows the summary as written).
    assert result["summary"] == f"CRITICAL CONTRACT VIOLATIONS: 1 required field missing across 1 endpoint. {sentence}"
    ContractAgentOutput.model_validate({"contract": {"agent_name": "contract_validation"}, **result})


@pytest.mark.asyncio
async def test_contract_agent_counts_several_findings_in_the_plural(monkeypatch):
    def drift(field: str) -> dict:
        return {"field_path": field, "violation_type": "type_drift", "expected": "number", "actual": "string",
                "severity": "warning", "method": "GET", "endpoint": "/api/products/{id}"}

    invoke = AsyncMock(return_value=json.dumps({
        "endpoint": "/api/products/{id}", "evidence_available": True, "exchanges_checked": 1,
        "violations": [drift("price"), drift("stock")],
    }))
    monkeypatch.setattr(contract_agent_module, "validate_api_contract", type("FakeTool", (), {"ainvoke": invoke})())

    result = await ContractAgent().validate_cluster(
        ["tc-1"], project_id="p1", run_id="r1", pipeline_run_id="pl1"
    )

    assert result["summary"] == "SCHEMA DRIFT DETECTED: 2 fields changed from historical responses."