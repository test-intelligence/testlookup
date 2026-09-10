"""Single-event live ingest must be rate-limited and backpressured.

Re-audit findings M4 and M3 (and N9, raised by the batch 1 review).
``/api/v1/stream/*`` has had two admission gates since the scalable-ingestion
work: a per-project rate limit and a Redis-memory backpressure check.
``POST /ws/events`` had neither. Every step on that route writes to Redis --
the run binding, the credential cache, the run's counters, the stream itself --
so one runaway producer could push Redis toward OOM for every project.

It cannot simply reuse the batch limiter. That one counts BATCHES (200 a minute
by default, ~100 events each); charging a batch token per event would throttle
an ordinary run to 200 results a minute. Single events get their own bucket and
budget, through the same shared mechanics.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services import ingestion_rate_limit

pytestmark = pytest.mark.regression

PROJECT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _fake_redis(incrby_return: int):
    return SimpleNamespace(
        incrby=AsyncMock(return_value=incrby_return),
        expire=AsyncMock(return_value=True),
        incr=AsyncMock(return_value=1),
    )


# ── The per-event limiter ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_single_event_uses_its_own_bucket(monkeypatch):
    """Not the batch bucket: the two budgets measure different things."""
    redis = _fake_redis(incrby_return=1)
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)

    await ingestion_rate_limit.enforce_live_event_rate_limit(PROJECT)

    key = redis.incrby.await_args.args[0]
    assert key.startswith("testlookup:rate:ingest_event:"), key
    assert PROJECT in key
    assert not key.startswith("testlookup:rate:ingest:"), (
        "single events are charged against the BATCH bucket, which would "
        "throttle an ordinary run to 200 results a minute"
    )


@pytest.mark.asyncio
async def test_a_run_well_past_the_batch_budget_is_not_throttled(monkeypatch):
    """300 events is past 200 batches but nowhere near the event budget."""
    monkeypatch.setattr(settings, "INGEST_RATE_LIMIT_PER_MINUTE", 200)
    monkeypatch.setattr(settings, "INGEST_EVENT_RATE_LIMIT_PER_MINUTE", 20000)
    redis = _fake_redis(incrby_return=300)
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)

    await ingestion_rate_limit.enforce_live_event_rate_limit(PROJECT)  # must not raise


@pytest.mark.asyncio
async def test_over_the_event_budget_is_a_429_that_says_events(monkeypatch):
    monkeypatch.setattr(settings, "INGEST_EVENT_RATE_LIMIT_PER_MINUTE", 100)
    redis = _fake_redis(incrby_return=101)
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)

    with pytest.raises(HTTPException) as exc:
        await ingestion_rate_limit.enforce_live_event_rate_limit(PROJECT)

    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers
    assert "events" in exc.value.detail and "batches" not in exc.value.detail


@pytest.mark.asyncio
async def test_zero_disables_the_event_budget(monkeypatch):
    monkeypatch.setattr(settings, "INGEST_EVENT_RATE_LIMIT_PER_MINUTE", 0)
    called = []
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: called.append(1))
    await ingestion_rate_limit.enforce_live_event_rate_limit(PROJECT)
    assert called == []


@pytest.mark.asyncio
async def test_a_redis_failure_fails_open(monkeypatch):
    """Same stance as the batch limiter: never block ingest on the gate's own outage."""
    redis = SimpleNamespace(
        incrby=AsyncMock(side_effect=ConnectionError("redis down")),
        expire=AsyncMock(),
        incr=AsyncMock(),
    )
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
    await ingestion_rate_limit.enforce_live_event_rate_limit(PROJECT)  # must not raise


# ── /ws/events uses both gates, in the right order ───────────────────────


@pytest.fixture
def live(monkeypatch):
    """Everything on the route stubbed except the two gates under test."""
    from app.routers import live as live_router

    calls: list[tuple[str, str | None]] = []

    async def _backpressure():
        calls.append(("backpressure", None))

    async def _event_limit(project_id, *, cost=1):
        calls.append(("rate_limit", project_id))

    async def _noop(*_a, **_k):
        return None

    async def _publish(run_id, event):
        calls.append(("publish", run_id))
        return "1-0"

    monkeypatch.setattr(
        "app.services.ingestion_backpressure.enforce_redis_memory_backpressure",
        _backpressure,
    )
    monkeypatch.setattr(
        "app.services.ingestion_rate_limit.enforce_live_event_rate_limit",
        _event_limit,
    )
    monkeypatch.setattr("app.streams.producer.publish_live_event", _publish)
    monkeypatch.setattr("app.streams.live_run_state.RedisLiveRunState.start", _noop)
    monkeypatch.setattr(
        "app.streams.live_run_state.RedisLiveRunState.record_test_event", _noop
    )
    monkeypatch.setattr(
        "app.services.live_event_authz.cached_streaming_project",
        AsyncMock(return_value=PROJECT),
    )
    monkeypatch.setattr(
        "app.services.live_event_authz.resolve_run_project",
        AsyncMock(return_value=PROJECT),
    )

    class _Coll:
        async def insert_one(self, _doc):
            return None

    monkeypatch.setattr(
        "app.db.mongo.get_mongo_db", lambda: {"live_execution_events": _Coll()}
    )
    monkeypatch.setattr(settings, "LIVE_EVENTS_REQUIRE_PROJECT_KEY", True)
    return SimpleNamespace(route=live_router, calls=calls)


async def _send(live, **overrides):
    params = {
        "run_id": "run-gated",
        "event": {"type": "test_result", "test_name": "t", "status": "PASSED"},
        "x_api_key": "qai_key",
        "x_webhook_secret": None,
        "db": SimpleNamespace(),
    }
    params.update(overrides)
    return await live.route.ingest_live_event(**params)


@pytest.mark.asyncio
async def test_the_route_charges_the_callers_own_project(live):
    await _send(live)
    assert ("rate_limit", PROJECT) in live.calls


@pytest.mark.asyncio
async def test_backpressure_runs_before_anything_is_published(live):
    await _send(live)
    names = [name for name, _ in live.calls]
    assert names.index("backpressure") < names.index("publish")
    assert names.index("rate_limit") < names.index("publish")


@pytest.mark.asyncio
async def test_an_unauthenticated_caller_spends_nobody_s_quota(live):
    """Charging before auth would let anyone exhaust another tenant's budget."""
    with pytest.raises(HTTPException) as exc:
        await _send(live, x_api_key=None, x_webhook_secret=None)
    assert exc.value.status_code == 401
    assert not any(name == "rate_limit" for name, _ in live.calls)


@pytest.mark.asyncio
async def test_a_429_stops_the_event_before_it_is_published(live, monkeypatch):
    async def _over(project_id, *, cost=1):
        raise HTTPException(429, detail="over", headers={"Retry-After": "7"})

    monkeypatch.setattr(
        "app.services.ingestion_rate_limit.enforce_live_event_rate_limit", _over
    )
    with pytest.raises(HTTPException) as exc:
        await _send(live)
    assert exc.value.status_code == 429
    assert not any(name == "publish" for name, _ in live.calls)
