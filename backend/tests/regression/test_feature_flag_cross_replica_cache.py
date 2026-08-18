"""Regression guard for feature-flag invalidation across replicas.

The live deployment runs multiple API and Celery processes. A process-local
30-second cache survived another process deleting the shared Redis key, so a
committed ops toggle could produce different answers depending on the replica.
"""
from unittest.mock import AsyncMock

import pytest

from app.services import feature_flags as ff


@pytest.mark.asyncio
async def test_gate_always_consults_shared_cache(monkeypatch):
    shared_flag = {
        "key": "cross_replica",
        "enabled_global": False,
        "enabled_projects": [],
        "enabled_roles": [],
        "rollout_percent": 100,
    }
    shared_read = AsyncMock(return_value=shared_flag)
    monkeypatch.setattr(ff, "_load_from_redis", shared_read)

    assert await ff.is_enabled("cross_replica") is False
    shared_read.assert_awaited_once_with("cross_replica")


def test_process_local_feature_flag_cache_is_not_reintroduced():
    assert not hasattr(ff, "_cache")
