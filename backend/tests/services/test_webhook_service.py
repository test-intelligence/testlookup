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
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
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
async def test_post_allowed_can_surface_transient_flag_lookup_failure():
    from unittest.mock import AsyncMock, patch

    from app.core.config import settings

    with patch.object(settings, "AI_OFFLINE_MODE", False), patch(
        "app.services.feature_flags.is_enabled",
        AsyncMock(side_effect=ConnectionError("feature store unavailable")),
    ):
        assert await svc._post_allowed() is False
        with pytest.raises(svc.WebhookGateLookupError):
            await svc._post_allowed(raise_on_lookup_error=True)


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
async def test_durable_emit_retries_transient_feature_flag_lookup_failure(monkeypatch):
    gate = AsyncMock(side_effect=svc.WebhookGateLookupError("lookup failed"))
    monkeypatch.setattr(svc, "_post_allowed", gate)

    with pytest.raises(svc.WebhookGateLookupError, match="lookup failed"):
        await svc.emit_event(
            "run.completed",
            project_id=uuid.uuid4(),
            payload={"run_id": str(uuid.uuid4())},
            delivery_scope="run-completed:stable:v1",
            raise_on_persistence_error=True,
        )

    gate.assert_awaited_once_with(raise_on_lookup_error=True)


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
async def test_durable_emit_event_propagates_persistence_failure(monkeypatch):
    class _BrokenSession:
        async def __aenter__(self):
            raise RuntimeError("webhook database unavailable")

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(svc, "AsyncSessionLocal", _BrokenSession)
    monkeypatch.setattr(svc, "_post_allowed", AsyncMock(return_value=True))

    with pytest.raises(RuntimeError, match="webhook database unavailable"):
        await svc.emit_event(
            "run.completed",
            project_id=uuid.uuid4(),
            payload={"run_id": str(uuid.uuid4())},
            delivery_scope=f"run-outbox:{uuid.uuid4()}",
            raise_on_persistence_error=True,
        )


@pytest.mark.asyncio
async def test_durable_emit_event_inserts_and_publishes_once(monkeypatch):
    subscription = SimpleNamespace(id=uuid.uuid4(), events=["run.completed"])
    delivery_id = uuid.uuid4()

    preferences = Mock()
    preferences.scalars.return_value.all.return_value = [subscription]
    inserted = Mock()
    inserted.all.return_value = [
        SimpleNamespace(id=delivery_id, subscription_id=subscription.id)
    ]
    duplicate = Mock()
    duplicate.all.return_value = []
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[preferences, inserted, preferences, duplicate]
        ),
        commit=AsyncMock(),
    )

    class _Session:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    from app.worker import tasks as worker_tasks

    publish = Mock()
    monkeypatch.setattr(svc, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(svc, "_post_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(worker_tasks.deliver_webhook, "delay", publish)

    kwargs = {
        "project_id": uuid.uuid4(),
        "payload": {"run_id": str(uuid.uuid4())},
        "delivery_scope": f"run-outbox:{uuid.uuid4()}",
        "raise_on_persistence_error": True,
    }
    assert await svc.emit_event("run.completed", **kwargs) == 1
    assert await svc.emit_event("run.completed", **kwargs) == 0
    publish.assert_called_once_with(delivery_id=str(delivery_id))
    insert_statement = db.execute.await_args_list[1].args[0]
    assert uuid.UUID(kwargs["payload"]["run_id"]) in (
        insert_statement.compile().params.values()
    )


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
async def test_replay_enqueue_failure_leaves_recoverable_pending_row(monkeypatch):
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
        async def execute(self, _statement):
            return _Result(original)

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

    worker_tasks = ModuleType("app.worker.tasks")
    worker_tasks.deliver_webhook = SimpleNamespace(
        delay=Mock(side_effect=RuntimeError("broker unavailable"))
    )
    monkeypatch.setitem(sys.modules, "app.worker.tasks", worker_tasks)
    monkeypatch.setattr(svc, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(svc, "_post_allowed", AsyncMock(return_value=True))

    replay_id = await svc.replay_delivery(original.id)

    assert replay_id == created[0].id
    assert created[0].status == "PENDING"


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
    monkeypatch.setattr(svc, "get_public_http_client", lambda: _Client())

    async def _apply_transition(
        _db,
        *,
        delivery_values,
        subscription_values=None,
        **_kwargs,
    ):
        for name, value in delivery_values.items():
            setattr(delivery, name, value)
        if subscription_values:
            for name, value in subscription_values.items():
                if name != "failure_count":
                    setattr(subscription, name, value)
        return True

    monkeypatch.setattr(svc, "_transition_processing_delivery", _apply_transition)

    result = await svc.deliver(delivery.id)

    assert result.get("retry") is not True
    assert delivery.attempt_count == 1
    assert delivery.status == expected_status


@pytest.mark.asyncio
async def test_deliver_sends_the_fresh_review_projection(monkeypatch):
    subscription = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        enabled=True,
        has_secret=False,
        target_url="https://hooks.example.test/testlookup",
        max_retries=5,
    )
    delivery = SimpleNamespace(
        id=uuid.uuid4(),
        subscription_id=subscription.id,
        run_id=uuid.uuid4(),
        event_type="release.decided",
        event_payload={"recommendation": "PENDING_REVIEW"},
        status="PENDING",
        attempt_count=0,
        dispatch_attempts=0,
        dispatch_token=None,
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

    posted: dict = {}

    class _Client:
        async def post(self, _url, **kwargs):
            posted.update(kwargs)
            return SimpleNamespace(status_code=200, text="accepted")

    refreshed = {
        "recommendation": "GO",
        "draft_recommendation": None,
        "review": {"state": "accepted"},
    }
    distribution_decision = object()
    refresh = AsyncMock(return_value=(refreshed, distribution_decision))
    record_distribution = AsyncMock()
    monkeypatch.setattr(svc, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(svc, "_post_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(svc.asyncio, "to_thread", AsyncMock(return_value=(True, "public")))
    monkeypatch.setattr(svc, "_refresh_review_gated_delivery", refresh)
    monkeypatch.setattr(svc, "get_public_http_client", _Client)
    successful_values = {}

    async def _transition(_db, **kwargs):
        successful_values.update(kwargs["delivery_values"])
        before_commit = kwargs.get("before_commit")
        if before_commit is not None:
            await before_commit(_db)
        return True

    monkeypatch.setattr(svc, "_transition_processing_delivery", _transition)
    monkeypatch.setattr(
        "app.services.report_distribution_policy.record_distribution",
        record_distribution,
    )

    result = await svc.deliver(delivery.id)

    assert result == {"status": "SUCCESS", "http_status": 200}
    refresh.assert_awaited_once()
    sent = json.loads(posted["content"])
    assert sent["data"] == refreshed
    assert successful_values["event_payload"] == refreshed
    record_distribution.assert_awaited_once()
    assert record_distribution.await_args.args[1] is distribution_decision
    assert record_distribution.await_args.kwargs == {
        "channel": "release.decided.webhook",
        "run_id": delivery.run_id,
        "project_id": subscription.project_id,
    }


@pytest.mark.asyncio
async def test_stale_worker_cannot_record_success_or_increment_subscription(monkeypatch):
    """A worker that loses its token during provider I/O cannot win afterward."""
    subscription = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        enabled=True,
        has_secret=False,
        target_url="https://hooks.example.test/testlookup",
        max_retries=5,
        total_delivered=0,
    )
    delivery = SimpleNamespace(
        id=uuid.uuid4(),
        subscription_id=subscription.id,
        event_type="run.completed",
        event_payload={"run_id": str(uuid.uuid4())},
        status="PENDING",
        attempt_count=0,
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
            return SimpleNamespace(status_code=200, text="accepted")

    transition = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(svc, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(svc, "_post_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(
        svc.asyncio,
        "to_thread",
        AsyncMock(return_value=(True, "public target")),
    )
    monkeypatch.setattr(svc, "get_public_http_client", lambda: _Client())
    monkeypatch.setattr(svc, "_transition_processing_delivery", transition)

    result = await svc.deliver(delivery.id)

    assert result == {"skipped": "stale_dispatch_token"}
    assert transition.await_count == 2
    assert transition.await_args_list[1].kwargs["delivery_values"]["status"] == "SUCCESS"
    assert subscription.total_delivered == 0


@pytest.mark.asyncio
async def test_processing_transition_uses_token_compare_and_set():
    token = uuid.uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(rowcount=0)),
        rollback=AsyncMock(),
        commit=AsyncMock(),
    )

    changed = await svc._transition_processing_delivery(
        db,
        delivery_id=uuid.uuid4(),
        dispatch_token=token,
        delivery_values={"status": "SUCCESS"},
        subscription_id=uuid.uuid4(),
        subscription_values={"total_delivered": 99},
    )

    assert changed is False
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
    assert db.execute.await_count == 1
    statement = db.execute.await_args.args[0]
    compiled = statement.compile()
    sql = str(compiled)
    assert "webhook_deliveries.id" in sql
    assert "webhook_deliveries.status" in sql
    assert "webhook_deliveries.dispatch_token" in sql
    assert token in compiled.params.values()
    assert "PROCESSING" in compiled.params.values()


@pytest.mark.asyncio
async def test_delivery_retries_when_feature_flag_store_is_unavailable(monkeypatch):
    subscription = SimpleNamespace(
        id=uuid.uuid4(),
        enabled=True,
    )
    delivery = SimpleNamespace(
        id=uuid.uuid4(),
        subscription_id=subscription.id,
        status="PENDING",
        dispatch_attempts=0,
        dispatch_token=None,
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

    transition = AsyncMock(return_value=True)
    monkeypatch.setattr(svc, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(
        svc,
        "_post_allowed",
        AsyncMock(side_effect=svc.WebhookGateLookupError("feature store unavailable")),
    )
    monkeypatch.setattr(svc, "_transition_processing_delivery", transition)

    result = await svc.deliver(delivery.id)

    assert result["retry"] is True
    assert "feature store unavailable" in result["error"]
    assert transition.await_args.kwargs["delivery_values"]["status"] == "PENDING"


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
    monkeypatch.setattr(svc, "get_public_http_client", lambda: _Client())
    terminalize = AsyncMock(return_value=True)
    monkeypatch.setattr(svc, "_mark_delivery_failed", terminalize)

    result = await svc.deliver(delivery.id)

    post.assert_not_awaited()
    assert result == {"error": "signing_secret_unavailable"}
    assert terminalize.await_args.kwargs["delivery_id"] == delivery.id
    assert terminalize.await_args.kwargs["dispatch_token"] == delivery.dispatch_token
    assert terminalize.await_args.kwargs["subscription_id"] == subscription.id


@pytest.mark.asyncio
@pytest.mark.parametrize("preflight", ["disabled", "unsafe", "missing_secret"])
async def test_stale_webhook_preflight_worker_cannot_terminalize_new_lease(
    monkeypatch,
    preflight,
):
    from app.services import secret_service

    subscription = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        enabled=preflight != "disabled",
        has_secret=preflight == "missing_secret",
        target_url="https://hooks.example.test/testlookup",
        max_retries=5,
    )
    delivery = SimpleNamespace(
        id=uuid.uuid4(),
        subscription_id=subscription.id,
        event_type="run.completed",
        event_payload={"ok": True},
        status="PENDING",
        attempt_count=0,
        dispatch_token=None,
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

    terminalize = AsyncMock(return_value=False)
    monkeypatch.setattr(svc, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(svc, "_mark_delivery_failed", terminalize)
    monkeypatch.setattr(svc, "_post_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(
        svc.asyncio,
        "to_thread",
        AsyncMock(
            return_value=(
                preflight != "unsafe",
                "private target" if preflight == "unsafe" else "public target",
            )
        ),
    )
    monkeypatch.setattr(secret_service, "read_secret", AsyncMock(return_value=None))

    result = await svc.deliver(delivery.id)

    assert result == {"skipped": "stale_dispatch_token"}
    terminalize.assert_awaited_once()
    assert terminalize.await_args.kwargs["dispatch_token"] is not None


@pytest.mark.asyncio
async def test_pending_webhook_relay_recovers_lost_broker_publish(monkeypatch):
    from app.worker import tasks as worker_tasks

    row = SimpleNamespace(
        id=uuid.uuid4(),
        dispatch_token=uuid.uuid4(),
    )
    db = SimpleNamespace(commit=AsyncMock())

    class _Session:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    publish = Mock()
    monkeypatch.setattr(svc, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(
        svc,
        "claim_pending_webhook_dispatches",
        AsyncMock(return_value=[row]),
    )
    monkeypatch.setattr(worker_tasks.deliver_webhook, "apply_async", publish)

    result = await svc.relay_pending_webhook_deliveries()

    assert result == {"claimed": 1, "published": 1, "failed": 0}
    publish.assert_called_once_with(
        kwargs={
            "delivery_id": str(row.id),
            "dispatch_token": str(row.dispatch_token),
        },
        queue="default",
        task_id=f"webhook-delivery-{row.id}",
    )


@pytest.mark.asyncio
async def test_expired_accepted_webhook_publish_reuses_token_without_exhaustion():
    token = uuid.uuid4()
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    row = SimpleNamespace(
        id=uuid.uuid4(),
        status="SENDING",
        dispatch_attempts=svc._MAX_WEBHOOK_DISPATCH_ATTEMPTS,
        dispatch_failures=0,
        dispatch_token=token,
        dispatch_lease_expires_at=expired,
        next_dispatch_at=expired,
        error=None,
    )
    result = Mock()
    result.scalars.return_value.all.return_value = [row]
    db = SimpleNamespace(execute=AsyncMock(return_value=result))

    claimed = await svc.claim_pending_webhook_dispatches(db, limit=1)

    assert claimed == [row]
    assert row.status == "SENDING"
    assert row.dispatch_attempts == svc._MAX_WEBHOOK_DISPATCH_ATTEMPTS + 1
    assert row.dispatch_failures == 0
    assert row.dispatch_token == token


@pytest.mark.asyncio
async def test_terminal_webhook_delivery_suppresses_duplicate_send(monkeypatch):
    delivery = SimpleNamespace(id=uuid.uuid4(), status="SUCCESS")
    result = Mock()
    result.scalar_one_or_none.return_value = delivery

    class _Session:
        async def execute(self, _statement):
            return result

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(svc, "AsyncSessionLocal", _Session)

    assert await svc.deliver(delivery.id) == {
        "skipped": "delivery_already_terminal"
    }
