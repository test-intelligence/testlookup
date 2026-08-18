from unittest.mock import AsyncMock, MagicMock

import pytest


def test_scim_pagination_coerces_out_of_range_values():
    from app.services.scim_service import normalize_scim_pagination

    assert normalize_scim_pagination(0, -1) == (1, 0)
    assert normalize_scim_pagination(-50, 10_000) == (1, 200)
    assert normalize_scim_pagination(4, 25) == (4, 25)


@pytest.mark.asyncio
async def test_count_zero_returns_total_without_executing_page_query():
    from app.services.scim_service import scim_list_users

    count_result = MagicMock()
    count_result.scalar.return_value = 37
    db = AsyncMock()
    db.execute = AsyncMock(return_value=count_result)

    users, total = await scim_list_users(db, start_index=1, count=0)

    assert users == []
    assert total == 37
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_count_zero_list_response_reports_no_page_resources(monkeypatch):
    from app.routers import scim as router

    monkeypatch.setattr(router, "scim_list_users", AsyncMock(return_value=([], 37)))
    identity_map = AsyncMock(return_value={})
    monkeypatch.setattr(router, "scim_identity_map", identity_map)
    db = AsyncMock()

    response = await router.scim_list(
        MagicMock(base_url="https://example.test/"),
        startIndex=1,
        count=0,
        filter=None,
        scim_token=MagicMock(sso_config_id=None),
        db=db,
    )

    assert response.totalResults == 37
    assert response.itemsPerPage == 0
    assert response.Resources == []
    identity_map.assert_awaited_once_with(db, [], None)


@pytest.mark.asyncio
async def test_list_route_passes_and_reports_normalized_pagination(monkeypatch):
    from app.routers import scim as router

    list_users = AsyncMock(return_value=([], 37))
    monkeypatch.setattr(router, "scim_list_users", list_users)
    monkeypatch.setattr(router, "scim_identity_map", AsyncMock(return_value={}))
    db = AsyncMock()

    response = await router.scim_list(
        MagicMock(base_url="https://example.test/"),
        startIndex=-5,
        count=10_000,
        filter=None,
        scim_token=MagicMock(sso_config_id=None),
        db=db,
    )

    list_users.assert_awaited_once_with(
        db,
        start_index=1,
        count=200,
        filter_str=None,
        sso_config_id=None,
    )
    assert response.startIndex == 1
