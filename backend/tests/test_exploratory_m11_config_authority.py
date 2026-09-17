"""M11 regressions for frozen configuration and runtime tool authority."""
from __future__ import annotations

from types import SimpleNamespace
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
    monkeypatch.setattr(
        module, "reconstruct_distributed_trace", SimpleNamespace(ainvoke=trace)
    )
    monkeypatch.setattr(
        module, "detect_log_rate_anomaly", SimpleNamespace(ainvoke=anomaly)
    )

    with workflow_step_scope("log_intelligence", allowed_tools=[]):
        output = await LogIntelligenceAgent().investigate(
            service_name="checkout",
            timestamp_utc="2026-09-17T00:00:00Z",
        )

    trace.assert_not_awaited()
    anomaly.assert_not_awaited()
    assert output["distributed_trace"]["error"] == "PermissionError"
    assert output["log_anomaly"]["error"] == "PermissionError"


@pytest.mark.asyncio
async def test_cluster_agent_does_not_call_a_tool_removed_by_the_frozen_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agents.cluster_agent as module

    called = AsyncMock(side_effect=AssertionError("clustering tool was called"))
    monkeypatch.setitem(
        __import__("sys").modules,
        "app.tools.embed_and_cluster",
        SimpleNamespace(embed_and_cluster=SimpleNamespace(ainvoke=called)),
    )

    class _Rows:
        async def execute(self, _statement):
            return [SimpleNamespace(id="test-1", test_name="checkout", error_message="boom")]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(module, "AsyncSessionLocal", lambda: _Rows())
    agent = module.ClusterAgent()
    agent.mark_stage_running = AsyncMock()
    agent.mark_stage_done = AsyncMock()
    agent.broadcast_progress = AsyncMock()
    agent.log_decision = AsyncMock()

    with workflow_step_scope("failure_clustering", allowed_tools=[]):
        output = await agent.run({
            "pipeline_run_id": "pipeline-1",
            "project_id": "project-1",
            "failed_test_ids": ["test-1"],
        })

    called.assert_not_awaited()
    assert output["failure_clusters"][0]["member_test_ids"] == ["test-1"]
