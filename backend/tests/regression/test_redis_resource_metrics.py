"""Native Redis observations use the configured authority and never invent health."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import httpx
from prometheus_client import generate_latest
import pytest
import redis
import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from app.core import metrics
from app.db import redis_client
from app.services import metric_collection, redis_resource_metrics as resources

pytestmark = pytest.mark.regression


def _info(used=70, maximum=100, rss=90, excluded=10):
    return {
        "used_memory": used, "maxmemory": maximum,
        "used_memory_rss": rss, "mem_not_counted_for_evict": excluded,
    }


def _clients(monkeypatch, application, broker=None):
    monkeypatch.setattr(redis_client, "get_redis", lambda: application)
    monkeypatch.setattr(redis_client, "get_broker_redis", lambda: application if broker is None else broker)


def _value(metric, role):
    return metric.labels(store_role=role)._value.get()


@pytest.mark.asyncio
async def test_shared_client_reads_once_and_preserves_bounded_role_authority(monkeypatch):
    client = SimpleNamespace(info=AsyncMock(return_value=_info()))
    _clients(monkeypatch, client)
    assert await metric_collection.collect_bounded("redis", resources.refresh_redis_resource_metrics)
    client.info.assert_awaited_once_with(section="memory")
    for role in resources.STORE_ROLES:
        assert _value(metrics.redis_memory_used_bytes, role) == 70
        assert _value(metrics.redis_memory_max_bytes, role) == 100
        assert _value(metrics.redis_memory_counted_for_evict_bytes, role) == 60
        assert _value(metrics.redis_memory_rss_bytes, role) == 90
        assert _value(metrics.redis_memory_limit_configured, role) == 1
    samples = [sample for family in metrics.redis_memory_used_bytes.collect() for sample in family.samples]
    assert {tuple(sample.labels) for sample in samples} == {("store_role",)}
    assert {sample.labels["store_role"] for sample in samples} == set(resources.STORE_ROLES)


@pytest.mark.asyncio
async def test_unlimited_memory_is_explicit_and_excluded_buffers_do_not_go_negative(monkeypatch):
    client = SimpleNamespace(info=AsyncMock(return_value=_info(used=20, maximum=0, excluded=30)))
    _clients(monkeypatch, client)
    assert await metric_collection.collect_bounded("redis", resources.refresh_redis_resource_metrics)
    assert _value(metrics.redis_memory_max_bytes, "broker") == 0
    assert _value(metrics.redis_memory_limit_configured, "broker") == 0
    assert _value(metrics.redis_memory_counted_for_evict_bytes, "broker") == 0


@pytest.mark.parametrize("field,value", [
    ("used_memory", None), ("used_memory", -1), ("used_memory", True),
    ("maxmemory", "NaN"), ("maxmemory", float("inf")),
    ("used_memory_rss", None), ("mem_not_counted_for_evict", None),
])
def test_missing_or_invalid_info_is_unknown_not_zero(field, value):
    info = _info()
    info[field] = value
    with pytest.raises(ValueError):
        resources._memory_observation(info)


@pytest.mark.asyncio
async def test_denied_or_incomplete_role_does_not_advance_freshness_or_publish_partial_snapshot(monkeypatch):
    application = SimpleNamespace(info=AsyncMock(return_value=_info(used=70)))
    broker = SimpleNamespace(info=AsyncMock(return_value=_info(used=40)))
    _clients(monkeypatch, application, broker)
    assert await metric_collection.collect_bounded("redis", resources.refresh_redis_resource_metrics)
    last = metrics.metric_collection_last_success.labels(collector="redis")._value.get()
    application.info.return_value = _info(used=99)
    for invalid in (ResponseError("synthetic ACL denial"), {"used_memory": 99}):
        broker.info = AsyncMock(side_effect=invalid) if isinstance(invalid, Exception) else AsyncMock(return_value=invalid)
        assert not await metric_collection.collect_bounded("redis", resources.refresh_redis_resource_metrics)
        assert _value(metrics.redis_memory_used_bytes, "application") == 70
        assert _value(metrics.redis_memory_used_bytes, "broker") == 40
        assert metrics.metric_collection_success.labels(collector="redis")._value.get() == 0
        assert metrics.metric_collection_last_success.labels(collector="redis")._value.get() == last
    broker.info = AsyncMock(return_value=_info(used=30))
    assert await metric_collection.collect_bounded("redis", resources.refresh_redis_resource_metrics)
    assert _value(metrics.redis_memory_used_bytes, "application") == 99
    assert _value(metrics.redis_memory_used_bytes, "broker") == 30


@pytest.mark.asyncio
async def test_stalled_info_is_bounded_without_zeroing_previous_observation(monkeypatch):
    monkeypatch.setattr(metric_collection, "COLLECTION_TIMEOUT_SECONDS", 0.02)
    client = SimpleNamespace(info=AsyncMock(return_value=_info()))
    _clients(monkeypatch, client)
    assert await metric_collection.collect_bounded("redis", resources.refresh_redis_resource_metrics)
    last = metrics.metric_collection_last_success.labels(collector="redis")._value.get()
    async def stalled(**kwargs):
        await asyncio.Event().wait()

    client.info = stalled
    start = asyncio.get_running_loop().time()
    assert not await metric_collection.collect_bounded("redis", resources.refresh_redis_resource_metrics)
    assert asyncio.get_running_loop().time() - start < 0.3
    assert _value(metrics.redis_memory_used_bytes, "broker") == 70
    assert metrics.metric_collection_last_success.labels(collector="redis")._value.get() == last
    await asyncio.sleep(0)


_RESP_HANDSHAKE_COMMANDS = {
    (b"HELLO", b"3"),
    (b"CLIENT", b"SETINFO", b"LIB-NAME", b"redis-py"),
    (b"CLIENT", b"SETINFO", b"LIB-VER", redis.__version__.encode()),
    # redis-py 8 negotiates read-only maintenance notices for this loopback IP.
    (b"CLIENT", b"MAINT_NOTIFICATIONS", b"ON", b"moving-endpoint-type", b"internal-ip"),
}


@asynccontextmanager
async def _resp_memory_server(info):
    """Synthetic local wire endpoint: real redis-py INFO request and parsing."""
    commands = []
    handlers = set()

    async def handle(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        try:
            while True:
                array = await reader.readline()
                if not array:
                    return
                assert array.startswith(b"*")
                parts = []
                for _ in range(int(array[1:])):
                    length = await reader.readline()
                    assert length.startswith(b"$")
                    parts.append(await reader.readexactly(int(length[1:])))
                    assert await reader.readexactly(2) == b"\r\n"
                commands.append(tuple(parts))
                if tuple(parts) == (b"HELLO", b"3"):
                    # redis-py 8 defaults to RESP3; model the negotiation rather
                    # than treating HELLO as a successful arbitrary command.
                    writer.write(
                        b"%7\r\n+server\r\n+redis\r\n+version\r\n+8.1.0\r\n"
                        b"+proto\r\n:3\r\n+id\r\n:1\r\n+mode\r\n+standalone\r\n"
                        b"+role\r\n+master\r\n+modules\r\n*0\r\n"
                    )
                elif tuple(parts) == (b"INFO", b"memory"):
                    payload = ("# Memory\r\n" + "".join(f"{key}:{value}\r\n" for key, value in info.items())).encode()
                    writer.write(b"$" + str(len(payload)).encode() + b"\r\n" + payload + b"\r\n")
                elif tuple(parts) in _RESP_HANDSHAKE_COMMANDS:
                    writer.write(b"+OK\r\n")  # redis-py's connection metadata handshake
                else:
                    writer.write(b"-ERR unsupported fixture command\r\n")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            handlers.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = aioredis.Redis(host="127.0.0.1", port=port, decode_responses=True,
                           socket_timeout=0.5, socket_connect_timeout=0.5)
    try:
        yield client, commands
    finally:
        await client.aclose()
        server.close()
        await server.wait_closed()
        if handlers:
            await asyncio.wait_for(asyncio.gather(*handlers, return_exceptions=True), timeout=0.5)


@pytest.mark.asyncio
async def test_real_redis_client_reads_distinct_application_and_broker_wire_endpoints(monkeypatch):
    async with _resp_memory_server(_info(used=80, maximum=100)) as (application, application_commands):
        async with _resp_memory_server(_info(used=3, maximum=10)) as (broker, broker_commands):
            _clients(monkeypatch, application, broker)
            assert await metric_collection.collect_bounded("redis", resources.refresh_redis_resource_metrics)
            assert _value(metrics.redis_memory_used_bytes, "application") == 80
            assert _value(metrics.redis_memory_max_bytes, "application") == 100
            assert _value(metrics.redis_memory_used_bytes, "broker") == 3
            assert _value(metrics.redis_memory_max_bytes, "broker") == 10
            for commands in (application_commands, broker_commands):
                assert commands.count((b"INFO", b"memory")) == 1
                assert set(commands) <= _RESP_HANDSHAKE_COMMANDS | {(b"INFO", b"memory")}


@pytest.mark.asyncio
async def test_actual_metrics_route_refreshes_and_exports_redis_observations(monkeypatch):
    from app import bootstrap
    from app.services import agent_operational_metrics, downstream_operational_metrics

    _clients(monkeypatch, SimpleNamespace(info=AsyncMock(return_value=_info())))
    monkeypatch.setattr(bootstrap, "_refresh_celery_queue_depths", AsyncMock(return_value=True))
    monkeypatch.setattr(agent_operational_metrics, "refresh_agent_operational_metrics", AsyncMock(return_value=True))
    monkeypatch.setattr(downstream_operational_metrics, "refresh_downstream_operational_metrics", AsyncMock(return_value=True))
    monkeypatch.setattr(bootstrap.settings, "METRICS_TOKEN", "")
    if hasattr(bootstrap.settings, "METRICS_TOKEN_FILE"):
        monkeypatch.setattr(bootstrap.settings, "METRICS_TOKEN_FILE", "")
    app = FastAPI()

    @app.get("/metrics")
    async def metrics_route(request: Request):
        return PlainTextResponse(generate_latest().decode())

    bootstrap._install_celery_queue_depth_collector(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert 'redis_memory_used_bytes{store_role="broker"} 70.0' in response.text
    assert 'redis_memory_max_bytes{store_role="application"} 100.0' in response.text
    assert 'testlookup_metric_collection_success{collector="redis"} 1.0' in response.text
