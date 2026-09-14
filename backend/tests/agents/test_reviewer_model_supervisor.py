"""T16 regressions for bounded reviewer model checks and supervisor routing."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents.reviewer_agent import ReviewerAgent
from app.models.agent_contracts import ReviewerSupervisorDecisionV1, ReviewVerdictV1
from app.services.agent_capability_registry import CAPABILITY_REGISTRY
from app.services.agent_config_resolver import ResolvedAgentConfig, ResolvedEndpoint
from app.services.agent_config_service import default_config
from app.services.model_router import ModelChoice, decide_escalation
from app.services.review_supervisor import supervise_review
from app.services.step_llm_budget import StepLLMBudget


def _endpoint(model: str) -> ResolvedEndpoint:
    return ResolvedEndpoint(
        provider="ollama", model=model, temperature=0.0, max_tokens=256,
        source="project",
    )


def _resolved(*, second_model: bool = True) -> ResolvedAgentConfig:
    capability = CAPABILITY_REGISTRY["reviewer"]
    config = default_config(capability.capability_id)
    config.review.second_model_check = second_model
    config.review.auto_reviewer = True
    config.review.policy = "human_required_plus_auto_reviewer"
    return ResolvedAgentConfig(
        agent_id=capability.capability_id,
        source="default",
        config_version=0,
        config=config,
        endpoints={"slm": _endpoint("qwen-7b"), "llm": _endpoint("llama-70b")},
        offline_mode=True,
        offline_mode_source="env",
        offline_mode_env_pinned=True,
    )


def _payload() -> dict:
    return {
        "reviewed_steps": [{
            "step_name": "root_cause_analysis",
            "output": {
                "contract": {
                    "agent_name": "root_cause_analysis",
                    "confidence_score": 80,
                    "evidence_count": 0,
                    "output_keys": ["analyses"],
                },
                "analyses": {
                    "tc-1": {
                        "is_flaky": False,
                        "confidence_score": 80,
                        "narrative": "failed_tests is 2",
                        "chain_of_thought": "private scratch work",
                    }
                },
            },
            "model_tier": "slm",
            "model_provider": "ollama",
            "model_name": "qwen-7b",
        }],
        "references": {"test_case_ids": ["tc-1"]},
        "numeric_facts": {"failed_tests": 2},
        "analyses": {
            "tc-1": {
                "is_flaky": False,
                "confidence_score": 80,
                "reasoning_trace": "private evidence scratch work",
            }
        },
    }


class _Response:
    def __init__(self, content: str):
        self.content = content


class _LLM:
    def __init__(self, response: str, prompts: list[str]):
        self.response = response
        self.prompts = prompts

    async def ainvoke(self, prompt: str):
        self.prompts.append(prompt)
        return _Response(self.response)


async def _quiet_agent(monkeypatch, resolved: ResolvedAgentConfig) -> ReviewerAgent:
    agent = ReviewerAgent()

    async def config(_state):
        return resolved

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent, "_resolved_config", config)
    monkeypatch.setattr(agent, "mark_stage_running", noop)
    monkeypatch.setattr(agent, "mark_stage_done", noop)
    monkeypatch.setattr(agent, "log_decision", noop)
    return agent


@pytest.mark.asyncio
async def test_model_families_use_distinct_endpoints_and_hide_reasoning(monkeypatch) -> None:
    prompts: list[str] = []
    responses = iter([
        '{"reference_ids":["tc-1"],"numeric_facts":{"failed_tests":2}}',
        '{"agreement_score":0.91,"unsupported_claims":[],"blocking_unsupported_claims":[]}',
    ])

    async def fake_get_llm(*, endpoint):
        assert endpoint.model in {"qwen-7b", "llama-70b"}
        return _LLM(next(responses), prompts)

    monkeypatch.setattr("app.agents.reviewer_agent.get_llm", fake_get_llm)
    agent = await _quiet_agent(monkeypatch, _resolved())
    result = await agent.run({
        "reviewer_input": _payload(),
        "step_llm_budget": {"limit": 2, "used": 0, "reasons": []},
    })

    assert result["review_verdict"]["verdict"] == "pass"
    assert {check["family"] for check in result["review_verdict"]["checks"]} == {1, 2, 3, 4, 5}
    assert result["review_verdict"]["second_model"] == {
        "provider": "ollama", "model": "llama-70b", "agreement_score": 0.91,
    }
    assert result["step_llm_budget"]["remaining"] == 0
    assert all("private scratch work" not in prompt for prompt in prompts)
    assert all("private evidence scratch work" not in prompt for prompt in prompts)


@pytest.mark.asyncio
async def test_unsupported_blocking_claim_retries_once_then_rejects(monkeypatch) -> None:
    responses = iter([
        '{"reference_ids":["tc-1"],"numeric_facts":{"failed_tests":2}}',
        '{"agreement_score":0.2,"unsupported_claims":[],"blocking_unsupported_claims":["release is safe"]}',
    ])

    async def fake_get_llm(*, endpoint):
        return _LLM(next(responses), [])

    monkeypatch.setattr("app.agents.reviewer_agent.get_llm", fake_get_llm)
    agent = await _quiet_agent(monkeypatch, _resolved())
    first = await agent.run({
        "reviewer_input": _payload(),
        "step_llm_budget": {"limit": 3, "used": 0, "reasons": []},
    })
    assert first["review_verdict"]["verdict"] == "retry"
    assert first["supervisor"] == {
        "route": "retry", "reviewed_steps": ["root_cause_analysis"],
        "tier_override": "llm", "status": None, "error_code": None,
        "requires_human_review": True, "retry_count": 1,
    }

    retry_verdict = ReviewVerdictV1.model_validate(first["review_verdict"])
    budget = StepLLMBudget(limit=3, used=2)
    rejected, route = supervise_review(retry_verdict, retry_count=1, budget=budget)
    assert rejected.verdict == "reject"
    assert route.route == "finalize"
    assert (route.status, route.error_code) == ("failed", "validation_failed")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extraction", "offender"),
    [
        (
            '{"reference_ids":["orphan-test"],"numeric_facts":{"failed_tests":2}}',
            "reference:orphan-test",
        ),
        (
            '{"reference_ids":["tc-1"],"numeric_facts":{"failed_tests":9}}',
            "numeric:failed_tests:9!=2",
        ),
    ],
)
async def test_family_three_flags_model_extracted_claims_absent_from_state(
    monkeypatch, extraction: str, offender: str
) -> None:
    responses = iter([
        extraction,
        '{"agreement_score":0.9,"unsupported_claims":[],"blocking_unsupported_claims":[]}',
    ])

    async def fake_get_llm(*, endpoint):
        return _LLM(next(responses), [])

    monkeypatch.setattr("app.agents.reviewer_agent.get_llm", fake_get_llm)
    agent = await _quiet_agent(monkeypatch, _resolved())
    result = await agent.run({
        "reviewer_input": _payload(),
        "step_llm_budget": {"limit": 2, "used": 0, "reasons": []},
    })

    family_three = next(
        check for check in result["review_verdict"]["checks"] if check["family"] == 3
    )
    assert result["review_verdict"]["verdict"] == "pass_with_flags"
    assert family_three["passed"] is False
    assert offender in family_three["offending_refs"]


@pytest.mark.asyncio
async def test_low_second_model_agreement_flags_without_retry(monkeypatch) -> None:
    responses = iter([
        '{"reference_ids":["tc-1"],"numeric_facts":{"failed_tests":2}}',
        '{"agreement_score":0.69,"unsupported_claims":[],"blocking_unsupported_claims":[]}',
    ])

    async def fake_get_llm(*, endpoint):
        return _LLM(next(responses), [])

    monkeypatch.setattr("app.agents.reviewer_agent.get_llm", fake_get_llm)
    agent = await _quiet_agent(monkeypatch, _resolved())
    result = await agent.run({
        "reviewer_input": _payload(),
        "step_llm_budget": {"limit": 2, "used": 0, "reasons": []},
    })

    assert result["review_verdict"]["verdict"] == "pass_with_flags"
    assert result["supervisor"]["route"] == "continue"
    assert result["review_verdict"]["second_model"]["agreement_score"] == 0.69


@pytest.mark.asyncio
async def test_exhausted_shared_budget_caps_verdict_at_flags_without_call(monkeypatch) -> None:
    async def forbidden(**_kwargs):  # pragma: no cover - mutation must not reach this
        raise AssertionError("model call escaped exhausted step budget")

    monkeypatch.setattr("app.agents.reviewer_agent.get_llm", forbidden)
    agent = await _quiet_agent(monkeypatch, _resolved())
    result = await agent.run({
        "reviewer_input": _payload(),
        "step_llm_budget": {"limit": 2, "used": 2, "reasons": ["workflow_loop", "model_escalation"]},
    })

    assert result["review_verdict"]["verdict"] == "pass_with_flags"
    assert result["supervisor"]["route"] == "continue"
    assert result["supervisor"]["requires_human_review"] is True
    assert result["step_llm_budget"]["reasons"] == ["workflow_loop", "model_escalation"]


@pytest.mark.asyncio
async def test_second_model_refuses_generator_identity_reuse(monkeypatch) -> None:
    resolved = _resolved()
    resolved.endpoints["llm"] = _endpoint("QWEN-7B")
    calls = 0

    async def fake_get_llm(*, endpoint):
        nonlocal calls
        calls += 1
        return _LLM(
            '{"reference_ids":["tc-1"],"numeric_facts":{"failed_tests":2}}', []
        )

    monkeypatch.setattr("app.agents.reviewer_agent.get_llm", fake_get_llm)
    agent = await _quiet_agent(monkeypatch, resolved)
    result = await agent.run({
        "reviewer_input": _payload(),
        "step_llm_budget": {"limit": 2, "used": 0, "reasons": []},
    })

    assert calls == 1  # family 3 only
    family_four = next(
        check for check in result["review_verdict"]["checks"] if check["family"] == 4
    )
    assert family_four["name"] == "independent_second_model"
    assert family_four["passed"] is False


@pytest.mark.asyncio
async def test_unauthorized_tool_finding_emits_incident_decision(monkeypatch) -> None:
    agent = ReviewerAgent()
    decisions: list[tuple[str, str]] = []

    async def config(_state):
        return None

    async def noop(*_args, **_kwargs):
        return None

    async def decision(_run_id, point, chosen, _rationale, **_kwargs):
        decisions.append((point, chosen))

    monkeypatch.setattr(agent, "_resolved_config", config)
    monkeypatch.setattr(agent, "mark_stage_running", noop)
    monkeypatch.setattr(agent, "mark_stage_done", noop)
    monkeypatch.setattr(agent, "log_decision", decision)
    payload = _payload()
    payload["reviewed_steps"][0].update({
        "tools_used": ["write_issue"],
        "tool_permissions": {"write_issue": "mutating"},
        "mode": "suggest",
    })

    result = await agent.run({"pipeline_run_id": "run-incident", "reviewer_input": payload})

    assert result["review_verdict"]["verdict"] == "reject"
    assert decisions[0] == ("reviewer_incident", "policy_denied")
    assert decisions[1] == ("deterministic_review", "reject")


def test_shared_budget_is_consumed_by_escalation_and_supervisor_retry() -> None:
    budget = StepLLMBudget.create(
        max_escalations=1, max_iterations=1, run_llm_calls_remaining=9
    )
    resolved = _resolved(second_model=False)
    resolved.config.model.tier = "auto"
    decision = decide_escalation(
        "root_cause_analysis",
        ModelChoice("slm", resolved.endpoints["slm"], "tier"),
        resolved,
        trigger="low_confidence",
        confidence=10,
        escalations=0,
        step_llm_calls_remaining=99,
        budget_remaining_usd=1.0,
        step_budget=budget,
    )
    assert decision.action == "escalate"
    assert budget.as_state() == {
        "limit": 2, "used": 1, "remaining": 1, "reasons": ["model_escalation"]
    }

    retry = ReviewVerdictV1(
        reviewed_steps=["root_cause_analysis"], checks=[], verdict="retry",
        hallucination_risk="high", requires_human_review=True,
    )
    _, route = supervise_review(retry, retry_count=0, budget=budget)
    assert route.route == "retry"
    assert budget.remaining == 0
    _, exhausted = supervise_review(retry, retry_count=0, budget=budget)
    assert exhausted.route == "finalize"


def test_supervisor_contract_rejects_incoherent_routes() -> None:
    with pytest.raises(ValidationError):
        ReviewerSupervisorDecisionV1(
            route="retry", reviewed_steps=["summary"], retry_count=1
        )


def test_step_budget_rejects_forged_state() -> None:
    with pytest.raises(ValueError):
        StepLLMBudget.from_state({"limit": 1, "used": 2, "reasons": []})
