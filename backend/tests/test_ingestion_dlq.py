"""Unit tests for the Phase 4.3 ingestion dead-letter queue.

The DLQ is a Redis-backed capped list of structured failure records
written when ``persist_live_session`` exhausts its retry budget.
Pins:

  1. ``record_persist_failure`` writes a JSON-encoded payload to the
     correct list key with the right fields populated.
  2. The write is bounded by ``LTRIM`` to ``_DLQ_MAX_ENTRIES`` so a
     runaway failure mode can't fill Redis.
  3. ``list_recent_failures`` round-trips the JSON.
  4. ``get_dlq_count`` returns ``LLEN``.
  5. ``INGESTION_DLQ_ENABLED=False`` short-circuits the write.
  6. Redis errors during write are swallowed (best-effort).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakePipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def __getattr__(self, name: str):
        def _record(*args, **kwargs):
            self.calls.append((name, args))
            return self
        return _record

    async def execute(self):
        self.calls.append(("execute", ()))
        return []


def _fake_redis_with_pipeline(pipe: _FakePipeline):
    return SimpleNamespace(
        pipeline=MagicMock(return_value=pipe),
        llen=AsyncMock(return_value=0),
        lrange=AsyncMock(return_value=[]),
    )


@pytest.fixture
def enabled(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "INGESTION_DLQ_ENABLED", True)


@pytest.mark.asyncio
async def test_record_persist_failure_writes_lpush_ltrim_pipeline(enabled):
    from app.services.ingestion_dlq import record_persist_failure

    pipe = _FakePipeline()
    redis = _fake_redis_with_pipeline(pipe)
    with patch("app.db.redis_client.get_redis", return_value=redis):
        await record_persist_failure(
            run_id="run-1", project_id="proj-1", task_id="task-x",
            retry_count=3, error="boom",
        )

    # Must call LPUSH + LTRIM + EXPIRE atomically inside one pipeline.
    op_names = [c[0] for c in pipe.calls]
    assert "lpush" in op_names
    assert "ltrim" in op_names
    assert "expire" in op_names
    assert op_names[-1] == "execute"

    # The LPUSH value is JSON; decode it and check the shape.
    lpush_call = next(c for c in pipe.calls if c[0] == "lpush")
    args = lpush_call[1]
    key, raw_json = args
    assert "persist_live_session" in key
    payload = json.loads(raw_json)
    assert payload["run_id"] == "run-1"
    assert payload["project_id"] == "proj-1"
    assert payload["task_id"] == "task-x"
    assert payload["retry_count"] == 3
    assert payload["error"] == "boom"
    assert payload["kind"] == "persist_live_session"
    # ``failed_at`` is a valid ISO timestamp.
    assert payload["failed_at"]


@pytest.mark.asyncio
async def test_record_persist_failure_truncates_long_error(enabled):
    """Stack traces can be enormous. The DLQ payload must cap the
    error string so one runaway trace doesn't blow the Redis value
    size budget."""
    from app.services.ingestion_dlq import record_persist_failure

    pipe = _FakePipeline()
    redis = _fake_redis_with_pipeline(pipe)
    long_error = "x" * 10_000
    with patch("app.db.redis_client.get_redis", return_value=redis):
        await record_persist_failure(
            run_id="r", project_id="p", task_id="t", retry_count=3, error=long_error,
        )

    lpush_call = next(c for c in pipe.calls if c[0] == "lpush")
    raw_json = lpush_call[1][1]
    payload = json.loads(raw_json)
    assert len(payload["error"]) <= 2000


@pytest.mark.asyncio
async def test_record_persist_failure_disabled_skips_redis(monkeypatch):
    from app.core import config
    from app.services.ingestion_dlq import record_persist_failure
    monkeypatch.setattr(config.settings, "INGESTION_DLQ_ENABLED", False)

    pipe = _FakePipeline()
    redis = _fake_redis_with_pipeline(pipe)
    with patch("app.db.redis_client.get_redis", return_value=redis):
        await record_persist_failure(
            run_id="r", project_id="p", task_id="t", retry_count=3, error="boom",
        )

    redis.pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_record_persist_failure_swallows_redis_errors(enabled):
    """Best-effort write: a Redis hiccup logs but doesn't raise.
    The DLQ is observability — losing one DLQ entry is far less bad
    than the original task-failure stack being lost in a retry loop."""
    from app.services.ingestion_dlq import record_persist_failure

    redis = SimpleNamespace(pipeline=MagicMock(side_effect=ConnectionError("down")))
    with patch("app.db.redis_client.get_redis", return_value=redis):
        # Must NOT raise.
        await record_persist_failure(
            run_id="r", project_id="p", task_id="t", retry_count=3, error="boom",
        )


@pytest.mark.asyncio
async def test_list_recent_failures_round_trips_json(enabled):
    from app.services.ingestion_dlq import list_recent_failures

    payload = {"kind": "persist_live_session", "run_id": "r1"}
    redis = SimpleNamespace(
        lrange=AsyncMock(return_value=[json.dumps(payload)]),
    )
    with patch("app.db.redis_client.get_redis", return_value=redis):
        out = await list_recent_failures()

    assert out == [payload]


@pytest.mark.asyncio
async def test_list_recent_failures_skips_malformed_entries(enabled):
    """A corrupted entry doesn't break the listing — the rest still
    return cleanly."""
    from app.services.ingestion_dlq import list_recent_failures

    good = json.dumps({"run_id": "r1"})
    bad = "not-json"
    redis = SimpleNamespace(
        lrange=AsyncMock(return_value=[good, bad, good]),
    )
    with patch("app.db.redis_client.get_redis", return_value=redis):
        out = await list_recent_failures()

    assert len(out) == 2  # the bad one dropped


@pytest.mark.asyncio
async def test_get_dlq_count_returns_llen(enabled):
    from app.services.ingestion_dlq import get_dlq_count

    redis = SimpleNamespace(llen=AsyncMock(return_value=42))
    with patch("app.db.redis_client.get_redis", return_value=redis):
        assert await get_dlq_count() == 42


@pytest.mark.asyncio
async def test_get_dlq_count_fails_to_zero_on_redis_error(enabled):
    from app.services.ingestion_dlq import get_dlq_count

    redis = SimpleNamespace(llen=AsyncMock(side_effect=ConnectionError("down")))
    with patch("app.db.redis_client.get_redis", return_value=redis):
        assert await get_dlq_count() == 0
