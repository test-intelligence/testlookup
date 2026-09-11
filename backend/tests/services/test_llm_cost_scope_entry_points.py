"""Re-audit M13: the entry points that run LLM work charge it to their project.

The per-call reservation (llm_cost_reservation.reserve) only knows whose cap to
hold against through ``cost_budget_scope``. A pipeline or report that does not
set it runs every call unreserved -- the cap would be back to check-then-act
for that path with every test still green. Each test records the scope the
graph (or agent) actually sees when it is invoked.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.llm_cost_reservation import current_cost_scope


class _Stop(Exception):
    """Ends the run once the scope has been observed."""


def _setup(run_id: str, project_id: str, workflow_type: str, stages: list[str]) -> dict:
    return {
        "test_run_id": run_id,
        "project_id": project_id,
        "workflow_type": workflow_type,
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


def _unblocked(monkeypatch, workflow, setup):
    from app.services.llm_cost_budget import CapDecision

    monkeypatch.setattr(workflow, "_create_pipeline_run", AsyncMock(return_value=setup))
    monkeypatch.setattr(workflow, "_load_checkpoint", AsyncMock(return_value=None))
    monkeypatch.setattr(
        workflow,
        "_resolve_analysis_mode_snapshot",
        AsyncMock(return_value={"requested": "llm", "resolved": "llm", "resolution_reason": "configured"}),
    )
    monkeypatch.setattr(workflow, "_persist_execution_context", AsyncMock())
    monkeypatch.setattr(workflow, "_mark_pipeline_done", AsyncMock())
    monkeypatch.setattr(workflow, "emit_event", AsyncMock())
    monkeypatch.setattr(
        "app.services.llm_cost_budget.check_and_apply_cap",
        AsyncMock(return_value=CapDecision(action="UNLIMITED")),
    )


def _recording_graph(seen: list):
    async def ainvoke(state):
        seen.append(current_cost_scope())
        raise _Stop()

    return SimpleNamespace(ainvoke=ainvoke)


@pytest.mark.asyncio
async def test_the_standard_pipeline_graph_runs_inside_its_projects_scope(monkeypatch):
    from app.agents import workflow

    run_id, project_id = str(uuid.uuid4()), str(uuid.uuid4())
    _unblocked(monkeypatch, workflow, _setup(run_id, project_id, "offline", ["ingestion", "summary"]))
    seen: list = []
    monkeypatch.setattr(workflow, "_offline_app", _recording_graph(seen))

    with pytest.raises(_Stop):
        await workflow.run_offline_pipeline(test_run_id=run_id, project_id=project_id, build_number="1")
    assert seen == [project_id]
    assert current_cost_scope() is None  # and it does not leak out


@pytest.mark.asyncio
async def test_the_deep_pipeline_graph_runs_inside_its_projects_scope(monkeypatch):
    from app.agents import workflow

    run_id, project_id = str(uuid.uuid4()), str(uuid.uuid4())
    _unblocked(monkeypatch, workflow, _setup(run_id, project_id, "deep", ["ingestion", "failure_clustering"]))
    seen: list = []
    monkeypatch.setattr(workflow, "_deep_app", _recording_graph(seen))

    with pytest.raises(_Stop):
        await workflow.run_deep_pipeline(test_run_id=run_id, project_id=project_id, build_number="1")
    assert seen == [project_id]


@pytest.mark.asyncio
async def test_the_run_compare_report_runs_inside_its_projects_scope(monkeypatch):
    from app.services import run_compare_ai_service as service

    project_id = uuid.uuid4()
    seen: list = []

    class Agent:
        async def generate(self, payload):
            seen.append(current_cost_scope())
            return {"fallback_used": False}

    monkeypatch.setattr(service, "mark_queued", AsyncMock())
    monkeypatch.setattr(service, "_get_row", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "RunCompareAgent", Agent)

    await service.generate_and_save_report(
        SimpleNamespace(flush=AsyncMock()),
        project_id=project_id,
        left_run_id=uuid.uuid4(),
        right_run_id=uuid.uuid4(),
        suite_name=None,
        compare_payload={"scope": "run"},
        cost_budget_prechecked=True,
    )
    assert seen == [str(project_id)]
    assert current_cost_scope() is None
