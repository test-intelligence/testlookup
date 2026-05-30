import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.deps import (
    _bind_api_key_project,
    get_accessible_project_ids,
    require_project_access,
    resolve_project_scope,
)
from app.models.postgres import UserRole


class _NoDb:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("project-bound API key should not need a DB membership lookup")


def _user(role=UserRole.ADMIN):
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True)


@pytest.mark.asyncio
async def test_project_bound_key_scopes_admin_access():
    project_id = uuid.uuid4()
    user = _bind_api_key_project(_user(UserRole.ADMIN), project_id)

    assert await get_accessible_project_ids(_NoDb(), user) == {project_id}
    assert await resolve_project_scope(_NoDb(), user, None) == (None, {project_id})
    assert await resolve_project_scope(_NoDb(), user, str(project_id)) == (project_id, None)


@pytest.mark.asyncio
async def test_project_bound_key_rejects_other_project_path():
    bound_project_id = uuid.uuid4()
    other_project_id = uuid.uuid4()
    user = _bind_api_key_project(_user(UserRole.ADMIN), bound_project_id)
    request = SimpleNamespace(path_params={"project_id": str(other_project_id)})
    guard = require_project_access()

    with pytest.raises(HTTPException) as exc:
        await guard(request, db=_NoDb(), current_user=user)

    assert exc.value.status_code == 403
