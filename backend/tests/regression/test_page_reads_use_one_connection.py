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
