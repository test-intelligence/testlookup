from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.models.postgres import WorkflowDefinition
from app.services import workflow_definition_service as svc


def _body(workflow_id: str = "wf.custom.fast") -> svc.WorkflowBodyV1:
    return svc.WorkflowBodyV1.model_validate({
        "workflow_id": workflow_id,
        "name": "Fast triage",
        "base": "offline",
        "steps": [
            {"id": "ingest", "agent_id": "agent.ingestion.v1"},
            {"id": "summary", "agent_id": "agent.summary.v1"},
        ],
        "edges": [{"from": "ingest", "to": "summary"}],
    })


def _row(*, status: str = "draft", version: int = 1) -> WorkflowDefinition:
    now = datetime.now(timezone.utc)
    return WorkflowDefinition(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        workflow_id="wf.custom.fast",
        version=version,
        name="Fast triage",
        description=None,
        base="offline",
        definition={
            **_body().model_dump(mode="json", by_alias=True, exclude={"name", "description"}),
            "project_id": str(uuid.uuid4()),
            "version": version,
        },
        status=status,
        published_at=now if status == "published" else None,
        created_at=now,
        updated_at=now,
    )


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _db() -> MagicMock:
    db = MagicMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    db.delete = AsyncMock()
    return db


def test_workflow_schema_is_strict_and_rejects_duplicate_steps() -> None:
    document = _body().model_dump(mode="json", by_alias=True)
    document["unknown"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        svc.WorkflowBodyV1.model_validate(document)

    document.pop("unknown")
    document["steps"].append(document["steps"][0])
    with pytest.raises(ValidationError, match="step ids must be unique"):
        svc.WorkflowBodyV1.model_validate(document)


def test_builtins_are_read_only_semantically_valid_templates() -> None:
    assert svc.BUILTIN_WORKFLOW_IDS == {"offline", "deep", "live"}
    item = svc.builtin("offline")
    assert item["read_only"] is True
    assert item["status"] == "published"
    assert svc.validation_result(item) == {
        "valid": True,
        "workflow_id": "offline",
        "version": 1,
        "errors": [],
        "validation_scope": "semantic",
        "compiler_validation": "passed",
    }


@pytest.mark.asyncio
async def test_updating_published_definition_creates_next_draft_without_mutating_source() -> None:
    db = _db()
    source = _row(status="published", version=3)
    before = dict(source.definition)
    db.execute.side_effect = [MagicMock(), _scalar_result(source)]

    created, made_version = await svc.update_definition(
        db, source.project_id, source.workflow_id, _body(), actor_id=uuid.uuid4()
    )

    assert made_version is True
    assert created is not source
    assert created.version == 4 and created.status == "draft"
    assert created.definition["version"] == 4
    assert source.version == 3 and source.status == "published"
    assert source.definition == before
    db.add.assert_called_once_with(created)


@pytest.mark.asyncio
async def test_editing_draft_invalidates_prior_workflow_evaluation() -> None:
    db = _db()
    source = _row(status="draft")
    source.eval_verdict = "pass"
    source.eval_coverage = 1.0
    source.eval_gate_run_id = uuid.uuid4()
    source.evaluated_at = datetime.now(timezone.utc)
    source.eval_regression_accepted = True
    source.eval_regression_reason = "previous definition"
    source.eval_regression_accepted_by = uuid.uuid4()
    source.eval_regression_accepted_at = datetime.now(timezone.utc)
    db.execute.side_effect = [MagicMock(), _scalar_result(source)]

    updated, made_version = await svc.update_definition(
        db, source.project_id, source.workflow_id, _body(), actor_id=uuid.uuid4()
    )

    assert updated is source and made_version is False
    assert updated.eval_verdict is None
    assert updated.eval_coverage is None
    assert updated.eval_gate_run_id is None
    assert updated.evaluated_at is None
    assert updated.eval_regression_accepted is False
    assert updated.eval_regression_reason is None
    assert updated.eval_regression_accepted_by is None
    assert updated.eval_regression_accepted_at is None


@pytest.mark.asyncio
async def test_published_definition_cannot_be_deleted() -> None:
    db = _db()
    with pytest.raises(svc.WorkflowConflict, match="immutable"):
        await svc.delete_definition(db, _row(status="published"))
    db.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_fork_rewrites_identity_and_starts_at_version_one() -> None:
    db = _db()
    db.execute.side_effect = [MagicMock(), _scalar_result(None)]
    project_id = uuid.uuid4()
    row = await svc.fork_definition(
        db,
        project_id,
        svc.builtin("live"),
        svc.WorkflowForkV1(workflow_id="wf.custom.live_copy", name="Live copy"),
        actor_id=uuid.uuid4(),
    )
    assert row.workflow_id == "wf.custom.live_copy"
    assert row.version == 1 and row.status == "draft"
    assert row.definition["project_id"] == str(project_id)
    assert row.definition["workflow_id"] == "wf.custom.live_copy"
