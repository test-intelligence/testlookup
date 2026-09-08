"""Regression coverage for stream-first live protocol integration seams."""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


pytestmark = pytest.mark.regression


class _LengthsRedis:
    def __init__(self, *, stream: int = 0, legacy: int = 0) -> None:
        self.stream = stream
        self.legacy = legacy

    async def xlen(self, _key):
        return self.stream

    async def llen(self, _key):
        return self.legacy


@pytest.mark.asyncio
async def test_close_drain_repeats_until_every_stream_chunk_is_committed(monkeypatch):
    from app.db import redis_client
    from app.services import live_persistence_scrub, live_session_drainer
    from app.worker import tasks

    redis = _LengthsRedis()
    drain = AsyncMock(side_effect=[{"drained": 2}, {"drained": 1}, {"drained": 0}])
    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)
    monkeypatch.setattr(live_session_drainer, "drain_run_buffer", drain)
    monkeypatch.setattr(
        live_persistence_scrub,
        "migrate_redis_list_to_evidence_stream",
        AsyncMock(),
    )

    total = await tasks._drain_live_evidence_before_finalize(
        run_id="run-1",
        project_id=str(uuid.uuid4()),
        build_number="42",
        suite_name="smoke",
    )

    assert total == 3
    assert drain.await_count == 3


@pytest.mark.asyncio
async def test_close_drain_refuses_to_finalize_uncommitted_stream_entries(monkeypatch):
    from app.db import redis_client
    from app.services import live_session_drainer
    from app.worker import tasks

    redis = _LengthsRedis(stream=2)
    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)
    monkeypatch.setattr(
        live_session_drainer,
        "drain_run_buffer",
        AsyncMock(return_value={"drained": 0}),
    )

    with pytest.raises(RuntimeError, match="2 uncommitted"):
        await tasks._drain_live_evidence_before_finalize(
            run_id="run-2",
            project_id=str(uuid.uuid4()),
            build_number="42",
            suite_name=None,
        )


@pytest.mark.asyncio
async def test_empty_stream_key_migrates_legacy_list_then_drains(monkeypatch):
    from app.db import redis_client
    from app.services import live_persistence_scrub, live_session_drainer
    from app.worker import tasks

    redis = _LengthsRedis(legacy=2)
    drain = AsyncMock(
        side_effect=[
            {"drained": 0},
            {"drained": 2},
            {"drained": 0},
            {"drained": 0},
        ]
    )
    migrate = AsyncMock(side_effect=[2, 0])

    async def _cleanup(*_args, **_kwargs):
        redis.legacy = 0
        return 1

    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)
    monkeypatch.setattr(live_session_drainer, "drain_run_buffer", drain)
    monkeypatch.setattr(
        live_persistence_scrub,
        "migrate_redis_list_to_evidence_stream",
        migrate,
    )
    monkeypatch.setattr(
        live_persistence_scrub,
        "remove_drained_legacy_redis_list",
        AsyncMock(side_effect=_cleanup),
    )

    total = await tasks._drain_live_evidence_before_finalize(
        run_id="legacy-run",
        project_id=str(uuid.uuid4()),
        build_number="old",
        suite_name=None,
    )

    assert total == 2
    assert migrate.await_count == 2


@pytest.mark.asyncio
async def test_run_detail_prefers_unprojected_stream_payload_over_legacy_list():
    from app.services.runs_service import _live_buffer_test_cases

    run_id = uuid.uuid4()
    payload = json.dumps({
        "test_name": "stream-case",
        "class_name": "Checkout",
        "status": "FAILED",
        "error_message": "password=secret",
    })
    redis = AsyncMock()
    redis.xrange = AsyncMock(return_value=[(
        "1-0",
        {"event_type": "test_result", "payload": payload},
    )])
    redis.lrange = AsyncMock(side_effect=AssertionError("legacy fallback used"))

    with patch("app.db.redis_client.get_redis", return_value=redis):
        items, total, pages = await _live_buffer_test_cases(run_id, 1, 50)

    assert total == pages == 1
    assert items[0]["test_name"] == "stream-case"
    redis.lrange.assert_not_awaited()
    assert redis.xrange.await_args.kwargs["count"] > 0


@pytest.mark.asyncio
async def test_run_detail_expands_the_current_producer_batch_manifest():
    from app.services.runs_service import _live_buffer_test_cases

    run_id = uuid.uuid4()
    events = [
        {"event_type": "live_heartbeat", "run_id": str(run_id)},
        {
            "event_type": "test_result",
            "run_id": str(run_id),
            "test_name": "manifest-case",
            "class_name": "Checkout",
            "status": "PASSED",
        },
    ]
    fields = {
        "batch_id": "batch-1",
        "batch_digest": "a" * 64,
        "event_count": "2",
        "session_id": str(run_id),
        "run_id": str(run_id),
        "events_json": json.dumps(events, separators=(",", ":")),
        "event_ids_json": json.dumps(["b" * 64, "c" * 64]),
        "trim_legacy": "1",
    }
    redis = AsyncMock()
    redis.xrange = AsyncMock(return_value=[("1-0", fields)])
    redis.lrange = AsyncMock(side_effect=AssertionError("legacy fallback used"))

    with patch("app.db.redis_client.get_redis", return_value=redis):
        items, total, _pages = await _live_buffer_test_cases(run_id, 1, 50)

    assert total == 1
    assert items[0]["test_name"] == "manifest-case"
    redis.lrange.assert_not_awaited()


@pytest.mark.asyncio
async def test_archive_recovery_stages_into_an_existing_empty_stream(monkeypatch):
    from app.services import live_run_recovery_service as recovery

    redis = SimpleNamespace(
        eval=AsyncMock(return_value=["accepted", "1", "1-0"]),
    )
    monkeypatch.setattr(recovery, "get_redis", lambda: redis)

    assert await recovery._stage_archive_to_redis(
        "recovered-run",
        [{"test_name": "case-a", "status": "PASSED"}],
    ) == 1

    args = redis.eval.await_args.args
    assert args[2].endswith(":recovered-run")
    assert args[3].endswith(":recovered-run")
    assert args[4] == f"{args[3]}:pending"
    assert args[13] == "1"
    assert len(json.loads(args[18])) == 1
    assert json.loads(args[17])[0]["test_name"] == "case-a"
    assert int(args[19]) == recovery._RECOVERY_DEDUPE_TTL_SECONDS
    assert "state = 'staged'" in args[0]
    assert args[0].index("state = 'staged'") < args[0].index("'XADD'")
    assert "'XADD', KEYS[1], stream_id" in args[0]
    assert "'__outstanding_events__'" not in args[0]
    assert "'SADD', KEYS[3], event_id" in args[0]
    assert "'__gate__', 'closed'" in args[0]


@pytest.mark.asyncio
async def test_archive_batch_retry_cannot_leave_a_partial_manifest(monkeypatch):
    from app.services import live_run_recovery_service as recovery

    class Redis:
        def __init__(self):
            self.attempts = []

        async def eval(self, *args):
            self.attempts.append(args)
            if len(self.attempts) == 1:
                # Model a lost reply after the atomic Lua admission committed.
                raise ConnectionError("reply lost after commit")
            return ["duplicate", args[13], "1-0"]

    redis = Redis()
    monkeypatch.setattr(recovery, "get_redis", lambda: redis)
    archive = [
        {"test_name": "a", "status": "PASSED"},
        {"test_name": "b", "status": "FAILED"},
    ]

    with pytest.raises(ConnectionError):
        await recovery._stage_archive_to_redis("retry-run", archive)

    assert await recovery._stage_archive_to_redis("retry-run", archive) == 2
    assert len(redis.attempts) == 2
    assert redis.attempts[0] == redis.attempts[1]
    assert json.loads(redis.attempts[1][17])[1]["test_name"] == "b"


@pytest.mark.asyncio
async def test_recovery_reports_manifest_events_and_batches_separately():
    from app.services.live_run_recovery_service import (
        buffered_live_evidence_counts,
    )

    redis = AsyncMock()
    redis.xlen = AsyncMock(return_value=2)
    redis.xrange = AsyncMock(return_value=[
        ("1-0", {"event_count": "3"}),
        ("2-0", {"event_count": "4"}),
    ])
    redis.llen = AsyncMock(side_effect=AssertionError("legacy fallback used"))

    assert await buffered_live_evidence_counts(redis, "counted-run") == (
        7,
        2,
        "redis_stream",
    )
    redis.llen.assert_not_awaited()


class _Result:
    def __init__(self, *, scalar=None, rows=None) -> None:
        self.value = scalar
        self.rows = rows or []

    def scalar(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value

    def all(self):
        return self.rows


class _Session:
    def __init__(self, results) -> None:
        self.results = list(results)
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, _statement, _params=None):
        return self.results.pop(0)

    async def commit(self):
        self.committed = True


def test_final_state_empty_reconstructs_terminal_counts_from_drained_rows():
    pytest.importorskip("celery")
    from app.worker import tasks

    run_id = uuid.uuid4()
    run = SimpleNamespace(end_time=None, primary_suite_name=None, suite_names=None)
    session = _Session([
        _Result(scalar=2),
        _Result(rows=[("PASSED", 1), ("FAILED", 1)]),
        _Result(scalar=run),
    ])
    finalize = AsyncMock()
    finalize_redis = AsyncMock()
    with (
        patch.object(
            tasks,
            "_drain_live_evidence_before_finalize",
            new=AsyncMock(return_value=2),
        ),
        patch("app.db.postgres.AsyncSessionLocal", return_value=session),
        patch("app.services.ingestion_pipeline.finalize_run", new=finalize),
        patch(
            "app.services.stream_service.finalize_closed_session_redis",
            new=finalize_redis,
        ),
    ):
        result = tasks.persist_live_session.apply(kwargs={
            "run_id": str(run_id),
            "project_id": str(uuid.uuid4()),
            "build_number": "legacy",
            "final_state": {},
        })

    if not result.successful():
        raise AssertionError(result.traceback)
    assert session.committed
    assert run.total_tests == 2
    assert run.passed_tests == run.failed_tests == 1
    assert str(run.status) in {"LaunchStatus.FAILED", "FAILED"}
    finalize.assert_awaited_once()
    finalize_redis.assert_awaited_once_with(str(run_id))
