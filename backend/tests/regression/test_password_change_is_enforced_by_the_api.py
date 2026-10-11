"""A session on a temporary password is refused by the API, not only the browser.

Homelab sweep, 2026-10-10: ``must_change_password`` was a prompt on the login
response and a redirect in the SPA; every API call served the token normally,
so a user on an initial or admin-reset password could skip the change by
calling the API. Owner decision: enforce it server-side.

Pinned here: a JWT session whose account must change its password gets 403
``password_change_required`` on every route except who-am-I, the two password
changes and logout -- through the main auth dependency and the ingest one. An
API key is not refused (a separate credential; an admin reset must not break a
pipeline), and an account that has changed its password passes.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


def _user(*, must_change: bool, kind: str):
    from app.core.deps import _bind_credential_kind

    user = SimpleNamespace(is_active=True, must_change_password=must_change, role="QA_ENGINEER")
    return _bind_credential_kind(user, kind)


def _request(path: str, method: str = "GET"):
    return SimpleNamespace(url=SimpleNamespace(path=path), method=method, scope={})


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/runs", "/api/v1/projects", "/api/v1/settings/ai/mode"])
async def test_a_temporary_password_session_is_refused_everywhere_else(path):
    from app.core.deps import CREDENTIAL_KIND_JWT, get_current_active_user

    with pytest.raises(HTTPException) as refused:
        await get_current_active_user(_request(path), _user(must_change=True, kind=CREDENTIAL_KIND_JWT))
    assert refused.value.status_code == 403
    assert refused.value.detail["code"] == "password_change_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/api/v1/auth/me", "/api/v1/auth/first-time-reset", "/api/v1/auth/change-password", "/api/v1/auth/logout",
])
async def test_it_can_still_change_the_password_and_sign_out(path):
    from app.core.deps import CREDENTIAL_KIND_JWT, get_current_active_user

    user = _user(must_change=True, kind=CREDENTIAL_KIND_JWT)
    assert await get_current_active_user(_request(path, "POST" if path != "/api/v1/auth/me" else "GET"), user) is user


@pytest.mark.asyncio
async def test_an_api_key_and_a_changed_password_pass():
    from app.core.deps import CREDENTIAL_KIND_API_KEY, CREDENTIAL_KIND_JWT, get_current_active_user

    key_user = _user(must_change=True, kind=CREDENTIAL_KIND_API_KEY)
    assert await get_current_active_user(_request("/api/v1/runs"), key_user) is key_user
    done = _user(must_change=False, kind=CREDENTIAL_KIND_JWT)
    assert await get_current_active_user(_request("/api/v1/runs"), done) is done


@pytest.mark.asyncio
async def test_ingest_by_jwt_is_refused_too():
    from app.core import deps

    user = _user(must_change=True, kind=deps.CREDENTIAL_KIND_JWT)
    with patch.object(deps, "_bearer_user_or_fall_through", AsyncMock(return_value=user)):
        with pytest.raises(HTTPException) as refused:
            await deps.get_api_key_context(db=None, bearer_token="t", x_api_key=None)
    assert refused.value.status_code == 403
    assert refused.value.detail["code"] == "password_change_required"
