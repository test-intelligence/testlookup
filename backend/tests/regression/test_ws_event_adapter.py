"""A ``/ws/events`` payload, translated for the SDK stream's path (re-audit N14).

The route used to accept any of these values and only publish them. Now that a
project-key event is admitted as an SDK ``LiveEvent``, the translation must not
turn yesterday's accepted payload into today's 422 -- nor let a payload the SDK
path cannot hold surface as a 500.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.services import ws_event_ingest as adapter

pytestmark = pytest.mark.regression


def test_type_becomes_event_type():
    assert adapter.to_live_event({"type": "run_complete"}).event_type == "run_complete"


def test_a_missing_type_is_a_test_result_as_the_route_always_treated_it():
    assert adapter.to_live_event({"test_name": "t"}).event_type == "test_result"


def test_values_the_sdk_model_cannot_hold_are_dropped_not_rejected():
    live = adapter.to_live_event({
        "type": "test_result",
        "test_name": "t",
        "status": "PASSED",
        "duration_ms": 12.7,
        "tags": "smoke",
        "metadata": ["not", "a", "dict"],
        "timestamp_ms": "soon",
    })
    assert live.duration_ms == 12
    assert live.tags is None
    assert live.metadata is None
    assert live.timestamp_ms is None


def test_a_negative_duration_is_dropped():
    assert adapter.to_live_event({"duration_ms": -5}).duration_ms is None


def test_over_long_names_are_cut_to_what_the_model_holds():
    live = adapter.to_live_event({"test_name": "n" * 5000, "suite_name": "s" * 900})
    assert len(live.test_name) == 1000
    assert len(live.suite_name) == 500


def test_the_result_fields_survive_the_translation():
    live = adapter.to_live_event({
        "type": "test_result", "test_name": "checkout", "status": "FAILED",
        "duration_ms": 30, "error_message": "boom", "stack_trace": "at x",
        "class_name": "C", "tags": ["smoke"], "timestamp_ms": 1_700_000_000_000,
    })
    assert (live.test_name, live.status, live.duration_ms) == ("checkout", "FAILED", 30)
    assert (live.error_message, live.stack_trace, live.class_name) == ("boom", "at x", "C")
    assert live.tags == ["smoke"]
    assert live.timestamp_ms == 1_700_000_000_000


def test_run_start_carries_its_build_into_the_session():
    meta = adapter.meta_for({"type": "run_start", "build_number": 42, "total_tests": "7"})
    assert meta.build_number == "42"
    assert meta.total_tests == 7


def test_only_run_start_carries_session_metadata():
    assert adapter.meta_for({"type": "test_result", "build_number": "42"}) is None


def test_the_session_cache_is_per_project():
    """One project's run_id must never resolve to another project's session."""
    run = "build-42"
    a, b = uuid.uuid4(), uuid.uuid4()
    assert adapter.session_cache_key(a, run) != adapter.session_cache_key(b, run)


@pytest.mark.asyncio
async def test_an_event_the_sdk_path_cannot_hold_is_a_422_not_a_500():
    with pytest.raises(HTTPException) as exc:
        await adapter.ingest_one(
            None,
            project_id=uuid.uuid4(),
            api_key_name="ci",
            run_id="r" * 101,  # the SDK request caps run_id at 100
            event={"type": "test_result"},
        )
    assert exc.value.status_code == 422


# ── The consumer, for a run_start the SDK path admitted ─────────────────


@pytest.mark.asyncio
async def test_the_consumer_starts_an_sdk_shaped_run_start_from_its_live_state(monkeypatch):
    """A run_start admitted through the SDK path carries no project; its session does.

    Raising here would retry the event three times and then dead-letter it.
    """
    from unittest.mock import AsyncMock

    from app.streams import live_consumer

    broadcasts: list[tuple[str, str]] = []

    async def _broadcast(project_id, message):
        broadcasts.append((project_id, message["type"]))

    monkeypatch.setattr(live_consumer, "_broadcast", _broadcast)
    monkeypatch.setattr(
        live_consumer.RedisLiveRunState, "get", AsyncMock(return_value={"project_id": "p-1"})
    )
    monkeypatch.setattr(live_consumer.RedisLiveRunState, "start", AsyncMock(return_value=None))

    await live_consumer.LiveEventStreamConsumer()._on_run_start(
        "session-1", {"event_type": "run_start", "run_id": "session-1"}
    )

    assert broadcasts == [("p-1", "live_run_started")]


@pytest.mark.asyncio
async def test_a_run_start_with_no_project_anywhere_still_fails(monkeypatch):
    from unittest.mock import AsyncMock

    from app.streams import live_consumer

    monkeypatch.setattr(live_consumer.RedisLiveRunState, "get", AsyncMock(return_value=None))
    with pytest.raises(ValueError):
        await live_consumer.LiveEventStreamConsumer()._on_run_start(
            "unknown-run", {"event_type": "run_start"}
        )


# ── A close is finalised in Redis only once it is durable ────────────────


@pytest.mark.asyncio
async def test_a_close_that_fails_to_commit_is_not_finalised_in_redis(monkeypatch):
    """The stream router's order: commit the close, then finalise Redis.

    Finalising first would mark the close durable in Redis while Postgres
    rolled it back -- a run Redis calls closed and the database never saw close.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services import stream_service

    finalize = AsyncMock()
    monkeypatch.setattr(
        stream_service,
        "ingest_via_api_key",
        AsyncMock(return_value=SimpleNamespace(session_id=str(uuid.uuid4()))),
    )
    monkeypatch.setattr(stream_service, "finalize_closed_session_redis", finalize)
    db = SimpleNamespace(commit=AsyncMock(side_effect=RuntimeError("commit failed")))

    with pytest.raises(RuntimeError):
        await adapter.ingest_one(
            db, project_id=uuid.uuid4(), api_key_name="ci", run_id="run-1",
            event={"type": "run_complete"},
        )
    finalize.assert_not_awaited()
