"""Operational series stay bounded while domain identities remain available."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core import metric_dimensions as dimensions
from app.models.postgres import TestCaseLifecycleState as LifecycleState, UserRole


def test_deployed_static_stages_and_governance_enums_fit_reviewed_inventory():
    agents = Path(__file__).resolve().parents[2] / "app" / "agents"
    declared = set()
    for path in agents.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef):
                for statement in node.body:
                    if isinstance(statement, ast.Assign) and any(
                        isinstance(target, ast.Name) and target.id == "stage_name"
                        for target in statement.targets
                    ) and isinstance(statement.value, ast.Constant):
                        declared.add(statement.value.value)
    assert declared <= dimensions.STAGE_NAMES
    assert {state.value for state in LifecycleState} | {"other"} == dimensions.LIFECYCLE_STATES
    assert {role.value for role in UserRole} | {"other"} == dimensions.ACTOR_ROLES


@pytest.mark.asyncio
async def test_custom_workflow_step_and_free_text_never_become_metric_labels(monkeypatch):
    from app.agents import base
    from app.services.workflow_step_context import workflow_step_scope

    class Agent(base.BaseAgent):
        stage_name = "summary"

        async def run(self, state):
            return state

    stage = SimpleNamespace(id=uuid.uuid4(), attempt=1)
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: None),
            SimpleNamespace(scalar_one_or_none=lambda: stage),
            SimpleNamespace(scalar_one_or_none=lambda: stage),
        ]), flush=AsyncMock(), commit=AsyncMock(),
    )

    class Session:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(base, "AsyncSessionLocal", Session)
    event = AsyncMock()
    monkeypatch.setattr(base, "emit_event", event)
    names = (
        "pipeline_stage_duration_seconds", "pipeline_stage_runs_total",
        "pipeline_stage_tokens_total", "pipeline_stage_cost_usd",
        "pipeline_stage_llm_calls_total", "pipeline_fallback_total",
        "pipeline_stage_errors_by_category",
    )
    instruments = {name: Mock() for name in names}
    for name, instrument in instruments.items():
        monkeypatch.setattr(base, name, instrument)
    custom_id = "customer-workflow-step-" + uuid.uuid4().hex
    reason = "provider response with arbitrary per-request detail"
    category = "customer-exception-" + uuid.uuid4().hex
    agent = Agent()
    with workflow_step_scope(custom_id):
        await agent.mark_stage_done(
            str(uuid.uuid4()), error="failed", input_tokens=2, output_tokens=3,
            llm_calls_count=1, cost_usd=0.1, fallback_reason=reason, error_category=category,
        )

    for instrument in instruments.values():
        assert instrument.labels.call_count >= 1
        assert {call.kwargs["stage_name"] for call in instrument.labels.call_args_list} == {"summary"}
    instruments["pipeline_fallback_total"].labels.assert_called_once_with(stage_name="summary", reason="other")
    instruments["pipeline_stage_errors_by_category"].labels.assert_called_once_with(stage_name="summary", error_category="other")
    assert stage.fallback_reason == reason
    assert stage.error_category == category
    assert event.await_args.kwargs["stage_name"] == custom_id
    assert event.await_args.kwargs["detail"]["fallback_reason"] == reason
    assert event.await_args.kwargs["detail"]["error_category"] == category
    # The SQL predicate still addresses the authored runtime step.
    assert custom_id in db.execute.await_args_list[2].args[0].compile().params.values()


def test_task_registry_is_frozen_and_hard_capped(monkeypatch):
    from app.worker import celery_app as worker

    registry = {f"registered.{index:04d}": object() for index in range(1000)}
    monkeypatch.setattr(worker, "celery_app", SimpleNamespace(tasks=registry))
    worker._metric_task_names.cache_clear()
    try:
        names = worker._metric_task_names()
        assert len(names) == dimensions.MAX_METRIC_TASK_NAMES
        registry["late-customer-task"] = object()
        assert worker._metric_task_names() is names
        assert worker._metric_task_name(SimpleNamespace(name="late-customer-task")) == "other"
        assert worker._metric_task_name(SimpleNamespace(name="registered.0000")) == "registered.0000"
        for index in range(1000):
            assert worker._metric_task_name(SimpleNamespace(name=f"unregistered-{index}")) == "other"
    finally:
        worker._metric_task_names.cache_clear()


def test_deployed_worker_registry_fits_task_budget():
    from app.worker import tasks  # noqa: F401 -- load the production registry
    from app.worker.celery_app import celery_app

    assert len(celery_app.tasks) <= dimensions.MAX_METRIC_TASK_NAMES
    assert "app.worker.tasks.run_flaky_quarantine_maintenance" in celery_app.tasks


def test_task_runtime_and_custom_states_use_finite_dimensions(monkeypatch):
    from app.core import metrics
    from app.worker import celery_app as worker

    histogram, counter = Mock(), Mock()
    monkeypatch.setattr(metrics, "celery_task_runtime_seconds", histogram)
    monkeypatch.setattr(metrics, "celery_tasks_total", counter)
    monkeypatch.setattr(worker, "_metric_task_names", lambda: frozenset({"registered.task"}))
    for index in range(1000):
        task = SimpleNamespace(name=f"unregistered-{index}", request=SimpleNamespace(delivery_info={"routing_key": f"ingestion.shard.{index}"}))
        worker._TASK_STARTED_AT["synthetic"] = 0
        worker._record_task_runtime(task_id="synthetic", task=task)
        worker._record_task_outcome(task=task, state=f"custom-state-{index}")
    assert {tuple(sorted(call.kwargs.items())) for call in histogram.labels.call_args_list} == {
        (("queue_name", "ingestion_shard"), ("task_name", "other")),
    }
    counter.labels.assert_called_with(task_name="other", status="other")
    assert "synthetic" not in worker._TASK_STARTED_AT
    assert {dimensions.task_queue_family(queue) for queue in (
        "ingestion.shard.0", "ingestion.shard.9000", "critical", "", "custom-route", "ingestion.shard.no",
    )} == {"ingestion_shard", "critical", "unknown", "other"}


def test_tenant_snapshot_series_are_retired():
    from app.core import metrics

    for name in ("workflow_eval_coverage", "test_cases_by_state", "automation_cases_orphaned"):
        assert not hasattr(metrics, name)


def test_machine_budget_matches_real_instruments_and_closed_dimensions():
    from app.core import metrics

    root = Path(__file__).resolve().parents[3]
    budget = json.loads((root / "infra/monitoring/metric-cardinality-budget.json").read_text(encoding="utf-8"))
    assert budget["task_registry_limit"] == dimensions.MAX_METRIC_TASK_NAMES
    total = 0
    for entry in budget["families"]:
        instrument = getattr(metrics, entry["instrument"])
        assert set(instrument._labelnames) == set(entry["dimensions"])
        children = 1
        for vocabulary in entry["dimensions"].values():
            if isinstance(vocabulary, list):
                children *= len(vocabulary)
            elif vocabulary == "MAX_METRIC_TASK_NAMES+other":
                children *= dimensions.MAX_METRIC_TASK_NAMES + 1
            else:
                children *= len(getattr(dimensions, vocabulary))
        assert entry["max_children"] == children
        samples = len(instrument._upper_bounds) + 3 if instrument._type == "histogram" else 2
        assert entry["max_samples_per_child"] == samples
        assert entry["max_series_per_process"] == children * samples
        assert entry["exported_name"] == instrument._name + ("_total" if instrument._type == "counter" else "")
        total += children * samples
    assert budget["conservative_series_per_process"] == total


def test_changed_dashboard_queries_are_the_executed_semantic_fixture():
    import yaml

    root = Path(__file__).resolve().parents[3]
    dashboard = json.loads((root / "infra/monitoring/grafana/dashboards/testlookup-overview.json").read_text(encoding="utf-8"))
    fixture = yaml.safe_load((root / "infra/monitoring/tests/metric-scope.yml").read_text(encoding="utf-8"))
    queries = {
        " ".join(target["expr"].split())
        for panel in dashboard["panels"] if panel.get("id") in {12, 31, 88}
        for target in panel["targets"]
    }
    tested = {
        " ".join(test["expr"].split())
        for group in fixture["tests"] for test in group["promql_expr_test"]
    }
    assert queries == tested
    assert fixture["rule_files"] == []  # A semantic fixture, never a loaded rule file.


def test_native_multiprocess_turnover_preserves_counts_without_identity_growth(tmp_path):
    root = Path(__file__).resolve().parents[3]
    directory = tmp_path / "private-metrics"
    directory.mkdir()
    env = dict(os.environ, PROMETHEUS_MULTIPROC_DIR=str(directory), PYTHONDONTWRITEBYTECODE="1")
    env.pop("prometheus_multiproc_dir", None)
    writer = r'''
import os, time
from types import SimpleNamespace
from app.services.test_management_metrics_service import _emit_counter
from app.worker.celery_app import _TASK_STARTED_AT, _record_task_runtime, _record_task_outcome
for index in range(1000):
    _emit_counter("transition", (f"project-{os.getpid()}-{index}", "draft", "active", "QA_ENGINEER"))
    _emit_counter("promotion", (f"project-{os.getpid()}-{index}",))
    task = SimpleNamespace(name="celery.group", request=SimpleNamespace(delivery_info={"routing_key": f"ingestion.shard.{index}"}))
    _TASK_STARTED_AT["synthetic"] = time.perf_counter() - 1
    _record_task_runtime(task_id="synthetic", task=task)
    _record_task_outcome(task=task, state="SUCCESS")
print(os.getpid())
'''
    reader = r'''
import json, sys
from prometheus_client import CollectorRegistry, generate_latest, multiprocess
from prometheus_client.parser import text_string_to_metric_families
if len(sys.argv) > 1:
    multiprocess.mark_process_dead(int(sys.argv[1]))
registry = CollectorRegistry()
multiprocess.MultiProcessCollector(registry)
prefixes = ("testlookup_test_case_transitions", "testlookup_test_case_promotions", "testlookup_celery_tasks", "celery_task_runtime_seconds")
samples = [dict(name=s.name, labels=s.labels, value=s.value) for f in text_string_to_metric_families(generate_latest(registry).decode()) for s in f.samples if s.name.startswith(prefixes)]
print(json.dumps(samples))
'''

    def run(script, *args):
        return subprocess.run(
            [sys.executable, "-c", script, *args], cwd=root / "backend", env=env,
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip()

    dead_pid = run(writer)
    before = json.loads(run(reader, dead_pid))
    run(writer)  # A replacement process uses a new PID and project identities.
    after = json.loads(run(reader))
    assert len(before) == len(after)
    assert "project-" not in json.dumps(after)
    for samples, count in ((before, 1000), (after, 2000)):
        values = {sample["name"]: sample["value"] for sample in samples if not sample["name"].endswith("_bucket")}
        assert values["testlookup_test_case_transitions_total"] == count
        assert values["testlookup_test_case_promotions_total"] == count
        assert values["testlookup_celery_tasks_total"] == count
        assert values["celery_task_runtime_seconds_count"] == count
        assert {sample["labels"]["queue_name"] for sample in samples if "queue_name" in sample["labels"]} == {"ingestion_shard"}
