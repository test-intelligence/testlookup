"""Routing, health and metrics observe the same logical broker queues."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from tests.celery_queue_fakes import PipelineRedis
from kombu.transport.redis import Channel

from app import bootstrap
from app.core.config import settings
from app.core.metrics import celery_queue_length
from app.services.celery_queue_metrics import read_celery_queue_depths
from app.worker.celery_app import _build_task_queues, celery_app
from app.worker.queue_inventory import BASE_TASK_QUEUES, all_task_queue_names, redis_queue_keys

pytestmark = pytest.mark.regression


@pytest.mark.parametrize("shards", [0, 1, 8, 16])
def test_declared_routed_and_observed_inventory_agrees(monkeypatch, shards):
    monkeypatch.setattr(settings, "LIVE_INGEST_SHARD_COUNT", shards)
    expected = (*BASE_TASK_QUEUES, *(f"ingestion.shard.{index}" for index in range(shards)))
    assert all_task_queue_names() == expected
    assert len(set(expected)) == len(expected)
    declared = _build_task_queues()
    assert tuple(queue.name for queue in declared) == expected
    assert all(queue.routing_key == queue.name for queue in declared)
    assert all(queue.queue_arguments == {"x-max-priority": 10} for queue in declared)
    assert {route["queue"] for route in celery_app.conf.task_routes.values()} <= set(expected)
    assert celery_app.conf.task_routes["app.worker.tasks.run_agent_child_investigation"]["queue"] == "agent_children"
    assert celery_app.conf.task_default_queue == "default"


@pytest.mark.parametrize("options", [{}, {"priority_steps": [0, 2, 5, 9], "sep": ":", "global_keyprefix": "test:"}])
def test_physical_keys_match_the_installed_kombu_transport(monkeypatch, options):
    monkeypatch.setattr(celery_app.conf, "broker_transport_options", options)
    channel = object.__new__(Channel)
    channel.priority_steps = options.get("priority_steps", Channel.priority_steps)
    channel.sep = options.get("sep", Channel.sep)
    prefix = options.get("global_keyprefix", "")
    expected = tuple(prefix + channel._q_for_pri("agent_children", priority) for priority in channel.priority_steps)
    assert redis_queue_keys("agent_children") == expected


class _Broker(PipelineRedis):
    def __init__(self, values=None):
        self.values = values or {}
        self.seen = []

    async def llen(self, key):
        self.seen.append(key)
        value = self.values.get(key, 0)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.mark.asyncio
async def test_child_work_in_priority_bins_is_visible_and_empty_recovery_resets_it(monkeypatch):
    from app.db import redis_client

    monkeypatch.setattr(settings, "LIVE_INGEST_SHARD_COUNT", 0)
    keys = redis_queue_keys("agent_children")
    broker = _Broker({keys[1]: 4, keys[-1]: 3})
    monkeypatch.setattr(redis_client, "get_broker_redis", lambda: broker)
    # Reading the application Redis instead would incorrectly report no work.
    monkeypatch.setattr(redis_client, "get_redis", lambda: pytest.fail("must inspect actual broker"))
    depths = await read_celery_queue_depths()
    assert depths["agent_children"] == 7 and depths["default"] == 0
    assert set(depths) == set(all_task_queue_names())
    assert set(keys) <= set(broker.seen)
    assert await bootstrap._refresh_celery_queue_depths()
    assert celery_queue_length.labels(queue_name="agent_children")._value.get() == 7
    broker.values.clear()
    assert await bootstrap._refresh_celery_queue_depths()
    assert celery_queue_length.labels(queue_name="agent_children")._value.get() == 0


@pytest.mark.asyncio
async def test_a_failed_priority_bin_is_unknown_without_discarding_other_queues(monkeypatch):
    from app.db import redis_client

    broker = _Broker({redis_queue_keys("agent_children")[-1]: ConnectionError("fake unavailable"), "default": 2})
    monkeypatch.setattr(redis_client, "get_broker_redis", lambda: broker)
    depths = await read_celery_queue_depths()
    assert depths["agent_children"] is None and depths["default"] == 2
    assert not await bootstrap._refresh_celery_queue_depths()


@pytest.mark.asyncio
async def test_health_uses_the_same_child_and_shard_priority_accounting(monkeypatch):
    from app.db import postgres, redis_client
    from app.routers import health
    from app.services import ingestion_backpressure, ingestion_dlq, run_downstream_outbox

    monkeypatch.setattr(settings, "LIVE_INGEST_SHARD_COUNT", 2)

    class ApplicationRedis:
        async def get(self, *_):
            return 0

        def scan_iter(self, **_):
            async def empty():
                if False:
                    yield None
            return empty()

    broker = _Broker({redis_queue_keys("agent_children")[1]: 5, redis_queue_keys("ingestion.shard.1")[-1]: 2})
    monkeypatch.setattr(redis_client, "get_redis", lambda: ApplicationRedis())
    monkeypatch.setattr(redis_client, "get_broker_redis", lambda: broker)
    monkeypatch.setattr(ingestion_backpressure, "get_redis_memory_snapshot", AsyncMock(return_value=MagicMock(used_bytes=10, max_bytes=1000, used_pct=1.0)))
    monkeypatch.setattr(ingestion_dlq, "get_dlq_count", AsyncMock(return_value=0))
    monkeypatch.setattr(ingestion_dlq, "get_stream_dlq_count", AsyncMock(return_value=0))
    monkeypatch.setattr(run_downstream_outbox, "pending_dispatch_count", AsyncMock(return_value=0))
    session = AsyncMock()
    monkeypatch.setattr(postgres, "AsyncSessionLocal", lambda: session)
    payload = await health.health_ingestion()
    assert payload["status"] == "ok" and payload["redis_reachable"] is True
    assert payload["queues"] == await read_celery_queue_depths()
    assert payload["queues"]["agent_children"] == 5
    assert payload["queues"]["ingestion.shard.1"] == 2


def test_broker_reuses_application_pool_only_for_the_same_url(monkeypatch):
    from app.db import redis_client

    monkeypatch.setattr(settings, "CELERY_BROKER_URL", settings.REDIS_URL)
    shared = object()
    monkeypatch.setattr(redis_client, "get_redis", lambda: shared)
    assert redis_client.get_broker_redis() is shared


@pytest.mark.asyncio
async def test_distinct_broker_database_uses_bounded_pool_and_closes(monkeypatch):
    from app.core.config import REDIS_SOCKET_TIMEOUT_SECONDS
    from app.db import redis_client

    for name in ("_pool", "_client", "_broker_pool", "_broker_client"):
        monkeypatch.setattr(redis_client, name, None)
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:6399/0")
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "redis://127.0.0.1:6399/2")
    broker = redis_client.get_broker_redis()
    options = broker.connection_pool.connection_kwargs
    assert options["db"] == 2
    assert options["socket_timeout"] == options["socket_connect_timeout"] == REDIS_SOCKET_TIMEOUT_SECONDS
    assert options["retry"].get_retries() == 0
    assert redis_client.get_broker_redis() is broker
    await redis_client.close_redis()
    assert redis_client._broker_client is None and redis_client._broker_pool is None
