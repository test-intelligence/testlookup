"""Atomic admission and durable-capacity regression tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_batch_admission_uses_one_lua_transaction_and_never_ltrims(monkeypatch):
    from app.services import stream_service

    redis = type("Redis", (), {})()
    redis.eval = AsyncMock(return_value=[b"accepted", b"2"])
    monkeypatch.setattr(stream_service, "get_redis", lambda: redis)
    monkeypatch.setattr(stream_service.settings, "LIVE_BUFFER_MAX_EVENTS_PER_RUN", 50_000)

    events = [
        {"event_type": "test_result", "test_name": "a", "status": "PASSED"},
        {"event_type": "test_result", "test_name": "b", "status": "FAILED"},
    ]
    accepted = await stream_service._persist_event_batch(
        "session-1", "run-1", events, batch_id="batch-1"
    )

    assert accepted == 2
    redis.eval.assert_awaited_once()
    args = redis.eval.await_args.args
    assert args[1] == 12
    assert args[2] == "testlookup:live:evidence:run-1"
    assert args[4] == "testlookup:live:batch_state:run-1"
    assert "LTRIM" not in args[0].upper()
    assert "XADD" in args[0] and "SADD" in args[0] and "SCARD" in args[0]
    assert "events_json" in args[0] and "event_ids_json" in args[0]

    from app.services.live_session_drainer import _decode_stream_entries

    projected = _decode_stream_entries([("10-0", {
        "batch_id": args[27],
        "batch_digest": args[28],
        "event_count": "2",
        "session_id": "session-1",
        "run_id": "run-1",
        "events_json": args[32],
        "event_ids_json": args[33],
        "trim_legacy": "1",
    })])
    assert [item["payload"]["test_name"] for item in projected] == ["a", "b"]


@pytest.mark.asyncio
async def test_capacity_rejects_the_whole_batch(monkeypatch):
    from app.services import stream_service

    redis = type("Redis", (), {})()
    redis.eval = AsyncMock(return_value=[b"capacity", b"49999"])
    monkeypatch.setattr(stream_service, "get_redis", lambda: redis)
    monkeypatch.setattr(stream_service.settings, "LIVE_BUFFER_MAX_EVENTS_PER_RUN", 50_000)

    with pytest.raises(stream_service.LiveEvidenceCapacityError):
        await stream_service._persist_event_batch(
            "session-1",
            "run-1",
            [{"event_type": "test_result", "test_name": "a"}] * 2,
            batch_id="batch-cap",
        )


@pytest.mark.asyncio
async def test_duplicate_batch_returns_original_count_without_new_identity(monkeypatch):
    from app.services import stream_service

    redis = type("Redis", (), {})()
    redis.eval = AsyncMock(return_value=[b"duplicate", b"3"])
    monkeypatch.setattr(stream_service, "get_redis", lambda: redis)

    accepted = await stream_service._persist_event_batch(
        "session-1",
        "run-1",
        [{"event_type": "live_heartbeat"}],
        batch_id="same-retry",
    )
    assert accepted == 3


@pytest.mark.asyncio
async def test_duplicate_test_batch_does_not_run_high_volume_detector(monkeypatch):
    from app.services import stream_service

    redis = type("Redis", (), {})()
    redis.eval = AsyncMock(return_value=[b"duplicate", b"1"])
    monkeypatch.setattr(stream_service, "get_redis", lambda: redis)
    resolver = AsyncMock(return_value="project-1")
    with patch.object(stream_service, "_resolve_project_id_for_run", resolver):
        await stream_service._persist_event_batch(
            "session-1", "run-1",
            [{"event_type": "test_result", "status": "PASSED"}],
            batch_id="retry",
        )
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_batch_reconciles_abandoned_staged_manifest(monkeypatch):
    from app.services import stream_service

    redis = type("Redis", (), {})()
    redis.eval = AsyncMock(side_effect=[
        [b"busy", b"0"],
        [b"reconciled", b"1"],
        [b"accepted", b"1"],
    ])
    monkeypatch.setattr(stream_service, "get_redis", lambda: redis)

    assert await stream_service._persist_event_batch(
        "session-1", "run-1", [{"event_type": "live_heartbeat"}],
        batch_id="after-crash",
    ) == 1
    assert redis.eval.await_count == 3
    assert redis.eval.await_args_list[1].args[0] == stream_service._RECONCILE_STAGED_BATCH_LUA


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [b"collision", b"closed"])
async def test_batch_identity_collision_and_closed_session_are_conflicts(
    monkeypatch, outcome,
):
    from app.services import stream_service

    redis = type("Redis", (), {})()
    redis.eval = AsyncMock(return_value=[outcome, b"0"])
    monkeypatch.setattr(stream_service, "get_redis", lambda: redis)

    with pytest.raises(stream_service.LiveBatchConflictError) as exc:
        await stream_service._persist_event_batch(
            "session-1", "run-1", [{"event_type": "test_result"}],
            batch_id="batch-1",
        )
    assert exc.value.status_code == 409


def test_legacy_batch_and_event_id_contract_is_stable():
    from app.services.stream_service import _legacy_batch_id, _stable_event_id

    events = [{"status": "PASSED", "test_name": "a"}]
    batch_a = _legacy_batch_id("session", "run", events)
    batch_b = _legacy_batch_id("session", "run", events)
    assert batch_a == batch_b
    assert batch_a.startswith("legacy-")
    assert _stable_event_id("session", batch_a, 0) == _stable_event_id(
        "session", batch_b, 0
    )
    assert _stable_event_id("session", batch_a, 0) != _stable_event_id(
        "session", batch_a, 1
    )


def test_closed_dedupe_receipts_get_bounded_replay_retention():
    from app.services import stream_service

    assert "HSET" in stream_service._CLOSE_ADMISSION_LUA
    assert "'closing'" in stream_service._CLOSE_ADMISSION_LUA
    assert "EXPIRE" not in stream_service._CLOSE_ADMISSION_LUA
    assert "EXPIRE" in stream_service._FINALIZE_CLOSE_LUA
    assert stream_service._CLOSED_DEDUPE_RETENTION_SECONDS == 15 * 24 * 60 * 60


@pytest.mark.asyncio
async def test_close_commit_failure_does_not_finalize_redis(monkeypatch):
    from app.routers import stream as stream_router

    session_id = "11111111-1111-1111-1111-111111111111"
    db = type("DB", (), {})()
    db.commit = AsyncMock(side_effect=RuntimeError("commit failed"))
    close = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(stream_router.stream_service, "close_session", close)
    monkeypatch.setattr(
        stream_router.stream_service, "finalize_closed_session_redis", finalize
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        await stream_router.close_session(
            session_id,
            db,
            (type("User", (), {})(), None),
        )

    close.assert_awaited_once()
    finalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_close_commit_finalizes_redis_after_commit(monkeypatch):
    from app.routers import stream as stream_router

    session_id = "11111111-1111-1111-1111-111111111111"
    order: list[str] = []
    db = type("DB", (), {})()
    db.commit = AsyncMock(side_effect=lambda: order.append("commit"))
    close = AsyncMock(side_effect=lambda *_args, **_kwargs: order.append("close"))
    finalize = AsyncMock(side_effect=lambda *_args: order.append("finalize"))
    monkeypatch.setattr(stream_router.stream_service, "close_session", close)
    monkeypatch.setattr(
        stream_router.stream_service, "finalize_closed_session_redis", finalize
    )

    await stream_router.close_session(
        session_id,
        db,
        (type("User", (), {})(), None),
    )

    assert order == ["close", "commit", "finalize"]


@pytest.mark.asyncio
async def test_close_redis_finalizer_is_idempotent_and_uses_retention(monkeypatch):
    from app.services import stream_service

    redis = type("Redis", (), {})()
    redis.eval = AsyncMock(return_value=[b"closed", b"0"])
    monkeypatch.setattr(stream_service, "get_redis", lambda: redis)

    session_id = "11111111-1111-1111-1111-111111111111"
    await stream_service.finalize_closed_session_redis(session_id)
    await stream_service.finalize_closed_session_redis(session_id)

    assert redis.eval.await_count == 2
    assert redis.eval.await_args.args[-1] == 15 * 24 * 60 * 60
