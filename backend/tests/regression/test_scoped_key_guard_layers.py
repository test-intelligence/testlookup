"""Each layer of the scoped-key rule, alone (review of QA-R4-1).

Every route using ``require_project_role`` today also carries
``require_project_access``, which refuses first, so deleting the check in
``require_project_role`` left every route test green. These call the guard
directly. A hand-built request with no method is refused as a write, with a
403, not an AttributeError's 500.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core import deps
from app.models.postgres import UserRole


def _scoped_user(scopes=("stream:write",)):
    user = SimpleNamespace(id=uuid.uuid4(), role=UserRole.ADMIN.value, is_active=True)
    grant = deps.ApiKeyGrant(key_id=uuid.uuid4(), scopes=tuple(scopes), expires_at=None)
    return deps._bind_api_key_grant(user, grant)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_require_project_role_refuses_a_stream_keys_write(method):
    check = deps.require_project_role(UserRole.QA_LEAD)
    request = SimpleNamespace(method=method, path_params={"project_id": str(uuid.uuid4())})

    with pytest.raises(HTTPException) as refused:
        await check(request=request, db=None, current_user=_scoped_user())

    assert refused.value.status_code == 403
    assert "project:admin" in refused.value.detail


def test_a_request_without_a_method_is_refused_as_a_write():
    with pytest.raises(HTTPException) as refused:
        deps._refuse_scoped_key_write(SimpleNamespace(path_params={}), _scoped_user())

    assert refused.value.status_code == 403


@pytest.mark.parametrize("method", ["GET", "head", "OPTIONS"])
def test_a_read_is_not_refused(method):
    deps._refuse_scoped_key_write(SimpleNamespace(method=method), _scoped_user())


def test_a_project_admin_key_is_not_refused():
    deps._refuse_scoped_key_write(
        SimpleNamespace(method="POST"), _scoped_user(("stream:write", "project:admin"))
    )
