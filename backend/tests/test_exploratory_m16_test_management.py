"""M16 regressions for stale lifecycle actions and test-plan integrity."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models import schemas
from app.services import test_case_lifecycle_service as lifecycle
from app.services import test_management_service as service


@pytest.mark.asyncio
async def test_case_edit_refuses_a_stale_case_version(monkeypatch) -> None:
    case = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        title="Current title",
        status="draft",
        version=8,
    )
    monkeypatch.setattr(
        service,
        "get_test_case_or_404",
        AsyncMock(return_value=case),
    )
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await service.update_managed_test_case(
            db,
            case.id,
            schemas.ManagedTestCaseUpdate(
                expected_version=7,
                title="Stale replacement",
            ),
            SimpleNamespace(id=uuid.uuid4()),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "current_version": 8,
        "expected_version": 7,
        "message": "Test case changed; refresh before saving edits",
    }
    assert case.title == "Current title"


@pytest.mark.asyncio
async def test_lifecycle_transition_refuses_a_stale_case_version(monkeypatch) -> None:
    case = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        status="draft",
        version=8,
        author_id=uuid.uuid4(),
        reviewer_id=None,
    )
    actor = SimpleNamespace(id=case.author_id, role="qa_engineer")
    monkeypatch.setattr(lifecycle, "_lock_case", AsyncMock(return_value=case))
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await lifecycle.transition(
            db,
            case.id,
            lifecycle.LifecycleAction.REQUEST_REVIEW,
            actor,
            expected_version=7,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "current_version": 8,
        "expected_version": 7,
        "message": "Test case changed; refresh before applying this action",
    }
    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_plan_item_refuses_a_case_from_another_project(monkeypatch) -> None:
    plan = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    foreign_case_id = uuid.uuid4()
    get_plan = AsyncMock(return_value=plan)
    monkeypatch.setattr(service, "get_plan_or_404", get_plan)
    db = AsyncMock()
    async def scalar(statement):
        sql = str(statement)
        if "managed_test_cases.project_id" in sql:
            return None
        if "managed_test_cases" in sql:
            return foreign_case_id
        return None

    db.scalar.side_effect = scalar

    with pytest.raises(HTTPException) as exc:
        await service.add_test_plan_item(
            db,
            plan.id,
            schemas.TestPlanItemCreate(test_case_id=foreign_case_id),
            SimpleNamespace(id=uuid.uuid4()),
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Test case not found"
    get_plan.assert_awaited_once_with(db, plan.id, for_update=True)
    db.add.assert_not_called()


def test_plan_execution_status_is_a_closed_vocabulary() -> None:
    with pytest.raises(ValidationError):
        schemas.ExecuteTestPlanItemRequest(execution_status="green")
