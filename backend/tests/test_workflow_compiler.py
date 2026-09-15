from __future__ import annotations

from copy import deepcopy
import random

import pytest

from app.agents import workflow
from app.agents.workflow_compiler import (
    WorkflowCompileError,
    compile_workflow,
    evaluate_condition,
    validate_workflow,
)
from app.services import workflow_definition_service as definitions
from app.services.agent_config_service import default_config


def _body(workflow_id: str = "wf.custom.compiler") -> definitions.WorkflowBodyV1:
    return definitions.WorkflowBodyV1.model_validate({
        "workflow_id": workflow_id,
        "name": "Compiler test",
        "base": "offline",
        "steps": [
            {"id": "ingest", "agent_id": "agent.ingestion.v1"},
            {"id": "summary", "agent_id": "agent.summary.v1"},
        ],
        "edges": [{"from": "ingest", "to": "summary"}],
    })


def _noop_executors(body: definitions.WorkflowBodyV1) -> dict[str, object]:
    async def execute(_state):
        return {}

    return {step.agent_id: execute for step in body.steps}


def _topology(graph) -> tuple[set[str], set[tuple[str, str]], dict[str, set[str]]]:
    branches = {
        source: {
            destination
            for branch in source_branches.values()
            for destination in (branch.ends or {}).values()
        }
        for source, source_branches in graph.branches.items()
    }
    return set(graph.nodes), set(graph.edges), branches


@pytest.mark.parametrize(
    ("workflow_id", "legacy_builder"),
    [
        ("offline", workflow._build_offline_graph),
        ("deep", workflow._build_deep_graph),
        ("live", workflow._build_live_graph),
    ],
)
def test_builtin_definitions_compile_to_the_live_graph_topology(
    workflow_id: str, legacy_builder,
) -> None:
    body = definitions.body_from_item(definitions.builtin(workflow_id))
    compiled = compile_workflow(body, node_executors=workflow.workflow_node_executors())

    assert _topology(compiled.graph) == _topology(legacy_builder())


def test_compiler_rejects_unknown_capability_and_missing_dependency() -> None:
    document = _body().model_dump(mode="json", by_alias=True)
    document["steps"][1]["agent_id"] = "agent.unknown.v1"
    unknown = definitions.WorkflowBodyV1.model_validate(document)
    assert any(
        "unknown capability 'agent.unknown.v1'" in error
        for error in validate_workflow(unknown).errors
    )

    document = _body().model_dump(mode="json", by_alias=True)
    document["steps"] = [
        {"id": "summary", "agent_id": "agent.summary.v1"},
        {"id": "triage", "agent_id": "agent.triage.v1"},
    ]
    document["edges"] = [{"from": "summary", "to": "triage"}]
    missing = definitions.WorkflowBodyV1.model_validate(document)
    errors = validate_workflow(missing).errors
    assert any("summary requires upstream capability 'ingestion'" in error for error in errors)
    assert any("triage requires upstream capability 'root_cause_analysis'" in error for error in errors)


def test_dependency_must_dominate_every_path() -> None:
    document = _body().model_dump(mode="json", by_alias=True)
    document["steps"] = [
        {"id": "ingest", "agent_id": "agent.ingestion.v1"},
        {"id": "analysis", "agent_id": "agent.root_cause_analysis.v1"},
        {"id": "triage", "agent_id": "agent.triage.v1"},
    ]
    document["edges"] = [
        {"from": "ingest", "to": "analysis"},
        {"from": "ingest", "to": "triage"},
        {"from": "analysis", "to": "triage"},
    ]
    body = definitions.WorkflowBodyV1.model_validate(document)

    assert any(
        "triage requires upstream capability 'root_cause_analysis' on every path" in error
        for error in validate_workflow(body).errors
    )


def test_same_capability_can_appear_at_multiple_named_steps() -> None:
    document = _body().model_dump(mode="json", by_alias=True)
    document["steps"].append({
        "id": "second_summary", "agent_id": "agent.summary.v1",
    })
    document["edges"].append({"from": "summary", "to": "second_summary"})

    body = definitions.WorkflowBodyV1.model_validate(document)

    assert validate_workflow(body).valid is True


def test_compiler_rejects_cycles_except_bounded_declared_loops() -> None:
    document = _body().model_dump(mode="json", by_alias=True)
    document["edges"].append({"from": "summary", "to": "ingest"})
    cyclic = definitions.WorkflowBodyV1.model_validate(document)
    assert "workflow edges contain a cycle; declare bounded back-edges in loops" in validate_workflow(cyclic).errors

    document = _body().model_dump(mode="json", by_alias=True)
    document["loops"] = [{
        "from": "summary",
        "to": "ingest",
        "when": {"field": "fallback_used", "op": "is_true"},
        "max_iterations": 2,
    }]
    bounded = definitions.WorkflowBodyV1.model_validate(document)
    assert validate_workflow(bounded).valid is True


def test_policy_checks_capability_mode_and_tool_allowlist() -> None:
    document = _body().model_dump(mode="json", by_alias=True)
    document["steps"] = [{
        "id": "defect", "agent_id": "agent.defect_commander.v1",
        "tools": ["list_run_failures"],
    }]
    document["edges"] = []
    body = definitions.WorkflowBodyV1.model_validate(document)
    config = default_config("agent.defect_commander.v1")
    config = config.model_copy(update={
        "enabled": True,
        "tools": config.tools.model_copy(update={"allowlist": []}),
    })

    errors = validate_workflow(body, agent_configs={config.agent_id: config}).errors
    assert any("permission 'mutating' is not allowed by mode 'shadow'" in error for error in errors)
    assert any("tool 'list_run_failures' is not in the configured allowlist" in error for error in errors)


def test_typed_conditions_validate_names_enums_types_and_limits() -> None:
    document = _body().model_dump(mode="json", by_alias=True)
    document["edges"] = [
        {
            "from": "ingest", "to": "summary",
            "when": {"field": "workflow_type", "op": "eq", "value": "invalid"},
        },
        {"from": "ingest", "to": "__end__", "when": {"op": "else"}},
    ]
    enum_error = definitions.WorkflowBodyV1.model_validate(document)
    assert any("outside workflow_type's enum" in error for error in validate_workflow(enum_error).errors)

    document["edges"][0]["when"] = {
        "field": "execution_path", "op": "eq", "value": "executed",
    }
    valid_execution_path = definitions.WorkflowBodyV1.model_validate(document)
    assert validate_workflow(valid_execution_path).valid is True

    document["edges"][0]["when"]["value"] = "AI_PRIMARY"
    invalid_execution_path = definitions.WorkflowBodyV1.model_validate(document)
    assert any(
        "outside execution_path's enum" in error
        for error in validate_workflow(invalid_execution_path).errors
    )

    document["edges"][0]["when"] = {"field": "missing", "op": "eq", "value": 1}
    unknown_field = definitions.WorkflowBodyV1.model_validate(document)
    assert any("unknown state field 'missing'" in error for error in validate_workflow(unknown_field).errors)

    document["edges"][0]["when"] = {
        "field": "total_tests", "op": "in", "value": list(range(51)),
    }
    oversized = definitions.WorkflowBodyV1.model_validate(document)
    assert any("at most 50" in error for error in validate_workflow(oversized).errors)

    document["edges"][0]["when"] = {
        "field": "total_tests", "op": "in", "value": ["one"],
    }
    wrong_in_type = definitions.WorkflowBodyV1.model_validate(document)
    assert any("in values must match the number field" in error for error in validate_workflow(wrong_in_type).errors)

    document["edges"][0]["when"] = {
        "field": "failed_test_ids", "op": "eq", "value": [],
    }
    collection_comparison = definitions.WorkflowBodyV1.model_validate(document)
    assert any("use a count operator" in error for error in validate_workflow(collection_comparison).errors)

    nested = {"field": "fallback_used", "op": "is_true"}
    for _ in range(6):
        nested = {"not": nested}
    document["edges"][0]["when"] = nested
    too_deep = definitions.WorkflowBodyV1.model_validate(document)
    assert any("depth exceeds 6" in error for error in validate_workflow(too_deep).errors)

    document["edges"][0]["when"] = {
        "all": [
            {"field": "fallback_used", "op": "is_true"}
            for _ in range(33)
        ]
    }
    too_wide = definitions.WorkflowBodyV1.model_validate(document)
    assert any("more than 32 leaves" in error for error in validate_workflow(too_wide).errors)


def test_branch_sets_are_ordered_and_exhaustive() -> None:
    document = _body().model_dump(mode="json", by_alias=True)
    document["steps"].append({"id": "anomaly", "agent_id": "agent.anomaly_detection.v1"})
    document["edges"] = [
        {
            "from": "ingest", "to": "anomaly",
            "when": {"field": "failed_test_ids", "op": "count_gt", "value": 0},
        },
        {
            "from": "ingest", "to": "summary",
            "when": {"field": "total_tests", "op": "gte", "value": 0},
        },
    ]
    body = definitions.WorkflowBodyV1.model_validate(document)
    assert any("requires exactly one else condition" in error for error in validate_workflow(body).errors)

    document["edges"].append({"from": "ingest", "to": "__end__", "when": {"op": "else"}})
    body = definitions.WorkflowBodyV1.model_validate(document)
    assert validate_workflow(body).valid is True


def test_condition_evaluation_uses_typed_state_and_frozen_config_refs() -> None:
    condition = {
        "all": [
            {"field": "failed_test_ids", "op": "count_gt", "value": 0},
            {"field": "pass_rate", "op": "lt", "ref": "thresholds.confidence_min"},
            {"not": {"field": "fallback_used", "op": "is_true"}},
        ]
    }
    state = {
        "failed_test_ids": ["case-1"],
        "pass_rate": 0.5,
        "fallback_used": False,
    }
    config = default_config("agent.ingestion.v1").model_dump(mode="json")
    assert evaluate_condition(condition, state=state, config=config) is True


def test_compile_error_is_fail_closed_and_does_not_emit_a_graph() -> None:
    document = deepcopy(_body().model_dump(mode="json", by_alias=True))
    document["steps"][1]["agent_id"] = "agent.unknown.v1"
    body = definitions.WorkflowBodyV1.model_validate(document)

    with pytest.raises(WorkflowCompileError, match="unknown capability"):
        compile_workflow(body, node_executors=_noop_executors(body))


@pytest.mark.asyncio
async def test_declared_loop_executes_no_more_than_its_bound() -> None:
    document = _body().model_dump(mode="json", by_alias=True)
    document["steps"].append({
        "id": "anomaly", "agent_id": "agent.anomaly_detection.v1",
    })
    document["edges"].append({"from": "summary", "to": "anomaly"})
    document["loops"] = [{
        "from": "summary",
        "to": "ingest",
        "when": {"field": "fallback_used", "op": "is_true"},
        "max_iterations": 2,
    }]
    body = definitions.WorkflowBodyV1.model_validate(document)

    async def ingest(state):
        return {"total_tests": int(state.get("total_tests", 0)) + 1}

    async def summary(_state):
        return {}

    async def anomaly(state):
        return {"low_confidence_count": int(state.get("low_confidence_count", 0)) + 1}

    compiled = compile_workflow(body, node_executors={
        "agent.ingestion.v1": ingest,
        "agent.summary.v1": summary,
        "agent.anomaly_detection.v1": anomaly,
    }).graph.compile()
    result = await compiled.ainvoke({
        "total_tests": 0, "fallback_used": True, "low_confidence_count": 0,
    })

    assert result["total_tests"] == 3
    assert result["low_confidence_count"] == 1
    assert result["_workflow_loop_iterations"] == {"loop_0": 2}


def _valid_condition(rng: random.Random, depth: int = 0) -> dict:
    if depth >= 4 or rng.random() < 0.45:
        leaf = rng.randrange(3)
        if leaf == 0:
            return {"field": "total_tests", "op": "gte", "value": rng.randrange(101)}
        if leaf == 1:
            return {"field": "fallback_used", "op": "eq", "value": bool(rng.randrange(2))}
        return {
            "field": "workflow_type",
            "op": "eq",
            "value": rng.choice(["offline", "deep", "live"]),
        }
    kind = rng.choice(["all", "any", "not"])
    if kind == "not":
        return {"not": _valid_condition(rng, depth + 1)}
    return {kind: [_valid_condition(rng, depth + 1) for _ in range(rng.randint(1, 3))]}


def test_typed_condition_ast_fuzz_is_total() -> None:
    rng = random.Random(3202)
    for _ in range(160):
        condition = _valid_condition(rng)
        document = _body().model_dump(mode="json", by_alias=True)
        document["edges"] = [
            {"from": "ingest", "to": "summary", "when": condition},
            {"from": "ingest", "to": "__end__", "when": {"op": "else"}},
        ]
        body = definitions.WorkflowBodyV1.model_validate(document)
        assert validate_workflow(body).valid is True
        assert isinstance(evaluate_condition(
            condition,
            state={
                "total_tests": rng.randrange(101),
                "fallback_used": bool(rng.randrange(2)),
                "workflow_type": rng.choice(["offline", "deep", "live"]),
            },
            config=default_config("agent.ingestion.v1").model_dump(mode="json"),
        ), bool)


def _untrusted_json(rng: random.Random, depth: int = 0):
    primitives = [None, True, False, rng.randint(-100, 100), "field", "unexpected"]
    if depth >= 4 or rng.random() < 0.4:
        return rng.choice(primitives)
    if rng.random() < 0.5:
        return [_untrusted_json(rng, depth + 1) for _ in range(rng.randrange(5))]
    return {
        rng.choice(["all", "any", "not", "field", "op", "value", "unknown"]):
            _untrusted_json(rng, depth + 1)
        for _ in range(rng.randrange(1, 5))
    }


def test_untrusted_condition_fuzz_never_escapes_validation() -> None:
    rng = random.Random(3203)
    for _ in range(160):
        condition = _untrusted_json(rng)
        if not isinstance(condition, dict):
            condition = {"unexpected": condition}
        document = _body().model_dump(mode="json", by_alias=True)
        document["edges"] = [
            {"from": "ingest", "to": "summary", "when": condition},
            {"from": "ingest", "to": "__end__", "when": {"op": "else"}},
        ]
        body = definitions.WorkflowBodyV1.model_validate(document)
        result = validate_workflow(body)
        assert isinstance(result.valid, bool)
        assert all(isinstance(error, str) for error in result.errors)
