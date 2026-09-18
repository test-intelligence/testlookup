from __future__ import annotations

from copy import deepcopy

import pytest

from app.agents import workflow
from app.agents.workflow_compiler import compile_workflow
from app.services.agent_planner import (
    attach_workflow_plan_and_verification,
    compute_workflow_plan_hash,
)
from app.services.workflow_definition_service import WorkflowBodyV1


def _body() -> WorkflowBodyV1:
    return WorkflowBodyV1.model_validate({
        "workflow_id": "wf.runtime.test",
        "name": "Runtime test",
        "base": "offline",
        "steps": [
            {"id": "first_ingest", "agent_id": "agent.ingestion.v1"},
            {"id": "second_ingest", "agent_id": "agent.ingestion.v1"},
        ],
        "edges": [{"from": "first_ingest", "to": "second_ingest"}],
    })


def _authority(snapshot: dict, plan: dict) -> dict:
    project_id = "00000000-0000-0000-0000-000000000004"
    workflow_id = "wf.runtime.test"
    workflow_version = 4
    workflow_ref = f"{workflow_id}@{workflow_version}"
    agent_configs: dict = {}
    resolved_agent_configs: dict = {}
    plan_sha256 = compute_workflow_plan_hash(plan)
    return {
        "project_id": project_id,
        "workflow_id": workflow_id,
        "workflow_version": workflow_version,
        "workflow_ref": workflow_ref,
        "workflow_definition": snapshot,
        "workflow_definition_sha256": workflow._definition_checksum(snapshot),
        "workflow_plan_sha256": plan_sha256,
        "workflow_agent_configs": agent_configs,
        "resolved_agent_configs": resolved_agent_configs,
        "workflow_runtime_authority_sha256": workflow._workflow_runtime_authority_checksum(
            project_id=project_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            workflow_ref=workflow_ref,
            definition=snapshot,
            plan_sha256=plan_sha256,
            agent_configs=agent_configs,
            resolved_agent_configs=resolved_agent_configs,
        ),
    }


@pytest.mark.asyncio
async def test_compiler_prefers_step_specific_executor_for_repeated_capability() -> None:
    calls: list[str] = []

    async def first(_state):
        calls.append("first")
        return {}

    async def second(_state):
        calls.append("second")
        return {}

    async def wrong(_state):
        calls.append("capability")
        return {}

    graph = compile_workflow(
        _body(),
        node_executors={
            "first_ingest": first,
            "second_ingest": second,
            "agent.ingestion.v1": wrong,
        },
    ).graph.compile()

    await graph.ainvoke({})
    assert calls == ["first", "second"]


def test_resume_authority_rejects_definition_plan_and_version_mutations() -> None:
    snapshot = _body().model_dump(mode="json", by_alias=True)
    plan = {
        "workflow_id": "wf.runtime.test",
        "workflow_version": 4,
        "workflow_ref": "wf.runtime.test@4",
        "stages": [{"stage": "first_ingest"}],
    }
    metadata = _authority(snapshot, plan)
    assert workflow._frozen_workflow_authority_valid(metadata, plan)

    changed = deepcopy(metadata)
    changed["workflow_version"] = 5
    assert not workflow._frozen_workflow_authority_valid(changed, plan)

    changed = deepcopy(metadata)
    changed["workflow_definition"]["deadline_seconds"] = 1
    assert not workflow._frozen_workflow_authority_valid(changed, plan)

    changed_plan = deepcopy(plan)
    changed_plan["stages"].append({"stage": "second_ingest"})
    assert not workflow._frozen_workflow_authority_valid(metadata, changed_plan)

    changed = deepcopy(metadata)
    changed["workflow_agent_configs"]["agent.ingestion.v1"] = {"mode": "act"}
    assert not workflow._frozen_workflow_authority_valid(changed, plan)

    coordinated = deepcopy(metadata)
    coordinated["workflow_version"] = 5
    coordinated["workflow_ref"] = "wf.runtime.test@5"
    coordinated_plan = deepcopy(plan)
    coordinated_plan["workflow_version"] = 5
    coordinated_plan["workflow_ref"] = "wf.runtime.test@5"
    coordinated["workflow_plan_sha256"] = compute_workflow_plan_hash(coordinated_plan)
    assert not workflow._frozen_workflow_authority_valid(coordinated, coordinated_plan)


def test_compile_frozen_workflow_refuses_tampered_snapshot() -> None:
    snapshot = _body().model_dump(mode="json", by_alias=True)
    plan = {
        "workflow_id": "wf.runtime.test",
        "workflow_version": 4,
        "workflow_ref": "wf.runtime.test@4",
        "stages": [{"stage": "first_ingest"}],
    }
    setup = _authority(snapshot, plan)
    assert workflow._compile_frozen_workflow(setup) is not None

    tampered = deepcopy(setup)
    tampered["workflow_definition"]["deadline_seconds"] = 2

    with pytest.raises(ValueError, match="snapshot_mismatch"):
        workflow._compile_frozen_workflow(tampered)


def test_custom_step_tools_narrow_the_frozen_project_allowlist() -> None:
    state = {
        "initial_workflow_plan": {
            "workflow_id": "wf.runtime.test",
            "stages": [{
                "stage": "named_summary",
                "capability_id": "agent.summary.v1",
                "tools": ["list_run_failures"],
            }],
        },
        "workflow_agent_configs": {
            "agent.summary.v1": {
                "tools": {"allowlist": ["list_run_failures", "search_logs"]},
            },
        },
    }

    assert workflow._stage_tool_allowlist(state, "named_summary") == [
        "list_run_failures"
    ]


def test_runtime_state_carries_frozen_tool_and_model_authority() -> None:
    setup = {
        "workflow_agent_configs": {"agent.summary.v1": {"mode": "act"}},
        "resolved_agent_configs": {
            "agent.summary.v1": {"config": {"model": {"tier": "slm"}}}
        },
    }

    assert workflow._workflow_runtime_state(setup) == setup


def test_checkpoint_restore_requires_exact_workflow_authority() -> None:
    metadata = {
        "workflow_ref": "wf.runtime.test@4",
        "workflow_definition_sha256": "definition-4",
        "workflow_plan_sha256": "plan-4",
        "workflow_runtime_authority_sha256": "runtime-4",
    }
    assert workflow._checkpoint_authority_matches(
        metadata,
        workflow_ref="wf.runtime.test@4",
        workflow_definition_sha256="definition-4",
        workflow_plan_sha256="plan-4",
        workflow_runtime_authority_sha256="runtime-4",
    )
    for key, value in (
        ("workflow_ref", "wf.runtime.test@5"),
        ("workflow_definition_sha256", "definition-5"),
        ("workflow_plan_sha256", "plan-5"),
        ("workflow_runtime_authority_sha256", "runtime-5"),
    ):
        changed = dict(metadata)
        changed[key] = value
        assert not workflow._checkpoint_authority_matches(
            changed,
            workflow_ref="wf.runtime.test@4",
            workflow_definition_sha256="definition-4",
            workflow_plan_sha256="plan-4",
            workflow_runtime_authority_sha256="runtime-4",
        )


def test_custom_plan_is_not_replaced_by_its_builtin_base_at_finalization() -> None:
    plan = {
        "workflow_id": "wf.runtime.test",
        "workflow_ref": "wf.runtime.test@4",
        "stages": [
            {
                "stage": "first_ingest",
                "planned": True,
                "required": False,
                "dependencies": [],
                "rationale": "published topology",
            }
        ],
    }
    plan["plan_sha256"] = compute_workflow_plan_hash(plan)
    result = attach_workflow_plan_and_verification(
        {
            "initial_workflow_plan": plan,
            "completed_stages": ["first_ingest"],
            "skipped_stages": [],
            "errors": [],
        },
        workflow_type="offline",
    )
    assert result["workflow_plan"]["workflow_ref"] == "wf.runtime.test@4"
    assert [item["stage"] for item in result["workflow_plan"]["stages"]] == [
        "first_ingest"
    ]


def test_named_step_outputs_use_instance_identity_for_verification_and_contracts() -> None:
    result = workflow._normalize_step_result_identity(
        {
            "completed_stages": ["ingestion"],
            "skipped_stages": ["ingestion"],
            "current_stage": "ingestion",
            "stage_errors": {"ingestion": ["example"]},
            "stage_metrics": {"ingestion": {"latency_ms": 1}},
            "agent_contracts": {"ingestion": {"schema": "v1"}},
        },
        step_id="first_ingest",
        capability_stage="ingestion",
    )

    assert result == {
        "completed_stages": ["first_ingest"],
        "skipped_stages": ["first_ingest"],
        "current_stage": "first_ingest",
        "stage_errors": {"first_ingest": ["example"]},
        "stage_metrics": {"first_ingest": {"latency_ms": 1}},
        "agent_contracts": {"first_ingest": {"schema": "v1"}},
    }


@pytest.mark.asyncio
async def test_runtime_reviewer_binds_declared_output_and_frozen_policy(monkeypatch) -> None:
    from app.agents.reviewer_agent import ReviewerAgent

    body = WorkflowBodyV1.model_validate({
        "workflow_id": "wf.runtime.review",
        "name": "Review binding",
        "base": "offline",
        "steps": [
            {
                "id": "named_summary",
                "agent_id": "agent.summary.v1",
                "tools": ["list_run_failures"],
            },
            {
                "id": "review_summary",
                "agent_id": "agent.reviewer.v1",
                "reviews": ["named_summary"],
            },
        ],
        "edges": [{"from": "named_summary", "to": "review_summary"}],
    })
    seen: list[dict] = []

    async def capture(_self, state):
        seen.append(state["reviewer_input"])
        return {"current_stage": "reviewer"}

    monkeypatch.setattr(ReviewerAgent, "run", capture)
    executors = workflow._workflow_runtime_executors(
        body,
        {"agent.summary.v1": {"mode": "act"}},
        {
            "agent.summary.v1": {
                "config": {
                    "model": {
                        "tier": "slm",
                        "slm": {"provider": "ollama", "model": "qwen2.5:7b"},
                    }
                }
            }
        },
    )
    raw = executors["review_summary"].__workflow_original_node__
    await raw({
        "_workflow_step_outputs": {
            "named_summary": {
                "summary_markdown": "verified",
                "tools_used": ["list_run_failures"],
            }
        },
        "failed_test_ids": ["case-1"],
        "failure_clusters": [{"cluster_id": "cluster-1"}],
        "total_tests": 5,
        "pass_rate": 0.8,
        "test_run_data": {},
        "analyses": {},
    })
    reviewed = seen[0]["reviewed_steps"][0]
    assert reviewed == {
        "step_name": "named_summary",
        "output": {
            "summary_markdown": "verified",
            "tools_used": ["list_run_failures"],
        },
        "mode": "act",
        "tools_used": ["list_run_failures"],
        "tool_permissions": {"list_run_failures": "read_only"},
        "model_tier": "slm",
        "model_provider": "ollama",
        "model_name": "qwen2.5:7b",
    }
    assert seen[0]["references"] == {
        "test_case_ids": ["case-1"],
        "cluster_ids": ["cluster-1"],
        "artifact_ids": [],
    }


@pytest.mark.asyncio
async def test_runtime_reviewer_applies_loop_count_and_stops_on_final_rejection(
    monkeypatch,
) -> None:
    from app.agents.reviewer_agent import ReviewerAgent

    body = WorkflowBodyV1.model_validate({
        "workflow_id": "wf.runtime.review_reject",
        "name": "Review reject",
        "base": "offline",
        "steps": [
            {"id": "summary", "agent_id": "agent.summary.v1"},
            {
                "id": "review",
                "agent_id": "agent.reviewer.v1",
                "reviews": ["summary"],
            },
        ],
        "edges": [
            {"from": "summary", "to": "review"},
            {"from": "review", "to": "__end__"},
        ],
        "loops": [{
            "from": "review",
            "to": "summary",
            "when": {"field": "supervisor_route", "op": "eq", "value": "retry"},
            "max_iterations": 1,
        }],
    })
    seen: list[dict] = []

    async def reject(_self, state):
        seen.append(state)
        return {
            "supervisor": {"route": "finalize", "error_code": "validation_failed"},
            "review_verdict": {"verdict": "reject"},
        }

    monkeypatch.setattr(ReviewerAgent, "run", reject)
    raw = workflow._workflow_runtime_executors(body)["review"].__workflow_original_node__

    with pytest.raises(ValueError, match="validation_failed"):
        await raw({
            "_workflow_loop_iterations": {"loop_0": 1},
            "_workflow_step_outputs": {"summary": {}},
            "failed_test_ids": [],
            "failure_clusters": [],
            "test_run_data": {},
            "analyses": {},
        })

    assert seen[0]["review_retry_count"] == 1
    assert seen[0]["review_max_iterations"] == 1


@pytest.mark.asyncio
async def test_runtime_shares_step_budget_and_applies_reviewer_tier_override(
    monkeypatch,
) -> None:
    from app.agents.reviewer_agent import ReviewerAgent
    from app.services.step_llm_budget import StepLLMBudget

    body = WorkflowBodyV1.model_validate({
        "workflow_id": "wf.runtime.review_retry",
        "name": "Review retry authority",
        "base": "offline",
        "steps": [
            {"id": "named_summary", "agent_id": "agent.summary.v1"},
            {
                "id": "review_summary",
                "agent_id": "agent.reviewer.v1",
                "reviews": ["named_summary"],
            },
        ],
        "edges": [
            {"from": "named_summary", "to": "review_summary"},
            {"from": "review_summary", "to": "__end__"},
        ],
        "loops": [{
            "from": "review_summary",
            "to": "named_summary",
            "when": {"field": "supervisor_route", "op": "eq", "value": "retry"},
            "max_iterations": 1,
        }],
    })
    producer_states: list[dict] = []

    async def producer(state):
        producer_states.append(state)
        return {
            "summary_markdown": "candidate",
            "step_llm_budget": state["step_llm_budget"],
        }

    async def retry(_self, state):
        budget = StepLLMBudget.from_state(state["step_llm_budget"])
        assert budget.consume("review_retry") is True
        return {
            "review_verdict": {"verdict": "retry"},
            "supervisor": {"route": "retry", "tier_override": "llm"},
            "step_llm_budget": budget.as_state(),
        }

    monkeypatch.setattr(
        workflow,
        "workflow_node_executors",
        lambda: {"agent.summary.v1": producer},
    )
    monkeypatch.setattr(ReviewerAgent, "run", retry)
    executors = workflow._workflow_runtime_executors(
        body,
        {
            "agent.summary.v1": {
                "model": {"escalation": {"max_escalations": 1}},
                "budget": {"max_llm_calls_per_run": 5},
            }
        },
    )
    target = executors["named_summary"].__workflow_original_node__
    reviewer = executors["review_summary"].__workflow_original_node__
    base = {
        "_workflow_step_outputs": {},
        "_workflow_step_llm_budgets": {},
        "_workflow_tier_overrides": {},
        "_workflow_loop_iterations": {},
        "failed_test_ids": [],
        "failure_clusters": [],
        "test_run_data": {},
        "analyses": {},
    }

    first = await target(base)
    reviewed = await reviewer({
        **base,
        **first,
        "_workflow_step_outputs": {"named_summary": first},
    })
    second = await target({**base, **first, **reviewed})

    assert producer_states[0]["step_llm_budget"] == {
        "limit": 2, "used": 0, "remaining": 2, "reasons": []
    }
    assert producer_states[1]["model_tier_override"] == "llm"
    assert producer_states[1]["step_llm_budget"]["used"] == 1
    assert producer_states[1]["step_llm_budget"]["reasons"] == ["review_retry"]
    assert second["_workflow_step_llm_budgets"]["named_summary"]["used"] == 1
