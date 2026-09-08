"""Execute upload-status transition scripts against a real Redis server."""
from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest

pytest.importorskip("sqlalchemy")
redis_asyncio = pytest.importorskip("redis.asyncio")

from app.services import upload_status  # noqa: E402


@pytest.mark.asyncio
@pytest.mark.integration
async def test_upload_status_lua_scripts_are_atomic_on_real_redis():
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        pytest.skip("REDIS_URL is not configured")

    terminal_task = f"status-lua-{uuid.uuid4()}"
    pending_task = f"status-clear-{uuid.uuid4()}"
    client = redis_asyncio.Redis.from_url(redis_url, decode_responses=True)
    try:
        await client.ping()
        with patch("app.db.redis_client.get_redis", return_value=client):
            assert await upload_status.set_status(
                terminal_task, state=upload_status.STATE_PENDING,
            ) is True
            assert await upload_status.set_status(
                terminal_task,
                state=upload_status.STATE_INGESTING,
                progress={"total": 4},
            ) is True
            assert await upload_status.set_status(
                terminal_task, state=upload_status.STATE_PARSING,
            ) is False
            assert await upload_status.set_status(
                terminal_task,
                state=upload_status.STATE_SUCCEEDED,
                result={"total": 4, "failed": 0},
            ) is True
            assert await upload_status.set_status(
                terminal_task, state=upload_status.STATE_PENDING,
            ) is False
            assert await upload_status.clear_pending_status(terminal_task) is False

            terminal = await upload_status.get_status(terminal_task)
            assert terminal is not None
            assert terminal["state"] == upload_status.STATE_SUCCEEDED
            assert terminal["result"] == {"total": 4, "failed": 0}

            assert await upload_status.set_status(
                pending_task, state=upload_status.STATE_PENDING,
            ) is True
            assert await upload_status.clear_pending_status(pending_task) is True
            assert await upload_status.get_status(pending_task) is None
    finally:
        await client.delete(
            f"upload:status:{terminal_task}",
            f"upload:status:{pending_task}",
        )
        await client.aclose()
