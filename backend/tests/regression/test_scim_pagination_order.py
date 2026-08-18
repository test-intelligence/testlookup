from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_scim_page_query_uses_stable_id_tiebreaker_after_created_at():
    from app.models.postgres import User
    from app.services.scim_service import scim_list_users

    count_result = MagicMock()
    count_result.scalar.return_value = 0
    page_result = MagicMock()
    page_result.scalars.return_value.all.return_value = []
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[count_result, page_result])

    await scim_list_users(db, start_index=11, count=10)

    page_query = db.execute.await_args_list[1].args[0]
    assert list(page_query._order_by_clauses) == [User.created_at, User.id]
    assert page_query._offset_clause.value == 10
    assert page_query._limit_clause.value == 10
