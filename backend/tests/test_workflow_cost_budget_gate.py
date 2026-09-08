"""Pipeline-wide cost-cap regressions for durable AI dispatch."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest


@pytest.mark.asyncio
async def test_hard_cap_completes_without_invoking_any_graph_or_llm(monkeypatch):
    from app.agents import workflow
    from app.services.llm_cost_budget import CapDecision

    run_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    setup = {
        "test_run_id": run_id,
        "project_id": project_id,
        "workflow_type": "offline",
        "initial_workflow_plan": {
            "stages": [
                "ingestion",
                "anomaly_detection",
                "root_cause_analysis",
                "summary",
                "triage",
            ]
        },
        "cluster_child_settings": {},
        "async_decision_report_supersession_enabled": False,
        "contract_agent_settings": {"enabled": False},
        "defect_commander_settings": {"enabled": False},
        "log_intelligence_settings": {"enabled": False},
        "regression_watchman_settings": {"enabled": False},
        "change_ownership_settings": {"enabled": False},
        "resume_attempt": 0,
    }
    graph_call = AsyncMock(side_effect=AssertionError("graph must remain gated"))
    mark_done = AsyncMock()
    persist_context = AsyncMock()
    emit = AsyncMock()

    monkeypatch.setattr(
        workflow, "_create_pipeline_run", AsyncMock(return_value=setup)
    )
    monkeypatch.setattr(workflow, "_load_checkpoint", AsyncMock(return_value=None))
    monkeypatch.setattr(
        workflow,
        "_resolve_analysis_mode_snapshot",
        AsyncMock(
            return_value={
                "requested": "llm",
                "resolved": "llm",
                "resolution_reason": "configured",
            }
        ),
    )
    monkeypatch.setattr(workflow, "_persist_execution_context", persist_context)
    monkeypatch.setattr(workflow, "_mark_pipeline_done", mark_done)
    monkeypatch.setattr(workflow, "emit_event", emit)
    monkeypatch.setattr(
        workflow,
        "_offline_app",
        SimpleNamespace(ainvoke=graph_call),
    )
    monkeypatch.setattr(
        "app.services.llm_cost_budget.check_and_apply_cap",
        AsyncMock(
            return_value=CapDecision(
                action="HARD_BLOCK",
                block=True,
                rationale="daily cap reached",
                utilization_pct=100.0,
            )
        ),
    )

    result = await workflow.run_offline_pipeline(
        test_run_id=run_id,
        project_id=project_id,
        build_number="42",
    )

    graph_call.assert_not_awaited()
    assert result["cost_budget_blocked"] is True
    assert result["_cost_budget_action"] == "HARD_BLOCK"
    assert result["skipped_stages"] == setup["initial_workflow_plan"]["stages"]
    persist_context.assert_awaited_once()
    assert persist_context.await_args.args[2]["block"] is True
    mark_done.assert_awaited_once()
    assert mark_done.await_args.kwargs["success"] is True


@pytest.mark.asyncio
async def test_deep_hard_cap_is_persisted_and_never_invokes_graph(monkeypatch):
    from app.agents import workflow
    from app.services.llm_cost_budget import CapDecision

    test_run_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    stages = ["ingestion", "failure_clustering", "cluster_investigation"]
    setup = {
        "test_run_id": test_run_id,
        "project_id": project_id,
        "workflow_type": "deep",
        "initial_workflow_plan": {"stages": stages},
        "cluster_child_settings": {},
        "async_decision_report_supersession_enabled": False,
        "contract_agent_settings": {"enabled": False},
        "defect_commander_settings": {"enabled": False},
        "log_intelligence_settings": {"enabled": False},
        "regression_watchman_settings": {"enabled": False},
        "change_ownership_settings": {"enabled": False},
        "resume_attempt": 0,
    }
    graph_call = AsyncMock(side_effect=AssertionError("deep graph must remain gated"))
    persist_deep_outputs = AsyncMock(
        side_effect=AssertionError("blocked deep output must not be persisted")
    )
    persist_context = AsyncMock()
    mark_done = AsyncMock()

    monkeypatch.setattr(workflow, "_create_pipeline_run", AsyncMock(return_value=setup))
    monkeypatch.setattr(workflow, "_load_checkpoint", AsyncMock(return_value=None))
    monkeypatch.setattr(
        workflow,
        "_resolve_analysis_mode_snapshot",
        AsyncMock(
            return_value={
                "requested": "llm",
                "resolved": "llm",
                "resolution_reason": "configured",
            }
        ),
    )
    monkeypatch.setattr(workflow, "_persist_execution_context", persist_context)
    monkeypatch.setattr(workflow, "_mark_pipeline_done", mark_done)
    monkeypatch.setattr(workflow, "emit_event", AsyncMock())
    monkeypatch.setattr(workflow, "_deep_app", SimpleNamespace(ainvoke=graph_call))
    monkeypatch.setattr(workflow, "_persist_deep_outputs", persist_deep_outputs)
    monkeypatch.setattr(
        "app.services.llm_cost_budget.check_and_apply_cap",
        AsyncMock(
            return_value=CapDecision(
                action="HARD_BLOCK",
                block=True,
                rationale="monthly cap reached",
                utilization_pct=101.0,
            )
        ),
    )

    result = await workflow.run_deep_pipeline(
        test_run_id=test_run_id,
        project_id=project_id,
        build_number="42",
    )

    graph_call.assert_not_awaited()
    persist_deep_outputs.assert_not_awaited()
    assert result["cost_budget_blocked"] is True
    assert result["skipped_stages"] == stages
    assert persist_context.await_args.args[2]["action"] == "HARD_BLOCK"
    mark_done.assert_awaited_once()


@pytest.mark.asyncio
async def test_partial_resume_preserves_original_budget_downgrade(monkeypatch):
    """Terminal metadata must keep the cap snapshot used by same-ID replay."""
    from app.agents import workflow
    from app.services import agent_action_ledger_service, run_downstream_outbox
    from app.services import llm_cost_budget

    pipeline_id = uuid.uuid4()
    run_id = uuid.uuid4()
    project_id = uuid.uuid4()
    budget_snapshot = {
        "action": "AUTO_DOWNGRADE_TO_RULES",
        "mode_override": "rules",
        "block": False,
        "rationale": "daily threshold reached",
        "utilization_pct": 91.0,
    }
    pipeline = SimpleNamespace(
        id=pipeline_id,
        test_run_id=run_id,
        project_id=project_id,
        workflow_type="offline",
        status="running",
        started_at=None,
        completed_at=None,
        error=None,
        execution_metadata={"cost_budget_decision": budget_snapshot},
    )
    summary_stage = SimpleNamespace(
        stage_name="summary",
        status="completed",
        attempt=1,
        idempotency_key="a" * 64,
        result_data={},
    )
    failed_stage = SimpleNamespace(
        stage_name="triage",
        status="failed",
        attempt=1,
        idempotency_key="b" * 64,
        result_data={},
        started_at="started",
        completed_at="completed",
        error="failed",
        stop_reason="capability_error",
        skipped_reason=None,
        execution_path="executed",
    )
    stages = [summary_stage, failed_stage]

    class _Result:
        def __init__(self, *, scalar=None, scalars=None):
            self._scalar = scalar
            self._scalars = scalars or []

        def scalar_one_or_none(self):
            return self._scalar

        def scalars(self):
            return SimpleNamespace(all=lambda: self._scalars)

    class _Session:
        def __init__(self, results):
            self._results = iter(results)
            self.commit = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, _statement):
            return next(self._results)

    terminal_session = _Session(
        [_Result(scalar=pipeline), _Result(scalars=stages), _Result()]
    )
    resume_session = _Session(
        [
            _Result(scalar=pipeline),
            _Result(scalar=project_id),
            _Result(scalars=stages),
        ]
    )
    sessions = iter([terminal_session, resume_session])
    monkeypatch.setattr(workflow, "AsyncSessionLocal", lambda: next(sessions))
    monkeypatch.setattr(
        agent_action_ledger_service,
        "persist_report_action_proposals",
        AsyncMock(),
    )
    monkeypatch.setattr(
        run_downstream_outbox,
        "stage_ai_summary_notification_operation",
        AsyncMock(),
    )

    final_state = {
        "project_id": str(project_id),
        "test_run_id": str(run_id),
        "build_number": "42",
        "initial_workflow_plan": {"stages": ["summary", "triage"]},
        "workflow_plan": {"stages": ["summary", "triage"]},
        "analysis_mode_requested": "llm",
        "analysis_mode_resolved": "llm",
        "analysis_mode_resolution": {
            "requested": "llm",
            "resolved": "llm",
        },
    }
    await workflow._mark_pipeline_done(
        str(pipeline_id), success=True, final_state=final_state
    )

    assert pipeline.status == "partial"
    assert pipeline.execution_metadata["cost_budget_decision"] == budget_snapshot

    replay_setup = await workflow._claim_pipeline_resume(str(pipeline_id))
    assert replay_setup is not None
    assert replay_setup["cost_budget_decision"] == budget_snapshot

    cap_check = AsyncMock(
        side_effect=AssertionError("resume must not re-evaluate the cost cap")
    )
    persist_context = AsyncMock()
    monkeypatch.setattr(llm_cost_budget, "check_and_apply_cap", cap_check)
    monkeypatch.setattr(workflow, "_persist_execution_context", persist_context)

    replay_budget = await workflow._prepare_pipeline_cost_budget(
        pipeline_run_id=str(pipeline_id),
        project_id=str(project_id),
        pipeline_setup=replay_setup,
        mode_snapshot=replay_setup["analysis_mode_resolution"],
        cost_budget_mode_override=None,
    )

    cap_check.assert_not_awaited()
    assert replay_budget == budget_snapshot
    assert replay_budget["mode_override"] == "rules"
    persist_context.assert_awaited_once()


def test_durable_task_forwards_prechecked_budget_downgrade(monkeypatch):
    import asyncio

    from app.agents import workflow
    from app.services import intelligence_snapshot_service
    from app.worker import tasks

    run_pipeline = AsyncMock(return_value={"completed_stages": [], "errors": []})
    monkeypatch.setattr(workflow, "run_offline_pipeline", run_pipeline)
    monkeypatch.setattr(tasks, "_is_duplicate", AsyncMock(return_value=False))
    monkeypatch.setattr(
        intelligence_snapshot_service, "invalidate", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)

    task = tasks.run_agent_pipeline
    task.push_request(
        id="budget-downgrade",
        retries=0,
        headers={"ai_mode_override": "rules"},
    )
    try:
        task.run(
            test_run_id=str(uuid.uuid4()),
            project_id=str(uuid.uuid4()),
            build_number="42",
        )
    finally:
        task.pop_request()

    assert run_pipeline.await_args.kwargs["cost_budget_mode_override"] == "rules"


def test_durable_deep_task_forwards_prechecked_budget_downgrade(monkeypatch):
    import asyncio

    from app.agents import workflow
    from app.services import intelligence_snapshot_service
    from app.worker import tasks

    run_pipeline = AsyncMock(return_value={"completed_stages": [], "errors": []})
    monkeypatch.setattr(workflow, "run_deep_pipeline", run_pipeline)
    monkeypatch.setattr(tasks, "_is_duplicate", AsyncMock(return_value=False))
    monkeypatch.setattr(
        intelligence_snapshot_service, "invalidate", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)

    task = tasks.run_agent_pipeline
    task.push_request(
        id="deep-budget-downgrade",
        retries=0,
        headers={"ai_mode_override": "ml"},
    )
    try:
        task.run(
            test_run_id=str(uuid.uuid4()),
            project_id=str(uuid.uuid4()),
            build_number="42",
            workflow_type="deep",
        )
    finally:
        task.pop_request()

    assert run_pipeline.await_args.kwargs["cost_budget_mode_override"] == "ml"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ml", "rules"])
async def test_regression_watchman_downgrade_never_constructs_llm(
    monkeypatch, mode
):
    from app.agents import regression_watchman

    agent = regression_watchman.RegressionWatchman()
    monkeypatch.setattr(agent, "_resolve_run_branch", AsyncMock(return_value="main"))
    monkeypatch.setattr(
        agent,
        "_fetch_cluster_history",
        AsyncMock(
            return_value={
                "cluster-1": {
                    "baseline_run_count": 0,
                    "recent_occurrences": 0,
                    "seen_in_baseline": False,
                }
            }
        ),
    )
    get_llm = AsyncMock(side_effect=AssertionError("LLM must remain gated"))
    monkeypatch.setattr(regression_watchman, "get_llm", get_llm)

    result = await agent._classify({
        "test_run_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "failure_clusters": [{
            "cluster_id": "cluster-1",
            "member_test_ids": ["test-1"],
        }],
        "analyses": {},
        "_cost_budget_prechecked": True,
        "_cost_budget_mode_override": mode,
        "_cost_budget_block": False,
    })

    get_llm.assert_not_awaited()
    assert result["cluster-1"]["reason_code"] == "LOW_EVIDENCE"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ml", "rules"])
async def test_release_risk_downgrade_never_constructs_llm(monkeypatch, mode):
    from app.agents import release_risk_agent
    from app.services import policy_evaluator_service

    agent = release_risk_agent.ReleaseRiskAgent()
    monkeypatch.setattr(
        agent,
        "_load_release_memory_context",
        AsyncMock(return_value={"open_defects": 0, "source": "test"}),
    )
    monkeypatch.setattr(
        release_risk_agent,
        "compute_dimension_scores",
        lambda **kwargs: {
            "user_impact": 50.0,
            "regression_likely": 50.0,
            "blast_radius": 50.0,
        },
    )
    monkeypatch.setattr(release_risk_agent, "compute_composite", lambda scores: 50.0)
    policy = SimpleNamespace(
        effective_composite=50.0,
        recommendation="CONDITIONAL_GO",
        policy_id=None,
        rule_evaluations=[],
        kind_rule_applied=False,
        kind_counterfactual=None,
        to_dict=lambda: {},
    )
    monkeypatch.setattr(
        policy_evaluator_service,
        "evaluate_policy",
        AsyncMock(return_value=policy),
    )
    get_llm = AsyncMock(side_effect=AssertionError("LLM must remain gated"))
    monkeypatch.setattr(release_risk_agent, "get_llm", get_llm)

    result = await agent._evaluate({
        "project_id": str(uuid.uuid4()),
        "analyses": {},
        "anomalies": [],
        "pass_rate": 80.0,
        "is_regression": False,
        "regression_tests": [],
        "failure_clusters": [],
        "executive_summary": "",
        "_cost_budget_prechecked": True,
        "_cost_budget_mode_override": mode,
        "_cost_budget_block": False,
    })

    get_llm.assert_not_awaited()
    assert result["recommendation"] == "CONDITIONAL_GO"
    assert "persisted cost-budget decision" in result["reasoning"]


def test_suite_comparison_rechecks_each_suite_and_falls_back_after_cap(monkeypatch):
    import asyncio

    from app.db import postgres
    from app.services import llm_cost_budget, run_compare_ai_service, run_compare_service
    from app.services.llm_cost_budget import CapDecision
    from app.worker import tasks

    test_run_id = uuid.uuid4()
    project_id = uuid.uuid4()
    previous_by_suite = {"alpha": uuid.uuid4(), "beta": uuid.uuid4()}
    suites_result = MagicMock()
    suites_result.all.return_value = [
        SimpleNamespace(suite_name="beta"),
        SimpleNamespace(suite_name="alpha"),
    ]
    primary_result = MagicMock()
    primary_result.scalar_one_or_none.return_value = None
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[suites_result, primary_result]),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(tasks, "_run_async", asyncio.run)
    monkeypatch.setattr(postgres, "AsyncSessionLocal", lambda: _SessionContext())
    budget_check = AsyncMock(
        side_effect=[
            CapDecision(action="UNLIMITED"),
            CapDecision(
                action="HARD_BLOCK",
                block=True,
                rationale="monthly cap reached after alpha",
                utilization_pct=100.0,
            ),
        ]
    )
    monkeypatch.setattr(
        llm_cost_budget,
        "check_and_apply_cap",
        budget_check,
    )

    async def _pair(_db, *, suite_name, **_kwargs):
        return (
            SimpleNamespace(id=previous_by_suite[suite_name], branch="main"),
            SimpleNamespace(id=test_run_id, branch="main"),
        )

    monkeypatch.setattr(
        run_compare_service,
        "resolve_suite_pair_for_run",
        AsyncMock(side_effect=_pair),
    )
    monkeypatch.setattr(
        run_compare_service,
        "compare_runs",
        AsyncMock(side_effect=lambda *_args, suite_name, **_kwargs: {
            "scope": "suite",
            "suite_name": suite_name,
        }),
    )
    monkeypatch.setattr(
        run_compare_ai_service,
        "is_report_ready",
        AsyncMock(side_effect=[False, True, False, True]),
    )
    generate = AsyncMock(return_value={"status": "ready"})
    monkeypatch.setattr(run_compare_ai_service, "generate_and_save_report", generate)

    task = tasks.precompute_suite_comparisons_for_run
    task.push_request(id="comparison-budget-per-suite", retries=0)
    try:
        result = task.run(
            test_run_id=str(test_run_id), project_id=str(project_id)
        )
    finally:
        task.pop_request()

    assert result["generated"] == 2
    assert result["budget_actions"] == ["UNLIMITED", "HARD_BLOCK"]
    assert result["deterministic_fallbacks"] == 1
    assert budget_check.await_count == 2
    assert [call.kwargs["suite_name"] for call in generate.await_args_list] == [
        "alpha",
        "beta",
    ]
    assert generate.await_args_list[0].kwargs["deterministic_only"] is False
    assert generate.await_args_list[1].kwargs["deterministic_only"] is True
    assert all(
        call.kwargs["cost_budget_prechecked"] is True
        for call in generate.await_args_list
    )


def test_suite_comparison_downgrade_uses_deterministic_report(monkeypatch):
    import asyncio

    from app.db import postgres
    from app.services import llm_cost_budget, run_compare_ai_service, run_compare_service
    from app.services.llm_cost_budget import CapDecision
    from app.worker import tasks

    test_run_id = uuid.uuid4()
    project_id = uuid.uuid4()
    previous_id = uuid.uuid4()
    suites_result = MagicMock()
    suites_result.all.return_value = [SimpleNamespace(suite_name="unit")]
    primary_result = MagicMock()
    primary_result.scalar_one_or_none.return_value = " unit "
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[suites_result, primary_result]),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    generate = AsyncMock(return_value={"status": "ready", "fallback_used": True})
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)
    monkeypatch.setattr(postgres, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(
        llm_cost_budget,
        "check_and_apply_cap",
        AsyncMock(
            return_value=CapDecision(
                action="AUTO_DOWNGRADE_TO_RULES",
                mode_override="rules",
                rationale="budget threshold reached",
                utilization_pct=90.0,
            )
        ),
    )
    monkeypatch.setattr(
        run_compare_service,
        "resolve_suite_pair_for_run",
        AsyncMock(
            return_value=(
                SimpleNamespace(id=previous_id, branch="main"),
                SimpleNamespace(id=test_run_id, branch="main"),
            )
        ),
    )
    monkeypatch.setattr(
        run_compare_ai_service,
        "is_report_ready",
        AsyncMock(side_effect=[False, True]),
    )
    monkeypatch.setattr(
        run_compare_service,
        "compare_runs",
        AsyncMock(return_value={"scope": "suite", "suite_name": "unit"}),
    )
    monkeypatch.setattr(run_compare_ai_service, "generate_and_save_report", generate)

    task = tasks.precompute_suite_comparisons_for_run
    task.push_request(id="comparison-budget-downgrade", retries=0)
    try:
        result = task.run(test_run_id=str(test_run_id), project_id=str(project_id))
    finally:
        task.pop_request()

    assert result["generated"] == 1
    assert result["cost_budget_mode_override"] == "rules"
    assert generate.await_args.kwargs["deterministic_only"] is True
    assert generate.await_args.kwargs["cost_budget_prechecked"] is True
    db.commit.assert_awaited_once()


def test_suite_comparison_retries_failures_and_resumes_ready_reports(monkeypatch):
    import asyncio

    from celery.exceptions import Retry

    from app.db import postgres
    from app.services import llm_cost_budget, run_compare_ai_service, run_compare_service
    from app.services.llm_cost_budget import CapDecision
    from app.worker import tasks

    test_run_id = uuid.uuid4()
    project_id = uuid.uuid4()
    previous_by_suite = {"alpha": uuid.uuid4(), "beta": uuid.uuid4()}

    def _db():
        suites_result = MagicMock()
        suites_result.all.return_value = [
            SimpleNamespace(suite_name="beta"),
            SimpleNamespace(suite_name="alpha"),
        ]
        primary_result = MagicMock()
        primary_result.scalar_one_or_none.return_value = "ALPHA"
        return SimpleNamespace(
            execute=AsyncMock(side_effect=[suites_result, primary_result]),
            commit=AsyncMock(),
            rollback=AsyncMock(),
        )

    databases = [_db(), _db()]
    sessions = iter(databases)

    class _SessionContext:
        async def __aenter__(self):
            return next(sessions)

        async def __aexit__(self, *_args):
            return False

    async def _pair(_db, *, suite_name, **kwargs):
        return (
            SimpleNamespace(id=previous_by_suite[suite_name], branch="main"),
            SimpleNamespace(id=test_run_id, branch="main"),
        )

    generate = AsyncMock(
        side_effect=[
            {"status": "ready"},
            RuntimeError("provider timeout"),
            {"status": "ready"},
        ]
    )
    ready = AsyncMock(side_effect=[False, True, False, True, False, True])
    mark_failed = AsyncMock()
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)
    monkeypatch.setattr(postgres, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(
        llm_cost_budget,
        "check_and_apply_cap",
        AsyncMock(return_value=CapDecision(action="UNLIMITED")),
    )
    monkeypatch.setattr(
        run_compare_service,
        "resolve_suite_pair_for_run",
        AsyncMock(side_effect=_pair),
    )
    monkeypatch.setattr(
        run_compare_service,
        "compare_runs",
        AsyncMock(return_value={"scope": "suite"}),
    )
    monkeypatch.setattr(run_compare_ai_service, "is_report_ready", ready)
    monkeypatch.setattr(
        run_compare_ai_service,
        "generate_and_save_report",
        generate,
    )
    monkeypatch.setattr(run_compare_ai_service, "mark_failed", mark_failed)

    task = tasks.precompute_suite_comparisons_for_run
    retry = MagicMock(side_effect=Retry())
    monkeypatch.setattr(task, "retry", retry)
    task.push_request(id="comparison-partial", retries=0)
    try:
        with pytest.raises(Retry):
            task.run(test_run_id=str(test_run_id), project_id=str(project_id))
    finally:
        task.pop_request()

    retry.assert_called_once()
    assert generate.await_args_list[0].kwargs["suite_name"] == "alpha"
    assert generate.await_args_list[1].kwargs["suite_name"] == "beta"
    mark_failed.assert_awaited_once()
    assert databases[0].commit.await_count == 2
    assert databases[0].rollback.await_count == 1

    task.push_request(id="comparison-resume", retries=1)
    try:
        result = task.run(test_run_id=str(test_run_id), project_id=str(project_id))
    finally:
        task.pop_request()

    assert result["generated"] == 1
    assert result["already_ready"] == 1
    assert generate.await_args_list[2].kwargs["suite_name"] == "beta"
    assert databases[1].commit.await_count == 1
    pair_calls = run_compare_service.resolve_suite_pair_for_run.await_args_list
    assert all(call.kwargs["right_run_id"] == test_run_id for call in pair_calls)


def test_suite_comparison_includes_primary_suite_without_case_suite_rows(monkeypatch):
    import asyncio

    from app.db import postgres
    from app.services import llm_cost_budget, run_compare_ai_service, run_compare_service
    from app.services.llm_cost_budget import CapDecision
    from app.worker import tasks

    test_run_id = uuid.uuid4()
    project_id = uuid.uuid4()
    previous_id = uuid.uuid4()
    suites_result = MagicMock()
    suites_result.all.return_value = []
    primary_result = MagicMock()
    primary_result.scalar_one_or_none.return_value = "  Primary Smoke  "
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[suites_result, primary_result]),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(tasks, "_run_async", asyncio.run)
    monkeypatch.setattr(postgres, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(
        llm_cost_budget,
        "check_and_apply_cap",
        AsyncMock(return_value=CapDecision(action="UNLIMITED")),
    )
    resolve_pair = AsyncMock(
        return_value=(
            SimpleNamespace(id=previous_id, branch="main"),
            SimpleNamespace(id=test_run_id, branch="main"),
        )
    )
    monkeypatch.setattr(run_compare_service, "resolve_suite_pair_for_run", resolve_pair)
    monkeypatch.setattr(
        run_compare_service,
        "compare_runs",
        AsyncMock(return_value={"scope": "suite"}),
    )
    monkeypatch.setattr(
        run_compare_ai_service,
        "is_report_ready",
        AsyncMock(side_effect=[False, True]),
    )
    monkeypatch.setattr(
        run_compare_ai_service,
        "generate_and_save_report",
        AsyncMock(return_value={"status": "ready"}),
    )

    task = tasks.precompute_suite_comparisons_for_run
    task.push_request(id="comparison-primary-suite", retries=0)
    try:
        result = task.run(test_run_id=str(test_run_id), project_id=str(project_id))
    finally:
        task.pop_request()

    assert result["generated"] == 1
    assert resolve_pair.await_args.kwargs["suite_name"] == "Primary Smoke"
