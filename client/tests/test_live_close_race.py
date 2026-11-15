"""Closing a live session must not lose, or race, the batch the flusher is sending.

``LiveSession._shutdown`` and ``LiveStream.close`` cancelled the background
flusher. A flusher that was posting lost that batch: its events had already
left the queue, and ``CancelledError`` skips ``except Exception``. The session
close then also raced it on the server, which answers a close during batch
admission with 409 "retry close"; ``_close_session`` logged that and gave up.
The JavaScript SDK had the same race (TL-SDK-002 [vitest]).
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import testlookup_reporter as tr


class _HeldBatches:
    """Fake HTTP: each batch POST waits until ``release`` is set."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.events: list[dict] = []
        self.completed = 0
        self.cancelled = 0

    async def post(self, path: str, *, json: dict, **_kwargs) -> httpx.Response:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        self.completed += 1
        self.events.extend(e for e in json["events"] if e.get("event_type") == "test_result")
        return httpx.Response(202, json={"accepted": len(json["events"])},
                              request=httpx.Request("POST", "http://test.invalid" + path))

    async def aclose(self) -> None:
        return None


async def _shutdown_while_a_batch_is_in_flight(owner, shutdown, http: _HeldBatches) -> None:
    await owner.record("release_pass", "PASSED", 1)
    await owner.record("release_fail", "FAILED", 1)
    await asyncio.wait_for(http.started.wait(), 5)  # the flusher has taken both and is posting
    closing = asyncio.ensure_future(shutdown())
    await asyncio.sleep(0.05)
    assert not closing.done(), "shutdown returned while the flusher's batch was in flight"
    assert http.cancelled == 0, "shutdown cancelled the batch the flusher was posting"
    http.release.set()
    await asyncio.wait_for(closing, 5)


def test_live_session_shutdown_waits_for_the_batch_in_flight():
    async def scenario() -> tuple[_HeldBatches, dict]:
        http = _HeldBatches()
        session = tr.LiveSession(
            session_id="session-1", session_token="token-1", run_id="run-1", http=http,
            base_url="http://test.invalid", batch_size=50, batch_interval=0.005,
        )
        await _shutdown_while_a_batch_is_in_flight(session, session._shutdown, http)
        return http, session.stats

    http, stats = asyncio.run(scenario())
    assert sorted(e["test_name"] for e in http.events) == ["release_fail", "release_pass"]
    assert stats == {"sent": 2, "failed": 0}


def test_live_stream_close_waits_for_the_batch_in_flight():
    async def scenario() -> tuple[_HeldBatches, dict]:
        http = _HeldBatches()
        stream = object.__new__(tr.LiveStream)
        stream._run_id, stream._meta, stream._session_id = "ci-build-1", {}, None
        stream._http, stream._stats = http, {"sent": 0, "failed": 0}
        stream._batch_size, stream._batch_interval = 50, 0.005
        stream._default_suite_name = None
        stream._last_real_enqueue_ms = 0.0
        stream._queue = asyncio.Queue()
        stream._heartbeat_task = None
        stream._flusher_task = asyncio.ensure_future(stream._flusher_loop())
        await _shutdown_while_a_batch_is_in_flight(stream, stream.close, http)
        return http, stream._stats

    http, stats = asyncio.run(scenario())
    assert sorted(e["test_name"] for e in http.events) == ["release_fail", "release_pass"]
    # Both results and the run_complete event were accepted.
    assert stats == {"sent": 3, "failed": 0}


class _Deletes:
    def __init__(self, statuses: list[int]) -> None:
        self.statuses = statuses
        self.count = 0

    async def delete(self, path: str) -> httpx.Response:
        status = self.statuses[min(self.count, len(self.statuses) - 1)]
        self.count += 1
        headers = {"Retry-After": "0"} if status == 409 else {}
        body = {"detail": "live batch admission is still in progress; retry close"} if status == 409 else None
        return httpx.Response(status, headers=headers, json=body,
                              request=httpx.Request("DELETE", "http://test.invalid" + path))


def _close_with(statuses: list[int], caplog) -> tuple[int, int]:
    reporter = object.__new__(tr.TestLookupReporter)
    reporter._http = _Deletes(statuses)
    with caplog.at_level(logging.WARNING, logger="testlookup_reporter"):
        asyncio.run(reporter._close_session("session-1"))
    warnings = [r for r in caplog.records if "Failed to close session" in r.getMessage()]
    return reporter._http.count, len(warnings)


def test_close_retries_the_409_sent_while_a_batch_is_being_admitted(caplog):
    assert _close_with([409, 204], caplog) == (2, 0)


def test_close_stops_retrying_a_409_after_a_bounded_number_of_attempts(caplog):
    assert _close_with([409], caplog) == (tr.CLOSE_ATTEMPTS, 1)


def test_close_does_not_retry_a_refusal_other_than_409(caplog):
    assert _close_with([404, 204], caplog) == (1, 1)
