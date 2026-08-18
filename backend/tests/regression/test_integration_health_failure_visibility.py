"""Integration-health authority outages must not look like empty health."""
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("aiosmtplib", MagicMock())

from app.routers import app_settings as router  # noqa: E402


@pytest.mark.asyncio
async def test_integration_health_propagates_database_failure():
    db = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("health database unavailable")))

    with pytest.raises(RuntimeError, match="health database unavailable"):
        await router.get_integration_health(db=db, _=SimpleNamespace())
