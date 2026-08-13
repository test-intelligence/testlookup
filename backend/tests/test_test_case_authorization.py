import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.deps import resolve_authorized_test_case
from app.models.postgres import UserRole


class _Result:
    def __init__(self, *, row=None, scalar=None):
        self.row = row
        self.scalar = scalar

    def one_or_none(self): return self.row
    def scalar_one_or_none(self): return self.scalar


class _Db:
    def __init__(self, results): self.results = list(results)
    async def execute(self, _statement): return self.results.pop(0)


@pytest.mark.asyncio
async def test_test_case_resolver_rejects_cross_tenant_member():
    project_id = uuid.uuid4()
    test_case = SimpleNamespace(id=uuid.uuid4(), test_run_id=uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4(), role=UserRole.QA_ENGINEER)
    test_run = SimpleNamespace(id=test_case.test_run_id, project_id=project_id)
    db = _Db([_Result(row=(test_case, test_run)), _Result(scalar=None)])

    with pytest.raises(HTTPException) as exc:
        await resolve_authorized_test_case(db, user, test_case.id)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_test_case_resolver_returns_server_owned_scope_for_member():
    project_id = uuid.uuid4()
    test_case = SimpleNamespace(id=uuid.uuid4(), test_run_id=uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4(), role=UserRole.QA_ENGINEER)
    test_run = SimpleNamespace(id=test_case.test_run_id, project_id=project_id)
    db = _Db([_Result(row=(test_case, test_run)), _Result(scalar=uuid.uuid4())])

    resolved = await resolve_authorized_test_case(db, user, test_case.id)

    assert resolved.test_case is test_case
    assert resolved.test_run is test_run
    assert resolved.run_id == test_case.test_run_id
    assert resolved.project_id == project_id
