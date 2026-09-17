from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents import summary_agent as summary_module
from app.agents.summary_agent import SummaryAgent
from app.services.agent_capability_registry import CAPABILITY_REGISTRY, DEFAULT_TIERS
from app.services.agent_config_resolver import ResolvedAgentConfig, ResolvedEndpoint
from app.services.agent_config_service import default_config
from app.services.step_llm_budget import StepLLMBudget


def _endpoint(model: str) -> ResolvedEndpoint:
    return ResolvedEndpoint(
        provider="ollama",
        model=model,
        temperature=0.1,
        max_tokens=2048,
        base_url="http://127.0.0.1:11434",
        source="project",
    )


def _resolved(*, tier: str = "auto") -> ResolvedAgentConfig:
    config = default_config("agent.summary.v1")
    config.model.tier = tier
    config.shadow.sample_rate = 1.0
    return ResolvedAgentConfig(
        agent_id="agent.summary.v1",
        source="default",
        config_version=0,
        config=config,
        endpoints={"slm": _endpoint("small"), "llm": _endpoint("large")},
        offline_mode=True,
        offline_mode_source="env",
        offline_mode_env_pinned=True,
    )


def test_summary_default_is_promoted_only_after_the_g2_gate_shipped() -> None:
    assert DEFAULT_TIERS["summary"] == "slm"
    assert CAPABILITY_REGISTRY["summary"].expected_cost_usd > 0


@pytest.mark.asyncio
async def test_summary_repairs_slm_once_then_escalates_and_enqueues_pair(
    monkeypatch,
) -> None:
    from app.core.metrics import model_escalations_total

    agent = SummaryAgent()
    agent.log_decision = AsyncMock()
    agent._routing_inputs = AsyncMock(return_value=(_resolved(), 1.0, 10))
    calls: list[str] = []

    async def fake_get_llm(*, endpoint):
        return SimpleNamespace(name=endpoint.model)

    async def fake_generate(*, llm, validation_failures, **_kwargs):
        calls.append(llm.name)
        if llm.name == "small":
            validation_failures.append("incident_view:missing what_failed")
        return {"source": llm.name, "attempt": len(calls)}

    monkeypatch.setattr(summary_module, "get_llm", fake_get_llm)
    monkeypatch.setattr(agent, "_generate_structured_report", fake_generate)
    monkeypatch.setattr(
        agent,
        "_report_failures",
        lambda _structured, *, validation_failures, **_kwargs: list(validation_failures),
    )
    queued: list[dict] = []
    monkeypatch.setattr(
        "app.services.tier_comparison_service.enqueue_shadow_pair",
        lambda **kwargs: queued.append(kwargs),
    )
    metric = model_escalations_total.labels(
        agent="summary", **{"from": "slm", "to": "llm"}
    )
    before = metric._value.get()
    budget = StepLLMBudget(limit=2)

    structured, routing = await agent._generate_tiered_report(
        run_data={},
        anomaly_summary="",
        anomalies=[],
        analyses={},
        similar_failures=[],
        stage_quality="normal",
        stage_errors={},
        pipeline_run_id="00000000-0000-0000-0000-000000000001",
        project_id="00000000-0000-0000-0000-000000000002",
        test_run_id="00000000-0000-0000-0000-000000000003",
        failed_test_ids=[],
        step_budget=budget,
    )

    assert calls == ["small", "small", "large"]
    assert structured["source"] == "large"
    assert routing == {
        "tier_requested": "auto",
        "tier_used": "llm",
        "escalations": 1,
        "fallback_used": False,
    }
    assert metric._value.get() == before + 1
    assert len(queued) == 1
    assert queued[0]["incumbent_tier"] == "llm"
    assert queued[0]["candidate_tier"] == "slm"
    assert queued[0]["incumbent_output"]["source"] == "large"
    assert queued[0]["candidate_output"]["source"] == "small"
    assert budget.as_state()["reasons"] == ["model_escalation"]


@pytest.mark.asyncio
async def test_summary_retry_override_uses_llm_directly(monkeypatch) -> None:
    agent = SummaryAgent()
    agent.log_decision = AsyncMock()
    agent._routing_inputs = AsyncMock(return_value=(_resolved(tier="slm"), 1.0, 10))

    async def fake_get_llm(*, endpoint):
        return SimpleNamespace(name=endpoint.model)

    async def fake_generate(*, llm, **_kwargs):
        return {"source": llm.name}

    monkeypatch.setattr(summary_module, "get_llm", fake_get_llm)
    monkeypatch.setattr(agent, "_generate_structured_report", fake_generate)
    monkeypatch.setattr(agent, "_report_failures", lambda *_args, **_kwargs: [])

    structured, routing = await agent._generate_tiered_report(
        run_data={},
        anomaly_summary="",
        anomalies=[],
        analyses={},
        similar_failures=[],
        stage_quality="normal",
        stage_errors={},
        pipeline_run_id="00000000-0000-0000-0000-000000000001",
        project_id="00000000-0000-0000-0000-000000000002",
        test_run_id="00000000-0000-0000-0000-000000000003",
        failed_test_ids=[],
        tier_override="llm",
    )

    assert structured == {"source": "large"}
    assert routing["tier_used"] == "llm"
    assert routing["escalations"] == 0


@pytest.mark.asyncio
async def test_valid_slm_summary_neither_repairs_nor_escalates(monkeypatch) -> None:
    agent = SummaryAgent()
    agent.log_decision = AsyncMock()
    agent._routing_inputs = AsyncMock(return_value=(_resolved(), 1.0, 10))
    generate = AsyncMock(return_value={"source": "small"})

    monkeypatch.setattr(summary_module, "get_llm", AsyncMock(return_value=object()))
    monkeypatch.setattr(agent, "_generate_structured_report", generate)
    monkeypatch.setattr(agent, "_report_failures", lambda *_args, **_kwargs: [])

    structured, routing = await agent._generate_tiered_report(
        run_data={},
        anomaly_summary="",
        anomalies=[],
        analyses={},
        similar_failures=[],
        stage_quality="normal",
        stage_errors={},
        pipeline_run_id="00000000-0000-0000-0000-000000000001",
        project_id="00000000-0000-0000-0000-000000000002",
        test_run_id="00000000-0000-0000-0000-000000000003",
        failed_test_ids=[],
    )

    assert structured == {"source": "small"}
    assert generate.await_count == 1
    assert routing["tier_used"] == "slm"
    assert routing["escalations"] == 0


@pytest.mark.asyncio
async def test_json_validation_failure_is_exposed_to_the_tier_orchestrator() -> None:
    class LLM:
        def with_structured_output(self, _schema):
            raise NotImplementedError

        async def ainvoke(self, _prompt):
            return SimpleNamespace(content="{}")

    failures: list[str] = []
    payload = await SummaryAgent()._call_json_layer(
        LLM(),
        "{system}\n{context}",
        "context",
        expected_keys=["what_failed", "likely_cause"],
        layer_name="incident_view",
        validation_failures=failures,
    )

    assert payload["what_failed"] == ""
    assert failures and failures[0].startswith("incident_view:")


@pytest.mark.asyncio
async def test_get_llm_consumes_the_resolved_endpoint_as_one_unit(monkeypatch) -> None:
    from unittest.mock import MagicMock

    from app.services import ai_config_resolver, llm_policy_service
    from app.services.llm_factory import get_llm

    constructor = MagicMock(return_value=SimpleNamespace())
    monkeypatch.setattr("langchain_ollama.ChatOllama", constructor)
    monkeypatch.setattr(
        ai_config_resolver,
        "get_effective_ai_config",
        AsyncMock(
            return_value={
                "provider": "openai",
                "model": "global-model",
                "temperature": 0.9,
                "max_tokens": 99,
                "base_url": "https://global.invalid",
                "offline_mode": False,
            }
        ),
    )
    monkeypatch.setattr(
        llm_policy_service,
        "enforce_provider_policy_async",
        AsyncMock(),
    )

    await get_llm(endpoint=_endpoint("small"))

    kwargs = constructor.call_args.kwargs
    assert kwargs["model"] == "small"
    assert kwargs["base_url"] == "http://127.0.0.1:11434"
    assert kwargs["temperature"] == 0.1
    assert kwargs["num_predict"] == 2048
