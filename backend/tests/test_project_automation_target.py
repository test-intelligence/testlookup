"""A project's automation target (W13 TCL-03, migration 0228): the schemas and the existing PUT.

``ProjectUpdate.automation_target_pct`` takes a whole percentage 0-100 (a 422
otherwise); ``ProjectResponse`` reads it with the ``require_case_review``
pattern (an unflushed ``None`` -> the default 60, never a 500). The write is the
UNCHANGED ``PUT /api/v1/projects/{project_id}`` (QA_LEAD+, ``exclude_none``):
driven here through its real body and the real activity ledger write, it
applies the field and records ``project.updated`` naming it in ``changed``
-- and records nothing when the value sent is the stored one.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.models.postgres import Project, ProjectActivityEvent
from app.models.schemas import ProjectResponse, ProjectUpdate


@pytest.mark.parametrize("value", [0, 1, 60, 75, 100])
def test_the_update_takes_a_whole_percentage(value):
    assert ProjectUpdate(automation_target_pct=value).model_dump(exclude_none=True) == {"automation_target_pct": value}


@pytest.mark.parametrize("value", [101, -1, 250])
def test_the_update_refuses_out_of_range(value):
    with pytest.raises(ValidationError) as exc:
        ProjectUpdate(automation_target_pct=value)
    (error,) = exc.value.errors()
    assert error["loc"] == ("automation_target_pct",)


def test_an_update_that_omits_it_changes_nothing():
    assert "automation_target_pct" not in ProjectUpdate(description="x").model_dump(exclude_none=True)


def _project_row(**overrides) -> SimpleNamespace:
    row = {
        "id": uuid.uuid4(), "name": "Payments", "slug": "payments", "is_active": True,
        "created_at": datetime(2026, 10, 1, tzinfo=timezone.utc), "require_case_review": False,
        "automation_target_pct": 75, **overrides,
    }
    return SimpleNamespace(**row)


def test_the_response_reads_the_stored_target():
    assert ProjectResponse.model_validate(_project_row()).automation_target_pct == 75


def test_an_unflushed_project_reads_the_default():
    assert ProjectResponse.model_validate(_project_row(automation_target_pct=None)).automation_target_pct == 60
    row = _project_row()
    del row.automation_target_pct
    assert ProjectResponse.model_validate(row).automation_target_pct == 60, "a hand-built row without the attribute"
    unflushed = Project(id=uuid.uuid4(), name="New", slug="new", is_active=True,
                        created_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert unflushed.automation_target_pct is None, "the ORM default applies only at INSERT"
    assert ProjectResponse.model_validate(unflushed).automation_target_pct == 60


def _session(project: SimpleNamespace) -> AsyncMock:
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=project)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_the_existing_put_applies_it_and_records_project_updated():
    from app.routers.projects import update_project

    project = _project_row(automation_target_pct=60)
    db = _session(project)
    actor = SimpleNamespace(id=uuid.uuid4(), full_name="Lee Lead", username="lead", role="QA_LEAD", api_key_id=None)

    answer = await update_project(project.id, ProjectUpdate(automation_target_pct=75), db, actor)

    assert answer is project and project.automation_target_pct == 75
    db.commit.assert_awaited_once()
    (row,), _ = db.add.call_args
    assert isinstance(row, ProjectActivityEvent)
    assert row.event_type == "project.updated" and row.project_id == project.id
    assert row.context["changed"] == "automation_target_pct"
    assert row.summary == "Project settings changed: automation_target_pct"
    assert ProjectResponse.model_validate(project).automation_target_pct == 75


@pytest.mark.asyncio
async def test_resending_the_stored_target_records_nothing():
    from app.routers.projects import update_project

    project = _project_row(automation_target_pct=60)
    db = _session(project)
    actor = SimpleNamespace(id=uuid.uuid4(), full_name="Lee Lead", username="lead", role="QA_LEAD", api_key_id=None)

    await update_project(project.id, ProjectUpdate(automation_target_pct=60), db, actor)

    db.add.assert_not_called()
    db.commit.assert_awaited_once()
