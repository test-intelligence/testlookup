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
async def test_the_adapter_stages_and_never_commits(monkeypatch):
    """The request owns the transaction; the route commits, then calls
    after_commit -- the stream router's order (see the route's own tests)."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services import stream_service

    finalize = AsyncMock()
    monkeypatch.setattr(
        stream_service,
        "ingest_via_api_key",
        AsyncMock(return_value=SimpleNamespace(session_id="session-1")),
    )
    monkeypatch.setattr(stream_service, "finalize_closed_session_redis", finalize)

    async def _no_commit():
        raise AssertionError("the adapter committed; the route owns the transaction")

    outcome = await adapter.ingest_one(
        SimpleNamespace(commit=_no_commit), project_id=uuid.uuid4(), api_key_name="ci",
        run_id="run-1", event={"type": "run_complete"},
    )
    assert (outcome.session_id, outcome.completes, outcome.staged) == ("session-1", True, True)
    finalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_after_commit_finalises_a_close_and_forgets_its_session(monkeypatch):
    from unittest.mock import AsyncMock

    from app.services import stream_service

    finalize, forget, remember = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(stream_service, "finalize_closed_session_redis", finalize)
    monkeypatch.setattr(adapter, "_forget_session", forget)
    monkeypatch.setattr(adapter, "_remember_session", remember)

    await adapter.after_commit(
        adapter.WsIngestOutcome("session-1", "key-1", completes=True, staged=True)
    )

    finalize.assert_awaited_once_with("session-1")
    forget.assert_awaited_once_with("key-1")
    remember.assert_not_awaited()


@pytest.mark.asyncio
async def test_after_commit_remembers_an_open_session(monkeypatch):
    from unittest.mock import AsyncMock

    from app.services import stream_service

    finalize, remember = AsyncMock(), AsyncMock()
    monkeypatch.setattr(stream_service, "finalize_closed_session_redis", finalize)
    monkeypatch.setattr(adapter, "_remember_session", remember)

    await adapter.after_commit(
        adapter.WsIngestOutcome("session-1", "key-1", completes=False, staged=True)
    )

    remember.assert_awaited_once_with("key-1", "session-1")
    finalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_after_commit_does_nothing_for_a_cached_event(monkeypatch):
    """A cached event staged nothing: nothing to finalise, nothing to remember."""
    from unittest.mock import AsyncMock

    from app.services import stream_service

    finalize, remember = AsyncMock(), AsyncMock()
    monkeypatch.setattr(stream_service, "finalize_closed_session_redis", finalize)
    monkeypatch.setattr(adapter, "_remember_session", remember)

    await adapter.after_commit(adapter.WsIngestOutcome("session-1"))

    finalize.assert_not_awaited()
    remember.assert_not_awaited()


# ── Batch ids, the session's name and the build (code review of N14) ─────


def _live(event):
    return adapter.to_live_event(event)


def test_a_client_event_id_names_the_batch():
    event = {"type": "test_result", "test_name": "t", "event_id": "result-7"}
    assert adapter.batch_id_for(event, _live(event)) == "ws-result-7"
    numbered = {"type": "test_result", "event_id": 7}
    assert adapter.batch_id_for(numbered, _live(numbered)) == "ws-7"


@pytest.mark.parametrize(
    "bad", [["a"], {"a": 1}, True, 1.5, "x" * 201, "tab" + chr(9) + "here", "del" + chr(127)]
)
def test_an_unusable_event_id_is_a_422(bad):
    event = {"type": "test_result", "event_id": bad}
    with pytest.raises(HTTPException) as exc:
        adapter.batch_id_for(event, _live(event))
    assert exc.value.status_code == 422


@pytest.mark.parametrize("kind", ["run_start", "run_complete"])
def test_once_per_run_events_are_recognised_by_their_content(kind):
    """None selects the admission's content identity, so a retry converges."""
    event = {"type": kind}
    assert adapter.batch_id_for(event, _live(event)) is None


def test_every_result_without_an_id_is_a_new_batch():
    event = {"type": "test_result", "test_name": "t", "status": "PASSED"}
    first, second = (adapter.batch_id_for(event, _live(event)) for _ in range(2))
    assert first != second
    assert first.startswith("ws-")


@pytest.mark.asyncio
async def test_run_start_never_takes_the_cached_session(monkeypatch):
    """Only PostgreSQL can say whether a run_start begins a new run."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services import stream_service

    async def _cached(_key):
        raise AssertionError("run_start consulted the session cache")

    monkeypatch.setattr(adapter, "_cached_session", _cached)
    monkeypatch.setattr(
        stream_service, "ingest_via_api_key",
        AsyncMock(return_value=SimpleNamespace(session_id="s-2")),
    )

    outcome = await adapter.ingest_one(
        object(), project_id=uuid.uuid4(), api_key_name="ci", run_id="nightly",
        event={"type": "run_start", "build_number": "2"},
    )
    assert outcome.session_id == "s-2"
    assert outcome.staged


@pytest.mark.asyncio
async def test_a_session_is_named_after_its_key_even_when_the_credential_was_cached(monkeypatch):
    """A cached credential carries no name, so it is looked up for the session."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services import stream_service

    named: list[str] = []

    async def _ingest(*, db, project_id, api_key_name, request):
        named.append(api_key_name)
        return SimpleNamespace(session_id="s-1")

    async def _name(_db, api_key):
        return {"qai_nightly": "ci-nightly"}.get(api_key)

    monkeypatch.setattr(stream_service, "ingest_via_api_key", _ingest)
    monkeypatch.setattr(adapter, "_api_key_name", _name)
    monkeypatch.setattr(adapter, "_cached_session", AsyncMock(return_value=None))

    for name, key in ((None, "qai_nightly"), ("from-the-check", "qai_nightly"), (None, "qai_unknown")):
        await adapter.ingest_one(
            object(), project_id=uuid.uuid4(), api_key_name=name, run_id="r",
            event={"type": "test_result", "test_name": "t"}, api_key=key,
        )
    assert named == ["ci-nightly", "from-the-check", "ws-events"]


@pytest.mark.asyncio
async def test_the_consumer_announces_the_runs_build_not_the_session_id(monkeypatch):
    from unittest.mock import AsyncMock

    from app.streams import live_consumer

    messages: list[dict] = []

    async def _broadcast(_project_id, message):
        messages.append(message)

    started = AsyncMock(return_value=None)
    monkeypatch.setattr(live_consumer, "_broadcast", _broadcast)
    monkeypatch.setattr(
        live_consumer.RedisLiveRunState, "get",
        AsyncMock(return_value={"project_id": "p-1", "build_number": "build-42"}),
    )
    monkeypatch.setattr(live_consumer.RedisLiveRunState, "start", started)

    await live_consumer.LiveEventStreamConsumer()._on_run_start(
        "session-uuid", {"event_type": "run_start", "run_id": "session-uuid"}
    )

    assert messages[0]["build_number"] == "build-42"
    assert started.await_args.args[2] == "build-42"
