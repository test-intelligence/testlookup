from __future__ import annotations

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("environment", "expected_size", "expected_overflow"),
    [("development", 5, 10), ("staging", 15, 30), ("production", 40, 100)],
)
async def test_search_config_publishes_engine_pool_defaults(
    monkeypatch,
    environment,
    expected_size,
    expected_overflow,
):
    """The settings page must report the concrete values the engine consumes."""
    from app.core.config import settings
    from app.routers.performance import get_search_config

    monkeypatch.setattr(settings, "APP_ENV", environment)
    monkeypatch.setattr(settings, "PG_POOL_SIZE", None)
    monkeypatch.setattr(settings, "PG_MAX_OVERFLOW", None)
    monkeypatch.setattr(settings, "PG_POOL_RECYCLE", 1200)

    result = await get_search_config(current_user=None)

    assert result["pg_pool_size"] == expected_size
    assert result["pg_max_overflow"] == expected_overflow
    assert result["pg_pool_recycle"] == 900


@pytest.mark.asyncio
async def test_search_config_publishes_explicit_pool_overrides(monkeypatch):
    from app.core.config import settings
    from app.routers.performance import get_search_config

    monkeypatch.setattr(settings, "PG_POOL_SIZE", 23)
    monkeypatch.setattr(settings, "PG_MAX_OVERFLOW", 47)
    monkeypatch.setattr(settings, "PG_POOL_RECYCLE", 321)

    result = await get_search_config(current_user=None)

    assert result["pg_pool_size"] == 23
    assert result["pg_max_overflow"] == 47
    assert result["pg_pool_recycle"] == 321
