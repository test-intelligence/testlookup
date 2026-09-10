"""Results streamed through ``POST /ws/events`` must be counted -- exactly once.

Re-audit H6. The live-event consumer's result handler only READS the run's
live-state counters and broadcasts them; its comment says the increment
happens at ingest, "in stream_service.ingest_event_batch()". That is true for
the SDK batch path, whose admission Lua recounts its ledger sets into the same
Redis hash. It was never true for ``/ws/events``: nothing on that route touched
the counters, and ``RedisLiveRunState.record_test_event`` had no caller
anywhere. So a run streamed through that endpoint showed zero results on the
live dashboard, never tripped the failure-rate early warning however many
tests failed, and broadcast a final ``live_run_complete`` whose totals were all
zero.

Since re-audit N14 the route has two branches, and each is counted in exactly
one place:

* A **project-scoped key** hands the event to the SDK stream's own ingest path
  (``services/ws_event_ingest.py``), whose admission script counts it. The
  route must not count it as well. That path is proven against real Redis and
  Postgres in ``tests/integration/test_ws_events_persist_postgres_redis.py``.
* The **legacy shared secret** (off by default) still publishes straight to
  the stream, so the route counts it at the producer -- the H6 fix, pinned by
  the counting tests below.

Counting in the consumer would double-count every SDK run: batch events are
published into the same stream. Two AST checks pin that placement.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.routers.live import ingest_live_event
from app.streams.live_run_state import _WARN_THRESHOLD, RedisLiveRunState

PROJECT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
RUN = "nightly-regression-42"
SECRET = "the-real-secret"


class _Pipeline:
    def __init__(self, redis: "_Redis") -> None:
        self.redis = redis
        self.ops: list = []

    def hincrby(self, key, field, amount):
        self.ops.append(("hincrby", key, field, amount))
        return self

    def hset(self, key, mapping=None, **_kw):
        self.ops.append(("hset", key, mapping or {}))
        return self

    def expire(self, key, seconds):
        self.ops.append(("expire", key, seconds))
        return self

    async def execute(self):
        for op in self.ops:
            if op[0] == "hincrby":
                _, key, field, amount = op
                row = self.redis.hashes.setdefault(key, {})
                row[field] = str(int(row.get(field, 0)) + amount)
            elif op[0] == "hset":
                _, key, mapping = op
                row = self.redis.hashes.setdefault(key, {})
                row.update({k: str(v) for k, v in mapping.items()})
        self.ops.clear()
        return []


class _Redis:
    """The subset RedisLiveRunState and the run binding actually use."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.values: dict[str, str] = {}
        self.sets: dict[str, set] = {}

    async def exists(self, key):
        return int(key in self.hashes or key in self.values)

    async def expire(self, _key, _seconds):
        return True

    async def sadd(self, key, *values):
        self.sets.setdefault(key, set()).update(values)
        return len(values)

    async def hset(self, key, mapping=None, **_kw):
        self.hashes.setdefault(key, {}).update(
            {k: str(v) for k, v in (mapping or {}).items()}
        )
        return len(mapping or {})

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def pipeline(self, transaction=True):
        return _Pipeline(self)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return None
        self.values[key] = str(value)
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        return int(self.values.pop(key, None) is not None)


@pytest.fixture
def live(monkeypatch):
    """The route with Redis faked and the transport stubbed; the checks are real."""
    redis = _Redis()
    for target in ("app.db.redis_client.get_redis", "app.streams.live_run_state.get_redis"):
        monkeypatch.setattr(target, lambda: redis, raising=False)

    published: list[tuple[str, dict]] = []

    async def _publish(run_id, event):
        published.append((run_id, event))
        return "1-0"

    monkeypatch.setattr("app.streams.producer.publish_live_event", _publish)

    class _Coll:
        async def insert_one(self, _doc):
            return None

    monkeypatch.setattr(
        "app.db.mongo.get_mongo_db", lambda: {"live_execution_events": _Coll()}
    )
    # The M4/M3 admission gates are exercised in their own suite; here they
    # would only depend on how the fake Redis happens to fail open.
    async def _gate_open(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.services.ingestion_backpressure.enforce_redis_memory_backpressure",
        _gate_open,
    )
    monkeypatch.setattr(
        "app.services.ingestion_rate_limit.enforce_live_event_rate_limit",
        _gate_open,
    )
    # The counting tests drive the legacy shared-secret path: since N14 it is
    # the only one the route still counts itself.
    monkeypatch.setattr(settings, "LIVE_EVENTS_REQUIRE_PROJECT_KEY", False)
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", SECRET)
    return SimpleNamespace(redis=redis, published=published)


async def _send(event: dict):
    return await ingest_live_event(
        run_id=RUN,
        event=event,
        x_api_key=None,
        x_webhook_secret=SECRET,
        db=SimpleNamespace(),
    )


async def _start():
    await _send({
        "type": "run_start", "project_id": PROJECT, "build_number": "42", "total_tests": 5,
    })


async def _result(status: str, name: str = "t"):
    await _send({"type": "test_result", "test_name": name, "status": status})


# ── The legacy path: counted at the producer ─────────────────────────────


@pytest.mark.asyncio
async def test_results_are_counted_by_status(live):
    await _start()
    await _result("PASSED", "a")
    await _result("FAILED", "b")
    await _result("FAILED", "c")
    await _result("SKIPPED", "d")

    state = await RedisLiveRunState.get(RUN)
    assert state is not None, "run_start did not create the live state"
    assert int(state["passed"]) == 1
    assert int(state["failed"]) == 2, (
        "results streamed through /ws/events are not counted — the live "
        "dashboard shows zero and the run finalises from zeros"
    )
    assert int(state["skipped"]) == 1


@pytest.mark.asyncio
async def test_a_result_before_the_consumer_ran_is_not_dropped(live):
    """The consumer creates the state asynchronously; the producer must not wait.

    Nothing here ever runs the consumer. Before the fix a result that arrived
    ahead of the consumer's run_start handling found no state and vanished.
    """
    await _start()
    await _result("FAILED")
    state = await RedisLiveRunState.get(RUN)
    assert int(state["failed"]) == 1


@pytest.mark.asyncio
async def test_the_consumers_later_start_does_not_wipe_the_counts(live):
    """start() is idempotent — the consumer calling it afterwards is a no-op."""
    await _start()
    await _result("FAILED")
    await _result("PASSED")

    await RedisLiveRunState.start(RUN, PROJECT, "42")  # what the consumer does

    state = await RedisLiveRunState.get(RUN)
    assert int(state["failed"]) == 1
    assert int(state["passed"]) == 1


@pytest.mark.asyncio
async def test_the_early_warning_can_fire_again(live):
    """should_warn reads these counters; with none it could never trip."""
    await _start()
    for i in range(_WARN_THRESHOLD + 2):
        await _result("FAILED", f"t{i}")

    state = await RedisLiveRunState.get(RUN)
    assert RedisLiveRunState.should_warn(state), (
        "a run where every test failed never raised the failure-rate warning"
    )


@pytest.mark.asyncio
async def test_a_nonsense_total_does_not_break_ingest(live):
    """A client-supplied count is data, not something to 500 on."""
    result = await _send({
        "type": "run_start", "project_id": PROJECT, "build_number": "42", "total_tests": "lots",
    })
    assert result["accepted"] is True


@pytest.mark.asyncio
async def test_every_event_is_still_published(live):
    await _start()
    await _result("PASSED")
    assert [e["type"] for _run, e in live.published] == ["run_start", "test_result"]


# ── The project-key path: counted by the SDK admission, never here ───────


@pytest.mark.asyncio
async def test_a_project_key_event_is_handed_over_and_not_counted_here(live, monkeypatch):
    """Counting it here as well would count every result twice (re-audit N14)."""
    admitted: list[str] = []

    async def _admit(_db, *, project_id, api_key_name, run_id, event):
        admitted.append(event["type"])
        return "session-1"

    monkeypatch.setattr("app.services.ws_event_ingest.ingest_one", _admit)
    monkeypatch.setattr(
        "app.services.live_event_authz.cached_streaming_project",
        AsyncMock(return_value=PROJECT),
    )
    monkeypatch.setattr(settings, "LIVE_EVENTS_REQUIRE_PROJECT_KEY", True)

    for event in (
        {"type": "run_start", "build_number": "42"},
        {"type": "test_result", "test_name": "t", "status": "FAILED"},
    ):
        result = await ingest_live_event(
            run_id=RUN, event=event, x_api_key="qai_key", x_webhook_secret=None,
            db=SimpleNamespace(),
        )
        assert result["session_id"] == "session-1"

    assert admitted == ["run_start", "test_result"]
    assert await RedisLiveRunState.get(RUN) is None, (
        "the route counted a project-key event itself; the SDK admission counts "
        "it too, so every result would count twice"
    )
    assert live.published == [], (
        "a project-key event was also published straight to the stream"
    )


# ── Counted in exactly one place ─────────────────────────────────────────


def _calls_in(func, name: str) -> int:
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
    )


def test_the_consumer_does_not_count_results():
    """Counting there would double-count every SDK batch run.

    Batch events are published into the same stream this consumer reads, and
    the batch admission already recounts them into the state hash.
    """
    from app.streams.live_consumer import LiveEventStreamConsumer

    assert _calls_in(LiveEventStreamConsumer._on_test_result, "record_test_event") == 0


def test_the_legacy_producer_does_count_results():
    assert _calls_in(ingest_live_event, "record_test_event") == 1
    assert _calls_in(ingest_live_event, "start") >= 1
