from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sys
import types
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.agents import workflow
from app.services import cluster_investigation_orchestrator as orchestrator


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalar(self):
        return self.rows[0] if self.rows else None


def _session_local(db):
    class _Context:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    return _Context


@pytest.mark.asyncio
async def test_settings_require_flag_and_policy_and_preserve_hard_zero(monkeypatch):
    project_id = uuid4()
    monkeypatch.setattr(orchestrator, "is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        orchestrator,
        "get_effective_policy",
        AsyncMock(return_value={
            "enabled": True,
            "budgets": {
                "max_cluster_children_per_run": 2,
                "max_cluster_members_per_child": 25,
                "max_cluster_child_llm_calls_per_parent": 0,
                "max_cluster_child_tokens_per_parent": 0,
                "max_cluster_child_cost_usd_per_parent": 0.0,
                "max_cluster_child_seconds_per_parent": 0,
                "max_active_cluster_children_per_project": 1,
                "max_cluster_children_per_day": 3,
            },
        }),
    )

    result = await orchestrator.resolve_cluster_child_settings(
        AsyncMock(), project_id
    )

    assert result["enabled"] is True
    assert result["max_children"] == 2
    assert result["max_active_per_project"] == 1
    assert result["aggregate_budget"] == {
        "max_llm_calls": 0,
        "max_tokens": 0,
        "max_cost_usd": 0.0,
        "max_seconds": 0,
    }


@pytest.mark.asyncio
async def test_dispatch_node_persists_cluster_authority_before_staging(monkeypatch):
    calls: list[str] = []
    persist = AsyncMock(side_effect=lambda *args: calls.append("persist"))
    stage = AsyncMock(side_effect=lambda **kwargs: calls.append("stage") or {
        "status": "dispatched",
        "planner_sha256": "a" * 64,
    })
    monkeypatch.setattr(
        "app.agents.deep_persistence.persist_failure_cluster_snapshot", persist
    )
    monkeypatch.setattr(
        "app.services.cluster_investigation_orchestrator.stage_cluster_investigations",
        stage,
    )
    state = {
        "test_run_id": str(uuid4()),
        "pipeline_run_id": str(uuid4()),
        "project_id": str(uuid4()),
        "failure_clusters": [{
            "cluster_id": "cl_001",
            "member_test_ids": [str(uuid4())],
        }],
        "cluster_child_settings": {"enabled": True},
        "_cost_budget_action": "DOWNGRADE_RULES",
        "_cost_budget_mode_override": "rules",
        "_cost_budget_block": False,
        "_cost_budget_rationale": "monthly cap",
        "_cost_budget_utilization_pct": 100.0,
    }

    result = await workflow.cluster_investigation_dispatch_node(state)

    assert calls == ["persist", "stage"]
    assert result["cluster_investigation_plan"]["planner_sha256"] == "a" * 64
    assert result["completed_stages"] == ["cluster_investigation_dispatch"]
    assert stage.await_args.kwargs["cost_budget_decision"] == {
        "action": "DOWNGRADE_RULES",
        "mode_override": "rules",
        "block": False,
        "rationale": "monthly cap",
        "utilization_pct": 100.0,
    }


def test_parent_downgrade_removes_llm_budget_from_every_cluster_child():
    from app.services.agent_planner import build_cluster_investigation_plan

    parent_id, project_id, run_id = uuid4(), uuid4(), uuid4()
    decision = {
        "action": "DOWNGRADE_RULES",
        "mode_override": "rules",
        "block": False,
    }
    parent = SimpleNamespace(
        execution_metadata={"cost_budget_decision": decision}
    )
    settings = {
        "aggregate_budget": {
            "max_llm_calls": 8,
            "max_tokens": 24_000,
            "max_cost_usd": 3.0,
            "max_seconds": 300,
        }
    }
    capped = orchestrator._apply_parent_cost_budget(parent, settings, decision)
    plan = build_cluster_investigation_plan(
        parent_pipeline_run_id=parent_id,
        project_id=project_id,
        run_id=run_id,
        clusters=[
            {
                "failure_cluster_id": str(uuid4()),
                "cluster_id": f"cluster-{index}",
                "member_test_ids": [str(uuid4())],
            }
            for index in range(2)
        ],
        aggregate_budget=capped["aggregate_budget"],
        max_children=2,
    )

    assert plan["selected"]
    for task in plan["selected"]:
        assert task["budget"]["max_llm_calls"] == 0
        assert task["budget"]["max_tokens"] == 0
        assert task["budget"]["max_cost_usd"] == 0.0
    assert settings["aggregate_budget"]["max_llm_calls"] == 8


@pytest.mark.asyncio
async def test_join_node_surfaces_child_degradation(monkeypatch):
    wait = AsyncMock(return_value={
        "status": "degraded",
        "stop_reasons": ["cluster_child_failed"],
        "children": [],
    })
    monkeypatch.setattr(
        "app.services.cluster_investigation_orchestrator.wait_for_cluster_children",
        wait,
    )
    state = {
        "pipeline_run_id": str(uuid4()),
        "cluster_child_settings": {
            "aggregate_budget": {"max_seconds": 9}
        },
        "stage_quality": "normal",
    }

    result = await workflow.cluster_investigation_join_node(state)

    assert result["stage_quality"] == "degraded"
    assert result["errors"] == [
        "cluster investigations degraded: cluster_child_failed"
    ]
    wait.assert_awaited_once_with(
        parent_pipeline_run_id=state["pipeline_run_id"],
        timeout_seconds=9,
    )


@pytest.mark.asyncio
async def test_join_node_dark_gated_async_supersession_does_not_wait(monkeypatch):
    wait = AsyncMock()
    schedule = AsyncMock(return_value={
        "request_id": str(uuid4()),
        "status": "pending",
    })
    monkeypatch.setattr(
        "app.services.cluster_investigation_orchestrator.wait_for_cluster_children",
        wait,
    )
    monkeypatch.setattr(
        "app.services.decision_report_supersession_service.schedule_decision_report_supersession",
        schedule,
    )
    state = {
        "pipeline_run_id": str(uuid4()),
        "test_run_id": str(uuid4()),
        "project_id": str(uuid4()),
        "async_decision_report_supersession_enabled": True,
        "cluster_child_settings": {"aggregate_budget": {"max_seconds": 300}},
        "stage_quality": "normal",
    }

    result = await workflow.cluster_investigation_join_node(state)

    wait.assert_not_awaited()
    schedule.assert_awaited_once()
    assert result["cluster_investigation_results"]["status"] == "pending"
    assert result["stage_quality"] == "degraded"
    assert "cluster investigations deferred" in result["errors"][0]


@pytest.mark.asyncio
async def test_join_node_discloses_capacity_skip_even_when_no_child_row(monkeypatch):
    monkeypatch.setattr(
        "app.services.cluster_investigation_orchestrator.wait_for_cluster_children",
        AsyncMock(return_value={
            "status": "complete",
            "stop_reasons": [],
            "children": [],
        }),
    )
    state = {
        "pipeline_run_id": str(uuid4()),
        "cluster_child_settings": {
            "aggregate_budget": {"max_seconds": 1}
        },
        "cluster_investigation_plan": {
            "dispatch_capacity_skips": [{
                "failure_cluster_id": str(uuid4()),
                "skip_reason": "project_child_capacity_exhausted",
            }]
        },
        "stage_quality": "normal",
    }
    result = await workflow.cluster_investigation_join_node(state)

    assert result["stage_quality"] == "degraded"
    assert result["cluster_investigation_results"]["stop_reasons"] == [
        "project_child_capacity_exhausted"
    ]


def test_deep_graph_contains_durable_dispatch_and_join():
    graph = workflow._build_deep_graph()  # noqa: SLF001
    nodes = set(graph.nodes)
    assert {
        "failure_clustering",
        "cluster_investigation_dispatch",
        "cluster_investigation_join",
        "summary",
    }.issubset(nodes)


def test_dispatch_settings_must_match_persisted_parent_snapshot():
    persisted = {
        "enabled": False,
        "max_children": 0,
        "aggregate_budget": {"max_seconds": 0},
    }
    parent = type("Parent", (), {
        "execution_metadata": {"cluster_child_settings": persisted}
    })()

    assert orchestrator._persisted_cluster_child_settings(  # noqa: SLF001
        parent, dict(persisted)
    ) == persisted
    with pytest.raises(ValueError, match="cluster_child_settings_mismatch"):
        orchestrator._persisted_cluster_child_settings(  # noqa: SLF001
            parent,
            {
                "enabled": True,
                "max_children": 4,
                "aggregate_budget": {"max_seconds": 300},
            },
        )


@pytest.mark.asyncio
async def test_join_timeout_terminalizes_queued_child_outbox_and_task(
    monkeypatch,
):
    parent_id = uuid4()
    child = SimpleNamespace(
        id=uuid4(), status="queued", completed_at=None, error=None,
        cancelled_by=None, cancel_requested=False,
        failure_cluster_id=uuid4(), cluster_scope_sha256="a" * 64,
        parent_task_id=f"pipeline:{parent_id}:task:cluster-investigation:test",
        spend={}, verdict={},
    )
    outbox = SimpleNamespace(
        status="sent", next_attempt_at=None, last_error=None
    )
    stage = SimpleNamespace(
        status="pending", stop_reason=None, completed_at=None,
        result_data=None,
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _Rows([child]),
        _Rows([child]),
        _Rows([outbox]),
        _Rows([child]),
        _Rows([stage]),
    ])
    db.commit = AsyncMock()
    monkeypatch.setattr(orchestrator, "AsyncSessionLocal", _session_local(db))
    monkeypatch.setattr(
        orchestrator,
        "relay_child_dispatch_outbox",
        AsyncMock(return_value={"claimed": 0, "sent": 0, "failed": 0}),
    )

    result = await orchestrator.wait_for_cluster_children(
        parent_pipeline_run_id=str(parent_id),
        timeout_seconds=0,
    )

    assert child.status == "cancelled"
    assert child.cancelled_by == "parent_join_timeout"
    assert outbox.status == "failed"
    assert outbox.next_attempt_at is None
    assert stage.status == "cancelled"
    assert stage.stop_reason == "cluster_child_join_timeout"
    assert stage.completed_at is not None
    assert result["status"] == "degraded"
    assert result["stop_reasons"] == ["cluster_child_join_timeout"]


@pytest.mark.asyncio
async def test_permanent_relay_failure_locks_parent_first_and_terminalizes(
    monkeypatch,
):
    outbox = SimpleNamespace(
        id=uuid4(), investigation_id=uuid4(), spawn_key="a" * 64,
        parent_pipeline_run_id=uuid4(), status="pending", attempts=9,
        next_attempt_at=None, sent_at=None, last_error=None,
    )
    investigation = SimpleNamespace(
        id=outbox.investigation_id, status="queued", completed_at=None,
        error=None, parent_task_id=(
            f"pipeline:{outbox.parent_pipeline_run_id}:task:cluster:test"
        ),
    )
    stage = SimpleNamespace(
        status="pending", stop_reason=None, completed_at=None
    )
    claim_db = AsyncMock()
    claim_db.execute = AsyncMock(return_value=_Rows([outbox]))
    claim_db.commit = AsyncMock()
    failure_db = AsyncMock()
    failure_db.execute = AsyncMock(side_effect=[
        _Rows([outbox]),
        _Rows([investigation]),
        _Rows([outbox]),
        _Rows([stage]),
    ])
    failure_db.commit = AsyncMock()

    class _Sessions:
        def __init__(self):
            self.items = iter((claim_db, failure_db))

        def __call__(self):
            db = next(self.items)

            class _Context:
                async def __aenter__(self):
                    return db

                async def __aexit__(self, *_args):
                    return False

            return _Context()

    task = SimpleNamespace(
        apply_async=lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("broker unavailable")
        )
    )
    worker_tasks = types.ModuleType("app.worker.tasks")
    worker_tasks.run_agent_child_investigation = task
    monkeypatch.setitem(sys.modules, "app.worker.tasks", worker_tasks)
    monkeypatch.setattr(orchestrator, "AsyncSessionLocal", _Sessions())

    result = await orchestrator.relay_child_dispatch_outbox()

    assert result == {"claimed": 1, "sent": 0, "failed": 1}
    assert outbox.status == "failed"
    assert outbox.attempts == 10
    assert outbox.next_attempt_at is None
    assert investigation.status == "failed"
    assert investigation.error == "cluster_child_dispatch_exhausted"
    assert stage.status == "failed"
    assert stage.stop_reason == "cluster_child_dispatch_exhausted"
    queries = [str(call.args[0]) for call in failure_db.execute.await_args_list]
    assert "agent_investigations" in queries[1]
    assert "agent_child_dispatch_outbox" in queries[2]


@pytest.mark.asyncio
async def test_stale_sent_outbox_is_redelivered_only_for_queued_child(
    monkeypatch,
):
    outbox = SimpleNamespace(
        id=uuid4(), investigation_id=uuid4(), spawn_key="b" * 64,
        status="sent", attempts=1, next_attempt_at=None,
        sent_at=datetime.now(timezone.utc) - timedelta(
            seconds=orchestrator.OUTBOX_LEASE_SECONDS + 1
        ),
        last_error=None,
    )
    claim_db = AsyncMock()
    claim_db.execute = AsyncMock(return_value=_Rows([outbox]))
    claim_db.commit = AsyncMock()
    sent_db = AsyncMock()
    sent_db.execute = AsyncMock(return_value=_Rows([outbox]))
    sent_db.commit = AsyncMock()

    class _Sessions:
        def __init__(self):
            self.items = iter((claim_db, sent_db))

        def __call__(self):
            db = next(self.items)

            class _Context:
                async def __aenter__(self):
                    return db

                async def __aexit__(self, *_args):
                    return False

            return _Context()

    calls: list[dict] = []
    worker_tasks = types.ModuleType("app.worker.tasks")
    worker_tasks.run_agent_child_investigation = SimpleNamespace(
        apply_async=lambda **kwargs: calls.append(kwargs)
    )
    monkeypatch.setitem(sys.modules, "app.worker.tasks", worker_tasks)
    monkeypatch.setattr(orchestrator, "AsyncSessionLocal", _Sessions())

    result = await orchestrator.relay_child_dispatch_outbox()

    assert result == {"claimed": 1, "sent": 1, "failed": 0}
    assert outbox.status == "sent"
    assert outbox.attempts == 2
    assert outbox.sent_at is not None
    assert calls == [{
        "args": [str(outbox.investigation_id)],
        "queue": "agent_children",
        "task_id": f"cluster-child-{outbox.spawn_key}",
    }]
    claim_statement = claim_db.execute.await_args_list[0].args[0]
    claim_sql = str(claim_statement)
    claim_params = claim_statement.compile().params
    assert "JOIN agent_investigations" in claim_sql
    assert "agent_child_dispatch_outbox.sent_at <=" in claim_sql
    assert "agent_investigations.status =" in claim_sql
    assert "queued" in claim_params.values()


@pytest.mark.asyncio
async def test_existing_spawn_is_reused_when_new_child_capacity_is_zero(
    monkeypatch,
):
    from app.services.agent_planner import build_cluster_investigation_plan

    parent_id, project_id, run_id = uuid4(), uuid4(), uuid4()
    member_id, failure_cluster_id = uuid4(), uuid4()
    settings = {
        "enabled": True, "max_children": 1, "max_members": 10,
        "max_active_per_project": 1, "max_children_per_day": 1,
        "aggregate_budget": {
            "max_llm_calls": 1, "max_tokens": 100,
            "max_cost_usd": 0.1, "max_seconds": 10,
        },
    }
    cluster = SimpleNamespace(
        id=failure_cluster_id, cluster_id="cl_reuse",
        member_test_ids=[str(member_id)],
    )
    plan = build_cluster_investigation_plan(
        parent_pipeline_run_id=parent_id, project_id=project_id,
        run_id=run_id,
        clusters=[{
            "failure_cluster_id": str(failure_cluster_id),
            "cluster_id": "cl_reuse",
            "member_test_ids": [str(member_id)],
        }],
        aggregate_budget=settings["aggregate_budget"],
    )
    task = plan["selected"][0]
    parent = SimpleNamespace(
        execution_metadata={"cluster_child_settings": settings}
    )
    existing = SimpleNamespace(
        id=uuid4(), status="queued", parent_pipeline_run_id=parent_id,
        failure_cluster_id=failure_cluster_id,
        cluster_scope_sha256=task["cluster_scope_sha256"],
        spawn_depth=1,
    )
    stage = SimpleNamespace(selected=True, status="pending")
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _Rows([]), _Rows([parent]), _Rows([cluster]), _Rows([member_id]),
        _Rows([1]), _Rows([1]), _Rows([existing]), _Rows([stage]),
    ])
    db.commit = AsyncMock()
    monkeypatch.setattr(orchestrator, "AsyncSessionLocal", _session_local(db))

    result = await orchestrator.stage_cluster_investigations(
        parent_pipeline_run_id=str(parent_id), project_id=str(project_id),
        run_id=str(run_id), frozen_settings=dict(settings),
    )

    assert result["dispatched_count"] == 1
    assert result["dispatch_capacity_skips"] == []
    assert result["dispatch_children"][0]["investigation_id"] == str(existing.id)
