"""A feature-flag authority outage must not masquerade as an empty list.

Since W25b the legacy ``GET /api/v1/settings/flags`` is an alias over the
canonical ``feature_flags.list_flags``; the guard now drives the route itself,
so a ``try/except: return []`` anywhere on that path fails it.
"""
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# The route under test does not use SMTP; keep this focused guard runnable in
# minimal local environments where that optional transport is not installed.
sys.modules.setdefault("aiosmtplib", MagicMock())

from app.routers import app_settings  # noqa: E402


@pytest.mark.asyncio
async def test_flag_list_propagates_database_failure():
    db = AsyncMock()
    db.execute.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        await app_settings.list_feature_flags(db=db)
