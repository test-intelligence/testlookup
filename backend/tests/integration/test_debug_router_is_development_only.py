"""The synthetic-data debug route answers 404 outside development.

``POST /api/v1/debug/generate-test-run`` writes synthetic Allure/TestNG results
to object storage and queues their ingestion into any project an instance
admin names. Nothing in the product calls it, yet ``register_routers`` mounted
it in every environment, so a production install served it (found while
checking the GPT-6 exploratory-testing brief, 2026-11-14). The dev seed router
already refused outside development with its own copy of the check.

Both now share ``require_development_environment``. On the debug router it is
the first dependency, so staging and production answer 404 before
authentication: a caller with no token learns nothing more than from a route
that does not exist.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

pytest.importorskip("httpx")
pytest.importorskip("jwt")

from app.core.config import settings  # noqa: E402
from app.core.deps import require_development_environment  # noqa: E402
from app.models.postgres import UserRole  # noqa: E402

pytestmark = pytest.mark.asyncio

DEBUG_URL = "/api/v1/debug/generate-test-run"


@pytest.mark.parametrize("app_env", ["production", "staging"])
async def test_a_caller_without_a_token_gets_404_outside_development(
    client, monkeypatch, app_env
):
    monkeypatch.setattr(settings, "APP_ENV", app_env)

    resp = await client.post(DEBUG_URL, params={"project_id": str(uuid.uuid4())})

    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Not found"


@pytest.mark.parametrize("app_env", ["production", "staging"])
async def test_an_instance_admin_gets_404_and_nothing_is_written_outside_development(
    client, auth_as, monkeypatch, app_env
):
    monkeypatch.setattr(settings, "APP_ENV", app_env)
    auth_as(role=UserRole.ADMIN)
    storage = MagicMock()
    storage.put_object = AsyncMock()

    with (
        patch("app.routers.debug.get_storage_provider", return_value=storage),
        patch("app.routers.debug.ingest_test_run") as task,
    ):
        resp = await client.post(DEBUG_URL, params={"project_id": str(uuid.uuid4())})

    assert resp.status_code == 404, resp.text
    # The gate's own sentence, not the handler's "Project not found".
    assert resp.json()["detail"] == "Not found"
    storage.put_object.assert_not_awaited()
    task.delay.assert_not_called()


async def test_development_keeps_the_route_behind_authentication(client, monkeypatch):
    """The gate is the only change: in development the route still demands a credential."""
    monkeypatch.setattr(settings, "APP_ENV", "development")

    resp = await client.post(DEBUG_URL, params={"project_id": str(uuid.uuid4())})

    assert resp.status_code == 401, resp.text


async def test_development_still_lets_an_instance_admin_reach_the_handler(
    client, auth_as, monkeypatch
):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    auth_as(role=UserRole.ADMIN)
    storage = MagicMock()
    storage.put_object = AsyncMock()

    with (
        patch("app.routers.debug.get_storage_provider", return_value=storage),
        patch("app.routers.debug.ingest_test_run"),
    ):
        resp = await client.post(DEBUG_URL, params={"project_id": str(uuid.uuid4())})

    # The handler ran: an unknown project is its own 404 sentence.
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Project not found"


@pytest.mark.parametrize("app_env", ["production", "staging"])
async def test_the_dev_seed_routes_still_answer_404_outside_development(
    client, auth_as, monkeypatch, app_env
):
    """The seed router now calls the shared gate; its refusal is unchanged."""
    monkeypatch.setattr(settings, "APP_ENV", app_env)
    auth_as(role=UserRole.ADMIN)

    with patch("app.routers.seed._run_seed_script", new=AsyncMock()) as script:
        status = await client.get("/api/v1/dev/seed/status")
        load = await client.post("/api/v1/dev/seed")

    assert (status.status_code, status.json()["detail"]) == (404, "Not found"), (
        status.text
    )
    assert (load.status_code, load.json()["detail"]) == (404, "Not found"), load.text
    script.assert_not_awaited()


@pytest.mark.parametrize("app_env", ["production", "staging"])
async def test_the_shared_gate_refuses_outside_development(monkeypatch, app_env):
    """The dev seed router calls the same dependency in each of its handlers."""
    monkeypatch.setattr(settings, "APP_ENV", app_env)

    with pytest.raises(HTTPException) as refused:
        require_development_environment()

    assert refused.value.status_code == 404
    assert refused.value.detail == "Not found"


async def test_the_shared_gate_allows_development(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "development")

    assert require_development_environment() is None
