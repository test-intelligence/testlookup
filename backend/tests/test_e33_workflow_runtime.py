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
    plan = {"stages": [{"stage": "first_ingest"}], "workflow_ref": "wf.runtime.test@4"}
    metadata = {
        "workflow_id": "wf.runtime.test",
        "workflow_version": 4,
        "workflow_ref": "wf.runtime.test@4",
        "workflow_definition": snapshot,
        "workflow_definition_sha256": workflow._definition_checksum(snapshot),
        "workflow_plan_sha256": compute_workflow_plan_hash(plan),
    }
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


def test_compile_frozen_workflow_refuses_tampered_snapshot() -> None:
    snapshot = _body().model_dump(mode="json", by_alias=True)
    setup = {
        "workflow_definition": snapshot,
        "workflow_definition_sha256": workflow._definition_checksum(snapshot),
    }
    assert workflow._compile_frozen_workflow(setup) is not None

    tampered = deepcopy(setup)
    tampered["workflow_definition"]["deadline_seconds"] = 2

    with pytest.raises(ValueError, match="snapshot_mismatch"):
        workflow._compile_frozen_workflow(tampered)


def test_checkpoint_restore_requires_exact_workflow_authority() -> None:
    metadata = {
        "workflow_ref": "wf.runtime.test@4",
        "workflow_definition_sha256": "definition-4",
        "workflow_plan_sha256": "plan-4",
    }
    assert workflow._checkpoint_authority_matches(
        metadata,
        workflow_ref="wf.runtime.test@4",
        workflow_definition_sha256="definition-4",
        workflow_plan_sha256="plan-4",
    )
    for key, value in (
        ("workflow_ref", "wf.runtime.test@5"),
        ("workflow_definition_sha256", "definition-5"),
        ("workflow_plan_sha256", "plan-5"),
    ):
        changed = dict(metadata)
        changed[key] = value
        assert not workflow._checkpoint_authority_matches(
            changed,
            workflow_ref="wf.runtime.test@4",
            workflow_definition_sha256="definition-4",
            workflow_plan_sha256="plan-4",
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
        "step_name": "summary",
        "output": {
            "summary_markdown": "verified",
            "tools_used": ["list_run_failures"],
        },
        "mode": "act",
        "tools_used": ["list_run_failures"],
        "tool_permissions": {"list_run_failures": "read_only"},
    }
    assert seen[0]["references"] == {
        "test_case_ids": ["case-1"],
        "cluster_ids": ["cluster-1"],
        "artifact_ids": [],
    }
