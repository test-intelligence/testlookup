"""Regression: live-session release linking dropped for slug run_ids.

Bug (found 2026-05-30 multi-pass review, `live-stream-ingestion` audit
finding #1): ``stream_service.close_session`` linked the run to its release
via ``link_run_or_default(test_run_id=uuid.UUID(session.run_id))``. But
``session.run_id`` is the SDK-supplied **slug** (e.g. ``local-abc12345``),
not a UUID — and the TestRun row is persisted under
``canonical_test_run_uuid(session.run_id)`` everywhere else. So
``uuid.UUID(slug)`` raised ``ValueError``, which the surrounding broad
``except`` swallowed → **release linking was silently skipped for every
slug-based live run** (and only worked by coincidence for UUID-form slugs).

Fix: pass ``canonical_test_run_uuid(session.run_id)`` — the same uuid the
TestRun is written under — so the link targets the real row.

If this test fails because someone reverted the call site to
``uuid.UUID(session.run_id)``, restore the canonical conversion.
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
    from app.services.stream_service import canonical_test_run_uuid

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
    fake_redis = SimpleNamespace(lrange=AsyncMock(return_value=[]))

    captured: dict = {}

    async def _capture_link(**kwargs):
        captured.update(kwargs)

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
    ):
        # The persist-enqueue and AI-pipeline blocks run AFTER release linking
        # and are each wrapped in their own try/except in close_session, so we
        # don't need to patch them — any failure (e.g. no Celery broker, or
        # celery not installed locally) is swallowed and can't affect the
        # release-link assertion, which fires earlier.
        await stream_service.close_session(db, str(session_uuid))

    # The slug run must have reached link_run_or_default (it never did before
    # — uuid.UUID(slug) raised and the broad except swallowed it).
    assert "test_run_id" in captured, (
        "link_run_or_default was never called — the release link is still "
        "being skipped for slug run_ids (see this file's docstring)."
    )
    # And it must be the canonical uuid the TestRun is actually written under.
    assert captured["test_run_id"] == canonical_test_run_uuid(slug)
    assert isinstance(captured["test_run_id"], uuid.UUID)
