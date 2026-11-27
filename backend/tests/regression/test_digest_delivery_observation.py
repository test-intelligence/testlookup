"""Durable scheduled-digest observation with real local SQL transactions.

Only the subscription table is created in ephemeral SQLite. Content, user
lookup, logs and providers are synthetic; no broker or provider is contacted.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import uuid

from prometheus_client import REGISTRY
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.postgres import DigestSubscription, NotificationLog, User
from app.services.digest_delivery_observation import (
    digest_claim_observation,
    digest_final_observation,
    observe_digest_phase,
    reset_digest_observation,
)
from app.services.digest_operational_metrics import (
    DELIVERY_GRACE_SECONDS,
    refresh_digest_operational_metrics,
)

pytestmark = pytest.mark.regression


def _sample(name, *, schedule="WEEKLY", **labels):
    value = REGISTRY.get_sample_value(
        f"testlookup_weekly_digest_{name}", {"schedule": schedule, **labels},
    )
    assert value is not None
    return value


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@pytest.fixture
async def world(monkeypatch, tmp_path):
    from app.db import postgres
    from app.services import digest_content_service, digest_scope_service
    from app.services.notification import email_service, manager
    from app.worker import tasks

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'digests.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: DigestSubscription.__table__.create(sync))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id, project_id = uuid.uuid4(), uuid.uuid4()
    logs = []
    user = SimpleNamespace(id=user_id, email="synthetic@example.invalid", is_active=True)

    class Session:
        def __init__(self):
            self.db = sessions()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            await self.db.close()

        def __getattr__(self, name):
            return getattr(self.db, name)

        async def execute(self, statement, *args, **kwargs):
            entities = getattr(statement, "column_descriptions", ())
            if entities and entities[0].get("entity") is User:
                return SimpleNamespace(scalar_one_or_none=lambda: user)
            return await self.db.execute(statement, *args, **kwargs)

        def add(self, row):
            assert isinstance(row, NotificationLog)
            logs.append(row)

    monkeypatch.setattr(postgres, "AsyncSessionLocal", Session, raising=False)
    generate = AsyncMock(return_value={"project_name": "Synthetic project", "is_zero_change": False})
    send = AsyncMock(return_value=None)
    monkeypatch.setattr(digest_content_service, "generate_digest", generate)
    monkeypatch.setattr(digest_content_service, "render_digest_html", lambda _digest: "<p>Synthetic</p>")
    monkeypatch.setattr(digest_scope_service, "resolve_digest_scope", AsyncMock(return_value=digest_scope_service.PROJECT_SCOPE))
    monkeypatch.setattr(email_service, "get_smtp_config", AsyncMock(return_value={"enabled": True}))
    monkeypatch.setattr(email_service, "send_html_email_with_attachments", send)
    monkeypatch.setattr(manager, "digest_owner_block_reason", AsyncMock(return_value=None))
    captured = []
    monkeypatch.setattr(tasks, "_run_async", captured.append)

    async def dispatch():
        tasks.dispatch_scheduled_digests.run()
        assert len(captured) == 1
        await captured.pop()

    async def create(**kwargs):
        values = {
            "id": uuid.uuid4(), "user_id": user_id, "project_id": project_id,
            "name": "Synthetic subscription", "schedule": "WEEKLY", "channel": "email",
            "next_delivery_at": datetime.now(timezone.utc) - timedelta(days=3),
            "created_at": datetime.now(timezone.utc) - timedelta(days=10),
        }
        values.update(kwargs)
        async with sessions() as db:
            row = DigestSubscription(**values)
            db.add(row)
            await db.commit()
            return row.id

    async def read(identifier):
        async with sessions() as db:
            return await db.get(DigestSubscription, identifier)

    yield SimpleNamespace(
        sessions=sessions, create=create, read=read, dispatch=dispatch,
        send=send, generate=generate, logs=logs, manager=manager,
    )
    for coroutine in captured:
        coroutine.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_generated_content_provider_failure_and_later_success_are_distinct(world):
    identifier = await world.create()
    initial = await world.read(identifier)

    async def failed_provider(**_kwargs):
        persisted = await world.read(identifier)
        assert persisted.last_delivery_outcome == "generated"
        assert persisted.last_content_generated_at is not None
        assert persisted.last_delivered_at is None
        raise RuntimeError("synthetic provider refused")

    world.send.side_effect = failed_provider
    await world.dispatch()
    failed = await world.read(identifier)
    assert failed.last_delivery_outcome == "failed"
    assert failed.last_delivered_at is None and failed.delivery_count == 0
    assert _utc(failed.pending_delivery_since_at) == _utc(initial.next_delivery_at)
    assert failed.delivery_observation_token is None
    await refresh_digest_operational_metrics()
    assert _sample("delivery_outcomes", outcome="failed") == 1
    assert _sample("oldest_overdue_seconds") > 0

    # Simulate the next legitimate due retry; the oldest unresolved slot stays.
    async with world.sessions() as db:
        await db.execute(update(DigestSubscription).where(DigestSubscription.id == identifier).values(
            next_delivery_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        ))
        await db.commit()
    world.send.side_effect = None
    await world.dispatch()
    sent = await world.read(identifier)
    assert sent.last_delivery_outcome == "sent"
    assert sent.last_delivered_at is not None and sent.delivery_count == 1
    assert sent.pending_delivery_since_at is None
    await refresh_digest_operational_metrics()
    assert _sample("oldest_overdue_seconds") == 0
    assert _sample("delivery_outcomes", outcome="sent") == 1
    assert [row.status for row in world.logs] == ["failed", "sent"]


@pytest.mark.asyncio
async def test_claim_then_worker_loss_survives_future_next_slot_and_new_collection(world):
    identifier = await world.create()
    original = await world.read(identifier)
    world.generate.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await world.dispatch()
    receipt = await world.read(identifier)
    assert _utc(receipt.next_delivery_at) > datetime.now(timezone.utc)
    assert _utc(receipt.pending_delivery_since_at) == _utc(original.next_delivery_at)
    assert receipt.last_delivery_outcome == "generating"
    assert receipt.delivery_observation_token is not None
    assert receipt.last_delivered_at is None
    world.send.assert_not_awaited()
    await refresh_digest_operational_metrics()
    assert _sample("subscriptions", state="unresolved") == 1
    assert _sample("subscriptions", state="due") == 1
    assert _sample("oldest_overdue_seconds") > 0


@pytest.mark.asyncio
async def test_generation_error_is_unknown_unresolved_without_delivery_success(world):
    identifier = await world.create()
    world.generate.side_effect = RuntimeError("synthetic generation failure")
    await world.dispatch()
    receipt = await world.read(identifier)
    assert receipt.last_delivery_outcome == "unknown"
    assert receipt.pending_delivery_since_at is not None
    assert receipt.last_content_generated_at is None
    assert receipt.last_delivered_at is None and receipt.delivery_count == 0
    world.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_intentional_no_change_skip_resolves_due_slot_without_claiming_delivery(world):
    identifier = await world.create(send_when_unchanged=False)
    world.generate.return_value = {"project_name": "Synthetic", "is_zero_change": True}
    await world.dispatch()
    skipped = await world.read(identifier)
    assert skipped.last_delivery_outcome == "skipped"
    assert skipped.pending_delivery_since_at is None
    assert skipped.last_delivered_at is None and skipped.delivery_count == 0
    world.send.assert_not_awaited()
    await refresh_digest_operational_metrics()
    assert _sample("oldest_overdue_seconds") == 0
    assert _sample("delivery_outcomes", outcome="skipped") == 1


@pytest.mark.asyncio
async def test_owner_authorization_skip_never_counts_a_provider_delivery(world, monkeypatch):
    identifier = await world.create()
    monkeypatch.setattr(world.manager, "digest_owner_block_reason", AsyncMock(return_value="synthetic access revoked"))
    await world.dispatch()
    row = await world.read(identifier)
    assert row.last_delivery_outcome == "skipped" and row.pending_delivery_since_at is None
    assert row.last_delivered_at is None and row.delivery_count == 0
    world.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_unsupported_legacy_channel_cannot_be_recorded_as_sent(world):
    identifier = await world.create(channel="invalid")
    await world.dispatch()
    row = await world.read(identifier)
    assert row.last_delivery_outcome == "failed" and row.pending_delivery_since_at is not None
    assert row.last_delivered_at is None and row.delivery_count == 0
    world.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_provider_completion_cannot_clear_a_new_claim(world):
    identifier = await world.create()
    newer_token = uuid.uuid4()
    newer_due = datetime.now(timezone.utc) - timedelta(hours=1)

    async def completed_after_new_claim(**_kwargs):
        async with world.sessions() as db:
            await db.execute(update(DigestSubscription).where(DigestSubscription.id == identifier).values(
                delivery_observation_token=newer_token, pending_delivery_since_at=newer_due,
                last_delivery_outcome="generating",
            ))
            await db.commit()

    world.send.side_effect = completed_after_new_claim
    await world.dispatch()
    row = await world.read(identifier)
    assert row.delivery_observation_token == newer_token
    assert _utc(row.pending_delivery_since_at) == newer_due
    assert row.last_delivery_outcome == "generating"
    # The old provider acceptance remains an honest sent NotificationLog,
    # while it cannot settle the new subscription slot or overwrite its state.
    assert [log.status for log in world.logs] == ["sent"]


@pytest.mark.asyncio
async def test_optional_phase_observation_failure_does_not_gate_provider(world, monkeypatch):
    identifier = await world.create()
    monkeypatch.setattr("app.services.digest_delivery_observation.observe_digest_phase", AsyncMock(return_value=False))
    await world.dispatch()
    row = await world.read(identifier)
    world.send.assert_awaited_once()
    assert row.last_delivery_outcome == "sent" and row.delivery_count == 1
    assert row.pending_delivery_since_at is None


@pytest.mark.asyncio
async def test_cas_rejects_stale_generation_and_completion_after_new_schedule(world):
    old_token, new_token = uuid.uuid4(), uuid.uuid4()
    identifier = await world.create(delivery_observation_token=new_token, last_delivery_outcome="generating")
    assert await observe_digest_phase(identifier, old_token, phase="generated") is False
    assert await observe_digest_phase(identifier, old_token, phase="skipped") is False
    current = await world.read(identifier)
    assert current.delivery_observation_token == new_token
    assert current.last_content_generated_at is None
    assert await observe_digest_phase(identifier, new_token, phase="generated") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("weekday", range(7))
async def test_due_work_is_weekday_independent_and_timezone_normalized(world, weekday):
    # The first of November 2026 is Sunday, so this covers every due weekday.
    due = datetime(2026, 11, 1 + weekday, 11, 0, tzinfo=timezone.utc)
    await world.create(next_delivery_at=due)
    now = due + timedelta(seconds=DELIVERY_GRACE_SECONDS + 17)
    await refresh_digest_operational_metrics(now=now.astimezone(timezone(timedelta(hours=-7))))
    assert _sample("subscriptions", state="due") == 1
    assert _sample("subscriptions", state="never_delivered") == 1
    assert _sample("oldest_overdue_seconds") == 17


@pytest.mark.asyncio
async def test_normal_daily_dispatch_lag_is_not_overdue(world):
    due = datetime(2026, 11, 4, 7, 1, tzinfo=timezone.utc)
    await world.create(next_delivery_at=due)
    await refresh_digest_operational_metrics(now=due + timedelta(hours=24, minutes=59))
    assert _sample("subscriptions", state="due") == 1
    assert _sample("oldest_overdue_seconds") == 0


@pytest.mark.asyncio
async def test_no_active_subscriptions_is_measured_zero_and_missing_schedule_is_explicit(world):
    await world.create(is_active=False)
    await world.create(is_paused=True)
    await world.create(schedule="DAILY")
    await refresh_digest_operational_metrics()
    for schedule in ("WEEKLY", "WEEKLY_RETRO"):
        assert _sample("subscriptions", schedule=schedule, state="enabled") == 0
        assert _sample("oldest_overdue_seconds", schedule=schedule) == 0
    await world.create(schedule="WEEKLY_RETRO", next_delivery_at=None)
    await refresh_digest_operational_metrics()
    assert _sample("subscriptions", schedule="WEEKLY_RETRO", state="enabled") == 1
    assert _sample("subscriptions", schedule="WEEKLY_RETRO", state="missing_schedule") == 1
    assert _sample("oldest_overdue_seconds", schedule="WEEKLY_RETRO") == 0


@pytest.mark.asyncio
async def test_unknown_durable_outcome_fails_collection_instead_of_healthy_zero(world):
    await world.create(last_delivery_outcome="invalid")
    with pytest.raises(ValueError, match="unknown digest"):
        await refresh_digest_operational_metrics()


def test_schedule_reset_invalidates_attempt_but_preserves_real_success_history():
    history = datetime(2026, 11, 1, tzinfo=timezone.utc)
    row = SimpleNamespace(pending_delivery_since_at=history, delivery_observation_token=uuid.uuid4(),
                          last_delivery_outcome="generated", last_delivered_at=history)
    reset_digest_observation(row)
    assert row.pending_delivery_since_at is None and row.delivery_observation_token is None
    assert row.last_delivery_outcome is None and row.last_delivered_at == history
    assert "pending_delivery_since_at" not in digest_final_observation("failed")


def test_migration_is_nullable_additive_with_no_invented_historical_state():
    path = Path(__file__).resolve().parents[2] / "migrations/versions/0262_digest_delivery_observations.py"
    spec = importlib.util.spec_from_file_location("digest_migration_0262", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0262" and module.down_revision == "0261"
    module.op = MagicMock()
    module.upgrade()
    added = [call.args[1] for call in module.op.add_column.call_args_list]
    assert len(added) == 5
    for column in added:
        actual = DigestSubscription.__table__.c[column.name]
        assert actual.nullable is True and column.nullable is True
        assert column.server_default is None
    module.op.execute.assert_not_called()
    module.downgrade()
    assert [call.args[1] for call in module.op.drop_column.call_args_list] == [c.name for c in reversed(added)]


def test_migration_round_trip_preserves_existing_rows_without_inventing_success():
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).resolve().parents[2] / "migrations/versions/0262_digest_delivery_observations.py"
    spec = importlib.util.spec_from_file_location("digest_migration_round_trip", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE digest_subscriptions (id TEXT PRIMARY KEY, "
                "next_delivery_at DATETIME, last_delivered_at DATETIME, delivery_count INTEGER)"
            )
            connection.exec_driver_sql(
                "INSERT INTO digest_subscriptions VALUES "
                "('synthetic', '2026-11-04 07:00:00', NULL, 7)"
            )
            module.op = Operations(MigrationContext.configure(connection))
            module.upgrade()
            row = connection.execute(sa.text("SELECT * FROM digest_subscriptions")).mappings().one()
            assert row["delivery_count"] == 7 and row["last_delivered_at"] is None
            assert row["next_delivery_at"] == "2026-11-04 07:00:00"
            assert all(row[name] is None for name, _ in module.COLUMNS)
            module.downgrade()
            remaining = {column["name"] for column in sa.inspect(connection).get_columns("digest_subscriptions")}
            assert remaining == {"id", "next_delivery_at", "last_delivered_at", "delivery_count"}
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_retry_claim_preserves_original_slot_in_real_update(world):
    original = datetime(2026, 10, 1, tzinfo=timezone.utc)
    identifier = await world.create(pending_delivery_since_at=original)
    token = uuid.uuid4()
    async with world.sessions() as db:
        await db.execute(update(DigestSubscription).where(DigestSubscription.id == identifier).values(
            next_delivery_at=datetime.now(timezone.utc) + timedelta(days=7),
            **digest_claim_observation(now=datetime.now(timezone.utc), token=token),
        ))
        await db.commit()
    receipt = await world.read(identifier)
    assert _utc(receipt.pending_delivery_since_at) == original
    assert receipt.delivery_observation_token == token
