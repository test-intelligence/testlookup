"""Shared SQL receipts with synthetic Mongo and mounted scoped recovery API."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

from fastapi import FastAPI, HTTPException
import httpx
from pymongo.errors import DuplicateKeyError, NetworkTimeout
from pymongo.read_preferences import ReadPreference
from pymongo.write_concern import WriteConcern
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import metrics
from app.models.postgres import PipelineEventOutbox, ProjectActivityEvent, UserRole
from app.services import pipeline_event_delivery as delivery
from app.services import pipeline_event_operational_metrics as health

pytestmark = pytest.mark.regression


@pytest.fixture
async def world(monkeypatch, tmp_path):
    from app.db import mongo, postgres

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'events.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: PipelineEventOutbox.__table__.create(sync))
        await connection.run_sync(lambda sync: ProjectActivityEvent.__table__.create(sync))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(postgres, "AsyncSessionLocal", sessions)
    state = SimpleNamespace(now=datetime.now(timezone.utc), project=uuid.uuid4(), other=uuid.uuid4(), sessions=sessions)
    monkeypatch.setattr(delivery, "_utcnow", lambda: state.now)

    class Collection:
        def __init__(self):
            self.documents = {}
            self.failure = None
            self.accept_then_cancel = False
            self.writes = 0
            self.write_concern = WriteConcern()
            self.options = None

        def with_options(self, **kwargs):
            self.options = kwargs
            return self

        async def insert_one(self, document):
            self.writes += 1
            if self.failure:
                raise self.failure
            if document["_id"] in self.documents:
                raise DuplicateKeyError("synthetic duplicate")
            stored = copy.deepcopy(document)
            stored["timestamp"] = stored["timestamp"].replace(tzinfo=None)  # BSON's default UTC readback.
            self.documents[document["_id"]] = stored
            if self.accept_then_cancel:
                raise asyncio.CancelledError()
            return SimpleNamespace(inserted_id=document["_id"])

        async def find_one(self, query):
            return copy.deepcopy(self.documents.get(query["_id"]))

    collection = Collection()
    monkeypatch.setattr(mongo, "get_mongo_db", lambda: {"pipeline_event_log": collection})

    async def create(*, project_id=None, **values):
        identifier = values.pop("id", uuid.uuid4())
        defaults = dict(
            id=identifier, project_id=project_id or state.project, pipeline_run_id=str(uuid.uuid4()),
            status="pending", attempts=0, created_at=state.now - timedelta(minutes=10), next_attempt_at=state.now,
        )
        defaults.update(values)
        defaults.setdefault("payload", {"pipeline_run_id": defaults["pipeline_run_id"], "event_type": "stage_completed",
                                       "timestamp": state.now.isoformat(), "detail": {"synthetic": "private-evidence"}})
        async with sessions() as db:
            db.add(PipelineEventOutbox(**defaults))
            await db.commit()
        return identifier

    async def read(identifier):
        async with sessions() as db:
            return await db.get(PipelineEventOutbox, identifier)

    state.create, state.read, state.collection = create, read, collection
    yield state
    await engine.dispose()


@pytest.mark.asyncio
async def test_outage_then_recovery_retains_shared_receipt_and_one_logical_event(world):
    identifier = await world.create()
    world.collection.failure = NetworkTimeout("synthetic private connection failure")
    result = await delivery.relay_pipeline_events()
    assert result["retry"] == 1
    failed = await world.read(identifier)
    assert failed.status == "pending" and failed.attempts == 1
    assert failed.last_error_code == "mongo_timeout"
    async with world.sessions() as independent_api:
        snapshot = await health.read_event_delivery_snapshot(independent_api, project_ids={world.project}, now=world.now)
    assert snapshot["backlog_count"] == 1 and snapshot["write_failure_count"] == 1
    world.collection.failure = None
    world.now += timedelta(seconds=10)
    assert (await delivery.relay_pipeline_events())["delivered"] == 1
    assert (await world.read(identifier)).status == "delivered"
    assert len(world.collection.documents) == 1
    assert (await delivery.relay_pipeline_events())["delivered"] == 0
    async with world.sessions() as another_api:
        assert (await health.read_event_delivery_snapshot(another_api))["status"] == "healthy"


@pytest.mark.asyncio
@pytest.mark.parametrize("configured,expected", [(WriteConcern(w="majority", j=False, wtimeout=7000), "majority"), (WriteConcern(w=0), 1)])
async def test_materialization_preserves_stronger_ack_and_uses_primary_duplicate_reads(world, configured, expected):
    world.collection.write_concern = configured
    await world.create()
    assert (await delivery.relay_pipeline_events())["delivered"] == 1
    applied = world.collection.options
    assert applied["write_concern"].document == {"w": expected, "j": True, "wtimeout": 2000}
    assert applied["read_preference"] == ReadPreference.PRIMARY


@pytest.mark.asyncio
async def test_process_loss_after_mongo_acceptance_replays_same_id_without_duplicate(world):
    identifier = await world.create()
    world.collection.accept_then_cancel = True
    with pytest.raises(asyncio.CancelledError):
        await delivery.relay_pipeline_events()
    old = await world.read(identifier)
    assert old.status == "sending" and len(world.collection.documents) == 1
    world.collection.accept_then_cancel = False
    world.now += timedelta(seconds=delivery.LEASE_SECONDS + 1)
    assert (await delivery.relay_pipeline_events())["delivered"] == 1
    assert len(world.collection.documents) == 1
    assert (await world.read(identifier)).attempts == 2


@pytest.mark.asyncio
async def test_duplicate_id_with_different_content_fails_without_overwriting_evidence(world):
    identifier = await world.create()
    original = {"_id": str(identifier), "detail": {"original": "synthetic"}}
    world.collection.documents[str(identifier)] = copy.deepcopy(original)
    assert (await delivery.relay_pipeline_events())["failed"] == 1
    assert (await world.read(identifier)).last_error_code == "identity_conflict"
    assert world.collection.documents[str(identifier)] == original
    assert (await delivery.relay_pipeline_events())["failed"] == 0


@pytest.mark.asyncio
async def test_deleted_or_replaced_claim_cannot_materialize(world):
    identifier = await world.create()
    claim = await delivery._claim_one()
    async with world.sessions() as db:
        await db.execute(update(PipelineEventOutbox).where(PipelineEventOutbox.id == identifier).values(lease_token=uuid.uuid4()))
        await db.commit()
    assert await delivery._deliver_owned(*claim) == "stale"
    async with world.sessions() as db:
        await db.execute(delete(PipelineEventOutbox).where(PipelineEventOutbox.id == identifier))
        await db.commit()
    assert await delivery._deliver_owned(*claim) == "stale"
    assert world.collection.writes == 0


@pytest.mark.asyncio
async def test_busy_project_purge_defers_without_mongo_write(world, monkeypatch):
    from app.services import pipeline_event_admission
    identifier = await world.create()
    claim = await delivery._claim_one()

    async def busy(*args, **kwargs):
        return False

    monkeypatch.setattr(pipeline_event_admission, "lock_project_event_history", busy)
    assert await delivery._deliver_owned(*claim) == "deferred"
    assert (await world.read(identifier)).status == "sending"
    assert world.collection.writes == 0


@pytest.mark.asyncio
async def test_attempt_ceiling_and_explicit_requeue_are_bounded_and_caller_committed(world):
    identifier = await world.create(attempts=delivery.MAX_ATTEMPTS)
    assert (await delivery.relay_pipeline_events())["failed"] == 1
    assert world.collection.writes == 0
    async with world.sessions() as db:
        assert await delivery.requeue_failed_event(db, identifier, project_id=world.other) is None
        result = await delivery.requeue_failed_event(db, identifier, project_id=world.project)
        assert result["previous_attempts"] == delivery.MAX_ATTEMPTS
        await db.rollback()
    assert (await world.read(identifier)).status == "failed"
    async with world.sessions() as db:
        await delivery.requeue_failed_event(db, identifier, project_id=world.project)
        await db.commit()
    assert (await delivery.relay_pipeline_events())["delivered"] == 1
    assert (await world.read(identifier)).attempts == 1


@pytest.mark.asyncio
async def test_prune_only_old_delivered_receipts_and_preserve_unresolved_evidence(world):
    old = await world.create(status="delivered", delivered_at=world.now - timedelta(hours=2))
    fresh = await world.create(status="delivered", delivered_at=world.now)
    failed = await world.create(status="failed")
    pending = await world.create()
    assert await delivery.prune_delivered_events() == 1
    assert await world.read(old) is None
    for identifier in (fresh, failed, pending):
        assert await world.read(identifier) is not None


@pytest.mark.asyncio
async def test_pressure_prune_restores_capacity_without_removing_unresolved_evidence(world, monkeypatch):
    from app.services import pipeline_event_admission
    monkeypatch.setattr(pipeline_event_admission, "PIPELINE_EVENT_CAPACITY", 3)
    delivered = await world.create(status="delivered", delivered_at=world.now)
    failed = await world.create(status="failed")
    pending = await world.create()
    assert await delivery.prune_delivered_events() == 1
    assert await world.read(delivered) is None
    assert await world.read(failed) is not None and await world.read(pending) is not None


@pytest.mark.asyncio
async def test_same_logical_id_after_receipt_expiry_preserves_original_mongo_timestamp(world):
    identifier = await world.create()
    receipt = await world.read(identifier)
    await delivery.relay_pipeline_events()
    original = copy.deepcopy(world.collection.documents[str(identifier)])
    world.now += timedelta(hours=2)
    assert await delivery.prune_delivered_events() == 1
    payload = copy.deepcopy(receipt.payload)
    payload["timestamp"] = world.now.isoformat()
    await world.create(id=identifier, pipeline_run_id=receipt.pipeline_run_id, payload=payload)
    assert (await delivery.relay_pipeline_events())["delivered"] == 1
    assert world.collection.documents[str(identifier)] == original


@pytest.mark.asyncio
async def test_health_is_shared_scoped_and_never_returns_event_detail(world):
    await world.create(status="failed", last_error_code="mongo_timeout", attempts=8)
    other_id = await world.create(project_id=world.other, status="failed", last_error_code="mongo_unavailable")
    async with world.sessions() as db:
        snapshot = await health.read_event_log_health(db, project_ids={world.project})
        empty = await health.read_event_log_health(db, project_ids=set())
    assert snapshot["dead_letter_count"] == 1 and snapshot["backlog_count"] == 1
    assert str(other_id) not in str(snapshot) and "private-evidence" not in str(snapshot)
    assert empty["status"] == "healthy" and empty["outbox_total_count"] == 0
    await health.refresh_pipeline_event_operational_metrics()
    assert metrics.pipeline_event_outbox_entries.labels(status="failed")._value.get() == 2
    assert metrics.pipeline_event_outbox_capacity._value.get() == 10_000


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["sending", "delivered"])
async def test_bad_lease_or_receipt_state_cannot_publish_fresh_healthy_zero(world, state):
    identifier = await world.create(status=state, lease_expires_at=None, delivered_at=None)
    with pytest.raises(ValueError, match="lifecycle"):
        await health.refresh_pipeline_event_operational_metrics()
    assert await delivery.prune_delivered_events() == 0
    assert await world.read(identifier) is not None


@pytest.mark.asyncio
async def test_mounted_replay_requires_role_project_scope_and_records_safe_activity(world, monkeypatch):
    from app.core import deps
    from app.db.postgres import get_db
    from app.routers import agents

    identifier = await world.create(status="failed", attempts=8, last_error_code="mongo_timeout")
    foreign = await world.create(project_id=world.other, status="failed")
    actor = SimpleNamespace(id=uuid.uuid4(), role=UserRole.QA_LEAD, username="synthetic-lead", is_active=True)
    app = FastAPI()
    app.include_router(agents.router)
    app.dependency_overrides[deps.get_current_active_user] = lambda: actor

    async def session():
        async with world.sessions() as db:
            yield db

    async def scope(db, user, requested):
        if requested and uuid.UUID(requested) != world.project:
            raise HTTPException(403, "No project access")
        return (uuid.UUID(requested) if requested else None), {world.project}

    app.dependency_overrides[get_db] = session
    monkeypatch.setattr(agents, "resolve_project_scope", scope)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post(f"/api/v1/agents/event-log/{foreign}/replay", params={"project_id": str(world.other)})).status_code == 403
        assert (await client.post(f"/api/v1/agents/event-log/{foreign}/replay", params={"project_id": str(world.project)})).status_code == 404
        actor.role = UserRole.VIEWER
        assert (await client.post(f"/api/v1/agents/event-log/{identifier}/replay", params={"project_id": str(world.project)})).status_code == 403
        actor.role = UserRole.QA_LEAD
        response = await client.post(f"/api/v1/agents/event-log/{identifier}/replay", params={"project_id": str(world.project)})
        assert response.status_code == 202 and response.json()["status"] == "pending"
        assert "private-evidence" not in response.text
        assert (await client.post(f"/api/v1/agents/event-log/{identifier}/replay", params={"project_id": str(world.project)})).status_code == 409
        observed = await client.get("/api/v1/agents/event-log/health")
        assert observed.status_code == 200 and observed.json()["backlog_count"] == 1
        assert str(foreign) not in observed.text
    async with world.sessions() as db:
        events = (await db.scalars(select(ProjectActivityEvent))).all()
    assert len(events) == 1 and events[0].event_type == "analysis.event_delivery_requeued"
    assert (await world.read(foreign)).status == "failed"
