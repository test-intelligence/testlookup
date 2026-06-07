"""Regression: Redis-backed upload status (PRD MRU-5/6).

Pins the status record the upload task writes and the /ingest/uploads/{task_id}
endpoint reads, so async parse/ingest outcomes surface to the UI instead of a
silent background run.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.services import upload_status  # noqa: E402


def _fake_redis_with_store():
    store: dict[str, str] = {}
    fake = AsyncMock()

    async def _set(key, value, ex=None):
        store[key] = value

    async def _get(key):
        return store.get(key)

    fake.set = AsyncMock(side_effect=_set)
    fake.get = AsyncMock(side_effect=_get)
    return fake, store


@pytest.mark.asyncio
async def test_set_and_get_status_roundtrip():
    fake, _ = _fake_redis_with_store()
    with patch("app.db.redis_client.get_redis", return_value=fake):
        await upload_status.set_status(
            "t1", run_id="r1", project_id="p1",
            state=upload_status.STATE_SUCCEEDED,
            result={"total": 3, "passed": 2, "failed": 1},
        )
        rec = await upload_status.get_status("t1")

    assert rec is not None
    assert rec["state"] == "succeeded"
    assert rec["run_id"] == "r1"
    assert rec["project_id"] == "p1"
    assert rec["result"] == {"total": 3, "passed": 2, "failed": 1}
    # Record carries a TTL so it self-expires.
    assert fake.set.call_args.kwargs.get("ex") == upload_status.STATUS_TTL_SECONDS


@pytest.mark.asyncio
async def test_get_status_missing_returns_none():
    fake = AsyncMock()
    fake.get = AsyncMock(return_value=None)
    with patch("app.db.redis_client.get_redis", return_value=fake):
        assert await upload_status.get_status("nope") is None


@pytest.mark.asyncio
async def test_set_status_swallows_redis_errors():
    """A Redis blip must never fail the ingest itself — status is advisory."""
    fake = AsyncMock()
    fake.set = AsyncMock(side_effect=RuntimeError("redis down"))
    with patch("app.db.redis_client.get_redis", return_value=fake):
        # Must not raise.
        await upload_status.set_status("t1", state=upload_status.STATE_PARSING)


@pytest.mark.asyncio
async def test_failed_status_carries_structured_error():
    fake, _ = _fake_redis_with_store()
    with patch("app.db.redis_client.get_redis", return_value=fake):
        await upload_status.set_status(
            "t2", run_id="r2", project_id="p2",
            state=upload_status.STATE_FAILED,
            error={"code": "parse_error", "message": "bad xml"},
        )
        rec = await upload_status.get_status("t2")

    assert rec["state"] == "failed"
    assert rec["error"] == {"code": "parse_error", "message": "bad xml"}
