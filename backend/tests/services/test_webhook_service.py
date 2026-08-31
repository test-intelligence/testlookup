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
import sys
import uuid
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

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


@pytest.mark.asyncio
async def test_emit_event_database_failure_is_best_effort(monkeypatch):
    """Webhook persistence failure must not escape into the primary operation."""

    class _BrokenSession:
        async def __aenter__(self):
            raise RuntimeError("webhook database unavailable")

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(svc, "AsyncSessionLocal", _BrokenSession)
    monkeypatch.setattr(svc, "_post_allowed", AsyncMock(return_value=True))

    count = await svc.emit_event(
        "defect.create_requested",
        project_id=uuid.uuid4(),
        payload={"summary": "primary operation must survive"},
    )

    assert count == 0


@pytest.mark.asyncio
async def test_emit_event_dispatches_later_subscriptions_after_enqueue_failure(
    monkeypatch,
):
    subscriptions = [
        SimpleNamespace(
            id=uuid.uuid4(),
            events=["run.completed"],
        )
        for _ in range(2)
    ]

    class _Scalars:
        def all(self):
            return subscriptions

    class _Result:
        def scalars(self):
            return _Scalars()

    class _Session:
        def __init__(self):
            self.pending = None

        async def execute(self, _statement):
            return _Result()

        def add(self, row):
            self.pending = row

        async def flush(self):
            self.pending.id = uuid.uuid4()

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    attempted = []

    def delay(**kwargs):
        attempted.append(kwargs["delivery_id"])
        if len(attempted) == 1:
            raise RuntimeError("first enqueue rejected")

    worker_tasks = ModuleType("app.worker.tasks")
    worker_tasks.deliver_webhook = SimpleNamespace(delay=delay)
    monkeypatch.setitem(sys.modules, "app.worker.tasks", worker_tasks)
    monkeypatch.setattr(svc, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(svc, "_post_allowed", AsyncMock(return_value=True))

    count = await svc.emit_event(
        "run.completed",
        project_id=uuid.uuid4(),
        payload={"run_id": str(uuid.uuid4())},
    )

    assert count == 2
    assert len(attempted) == 2


@pytest.mark.asyncio
async def test_replay_enqueue_failure_marks_new_delivery_failed(monkeypatch):
    original = SimpleNamespace(
        id=uuid.uuid4(),
        subscription_id=uuid.uuid4(),
        event_type="run.completed",
        event_payload={"run_id": str(uuid.uuid4())},
        status="FAILED",
    )
    created = []

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class _Session:
        def __init__(self, replay_lookup=False):
            self.replay_lookup = replay_lookup

        async def execute(self, _statement):
            return _Result(created[0] if self.replay_lookup else original)

        def add(self, row):
            created.append(row)

        async def flush(self):
            created[0].id = uuid.uuid4()

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    sessions = iter((_Session(), _Session(replay_lookup=True)))
    worker_tasks = ModuleType("app.worker.tasks")
    worker_tasks.deliver_webhook = SimpleNamespace(
        delay=Mock(side_effect=RuntimeError("broker unavailable"))
    )
    monkeypatch.setitem(sys.modules, "app.worker.tasks", worker_tasks)
    monkeypatch.setattr(svc, "AsyncSessionLocal", lambda: next(sessions))
    monkeypatch.setattr(svc, "_post_allowed", AsyncMock(return_value=True))

    replay_id = await svc.replay_delivery(original.id)

    assert replay_id is None
    assert created[0].status == "FAILED"
    assert created[0].error == "webhook replay enqueue failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("http_status", "expected_status"),
    [
        (None, "DLQ"),  # network exception
        (503, "DLQ"),
        (400, "FAILED"),
    ],
)
async def test_deliver_respects_zero_retry_budget(
    monkeypatch, http_status, expected_status,
):
    """An explicit zero must never be replaced by the default retry limit."""
    subscription = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        enabled=True,
        has_secret=False,
        target_url="https://hooks.example.test/testlookup",
        max_retries=0,
        last_delivered_at=None,
        last_failure_at=None,
        last_error=None,
        failure_count=0,
        total_delivered=0,
    )
    delivery = SimpleNamespace(
        id=uuid.uuid4(),
        subscription_id=subscription.id,
        event_type="run.completed",
        event_payload={"ok": True},
        status="PENDING",
        attempt_count=0,
        error=None,
        http_status=None,
        response_preview=None,
        delivered_at=None,
    )

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class _Session:
        calls = 0

        async def execute(self, _statement):
            self.calls += 1
            return _Result(delivery if self.calls == 1 else subscription)

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            if http_status is None:
                raise OSError("receiver unavailable")
            return SimpleNamespace(status_code=http_status, text="receiver response")

    monkeypatch.setattr(svc, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(svc, "_post_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(
        svc.asyncio,
        "to_thread",
        AsyncMock(return_value=(True, "public target")),
    )
    monkeypatch.setattr(svc.httpx, "AsyncClient", _Client)

    result = await svc.deliver(delivery.id)

    assert result.get("retry") is not True
    assert delivery.attempt_count == 1
    assert delivery.status == expected_status


@pytest.mark.asyncio
async def test_deliver_does_not_send_unsigned_when_configured_secret_is_missing(
    monkeypatch,
):
    from app.services import secret_service

    subscription = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        enabled=True,
        has_secret=True,
        target_url="https://hooks.example.test/testlookup",
        max_retries=5,
        last_failure_at=None,
        last_error=None,
        failure_count=0,
        last_delivered_at=None,
        total_delivered=0,
    )
    delivery = SimpleNamespace(
        id=uuid.uuid4(),
        subscription_id=subscription.id,
        event_type="run.completed",
        event_payload={"ok": True},
        status="PENDING",
        attempt_count=0,
        error=None,
    )

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class _Session:
        calls = 0

        async def execute(self, _statement):
            self.calls += 1
            return _Result(delivery if self.calls == 1 else subscription)

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    post = AsyncMock(
        return_value=SimpleNamespace(status_code=200, text="unexpected success")
    )

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *args, **kwargs):
            return await post(*args, **kwargs)

    monkeypatch.setattr(svc, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(svc, "_post_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(
        svc.asyncio,
        "to_thread",
        AsyncMock(return_value=(True, "public target")),
    )
    monkeypatch.setattr(secret_service, "read_secret", AsyncMock(return_value=None))
    monkeypatch.setattr(svc.httpx, "AsyncClient", _Client)

    result = await svc.deliver(delivery.id)

    post.assert_not_awaited()
    assert result == {"error": "signing_secret_unavailable"}
    assert delivery.status == "FAILED"
    assert "signing secret unavailable" in delivery.error
