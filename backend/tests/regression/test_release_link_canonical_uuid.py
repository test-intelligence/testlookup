"""Regression: live-session release linking dropped for slug run_ids.

``LiveSession.id`` is the internal run UUID shared with ``TestRun.id``.
``LiveSession.run_id`` is only the SDK-supplied display slug and may be
non-UUID. Release linking must therefore use ``session.id`` directly; deriving
identity from the external slug silently drops links or creates collisions.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def test_uuid_of_sdk_slug_would_raise():
    """Documents WHY the bug existed: the SDK slug is not a UUID, so the old
    ``uuid.UUID(session.run_id)`` call raised and got swallowed."""
    with pytest.raises(ValueError):
        uuid.UUID("local-abc12345")


@pytest.mark.asyncio
async def test_release_link_uses_canonical_uuid_for_slug_run():
    from app.services import stream_service

    session_uuid = uuid.uuid4()           # the LiveSession PK (a real UUID)
    slug = "local-abc12345"               # the run_id slug — NOT a UUID
    project_id = uuid.uuid4()

    fake_session = SimpleNamespace(
        id=session_uuid,
        run_id=slug,
        status="active",                  # not "completed" → proceeds
        project_id=project_id,
        release_name="2026.06",
        completed_at=None,
        extra_metadata=None,
        build_number="b1",
        client_name=None,
        framework="",
        branch="",
        commit_hash="",
        suite_name=None,
    )

    db = AsyncMock()
    db.get = AsyncMock(return_value=fake_session)

    # Empty buffer → the event-archive block short-circuits before any
    # db.execute / TestRun select.
    fake_redis = SimpleNamespace(
        eval=AsyncMock(return_value=[b"closing", b'{"total":0,"failed":0,"passed":0,"skipped":0,"broken":0,"unknown":0,"events_received":0}', b"0"]),
        lrange=AsyncMock(return_value=[]),
    )

    captured: dict = {}

    async def _capture_link(**kwargs):
        captured.update(kwargs)

    stage_persist = AsyncMock(return_value=True)
    with (
        patch(
            "app.streams.live_run_state.RedisLiveRunState.complete",
            new=AsyncMock(return_value={}),
        ),
        patch("app.services.stream_service.upsert_test_run", new=AsyncMock()),
        patch("app.db.redis_client.get_redis", return_value=fake_redis),
        patch(
            "app.services.release_linker.link_run_or_default",
            new=AsyncMock(side_effect=_capture_link),
        ),
        patch(
            "app.services.run_downstream_outbox.stage_live_persist_operation",
            new=stage_persist,
        ),
    ):
        await stream_service.close_session(db, str(session_uuid))

    # The slug run must have reached link_run_or_default (it never did before
    # — uuid.UUID(slug) raised and the broad except swallowed it).
    assert "test_run_id" in captured, (
        "link_run_or_default was never called — the release link is still "
        "being skipped for slug run_ids (see this file's docstring)."
    )
    # And it must be the canonical uuid the TestRun is actually written under.
    assert captured["test_run_id"] == session_uuid
    assert isinstance(captured["test_run_id"], uuid.UUID)
    stage_persist.assert_awaited_once()
    assert stage_persist.await_args.kwargs["canonical_run_id"] == session_uuid
