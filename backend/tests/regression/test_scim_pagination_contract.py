from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _user(index: int):
    return SimpleNamespace(
        id=uuid.uuid4(),
        username=f"user-{index}",
        email=f"user-{index}@example.com",
        full_name=f"User {index}",
        is_active=True,
        created_at=None,
        updated_at=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("returned", [0, 1, 3])
async def test_items_per_page_reports_actual_resources(monkeypatch, returned):
    from app.routers import scim as router

    users = [_user(index) for index in range(returned)]
    monkeypatch.setattr(
        router,
        "scim_list_users",
        AsyncMock(return_value=(users, 9)),
    )
    monkeypatch.setattr(router, "scim_identity_map", AsyncMock(return_value={}))
    request = SimpleNamespace(base_url="https://example.test/")
    token = SimpleNamespace(sso_config_id=uuid.uuid4())

    response = await router.scim_list(
        request,
        startIndex=4,
        count=100,
        filter=None,
        scim_token=token,
        db=AsyncMock(),
    )

    assert response.totalResults == 9
    assert response.startIndex == 4
    assert response.itemsPerPage == returned
    assert len(response.Resources) == returned
