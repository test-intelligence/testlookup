from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.models.postgres import TestCaseAuditLog as AuditLogModel
from app.services import rag_review_service as service


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _DB:
    def __init__(self, batch, case):
        self.batch = batch
        self.case = case
        self.added = []
        self.info = {}
        self.flush = AsyncMock()

    async def execute(self, statement):
        sql = str(statement)
        if "generation_batches" in sql:
            return _Result(self.batch)
        if "managed_test_cases" in sql:
            return _Result(self.case)
        if "test_case_audit_logs" in sql:
            actions = [
                row.action
                for row in self.added
                if isinstance(row, AuditLogModel)
            ]
            return _Result(actions[-1] if actions else None)
        raise AssertionError(sql)

    def add(self, row):
        self.added.append(row)


def _fixtures(*, status="draft"):
    project_id = uuid.uuid4()
    batch_id = uuid.uuid4()
    case_id = uuid.uuid4()
    user = SimpleNamespace(
        id=uuid.uuid4(),
        role="QA_ENGINEER",
        full_name="Reviewer",
        username="reviewer",
    )
    batch = SimpleNamespace(
        id=batch_id,
        project_id=project_id,
        cases_accepted=0,
        cases_rejected=0,
    )
    case = SimpleNamespace(
        id=case_id,
        project_id=project_id,
        generation_batch_id=batch_id,
        status=status,
        version=1,
        title="Generated title",
        description="Original",
    )
    return _DB(batch, case), batch, case, user


def _service_patches():
    return (
        patch.object(service, "get_accessible_project_ids", AsyncMock(return_value=None)),
        patch(
            "app.services.rag_faithfulness_service.check_accept",
            AsyncMock(return_value={"allow": True}),
        ),
    )


@pytest.mark.asyncio
async def test_repeat_accept_is_409_and_edits_and_audit_happen_once():
    db, batch, case, user = _fixtures()
    access, faithful = _service_patches()
    with access, faithful:
        await service.accept_case(
            db, batch.id, case.id, {"title": "First accepted title"}, user
        )
        with pytest.raises(HTTPException) as exc:
            await service.accept_case(
                db, batch.id, case.id, {"title": "Repeated edit"}, user
            )

    assert exc.value.status_code == 409
    assert exc.value.detail["disposition"] == "accepted"
    assert case.title == "First accepted title"
    assert case.version == 2
    assert batch.cases_accepted == 1
    assert [row.action for row in db.added if isinstance(row, AuditLogModel)].count(
        service.GENERATION_ACCEPTED_ACTION
    ) == 1


@pytest.mark.asyncio
async def test_bulk_accept_deduplicates_ids_preserving_first_order():
    db, batch, case, user = _fixtures()
    access, faithful = _service_patches()
    with access, faithful:
        accepted = await service.bulk_accept(
            db, batch.id, [case.id, case.id, case.id], user
        )

    assert [item.id for item in accepted] == [case.id]
    assert batch.cases_accepted == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["deprecated", "archived", "rejected"])
async def test_accept_refuses_non_initial_draft_without_mutation(status):
    db, batch, case, user = _fixtures(status=status)
    access, faithful = _service_patches()
    with access, faithful, pytest.raises(HTTPException) as exc:
        await service.accept_case(db, batch.id, case.id, {"title": "Mutated"}, user)

    assert exc.value.status_code == 409
    assert case.title == "Generated title"
    assert batch.cases_accepted == 0


@pytest.mark.asyncio
async def test_repeat_reject_and_accept_after_reject_conflict_without_reason_rewrite():
    db, batch, case, user = _fixtures()

    async def _transition(_db, _case_id, action, _user, **_kwargs):
        if action == service.LifecycleAction.REJECT:
            case.status = "rejected"
        return SimpleNamespace(case=case)

    access, _faithful = _service_patches()
    with access, patch.object(service, "transition", AsyncMock(side_effect=_transition)):
        await service.reject_case(db, batch.id, case.id, "not useful", user)
        first_description = case.description
        with pytest.raises(HTTPException) as repeat:
            await service.reject_case(db, batch.id, case.id, "again", user)
        with pytest.raises(HTTPException) as conflict:
            await service.accept_case(db, batch.id, case.id, None, user)

    assert repeat.value.status_code == 409
    assert conflict.value.status_code == 409
    assert case.description == first_description
    assert case.description.count("[Rejected:") == 1
    assert batch.cases_rejected == 1
    assert [row.action for row in db.added if isinstance(row, AuditLogModel)].count(
        service.GENERATION_REJECTED_ACTION
    ) == 1
