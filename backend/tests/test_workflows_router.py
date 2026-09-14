from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.models.postgres import WorkflowDefinition
from app.routers import workflows
from app.services import workflow_definition_service as svc
from app.services import workflow_evaluation_service as eval_svc


def _row(*, verdict: str | None = None) -> WorkflowDefinition:
    now = datetime.now(timezone.utc)
    return WorkflowDefinition(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        workflow_id="wf.custom.eval",
        version=1,
        name="Evaluated workflow",
        base="offline",
        definition={
            "workflow_id": "wf.custom.eval",
            "version": 1,
            "project_id": str(uuid.uuid4()),
            "base": "offline",
            "steps": [{"id": "summary", "agent_id": "agent.summary.v1"}],
        },
        status="draft",
        eval_verdict=verdict,
        eval_regression_accepted=False,
        created_at=now,
        updated_at=now,
    )


def _dependency_names(endpoint) -> set[str]:
    names: set[str] = set()
    for dependency in endpoint.__dict__.get("__wrapped_dependencies__", []):
        names.add(getattr(dependency, "__name__", ""))
    return names


def test_every_route_is_project_scoped_and_mutations_require_qa_lead() -> None:
    for route in workflows.router.routes:
        assert "{project_id}" in route.path
        signature = inspect.signature(route.endpoint)
        assert "current_user" in signature.parameters
        if set(route.methods or ()) & {"POST", "PUT", "DELETE"} and not route.path.endswith("/validate"):
            assert "_lead" in signature.parameters


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_builtin_mutations_return_405(operation: str) -> None:
    if operation == "update":
        call = workflows.update_workflow(
            uuid.uuid4(), "offline", svc.WorkflowBodyV1.model_validate({
                "workflow_id": "offline", "name": "No", "base": "offline",
                "steps": [{"id": "ingest", "agent_id": "agent.ingestion.v1"}],
            }), AsyncMock(), object(), object()
        )
    else:
        call = workflows.delete_workflow(uuid.uuid4(), "offline", AsyncMock(), object(), object())
    with pytest.raises(HTTPException) as exc:
        await call
    assert exc.value.status_code == 405


def test_router_is_registered_as_protected() -> None:
    from app import bootstrap

    assert workflows.router in bootstrap.PROTECTED_ROUTERS


@pytest.mark.asyncio
async def test_evaluate_route_persists_g4_and_records_activity(monkeypatch) -> None:
    row = _row()
    db = AsyncMock()
    actor = SimpleNamespace(id=uuid.uuid4())
    result = {
        "verdict": "insufficient_samples",
        "coverage": 0.5,
        "sample_count": 20,
    }
    monkeypatch.setattr(workflows, "_get", AsyncMock(return_value=row))
    evaluate = AsyncMock(return_value=result)
    monkeypatch.setattr(eval_svc, "evaluate_definition", evaluate)
    activity = AsyncMock()
    monkeypatch.setattr(workflows, "_activity", activity)

    response = await workflows.evaluate_workflow(
        row.project_id,
        row.workflow_id,
        svc.WorkflowEvaluateV1(sample_limit=20),
        db,
        actor,
        actor,
    )

    assert response == result
    evaluate.assert_awaited_once()
    activity.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_refuses_measured_regression_without_override(monkeypatch) -> None:
    row = _row(verdict="fail")
    db = AsyncMock()
    actor = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(workflows, "_get", AsyncMock(return_value=row))

    with pytest.raises(HTTPException) as exc:
        await workflows.publish_workflow(
            row.project_id,
            row.workflow_id,
            svc.WorkflowPublishV1(),
            db,
            actor,
            actor,
        )

    assert exc.value.status_code == 409
    assert row.status == "draft"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_records_reasoned_regression_acceptance(monkeypatch) -> None:
    row = _row(verdict="fail")
    db = AsyncMock()
    actor = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(workflows, "_get", AsyncMock(return_value=row))
    activity = AsyncMock()
    monkeypatch.setattr(workflows, "_activity", activity)

    response = await workflows.publish_workflow(
        row.project_id,
        row.workflow_id,
        svc.WorkflowPublishV1(
            accept_regression=True,
            reason="Accepted for a time-critical release",
        ),
        db,
        actor,
        actor,
    )

    assert response["status"] == "published"
    assert response["eval_regression_accepted"] is True
    assert response["eval_regression_reason"] == "Accepted for a time-critical release"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_triggers_missing_evaluation_and_allows_insufficient_evidence(
    monkeypatch,
) -> None:
    row = _row()
    db = AsyncMock()
    actor = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(workflows, "_get", AsyncMock(return_value=row))

    async def evaluate(*_args, **_kwargs):
        row.eval_verdict = "insufficient_samples"
        row.eval_coverage = 0.25
        row.eval_gate_run_id = uuid.uuid4()
        row.evaluated_at = datetime.now(timezone.utc)
        return {
            "verdict": "insufficient_samples",
            "coverage": 0.25,
            "sample_count": 20,
        }

    evaluate_mock = AsyncMock(side_effect=evaluate)
    monkeypatch.setattr(eval_svc, "evaluate_definition", evaluate_mock)
    monkeypatch.setattr(workflows, "_activity", AsyncMock())

    response = await workflows.publish_workflow(
        row.project_id,
        row.workflow_id,
        svc.WorkflowPublishV1(),
        db,
        actor,
        actor,
    )

    evaluate_mock.assert_awaited_once()
    assert response["status"] == "published"
    assert response["eval_verdict"] == "insufficient_samples"
    assert response["eval_coverage"] == 0.25
