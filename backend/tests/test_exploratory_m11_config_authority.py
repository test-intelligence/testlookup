"""M11 regressions for frozen configuration and runtime tool authority."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agents.log_intelligence_agent import LogIntelligenceAgent
from app.agents.workflow import _stage_tool_allowlist
from app.services.workflow_step_context import tool_allowed, workflow_step_scope


def test_empty_tool_allowlist_is_authoritative_inside_a_step() -> None:
    assert tool_allowed("validate_api_contract") is True
    with workflow_step_scope("contract_validation", allowed_tools=[]):
        assert tool_allowed("validate_api_contract") is False
    assert tool_allowed("validate_api_contract") is True


def test_named_step_reads_tools_from_its_frozen_capability_snapshot() -> None:
    state = {
        "initial_workflow_plan": {
            "stages": [{
                "stage": "logs_for_checkout",
                "capability_id": "agent.log_intelligence.v1",
            }]
        },
        "resolved_agent_configs": {
            "agent.log_intelligence.v1": {
                "config": {"tools": {"allowlist": ["detect_log_rate_anomaly"]}}
            }
        },
    }
    assert _stage_tool_allowlist(state, "logs_for_checkout") == [
        "detect_log_rate_anomaly"
    ]


@pytest.mark.asyncio
async def test_log_agent_does_not_call_tools_removed_by_the_frozen_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agents.log_intelligence_agent as module

    trace = AsyncMock(side_effect=AssertionError("trace tool was called"))
    anomaly = AsyncMock(side_effect=AssertionError("anomaly tool was called"))
    monkeypatch.setattr(module.reconstruct_distributed_trace, "ainvoke", trace)
    monkeypatch.setattr(module.detect_log_rate_anomaly, "ainvoke", anomaly)

    with workflow_step_scope("log_intelligence", allowed_tools=[]):
        output = await LogIntelligenceAgent().investigate(
            service_name="checkout",
            timestamp_utc="2026-09-17T00:00:00Z",
        )

    trace.assert_not_awaited()
    anomaly.assert_not_awaited()
    assert output["distributed_trace"]["error"] == "PermissionError"
    assert output["log_anomaly"]["error"] == "PermissionError"
