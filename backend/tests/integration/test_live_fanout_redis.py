"""Real-Redis proof that independent API relays see the same ordered events."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import uuid

import pytest

from app.streams import live_fanout

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _relay_process(redis_url, global_stream, project_template, project_id, output, index):
    async def run():
        import redis.asyncio as redis_asyncio

        client = redis_asyncio.Redis.from_url(redis_url, decode_responses=True)
        live_fanout.LIVE_FANOUT_STREAM = global_stream
        live_fanout.LIVE_FANOUT_PROJECT_STREAM_KEY = project_template
        live_fanout.get_redis = lambda: client
        received = []

        async def deliver(delivered_project, payload):
            if delivered_project == project_id:
                received.append(payload)

        relay = live_fanout.LiveFanoutSubscriber(deliver)
        await relay.initialize()
        output.put(("ready", index))
        task = asyncio.create_task(relay.run())
        try:
            async with asyncio.timeout(10):
                while len(received) < 3:
                    await asyncio.sleep(0.01)
            output.put(("events", index, received))
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await client.aclose()

    asyncio.run(run())


async def test_two_independent_relays_receive_identical_ordered_notifications(monkeypatch):
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        pytest.skip("REDIS_URL is not configured")
    redis_asyncio = pytest.importorskip("redis.asyncio")
    client = redis_asyncio.Redis.from_url(redis_url, decode_responses=True)
    project_id = str(uuid.uuid4())
    test_prefix = f"testlookup:test:live-fanout:{project_id}"
    global_stream = f"{test_prefix}:global"
    project_stream_template = f"{test_prefix}:project:{{project_id}}"
    dedupe_template = f"{test_prefix}:dedupe:{{event_id}}"
    monkeypatch.setattr(live_fanout, "LIVE_FANOUT_STREAM", global_stream)
    monkeypatch.setattr(live_fanout, "LIVE_FANOUT_PROJECT_STREAM_KEY", project_stream_template)
    monkeypatch.setattr(live_fanout, "LIVE_FANOUT_DEDUP_KEY", dedupe_template)
    monkeypatch.setattr(live_fanout, "get_redis", lambda: client)
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    processes = [
        context.Process(
            target=_relay_process,
            args=(redis_url, global_stream, project_stream_template, project_id, output, index),
        )
        for index in ("a", "b")
    ]
    for process in processes:
        process.start()
    try:
        ready = [await asyncio.to_thread(output.get, True, 15) for _ in processes]
        assert {item[:2] for item in ready} == {("ready", "a"), ("ready", "b")}
        for number in range(3):
            await live_fanout.publish_live_notification(
                project_id,
                {"type": "probe", "number": number},
                logical_event_id=f"fanout-integration:{project_id}:{number}",
            )
        duplicate_id = await live_fanout.publish_live_notification(
            project_id,
            {"type": "probe", "number": 0},
            logical_event_id=f"fanout-integration:{project_id}:0",
        )
        reports = [await asyncio.to_thread(output.get, True, 15) for _ in processes]
        by_process = {item[1]: item[2] for item in reports if item[0] == "events"}
        received_a = by_process["a"]
        received_b = by_process["b"]
        assert received_a == received_b
        assert [event["number"] for event in received_a] == [0, 1, 2]
        assert [event["sequence_id"] for event in received_a] == sorted(
            event["sequence_id"] for event in received_a
        )
        assert duplicate_id == received_a[0]["sequence_id"]
        assert await client.xlen(global_stream) == 3
        assert await client.xlen(project_stream_template.format(project_id=project_id)) == 3
    finally:
        for process in processes:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
        keys = [global_stream, project_stream_template.format(project_id=project_id)]
        keys.extend(
            dedupe_template.format(event_id=f"fanout-integration:{project_id}:{number}")
            for number in range(3)
        )
        await client.delete(*keys)
        await client.aclose()
