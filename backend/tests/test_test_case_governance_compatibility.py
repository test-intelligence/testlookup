"""Compatibility-window contracts that prevent lifecycle rollout regressions."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from app.routers import test_management_plans
from app.models.schemas import ManagedTestCaseUpdate
from app.services import test_management_service
from tests.conftest import FakeExecuteResult


@pytest.mark.asyncio
async def test_ai_plan_eligibility_includes_approved_and_active_only():
    project_id = uuid.uuid4()
    payload = SimpleNamespace(project_id=project_id)
    actor = SimpleNamespace(id=uuid.uuid4(), role="QA_LEAD")
    result = FakeExecuteResult()
    result.scalars = lambda: SimpleNamespace(all=lambda: [])
    db = SimpleNamespace(
        execute=AsyncMock(return_value=result),
    )

    with patch(
        "app.core.deps.resolve_project_scope", AsyncMock(return_value=(project_id, None))
    ):
        with pytest.raises(HTTPException) as exc:
            await test_management_plans.ai_create_plan(payload, db, actor)

    assert exc.value.status_code == 400
    statement = db.execute.await_args.args[0]
    compiled = statement.compile()
    assert project_id in compiled.params.values()
    assert ["approved", "active"] in compiled.params.values()
    assert "managed_test_cases.status IN" in str(compiled)


def test_transition_and_deprecation_request_schemas_pin_wire_compatibility():
    from app.models.schemas import TestCaseDeprecateRequest, TestCaseTransitionRequest

    assert TestCaseTransitionRequest(action="withdraw_review").action == "withdraw_review"
    assert TestCaseTransitionRequest(action="unclaim").action == "unclaim"
    assert TestCaseDeprecateRequest(reason="Intentional retirement").reason == (
        "Intentional retirement"
    )
    with pytest.raises(ValueError):
        TestCaseDeprecateRequest(reason="")


@pytest.mark.asyncio
async def test_default_authored_list_hides_terminal_rows_and_include_archived_is_explicit():
    paginate = AsyncMock(return_value=([], 0, 0))
    db = SimpleNamespace()
    with patch.object(test_management_service, "paginate_scalars", paginate):
        await test_management_service.list_managed_test_cases(
            db, project_id=uuid.uuid4(), page=1, size=25
        )
        default_statement = paginate.await_args.args[1]

        await test_management_service.list_managed_test_cases(
            db,
            project_id=uuid.uuid4(),
            page=1,
            size=25,
            include_archived=True,
        )
        archived_statement = paginate.await_args.args[1]

    default_compiled = default_statement.compile()
    assert ["deprecated", "archived"] in default_compiled.params.values()
    assert "managed_test_cases.status NOT IN" in str(default_compiled)

    archived_compiled = archived_statement.compile()
    assert "deprecated" in archived_compiled.params.values()
    assert "archived" not in archived_compiled.params.values()
    assert "managed_test_cases.status !=" in str(archived_compiled)


@pytest.mark.asyncio
async def test_change_summary_only_patch_is_a_true_noop_without_version_or_audit():
    test_case = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        title="Stable title",
        status="draft",
        version=8,
    )
    db = SimpleNamespace()
    snapshot = Mock()
    transition = AsyncMock()
    audit = AsyncMock()
    with (
        patch.object(
            test_management_service,
            "get_test_case_or_404",
            AsyncMock(return_value=test_case),
        ),
        patch.object(test_management_service, "stage_test_case_snapshot", snapshot),
        patch.object(test_management_service, "transition", transition),
        patch.object(test_management_service, "audit_event", audit),
    ):
        returned = await test_management_service.update_managed_test_case(
            db,
            test_case.id,
            ManagedTestCaseUpdate(change_summary="label only"),
            SimpleNamespace(id=uuid.uuid4()),
        )

    assert returned is test_case
    assert test_case.version == 8
    snapshot.assert_not_called()
    transition.assert_not_awaited()
    audit.assert_not_awaited()
