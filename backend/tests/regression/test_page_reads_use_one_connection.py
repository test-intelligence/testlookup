"""E2E pass 2026-10-10: the Flaky tests page sat 30 s on "0 flaky tests".

``GET /projects/{id}/flaky-coach`` opened its own ``AsyncSessionLocal()``
while its guards already held the request's ``get_db`` session, and a cold
storage-config read took a third connection. The API's pool is deliberately
small (2 + 1 overflow per process, the connection budget), so two concurrent
page loads exhausted it and both waited out ``pool_timeout``::

    QueuePool limit of size 2 overflow 1 reached, connection timed out,
    timeout 30.00 ... path=/api/v1/projects/<id>/flaky-coach  duration_ms=30191

These handlers now use the request's session.
"""
from __future__ import annotations

import inspect

import pytest

from app.db.postgres import get_db

pytestmark = pytest.mark.regression


def _handlers():
    from app.routers import release_readiness, test_health

    return [
        test_health.get_test_health,
        test_health.get_project_flaky_coach,
        test_health.refresh_project_flaky_coach,
        release_readiness.get_release_decision,
    ]


@pytest.mark.parametrize("handler", _handlers(), ids=lambda h: h.__name__)
def test_the_handler_uses_the_requests_session(handler):
    db = inspect.signature(handler).parameters.get("db")
    assert db is not None and db.default.dependency is get_db, handler.__name__
    assert "AsyncSessionLocal(" not in inspect.getsource(handler), handler.__name__


@pytest.mark.asyncio
async def test_a_cold_flaky_coach_releases_the_request_connection_before_populating():
    """On a cache miss the coach populates on a dedicated write session (which
    reads storage config on yet another). Holding the request's connection
    across that made one cold load need the whole 2+1 pool."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services import test_health_coach_service as svc

    order: list[str] = []
    db = MagicMock()
    db.new, db.dirty, db.deleted = [], [], []
    db.close = AsyncMock(side_effect=lambda: order.append("close"))
    populate = AsyncMock(side_effect=lambda *a, **k: order.append("populate"))
    loads = AsyncMock(side_effect=[[], []])
    with patch.object(svc, "_load_flaky_cache", loads), \
            patch.object(svc, "_populate_flaky_cache_in_new_session", populate):
        try:
            await svc.get_flaky_coach(uuid.uuid4(), db, days=30, limit=50)
        except Exception:  # noqa: BLE001 -- what follows the populate is not under test
            pass
    assert order[:2] == ["close", "populate"], order
