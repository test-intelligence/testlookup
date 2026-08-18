from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filter_str",
    [
        'notuserName eq "alice"',
        'userName eq "alice" trailing',
        'prefix emails.value eq "alice@example.com"',
        'externalIdentifier eq "idp-1"',
    ],
)
async def test_supported_attribute_substrings_do_not_execute_directory_query(filter_str):
    from app.services.scim_service import scim_list_users

    db = AsyncMock()

    assert await scim_list_users(db, filter_str=filter_str) == ([], 0)
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filter_str",
    ['USERNAME EQ "alice"', "  userName   eq   'alice'  "],
)
async def test_supported_filter_is_complete_and_case_insensitive(filter_str):
    from app.services.scim_service import scim_list_users

    user = SimpleNamespace(username="alice")
    count_result = MagicMock()
    count_result.scalar.return_value = 1
    page_result = MagicMock()
    page_result.scalars.return_value.all.return_value = [user]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[count_result, page_result])

    assert await scim_list_users(db, filter_str=filter_str) == ([user], 1)
    assert db.execute.await_count == 2


def test_filter_parser_rejects_empty_values_and_accepts_supported_aliases():
    from app.services.scim_service import _parse_scim_filter

    assert _parse_scim_filter('userName eq ""') is None
    assert _parse_scim_filter('email eq "alice@example.com"') == (
        "email",
        "alice@example.com",
    )
    assert _parse_scim_filter('emails.value eq "alice@example.com"') == (
        "emails.value",
        "alice@example.com",
    )
    assert _parse_scim_filter('externalId eq "idp-1"') == ("externalid", "idp-1")
