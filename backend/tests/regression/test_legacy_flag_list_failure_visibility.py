"""A feature-flag authority outage must not masquerade as an empty list."""
from unittest.mock import AsyncMock

import pytest

from app.services.feature_flag_service import get_all_flags


@pytest.mark.asyncio
async def test_flag_list_propagates_database_failure():
    db = AsyncMock()
    db.execute.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        await get_all_flags(db)
