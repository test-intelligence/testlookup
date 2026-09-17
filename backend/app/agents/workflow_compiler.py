"""Semantic validation and LangGraph compilation for workflow definitions.

E3.1 stores a strict wire document.  This module is the E3.2 authority for
turning that document into a bounded graph: capabilities come from the
registry, dependencies must dominate their consumers, cycles live only in the
explicit loop list, policy is checked against project agent configs, and
conditions use a small typed JSON AST.
"""
from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Hashable
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence, cast

from langgraph.graph import END, StateGraph

from app.agents.state import WorkflowState
from app.models.enums import ExecutionPath
from app.services.agent_capability_registry import CAPABILITY_REGISTRY, WORKFLOW_ELIGIBLE
from app.services.agent_config_service import (
    AGENT_TOOL_PERMISSIONS,
    AgentConfigV1,
    default_config,
    mode_permits,
)
from app.services.workflow_definition_service import WorkflowBodyV1, WorkflowEdgeV1

NodeExecutor = Callable[[WorkflowState], Awaitable[dict[str, Any]] | dict[str, Any]]
END_STEP = str(END)
MAX_CONDITION_DEPTH = 6
MAX_CONDITION_LEAVES = 32
MAX_IN_VALUES = 50


@dataclass(frozen=True)
class WorkflowValidation:
    valid: bool
    errors: tuple[str, ...]


@dataclass(frozen=True)
class CompiledWorkflow:
    graph: StateGraph
    entrypoint: str
    validation: WorkflowValidation


class WorkflowCompileError(ValueError):
    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True)
class _FieldSpec:
    kind: str
    enum: frozenset[str] = frozenset()


# This is the published condition projection of WorkflowState.  It deliberately
# exposes bounded, typed facts rather than the whole mutable state document.
_STATE_FIELDS: dict[str, _FieldSpec] = {
    "workflow_type": _FieldSpec("str", frozenset({"offline", "deep", "live"})),
    "failed_test_ids": _FieldSpec("collection"),
    "total_tests": _FieldSpec("number"),
    "pass_rate": _FieldSpec("number"),
    "is_regression": _FieldSpec("bool"),
    "regression_tests": _FieldSpec("collection"),
    "analyses": _FieldSpec("collection"),
    "triageable_analysis_count": _FieldSpec("number"),
    "stage_quality": _FieldSpec("str", frozenset({"normal", "degraded"})),
    "low_confidence_count": _FieldSpec("number"),
    "fallback_used": _FieldSpec("bool"),
    "completed_stages": _FieldSpec("collection"),
    "skipped_stages": _FieldSpec("collection"),
    "execution_path": _FieldSpec(
        "str", frozenset(item.value for item in ExecutionPath)
    ),
    "review_verdict": _FieldSpec(
        "str", frozenset({"pass", "pass_with_flags", "retry", "reject"})
    ),
    "supervisor_route": _FieldSpec(
        "str", frozenset({"continue", "retry", "finalize"})
    ),
}
_LEAF_OPS = frozenset({
    "eq", "ne", "lt", "lte", "gt", "gte", "in",
    "count_eq", "count_gt", "count_gte", "count_lt", "count_lte",
    "is_null", "is_true", "else",
})
_RELATIONAL_OPS = frozenset({"lt", "lte", "gt", "gte"})
_COUNT_OPS = frozenset({"count_eq", "count_gt", "count_gte", "count_lt", "count_lte"})


def _config_document(value: AgentConfigV1 | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, AgentConfigV1):
        return value.model_dump(mode="json")
    return dict(value)


def _value_at(document: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = document
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "str"
    if isinstance(value, (list, tuple, set, dict)):
        return "collection"
    if value is None:
        return "null"
    return "other"


def _condition_errors(
    node: Any,
    *,
    config: Mapping[str, Any],
    path: str,
    depth: int = 1,
) -> tuple[list[str], int]:
    if depth > MAX_CONDITION_DEPTH:
        return [f"{path}: condition depth exceeds {MAX_CONDITION_DEPTH}"], 0
    if not isinstance(node, dict):
        return [f"{path}: condition node must be an object"], 0
    composite = [key for key in ("all", "any", "not") if key in node]
    if composite:
        if len(composite) != 1 or len(node) != 1:
            return [f"{path}: a composite condition must contain exactly one of all/any/not"], 0
        key = composite[0]
        children = node[key]
        if key == "not":
            composite_errors, leaves = _condition_errors(
                children, config=config, path=f"{path}.not", depth=depth + 1
            )
            return composite_errors, leaves
        if not isinstance(children, list) or not children:
            return [f"{path}.{key}: must be a non-empty list"], 0
        errors: list[str] = []
        leaves = 0
        for index, child in enumerate(children):
            child_errors, child_leaves = _condition_errors(
                child, config=config, path=f"{path}.{key}[{index}]", depth=depth + 1
            )
            errors.extend(child_errors)
            leaves += child_leaves
        if leaves > MAX_CONDITION_LEAVES:
            errors.append(f"{path}: condition contains more than {MAX_CONDITION_LEAVES} leaves")
        return errors, leaves

    allowed = {"field", "op", "value", "ref"}
    extra = sorted(set(node) - allowed)
    errors = [f"{path}: unknown condition keys {extra}"] if extra else []
    op = node.get("op")
    if not isinstance(op, str) or op not in _LEAF_OPS:
        errors.append(f"{path}: unknown condition op {op!r}")
        return errors, 1
    if op == "else":
        if set(node) != {"op"}:
            errors.append(f"{path}: else must be exactly {{'op': 'else'}}")
        return errors, 1

    field = node.get("field")
    spec = _STATE_FIELDS.get(field) if isinstance(field, str) else None
    if spec is None:
        errors.append(f"{path}: unknown state field {field!r}")
        return errors, 1
    has_value = "value" in node
    has_ref = "ref" in node
    if op in {"is_null", "is_true"}:
        if has_value or has_ref:
            errors.append(f"{path}: {op} does not accept value or ref")
        if op == "is_true" and spec.kind != "bool":
            errors.append(f"{path}: is_true requires a boolean field")
        return errors, 1
    if has_value == has_ref:
        errors.append(f"{path}: {op} requires exactly one of value or ref")
        return errors, 1

    expected = spec.kind
    compared: Any
    if has_ref:
        ref = node.get("ref")
        found, compared = _value_at(config, ref) if isinstance(ref, str) else (False, None)
        if not found:
            errors.append(f"{path}: unknown frozen config ref {ref!r}")
            return errors, 1
    else:
        compared = node.get("value")

    if op == "in":
        if not isinstance(compared, list):
            errors.append(f"{path}: in requires a list value or ref")
        elif len(compared) > MAX_IN_VALUES:
            errors.append(f"{path}: in accepts at most {MAX_IN_VALUES} values")
        elif spec.kind == "collection":
            errors.append(f"{path}: in requires a scalar field")
        elif any(_kind(value) not in {spec.kind, "null"} for value in compared):
            errors.append(f"{path}: in values must match the {spec.kind} field")
        elif spec.enum:
            invalid = sorted({str(value) for value in compared} - set(spec.enum))
            if invalid:
                errors.append(f"{path}: values {invalid} are outside {field}'s enum")
        return errors, 1
    if op in _COUNT_OPS:
        if spec.kind != "collection":
            errors.append(f"{path}: {op} requires a collection field")
        if _kind(compared) != "number" or isinstance(compared, float) and not compared.is_integer():
            errors.append(f"{path}: {op} requires an integer comparison")
        return errors, 1
    if op in _RELATIONAL_OPS and expected != "number":
        errors.append(f"{path}: {op} requires a numeric field")
    observed_kind = _kind(compared)
    if expected == "collection":
        errors.append(f"{path}: {op} requires a scalar field; use a count operator")
    elif expected == "number" and observed_kind != "number":
        errors.append(f"{path}: {op} requires a numeric value or ref")
    elif expected in {"str", "bool"} and observed_kind not in {expected, "null"}:
        errors.append(f"{path}: {op} compares {expected} with {observed_kind}")
    if spec.enum and compared is not None and str(compared) not in spec.enum:
        errors.append(f"{path}: value {compared!r} is outside {field}'s enum")
    return errors, 1


def _project_state(state: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    projected = {name: state.get(name) for name in _STATE_FIELDS}
    analyses = state.get("analyses")
    threshold_found, threshold = _value_at(config, "thresholds.confidence_min")
    if not threshold_found or not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        threshold = 0.7
    projected["triageable_analysis_count"] = (
        sum(
            1
            for analysis in analyses.values()
            if isinstance(analysis, Mapping)
            and isinstance(analysis.get("confidence_score", 0), (int, float))
            and analysis.get("confidence_score", 0) >= threshold
            and not analysis.get("is_flaky", False)
        )
        if isinstance(analyses, Mapping)
        else 0
    )
    review = state.get("review_verdict")
    if isinstance(review, Mapping):
        projected["review_verdict"] = review.get("verdict")
    supervisor = state.get("supervisor")
    if isinstance(supervisor, Mapping):
        projected["supervisor_route"] = supervisor.get("route")
    return projected


def _compare(op: str, left: Any, right: Any = None) -> bool:
    if op == "eq":
        return bool(left == right)
    if op == "ne":
        return bool(left != right)
    if op in {"lt", "lte", "gt", "gte", "in"}:
        try:
            if op == "lt":
                return bool(left < right)
            if op == "lte":
                return bool(left <= right)
            if op == "gt":
                return bool(left > right)
            if op == "gte":
                return bool(left >= right)
            return bool(left in right)
        except (TypeError, ValueError):
            # Missing/null runtime facts are an unmet branch condition.  A
            # published graph must not crash merely because optional evidence
            # was absent from this run.
            return False
    if op == "is_null":
        return left is None
    if op == "is_true":
        return left is True
    if op.startswith("count_"):
        size = len(left) if isinstance(left, (list, tuple, set, dict)) else 0
        return _compare(op.removeprefix("count_"), size, right)
    if op == "else":
        return True
    return False


def evaluate_condition(
    node: Mapping[str, Any], *, state: Mapping[str, Any], config: Mapping[str, Any]
) -> bool:
    """Evaluate a condition already accepted by :func:`validate_workflow`."""

    if "all" in node:
        return all(evaluate_condition(child, state=state, config=config) for child in node["all"])
    if "any" in node:
        return any(evaluate_condition(child, state=state, config=config) for child in node["any"])
    if "not" in node:
        return not evaluate_condition(node["not"], state=state, config=config)
    op = str(node["op"])
    if op == "else":
        return True
    projected = _project_state(state, config)
    left = projected.get(str(node["field"]))
    if "ref" in node:
        _, right = _value_at(config, str(node["ref"]))
    else:
        right = node.get("value")
    return _compare(op, left, right)


def _normalise_configs(
    body: WorkflowBodyV1,
    agent_configs: Mapping[str, AgentConfigV1 | Mapping[str, Any]] | None,
) -> dict[str, AgentConfigV1]:
    supplied = agent_configs or {}
    configs: dict[str, AgentConfigV1] = {}
    for step in body.steps:
        raw = supplied.get(step.agent_id)
        if raw is None:
            try:
                configs[step.agent_id] = default_config(step.agent_id)
            except KeyError:
                continue
        elif isinstance(raw, AgentConfigV1):
            configs[step.agent_id] = raw
        else:
            configs[step.agent_id] = AgentConfigV1.model_validate(raw)
    return configs


def _edge_sources(edge: WorkflowEdgeV1) -> list[str]:
    return [edge.source] if isinstance(edge.source, str) else list(edge.source)


def _entry_and_dominators(
    step_ids: set[str], edges: list[tuple[str, str]], errors: list[str]
) -> tuple[str | None, dict[str, set[str]]]:
    predecessors: dict[str, set[str]] = {step: set() for step in step_ids}
    successors: dict[str, set[str]] = {step: set() for step in step_ids}
    for source, target in edges:
        if source in step_ids and target in step_ids:
            predecessors[target].add(source)
            successors[source].add(target)
    entries = sorted(step for step, incoming in predecessors.items() if not incoming)
    if len(entries) != 1:
        errors.append(f"workflow must have exactly one entry step; found {entries}")
        return None, {}
    entry = entries[0]
    reachable = {entry}
    queue = deque([entry])
    while queue:
        source = queue.popleft()
        for target in successors[source]:
            if target not in reachable:
                reachable.add(target)
                queue.append(target)
    for step in sorted(step_ids - reachable):
        errors.append(f"step {step!r} is unreachable from entry {entry!r}")
    dominators = {step: set(step_ids) for step in step_ids}
    dominators[entry] = {entry}
    changed = True
    while changed:
        changed = False
        for step in sorted(step_ids - {entry}):
            incoming = predecessors[step]
            updated = {step} | (set.intersection(*(dominators[item] for item in incoming)) if incoming else set())
            if updated != dominators[step]:
                dominators[step] = updated
                changed = True
    return entry, dominators


def validate_workflow(
    body: WorkflowBodyV1,
    *,
    agent_configs: Mapping[str, AgentConfigV1 | Mapping[str, Any]] | None = None,
) -> WorkflowValidation:
    errors: list[str] = []
    steps = {step.id: step for step in body.steps}
    step_ids = set(steps)
    stage_by_id: dict[str, str] = {}
    steps_by_stage: dict[str, list[str]] = defaultdict(list)
    try:
        configs = _normalise_configs(body, agent_configs)
    except Exception as exc:  # Pydantic supplies the field-level config detail
        return WorkflowValidation(
            valid=False,
            errors=(f"agent config is invalid: {exc}",),
        )

    for step in body.steps:
        matches = [stage for stage, spec in CAPABILITY_REGISTRY.items() if spec.capability_id == step.agent_id]
        if not matches:
            errors.append(f"step {step.id}: unknown capability {step.agent_id!r}")
            continue
        stage = matches[0]
        spec = CAPABILITY_REGISTRY[stage]
        if spec.execution in {"child_spawned", "runtime"}:
            errors.append(f"step {step.id}: capability {step.agent_id!r} cannot be a top-level workflow step")
        elif stage not in WORKFLOW_ELIGIBLE:
            errors.append(
                f"step {step.id}: capability {step.agent_id!r} has no workflow runtime executor"
            )
        stage_by_id[step.id] = stage
        steps_by_stage[stage].append(step.id)
        if step.config_ref is not None and step.config_ref != step.agent_id:
            errors.append(
                f"step {step.id}: config_ref must equal its agent_id so it resolves inside the project"
            )
        config = configs.get(step.agent_id)
        if config is not None:
            if config.agent_id != step.agent_id:
                errors.append(f"step {step.id}: resolved config belongs to {config.agent_id!r}")
            if config.enabled and not mode_permits(config.mode, spec.permission):
                errors.append(
                    f"step {step.id}: capability permission {spec.permission!r} "
                    f"is not allowed by mode {config.mode!r}"
                )
            for tool in step.tools:
                permission = AGENT_TOOL_PERMISSIONS.get(tool)
                if permission is None:
                    errors.append(f"step {step.id}: unknown tool {tool!r}")
                elif tool not in config.tools.allowlist:
                    errors.append(f"step {step.id}: tool {tool!r} is not in the configured allowlist")
                elif not mode_permits(config.mode, permission):
                    errors.append(
                        f"step {step.id}: tool {tool!r} permission {permission!r} "
                        f"is not allowed by mode {config.mode!r}"
                    )
        if len(step.tools) != len(set(step.tools)):
            errors.append(f"step {step.id}: tools must be unique")
        for reviewed in step.reviews:
            if reviewed not in step_ids:
                errors.append(f"step {step.id}: reviews unknown step {reviewed!r}")
        if step.reviews and stage != "reviewer":
            errors.append(f"step {step.id}: only agent.reviewer.v1 may declare reviews")

    graph_edges: list[tuple[str, str]] = []
    conditional_by_source: dict[str, list[WorkflowEdgeV1]] = defaultdict(list)
    seen_edges: set[tuple[tuple[str, ...], str, str]] = set()
    for index, edge in enumerate(body.edges):
        sources = _edge_sources(edge)
        if not sources or any(source not in step_ids for source in sources):
            errors.append(f"edge[{index}]: every source must name a workflow step")
        if edge.to != END_STEP and edge.to not in step_ids:
            errors.append(f"edge[{index}]: target {edge.to!r} is not a workflow step or {END_STEP!r}")
        if len(sources) > 1 and edge.join is None:
            errors.append(f"edge[{index}]: multiple sources require join='all' or join='any'")
        if len(sources) == 1 and edge.join is not None:
            errors.append(f"edge[{index}]: join is valid only with multiple sources")
        if edge.when is not None and len(sources) != 1:
            errors.append(f"edge[{index}]: conditional edges require exactly one source")
        identity = (tuple(sources), edge.to, repr(edge.when))
        if identity in seen_edges:
            errors.append(f"edge[{index}]: duplicate edge")
        seen_edges.add(identity)
        for source in sources:
            graph_edges.append((source, edge.to))
        if edge.when is not None and len(sources) == 1:
            conditional_by_source[sources[0]].append(edge)
            config = configs.get(steps[sources[0]].agent_id)
            config_doc = _config_document(config) if config else {}
            condition_errors, _ = _condition_errors(
                edge.when, config=config_doc, path=f"edge[{index}].when"
            )
            errors.extend(condition_errors)

    for source, conditional in conditional_by_source.items():
        else_count = sum(edge.when == {"op": "else"} for edge in conditional)
        if else_count != 1:
            errors.append(
                f"conditional branch from {source!r} requires exactly one else condition"
            )
        elif conditional[-1].when != {"op": "else"}:
            errors.append(f"conditional branch from {source!r} must declare else last")

    loop_sources = {loop.source for loop in body.loops}
    for source in sorted(loop_sources):
        outgoing = [edge for edge in body.edges if source in _edge_sources(edge)]
        unconditional = [edge for edge in outgoing if edge.when is None]
        if any(len(_edge_sources(edge)) != 1 for edge in outgoing):
            errors.append(
                f"loop source {source!r} cannot also participate in a multi-source edge"
            )
        if unconditional and (len(unconditional) != 1 or len(outgoing) != 1):
            errors.append(
                f"loop source {source!r} requires one unconditional continuation "
                "or an exhaustive conditional branch"
            )

    adjacency: dict[str, set[str]] = {step: set() for step in step_ids}
    indegree = {step: 0 for step in step_ids}
    for source, target in graph_edges:
        if source in step_ids and target in step_ids and target not in adjacency[source]:
            adjacency[source].add(target)
            indegree[target] += 1
    queue = deque(sorted(step for step, degree in indegree.items() if degree == 0))
    visited = 0
    while queue:
        source = queue.popleft()
        visited += 1
        for target in sorted(adjacency[source]):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if visited != len(step_ids):
        errors.append("workflow edges contain a cycle; declare bounded back-edges in loops")

    entry, dominators = _entry_and_dominators(step_ids, graph_edges, errors)
    minimum_depth = {step: len(step_ids) + 1 for step in step_ids}
    always_scheduled: set[str] = set()
    if entry is not None:
        minimum_depth[entry] = 0
        always_scheduled.add(entry)
        changed = True
        while changed:
            changed = False
            for edge in body.edges:
                if edge.when is not None:
                    continue
                sources = _edge_sources(edge)
                if edge.to not in step_ids or not all(source in step_ids for source in sources):
                    continue
                candidate = min(minimum_depth[source] for source in sources) + 1
                if candidate < minimum_depth[edge.to]:
                    minimum_depth[edge.to] = candidate
                    changed = True
                if all(source in always_scheduled for source in sources) and edge.to not in always_scheduled:
                    always_scheduled.add(edge.to)
                    changed = True
    if entry is not None and dominators:
        def dependency_groups(stage: str) -> list[list[str]]:
            dependencies = steps_by_stage.get(stage)
            if dependencies:
                return [dependencies]
            spec = CAPABILITY_REGISTRY.get(stage)
            if spec is not None and spec.execution == "child_spawned":
                nested: list[list[str]] = []
                for item in spec.dependencies:
                    nested.extend(dependency_groups(item))
                return nested
            return [[]]

        for step_id, stage in stage_by_id.items():
            for dependency_stage in CAPABILITY_REGISTRY[stage].dependencies:
                for alternatives in dependency_groups(dependency_stage):
                    def guaranteed(required_step: str) -> bool:
                        return (
                            required_step in dominators.get(step_id, set())
                            or (
                                required_step in always_scheduled
                                and minimum_depth.get(required_step, len(step_ids) + 1)
                                < minimum_depth.get(step_id, len(step_ids) + 1)
                            )
                        )

                    if not alternatives or not any(guaranteed(item) for item in alternatives):
                        errors.append(
                            f"step {step_id}: {stage} requires upstream capability "
                            f"{dependency_stage!r} on every path"
                        )
            for reviewed in steps[step_id].reviews:
                if reviewed not in dominators.get(step_id, set()):
                    errors.append(f"step {step_id}: reviewed step {reviewed!r} is not upstream on every path")

    for index, loop in enumerate(body.loops):
        if loop.source not in step_ids or loop.to not in step_ids:
            errors.append(f"loop[{index}]: source and target must name workflow steps")
            continue
        if loop.to not in dominators.get(loop.source, set()):
            errors.append(f"loop[{index}]: target must be an upstream ancestor of its source")
        config = configs.get(steps[loop.source].agent_id)
        config_doc = _config_document(config) if config else {}
        condition_errors, _ = _condition_errors(
            loop.when, config=config_doc, path=f"loop[{index}].when"
        )
        errors.extend(condition_errors)

    for step in body.steps:
        if step.agent_id != "agent.reviewer.v1":
            continue
        retry_loops = [
            loop
            for loop in body.loops
            if loop.source == step.id
            and loop.to in step.reviews
            and loop.when
            == {"field": "supervisor_route", "op": "eq", "value": "retry"}
            and loop.max_iterations == 1
        ]
        if len(retry_loops) != 1:
            errors.append(
                f"step {step.id}: reviewer requires exactly one bounded retry loop "
                "to a reviewed step using supervisor_route == 'retry' and max_iterations=1"
            )

    return WorkflowValidation(valid=not errors, errors=tuple(dict.fromkeys(errors)))


def _branch_router(
    source: str,
    branches: Sequence[tuple[str, Mapping[str, Any], str, int | None]],
    config: Mapping[str, Any],
) -> Callable[[WorkflowState], str]:
    def route(state: WorkflowState) -> str:
        for label, condition, _target, loop_limit in branches:
            if loop_limit is not None:
                counters = state.get("_workflow_loop_iterations") or {}
                if int(counters.get(label, 0)) >= loop_limit:
                    continue
            if condition.get("op") == "else" or evaluate_condition(
                condition, state=state, config=config
            ):
                return label
        return END_STEP

    route.__name__ = f"route_{source}"
    return route


def compile_workflow(
    body: WorkflowBodyV1,
    *,
    agent_configs: Mapping[str, AgentConfigV1 | Mapping[str, Any]] | None = None,
    node_executors: Mapping[str, NodeExecutor] | None = None,
) -> CompiledWorkflow:
    validation = validate_workflow(body, agent_configs=agent_configs)
    if not validation.valid:
        raise WorkflowCompileError(validation.errors)
    if node_executors is None:
        from app.agents.workflow import workflow_node_executors

        node_executors = workflow_node_executors()
    configs = _normalise_configs(body, agent_configs)
    steps = {step.id: step for step in body.steps}
    graph = StateGraph(WorkflowState)
    for step in body.steps:
        # A published workflow may use the same capability more than once
        # under distinct step ids.  Runtime binding therefore gets first
        # refusal by step identity; capability keyed maps remain compatible.
        executor = node_executors.get(step.id) or node_executors.get(step.agent_id)
        if executor is None:
            raise WorkflowCompileError([f"step {step.id}: no runtime executor for {step.agent_id!r}"])
        # LangGraph accepts both members of NodeExecutor; its overload does not
        # accept the union alias as a single argument type.
        graph.add_node(step.id, cast(Any, executor))

    all_edges = [
        (source, edge.to)
        for edge in body.edges
        for source in _edge_sources(edge)
        if edge.to != END_STEP
    ]
    indegree = {step: 0 for step in steps}
    for _source, target in all_edges:
        indegree[target] += 1
    entrypoint = next(step for step, degree in indegree.items() if degree == 0)
    graph.set_entry_point(entrypoint)

    conditional_by_source: dict[str, list[tuple[str, Mapping[str, Any], str, int | None]]] = defaultdict(list)
    loop_sources = {loop.source for loop in body.loops}
    for index, loop in enumerate(body.loops):
        loop_label = f"loop_{index}"
        guard_node = f"__workflow_{loop_label}"

        async def increment_loop(
            state: WorkflowState, *, counter: str = loop_label
        ) -> dict[str, Any]:
            counters = dict(state.get("_workflow_loop_iterations") or {})
            counters[counter] = int(counters.get(counter, 0)) + 1
            return {"_workflow_loop_iterations": counters}

        graph.add_node(guard_node, increment_loop)
        graph.add_edge(guard_node, loop.to)
        conditional_by_source[loop.source].append(
            (loop_label, loop.when, guard_node, loop.max_iterations)
        )
    for index, edge in enumerate(body.edges):
        sources = _edge_sources(edge)
        edge_target: Any = END if edge.to == END_STEP else edge.to
        if edge.when is not None:
            conditional_by_source[sources[0]].append(
                (f"edge_{index}", edge.when, edge.to, None)
            )
        elif sources[0] in loop_sources:
            conditional_by_source[sources[0]].append(
                (f"edge_{index}", {"op": "else"}, edge.to, None)
            )
        elif len(sources) > 1 and edge.join == "all":
            graph.add_edge(sources, edge_target)
        else:
            for source in sources:
                graph.add_edge(source, edge_target)

    for source, branches in conditional_by_source.items():
        config = configs.get(steps[source].agent_id)
        config_doc = _config_document(config) if config else {}
        destinations: dict[Hashable, str] = {
            label: END if target == END_STEP else target
            for label, _condition, target, _loop_limit in branches
        }
        if not any(condition.get("op") == "else" for _, condition, _, _ in branches):
            destinations.setdefault(END_STEP, END)
        graph.add_conditional_edges(
            source,
            _branch_router(source, branches, config_doc),
            destinations,
        )

    outgoing = {source for source, _target in all_edges} | set(conditional_by_source)
    for step_id in sorted(set(steps) - outgoing):
        graph.add_edge(step_id, END)
    return CompiledWorkflow(graph=graph, entrypoint=entrypoint, validation=validation)


__all__ = [
    "CompiledWorkflow", "END_STEP", "WorkflowCompileError", "WorkflowValidation",
    "compile_workflow", "evaluate_condition", "validate_workflow",
]
