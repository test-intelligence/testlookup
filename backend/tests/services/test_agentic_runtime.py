from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.agentic_runtime import AgenticRunV1
from app.services.agent_capability_registry import CAPABILITY_REGISTRY
from app.services.agent_planner import build_workflow_plan
from app.services.agentic_runtime_service import (
    build_agentic_run_projection,
    build_recursive_agentic_run_projection,
)


def test_registry_covers_every_planned_stage_and_exposes_governance_metadata():
    for workflow_type in ("offline", "live", "deep"):
        plan = build_workflow_plan(workflow_type=workflow_type, failed_test_ids=["t1"], analyses={})
        assert plan["schema_version"] == 2
        assert len(plan["plan_sha256"]) == 64
        for stage in plan["stages"]:
            assert stage["stage"] in CAPABILITY_REGISTRY
            assert stage["capability_id"].startswith("agent.")
            assert stage["sla"]["timeout_seconds"] > 0
            assert "fallback" in stage
            assert "dependencies" in stage


def test_planner_is_deterministic_and_all_green_skips_failure_capabilities():
    first = build_workflow_plan(workflow_type="deep", failed_test_ids=[], analyses={})
    second = build_workflow_plan(workflow_type="deep", failed_test_ids=[], analyses={})
    assert first == second
    skipped = {item["stage"] for item in first["stages"] if not item["planned"]}
    assert {"anomaly_detection", "failure_clustering", "root_cause_analysis"} <= skipped


def test_projection_maps_stage_as_child_task_with_budget_usage_and_stop_reason():
    now = datetime.now(timezone.utc)
    pipeline_id = uuid4()
    run_id = uuid4()
    plan = build_workflow_plan(workflow_type="deep", failed_test_ids=["t1"], analyses={})
    pipeline = SimpleNamespace(
        id=pipeline_id,
        test_run_id=run_id,
        workflow_type="deep",
        status="partial",
        started_at=now,
        completed_at=now,
        error=None,
        execution_metadata={
            "workflow_plan": plan,
            "workflow_verification": {"status": "warning"},
            "budget_spend": {
                "budget_exhausted": True,
                "budget_stop_reasons": ["llm_call_budget_exhausted"],
            },
        },
    )
    stage = SimpleNamespace(
        stage_name="anomaly_detection",
        status="failed",
        route_rationale="selected for failed run",
        skipped_reason=None,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        llm_calls_count=1,
        cost_usd=0.01,
        started_at=now,
        completed_at=now,
        error="provider timeout",
        result_data={"stop_reason": "llm_call_budget_exhausted"},
    )

    projection = build_agentic_run_projection(pipeline, [stage])

    assert projection.plan_sha256 == plan["plan_sha256"]
    assert projection.tasks[0].task_id == projection.root_task_id
    assert projection.tasks[1].parent_task_id == projection.root_task_id
    assert projection.tasks[1].capability_id == "agent.anomaly_detection.v1"
    assert projection.tasks[1].usage.total_tokens == 15
    assert projection.tasks[1].stop_reason == "timeout"
    assert projection.tasks[1].enrichment_stop_reason == "llm_call_budget_exhausted"
    assert projection.terminal_outcome.workflow_verification["status"] == "warning"
    assert projection.terminal_outcome.budget_exhausted is True
    assert projection.terminal_outcome.budget_stop_reasons == (
        "llm_call_budget_exhausted",
    )


def test_projection_accepts_legacy_plan_without_capability_ids():
    pipeline_id = uuid4()
    pipeline = SimpleNamespace(
        id=pipeline_id,
        test_run_id=uuid4(),
        workflow_type="live",
        status="completed",
        started_at=None,
        completed_at=None,
        error=None,
        execution_metadata={
            "workflow_plan": {
                "planner_version": "workflow-planner:v1",
                "stages": [{"stage": "ingestion", "planned": True, "required": True}],
            }
        },
    )
    stage = SimpleNamespace(
        stage_name="ingestion", status="completed", route_rationale=None,
        skipped_reason=None, input_tokens=None, output_tokens=None,
        total_tokens=None, llm_calls_count=None, cost_usd=None,
        started_at=None, completed_at=None, error=None,
    )

    projection = build_agentic_run_projection(pipeline, [stage])

    assert projection.selected_capability_ids == ("agent.ingestion.v1",)
    assert projection.plan_sha256 is None
    assert projection.plan_integrity_status == "legacy_unhashed"


def test_accounting_failure_is_task_visible_but_not_misreported_as_exhaustion():
    now = datetime.now(timezone.utc)
    pipeline = SimpleNamespace(
        id=uuid4(), test_run_id=uuid4(), workflow_type="investigation",
        status="completed", started_at=now, completed_at=now, error=None,
        execution_metadata={
            "budget_spend": {
                "budget_exhausted": False,
                "budget_stop_reasons": ["budget_settlement_failed"],
            }
        },
    )
    plan_stage = SimpleNamespace(
        stage_name="investigator_plan", status="completed", route_rationale=None,
        skipped_reason=None, input_tokens=0, output_tokens=0,
        total_tokens=0, llm_calls_count=0, cost_usd=0.0,
        started_at=now, completed_at=now, error=None, result_data={},
    )
    stage = SimpleNamespace(
        stage_name="hypothesis_infra", status="completed", route_rationale=None,
        skipped_reason=None, input_tokens=10, output_tokens=5,
        total_tokens=15, llm_calls_count=1, cost_usd=0.01,
        started_at=now, completed_at=now, error=None,
        result_data={"stop_reason": "budget_settlement_failed"},
    )
    projection = build_agentic_run_projection(pipeline, [plan_stage, stage])
    assert projection.tasks[2].enrichment_stop_reason == "budget_settlement_failed"
    assert projection.terminal_outcome.budget_exhausted is False
    assert projection.terminal_outcome.budget_stop_reasons == (
        "budget_settlement_failed",
    )


def test_cancelled_enrichment_stop_reason_is_preserved_in_runtime_projection():
    now = datetime.now(timezone.utc)
    pipeline = SimpleNamespace(
        id=uuid4(), test_run_id=uuid4(), workflow_type="investigation",
        status="cancelled", started_at=now, completed_at=now, error=None,
        execution_metadata={},
    )
    stage = SimpleNamespace(
        stage_name="hypothesis_infra", status="cancelled", route_rationale=None,
        skipped_reason=None, input_tokens=0, output_tokens=0,
        total_tokens=0, llm_calls_count=0, cost_usd=0.0,
        started_at=now, completed_at=now, error=None,
        result_data={"stop_reason": "cancelled"},
    )
    projection = build_agentic_run_projection(pipeline, [stage])
    assert projection.tasks[1].enrichment_stop_reason == "cancelled"


def test_selected_cluster_timeout_is_partial_and_preserves_reason():
    now = datetime.now(timezone.utc)
    pipeline = SimpleNamespace(
        id=uuid4(), test_run_id=uuid4(), workflow_type="deep",
        status="completed", started_at=now, completed_at=now, error=None,
        execution_metadata={},
    )
    stage = SimpleNamespace(
        stage_name="cluster_investigation", status="partial",
        route_rationale="selected cluster child", skipped_reason=None,
        input_tokens=0, output_tokens=0, total_tokens=0,
        llm_calls_count=0, cost_usd=0.0, started_at=now,
        completed_at=now, error=None,
        result_data={"stop_reason": "cluster_child_join_timeout"},
        selected=True, required=False, dependencies=[],
        allocated_budget={"max_seconds": 1}, stop_reason=(
            "cluster_child_join_timeout"
        ), task_key="cluster-investigation:test", attempt=1,
        capability_id="agent.cluster_investigation.v1",
        parent_task_key=None, failure_cluster_id=uuid4(),
    )

    projection = build_agentic_run_projection(pipeline, [stage])
    task = projection.tasks[1]
    assert task.selected is True
    assert task.status == "partial"
    assert task.stop_reason == "partial"
    assert task.enrichment_stop_reason == "cluster_child_join_timeout"


def test_investigation_projection_and_unknown_stage_are_consistent_and_safe():
    pipeline = SimpleNamespace(
        id=uuid4(), test_run_id=uuid4(), workflow_type="investigation",
        status="failed", started_at=None, completed_at=None,
        error="token=secret-value", execution_metadata={},
    )

    def stage(name):
        return SimpleNamespace(
            stage_name=name, status="failed", route_rationale=None,
            skipped_reason=None, input_tokens=0, output_tokens=0,
            total_tokens=0, llm_calls_count=0, cost_usd=0,
            started_at=None, completed_at=None, error="failed",
        )

    projection = build_agentic_run_projection(
        pipeline, [stage("workflow"), stage("future_stage")]
    )

    assert "agent.workflow.v1" in projection.selected_capability_ids
    assert "legacy.future_stage.v1" in projection.selected_capability_ids
    assert "secret-value" not in (projection.terminal_outcome.error or "")


def test_tampered_plan_is_reported_failed_with_recomputed_hash():
    plan = build_workflow_plan(workflow_type="live", failed_test_ids=[], analyses={})
    original_hash = plan["plan_sha256"]
    plan["stages"][0]["rationale"] = "tampered"
    pipeline = SimpleNamespace(
        id=uuid4(), test_run_id=uuid4(), workflow_type="live", status="running",
        started_at=None, completed_at=None, error=None,
        execution_metadata={"initial_workflow_plan": plan},
    )
    projection = build_agentic_run_projection(pipeline, [])
    assert projection.plan_integrity_status == "failed"
    assert projection.plan_sha256 != original_hash


def test_missing_plan_is_legacy_unhashed_not_inferred_as_verified():
    pipeline = SimpleNamespace(
        id=uuid4(), test_run_id=uuid4(), workflow_type="live", status="running",
        started_at=None, completed_at=None, error=None, execution_metadata={},
    )
    projection = build_agentic_run_projection(pipeline, [])
    assert projection.plan_integrity_status == "legacy_unhashed"
    assert projection.plan_sha256 is None
    assert projection.planner_version == "legacy"


def test_stage_error_is_redacted_and_task_truncation_is_explicit():
    pipeline = SimpleNamespace(
        id=uuid4(), test_run_id=uuid4(), workflow_type="live", status="running",
        started_at=None, completed_at=None, error=None, execution_metadata={},
    )
    rows = [SimpleNamespace(
        stage_name=f"future_{index}", status="failed", route_rationale=None,
        skipped_reason=None, input_tokens="bad", output_tokens=-1,
        total_tokens=999, llm_calls_count=-2, cost_usd="bad",
        started_at=None, completed_at=None,
        error="https://user:pass@example.invalid/log?token=secret-value",
    ) for index in range(205)]
    projection = build_agentic_run_projection(pipeline, rows)
    assert projection.tasks_truncated is True
    assert projection.observed_task_rows == 205
    assert len(projection.tasks) == 201
    assert "secret-value" not in (projection.tasks[1].error or "")
    assert projection.tasks[1].usage.total_tokens == 0


def test_zero_run_budget_is_preserved_as_hard_cap_not_unspecified():
    pipeline = SimpleNamespace(
        id=uuid4(), test_run_id=uuid4(), workflow_type="investigation",
        status="running", started_at=None, completed_at=None, error=None,
        execution_metadata={
            "run_budget": {
                "max_llm_calls": 0, "max_tokens": 0,
                "max_cost_usd": 0, "max_seconds": 0,
            }
        },
    )
    projection = build_agentic_run_projection(pipeline, [])
    assert projection.run_budget.max_llm_calls == 0
    assert projection.run_budget.max_tokens == 0
    assert projection.run_budget.max_cost_usd == 0
    assert projection.run_budget.max_seconds == 0


@pytest.mark.asyncio
async def test_runtime_endpoint_authorizes_before_loading_stage_data(monkeypatch):
    from app.routers import agents as router

    pipeline_id = uuid4()
    pipeline = SimpleNamespace(
        id=pipeline_id, test_run_id=uuid4(), workflow_type="live",
        status="running", started_at=None, completed_at=None, error=None,
        execution_metadata={},
    )
    events = []

    async def _authorize(*_args, **_kwargs):
        events.append("authorize")

    authorized = AsyncMock(side_effect=_authorize)
    monkeypatch.setattr(router, "_load_pipeline_or_404", AsyncMock(return_value=pipeline))
    monkeypatch.setattr(router, "_require_pipeline_access", authorized)
    scalars = SimpleNamespace(all=lambda: [])
    async def _execute(*_args, **_kwargs):
        events.append("query")
        return SimpleNamespace(scalars=lambda: scalars)

    db = SimpleNamespace(execute=AsyncMock(side_effect=_execute))

    response = await router.get_agentic_runtime(pipeline_id, db=db, current_user=object())

    assert isinstance(response, AgenticRunV1)
    authorized.assert_awaited_once()
    assert events[0] == "authorize"
    # Parent stages, then bounded parent-linked investigations. Authorization
    # must have completed before either query is allowed to execute.
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_runtime_endpoint_does_not_query_stages_for_foreign_tenant(monkeypatch):
    from app.routers import agents as router

    pipeline_id = uuid4()
    pipeline = SimpleNamespace(id=pipeline_id)
    monkeypatch.setattr(router, "_load_pipeline_or_404", AsyncMock(return_value=pipeline))
    monkeypatch.setattr(
        router,
        "_require_pipeline_access",
        AsyncMock(side_effect=HTTPException(403, detail="forbidden")),
    )
    db = SimpleNamespace(execute=AsyncMock())

    with pytest.raises(HTTPException) as exc:
        await router.get_agentic_runtime(pipeline_id, db=db, current_user=object())

    assert exc.value.status_code == 403
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_endpoint_excludes_child_investigation_run_mismatch(monkeypatch):
    from app.routers import agents as router

    pipeline_id = uuid4()
    run_id = uuid4()
    pipeline = _runtime_pipeline(run_id=run_id, workflow_type="deep")
    pipeline.id = pipeline_id
    foreign_investigation = SimpleNamespace(
        id=uuid4(), run_id=uuid4(), project_id=uuid4(),
        parent_pipeline_run_id=pipeline_id, created_at=datetime.now(timezone.utc),
    )
    empty = SimpleNamespace(all=lambda: [])
    foreign = SimpleNamespace(all=lambda: [foreign_investigation])
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalars=lambda: empty),
        SimpleNamespace(scalars=lambda: foreign),
    ]))
    monkeypatch.setattr(router, "_load_pipeline_or_404", AsyncMock(return_value=pipeline))
    monkeypatch.setattr(router, "_require_pipeline_access", AsyncMock())

    response = await router.get_agentic_runtime(pipeline_id, db=db, current_user=object())

    assert response.observed_child_runs == 0
    assert response.child_agentic_run_ids == ()
    assert db.execute.await_count == 2


def _runtime_pipeline(*, run_id=None, status="completed", workflow_type="investigation"):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(), test_run_id=run_id or uuid4(), workflow_type=workflow_type,
        status=status, started_at=now, completed_at=now if status in {"completed", "failed"} else None,
        error="provider token=child-secret" if status == "failed" else None,
        execution_metadata={},
    )


def _runtime_stage(name, *, status="completed", **extra):
    now = datetime.now(timezone.utc)
    values = {
        "stage_name": name, "status": status, "route_rationale": None,
        "skipped_reason": None, "input_tokens": 2, "output_tokens": 3,
        "total_tokens": 5, "llm_calls_count": 1, "cost_usd": 0.01,
        "started_at": now, "completed_at": now, "error": None,
        "result_data": {},
    }
    values.update(extra)
    return SimpleNamespace(**values)


def test_stage_projection_prefers_durable_identity_selection_budget_and_cost_stop():
    pipeline = _runtime_pipeline(status="completed")
    stage = _runtime_stage(
        "hypothesis_infra",
        task_key="durable:task:infra",
        parent_task_key="missing:durable:parent",
        capability_id="agent.custom_infra.v2",
        attempt=3,
        selected=False,
        required=True,
        dependencies=["durable:dependency"],
        allocated_budget={"max_cost_usd": 2.5, "max_retries": 0},
        stop_reason="cost_budget_exhausted",
        failure_cluster_id="cl_001",
    )
    pipeline.execution_metadata = {
        "budget_spend": {
            "budget_exhausted": False,
            "budget_stop_reasons": ["cost_budget_exhausted"],
        }
    }
    projection = build_agentic_run_projection(pipeline, [stage])
    task = projection.tasks[1]
    assert task.task_id == f"pipeline:{pipeline.id}:task:durable:task:infra"
    assert task.parent_task_id == projection.root_task_id
    assert task.source_pipeline_run_id == str(pipeline.id)
    assert task.failure_cluster_id == "cl_001"
    assert task.capability_id == "agent.custom_infra.v2"
    assert task.attempt == 3 and task.selected is False and task.required is True
    assert task.dependencies == ("durable:dependency",)
    assert task.budget.max_cost_usd == 2.5
    assert task.enrichment_stop_reason == "cost_budget_exhausted"
    assert projection.terminal_outcome.budget_exhausted is True


@pytest.mark.parametrize("child_status", ["completed", "running", "failed"])
def test_recursive_projection_materializes_child_tree_and_typed_sanitized_evidence(child_status):
    run_id = uuid4()
    parent = _runtime_pipeline(run_id=run_id, workflow_type="deep")
    child = _runtime_pipeline(run_id=run_id, status=child_status)
    stages = [
        _runtime_stage("investigator_plan"),
        _runtime_stage("hypothesis_infra"),
        _runtime_stage("investigator_synthesis"),
    ]
    investigation = SimpleNamespace(
        id=uuid4(), run_id=run_id, parent_task_id=f"pipeline:{parent.id}",
        failure_cluster_id="cl_001",
        hypotheses=[{
            "id": "infra", "title": "Infrastructure", "status": "validated",
            "confidence": 90, "confidence_basis": "heuristic_estimate",
            "summary": "Shared runner saturation",
            "evidence": ["https://user:pass@example.invalid/log?token=secret-value"],
        }],
        verdict={
            "primary_cause": "infra", "confidence": 88,
            "narrative": "Runner pressure explains the cluster.",
            "recommended_actions": ["Scale the runner pool."],
        },
    )
    projection = build_recursive_agentic_run_projection(
        parent, [], [(child, stages, investigation)]
    )
    child_root = next(task for task in projection.tasks if task.task_id == f"pipeline:{child.id}")
    assert child_root.parent_task_id == projection.root_task_id
    assert child_root.failure_cluster_id == "cl_001"
    assert child_root.status == child_status
    assert projection.child_agentic_run_ids == (f"agentic:{child.id}",)
    assert projection.observed_child_runs == 1
    assert projection.children_truncated is False
    assert {finding.classification for finding in projection.findings} >= {"inference", "recommendation"}
    assert projection.evidence and projection.evidence[0].classification == "restricted"
    assert "secret-value" not in projection.evidence[0].description
    hypothesis_task = next(task for task in projection.tasks if task.stage_name == "hypothesis_infra")
    assert hypothesis_task.finding_ids and hypothesis_task.evidence_ids
    assert all(item.task_id in {task.task_id for task in projection.tasks} for item in projection.evidence)


def test_recursive_projection_excludes_tenant_mismatch_and_repairs_duplicate_or_dangling_ids():
    run_id = uuid4()
    parent = _runtime_pipeline(run_id=run_id, workflow_type="deep")
    authorized = _runtime_pipeline(run_id=run_id)
    foreign = _runtime_pipeline(run_id=uuid4())
    duplicate_stages = [
        _runtime_stage("one", task_key="duplicate", parent_task_key="missing"),
        _runtime_stage("two", task_key="duplicate", parent_task_key="missing"),
    ]
    inv = SimpleNamespace(
        id=uuid4(), run_id=run_id, parent_task_id="missing-parent",
        cluster_id="cl_safe", hypotheses=[], verdict=None,
    )
    foreign_inv = SimpleNamespace(
        id=uuid4(), run_id=foreign.test_run_id, parent_task_id=None,
        cluster_id="cl_foreign", hypotheses=[], verdict=None,
    )
    projection = build_recursive_agentic_run_projection(
        parent, [], [(authorized, duplicate_stages, inv), (foreign, [], foreign_inv)]
    )
    ids = [task.task_id for task in projection.tasks]
    assert len(ids) == len(set(ids))
    assert all(not task.parent_task_id or task.parent_task_id in set(ids) for task in projection.tasks)
    assert projection.child_agentic_run_ids == (f"agentic:{authorized.id}",)
    assert projection.observed_child_runs == 2


def test_recursive_projection_reports_child_truncation(monkeypatch):
    from app.services import agentic_runtime_service as runtime_service

    monkeypatch.setattr(runtime_service, "MAX_RUNTIME_CHILDREN", 1)
    run_id = uuid4()
    parent = _runtime_pipeline(run_id=run_id, workflow_type="deep")
    children = []
    for index in range(2):
        child = _runtime_pipeline(run_id=run_id)
        investigation = SimpleNamespace(
            id=uuid4(), run_id=run_id, parent_task_id=None,
            cluster_id=f"cl_{index}", hypotheses=[], verdict=None,
        )
        children.append((child, [], investigation))
    projection = build_recursive_agentic_run_projection(parent, [], children)
    assert projection.observed_child_runs == 2
    assert projection.children_truncated is True
    assert len(projection.child_agentic_run_ids) == 1
