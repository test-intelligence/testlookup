"""
Unit tests for ``services.webhook_service`` — pure helpers.

Covers HMAC signature determinism, event catalog / validation, the
offline-mode gate, and the supported-events list shape. The DB-bound
entry points (``emit_event``, ``deliver``) are covered by integration
tests against the fake session.
"""
from __future__ import annotations

import hashlib
import hmac

import pytest

from app.services import webhook_service as svc


# ── compute_signature ─────────────────────────────────────────────────────


def test_compute_signature_matches_raw_hmac():
    secret = "shh-super-secret"
    body = b'{"event_type":"run.completed","data":{}}'
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert svc.compute_signature(secret, body) == expected


def test_compute_signature_is_deterministic():
    a = svc.compute_signature("x", b"abc")
    b = svc.compute_signature("x", b"abc")
    assert a == b


def test_compute_signature_changes_with_body():
    a = svc.compute_signature("x", b"abc")
    b = svc.compute_signature("x", b"abd")
    assert a != b


def test_compute_signature_changes_with_secret():
    a = svc.compute_signature("x", b"abc")
    b = svc.compute_signature("y", b"abc")
    assert a != b


# ── Event catalog ─────────────────────────────────────────────────────────


def test_supported_events_list_shape():
    events = svc.list_supported_events()
    assert isinstance(events, list)
    assert all(set(e.keys()) == {"event_type", "description"} for e in events)
    types = {e["event_type"] for e in events}
    # All five documented events must be present.
    assert {
        "run.completed",
        "defect.promoted",
        "release.decided",
        "flaky.quarantined",
        "quota.exceeded",
    }.issubset(types)


def test_validate_events_accepts_valid_list():
    out = svc.validate_events(["run.completed", "flaky.quarantined"])
    assert out == ["run.completed", "flaky.quarantined"]


def test_validate_events_rejects_unknown():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        svc.validate_events(["run.completed", "not.a.real.event"])
    assert exc.value.status_code == 422
    assert "not.a.real.event" in exc.value.detail


# ── Offline-mode gate ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_allowed_false_when_offline_mode():
    from unittest.mock import AsyncMock, patch
    from app.core.config import settings
    with patch.object(settings, "AI_OFFLINE_MODE", True), patch(
        "app.services.feature_flags.is_enabled",
        AsyncMock(return_value=True),
    ):
        assert await svc._post_allowed() is False


@pytest.mark.asyncio
async def test_post_allowed_false_when_flag_off():
    from unittest.mock import AsyncMock, patch
    from app.core.config import settings
    with patch.object(settings, "AI_OFFLINE_MODE", False), patch(
        "app.services.feature_flags.is_enabled",
        AsyncMock(return_value=False),
    ):
        assert await svc._post_allowed() is False


@pytest.mark.asyncio
async def test_post_allowed_true_when_online_and_flagged():
    from unittest.mock import AsyncMock, patch
    from app.core.config import settings
    with patch.object(settings, "AI_OFFLINE_MODE", False), patch(
        "app.services.feature_flags.is_enabled",
        AsyncMock(return_value=True),
    ):
        assert await svc._post_allowed() is True


@pytest.mark.asyncio
async def test_emit_event_returns_zero_when_disabled():
    """emit_event should be a zero-DB-touch no-op when gated off."""
    import uuid as _u
    from unittest.mock import AsyncMock, patch
    with patch(
        "app.services.webhook_service._post_allowed",
        AsyncMock(return_value=False),
    ), patch(
        "app.db.postgres.AsyncSessionLocal"
    ) as fake_session:
        count = await svc.emit_event(
            "run.completed",
            project_id=_u.uuid4(),
            payload={"ok": True},
        )
    assert count == 0
    # We should never have touched the DB.
    fake_session.assert_not_called()


@pytest.mark.asyncio
async def test_emit_event_ignores_unknown_event_type():
    import uuid as _u
    from unittest.mock import AsyncMock, patch
    with patch(
        "app.services.webhook_service._post_allowed",
        AsyncMock(return_value=True),
    ):
        count = await svc.emit_event(
            "something.not.supported",
            project_id=_u.uuid4(),
            payload={},
        )
    assert count == 0
