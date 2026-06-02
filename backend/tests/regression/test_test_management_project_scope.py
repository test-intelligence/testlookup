"""Regression: test-management by-id + create endpoints were a cross-tenant IDOR.

Bug pinned (review/test-management-service, 2026-06-01):

The service helpers (get_*_or_404) fetch by PK only, and the routers only scoped
the *list* endpoints. Every ``/cases/{id}*`` and ``/plans/{id}*`` endpoint, plus
the create paths (which trusted payload.project_id), let any authenticated user
read/create/edit/delete/review another tenant's test assets. Fix: shared
``require_case_access`` / ``require_plan_access`` dependencies that fetch the
entity and call ``resolve_project_scope`` (HTTP 403 on denied), applied to every
by-id endpoint; create paths now ``resolve_project_scope(payload.project_id)``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")

from app.routers import test_management_shared as shared  # noqa: E402


def _deny(*_a, **_k):
    raise HTTPException(status_code=403, detail="Forbidden")


@pytest.mark.asyncio
async def test_require_case_access_denies_foreign_project():
    case = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    db = AsyncMock()
    db.get = AsyncMock(return_value=case)

    with patch.object(shared, "resolve_project_scope", AsyncMock(side_effect=_deny)):
        with pytest.raises(HTTPException) as exc:
            await shared.require_case_access(case.id, db=db, current_user=SimpleNamespace(id=uuid.uuid4()))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_case_access_allows_own_project():
    case = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    db = AsyncMock()
    db.get = AsyncMock(return_value=case)

    scope = AsyncMock(return_value=(case.project_id, None))
    with patch.object(shared, "resolve_project_scope", scope):
        result = await shared.require_case_access(case.id, db=db, current_user=SimpleNamespace(id=uuid.uuid4()))
    assert result is case
    # The access check ran against the case's own project_id.
    scope.assert_awaited_once()
    assert str(case.project_id) in str(scope.await_args)


@pytest.mark.asyncio
async def test_require_case_access_404_when_missing():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    with patch.object(shared, "resolve_project_scope", AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await shared.require_case_access(uuid.uuid4(), db=db, current_user=SimpleNamespace(id=uuid.uuid4()))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_require_plan_access_denies_foreign_project():
    plan = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    db = AsyncMock()
    db.get = AsyncMock(return_value=plan)
    with patch.object(shared, "resolve_project_scope", AsyncMock(side_effect=_deny)):
        with pytest.raises(HTTPException) as exc:
            await shared.require_plan_access(plan.id, db=db, current_user=SimpleNamespace(id=uuid.uuid4()))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_create_managed_test_case_enforces_project_access():
    """create_managed_test_case must reject a project the caller can't access
    (it previously only checked the project existed)."""
    from app.services import test_management_service as svc

    payload = SimpleNamespace(project_id=uuid.uuid4(), suite_name=None)
    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(id=payload.project_id))  # project exists

    with patch("app.core.deps.resolve_project_scope", AsyncMock(side_effect=_deny)):
        with pytest.raises(HTTPException) as exc:
            await svc.create_managed_test_case(db, payload, SimpleNamespace(id=uuid.uuid4()))
    assert exc.value.status_code == 403
    db.add.assert_not_called()  # nothing staged for a forbidden project
