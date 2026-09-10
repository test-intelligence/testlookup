"""A failed live-event audit write must leave a trace, and must not block ingest.

Re-audit finding M10. ``POST /ws/events`` keeps a sanitized copy of every live
event in Mongo for the operational audit trail. The write is non-fatal on
purpose -- the event still reaches the Redis stream, so the run, the dashboard
and the analysis are unaffected. But it was::

    except Exception:
        pass

No log and no count. A Mongo outage erased the audit trail for every live
event in the window, and nothing anywhere recorded that it had happened.

These drive the real handler with Mongo raising, and read the counter back.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("app.core.metrics")

from app.core import metrics  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.routers.live import ingest_live_event  # noqa: E402

pytestmark = pytest.mark.regression

PROJECT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _failures() -> float:
    return metrics.live_event_archive_failures_total._value.get()


@pytest.fixture
def wired(monkeypatch):
    published: list[tuple[str, dict]] = []

    async def _publish(run_id, event):
        published.append((run_id, event))
        return "1-0"

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr("app.streams.producer.publish_live_event", _publish)
    # Since re-audit N14 a project-key event is handed to the SDK stream's
    # path (services/ws_event_ingest.py) instead of published directly. That
    # adapter is the transport for this branch now, so it is stubbed the same
    # way, into the same list.
    async def _admit(_db, *, project_id, api_key_name, run_id, event):
        published.append((run_id, event))
        return "session-1"

    monkeypatch.setattr("app.services.ws_event_ingest.ingest_one", _admit)
    monkeypatch.setattr(
        "app.streams.live_run_state.RedisLiveRunState.start", _noop
    )
    monkeypatch.setattr(
        "app.streams.live_run_state.RedisLiveRunState.record_test_event", _noop
    )
    # The M4/M3 admission gates would otherwise reach for a real Redis.
    monkeypatch.setattr(
        "app.services.ingestion_backpressure.enforce_redis_memory_backpressure",
        _noop,
    )
    monkeypatch.setattr(
        "app.services.ingestion_rate_limit.enforce_live_event_rate_limit", _noop
    )
    monkeypatch.setattr(
        "app.services.live_event_authz.cached_streaming_project",
        AsyncMock(return_value=PROJECT),
    )
    monkeypatch.setattr(
        "app.services.live_event_authz.resolve_run_project",
        AsyncMock(return_value=PROJECT),
    )
    monkeypatch.setattr(settings, "LIVE_EVENTS_REQUIRE_PROJECT_KEY", True)
    return SimpleNamespace(published=published)


def _mongo(monkeypatch, *, fails: bool):
    class _Coll:
        async def insert_one(self, _doc):
            if fails:
                raise ConnectionError("mongo is down")
            return None

    monkeypatch.setattr(
        "app.db.mongo.get_mongo_db", lambda: {"live_execution_events": _Coll()}
    )


async def _send():
    return await ingest_live_event(
        run_id="run-archive",
        event={"type": "test_result", "test_name": "t", "status": "PASSED"},
        x_api_key="qai_key",
        x_webhook_secret=None,
        db=SimpleNamespace(),
    )


@pytest.mark.asyncio
async def test_a_failed_audit_write_is_counted(wired, monkeypatch):
    _mongo(monkeypatch, fails=True)
    before = _failures()

    await _send()

    assert _failures() == before + 1, (
        "a failed audit write left no metric — a Mongo outage erases the live "
        "audit trail and nothing records that it happened"
    )


@pytest.mark.asyncio
async def test_a_failed_audit_write_is_logged(wired, monkeypatch, caplog):
    _mongo(monkeypatch, fails=True)

    with caplog.at_level(logging.WARNING, logger="app.routers.live"):
        await _send()

    assert any("live_event_archive_failed" in r.getMessage() for r in caplog.records), (
        "a failed audit write produced no log line"
    )


@pytest.mark.asyncio
async def test_the_event_is_still_published_when_the_audit_write_fails(
    wired, monkeypatch
):
    """Non-fatal is the point: the audit copy must never block ingest."""
    _mongo(monkeypatch, fails=True)

    result = await _send()

    assert result["accepted"] is True
    assert len(wired.published) == 1


@pytest.mark.asyncio
async def test_a_successful_audit_write_is_not_counted(wired, monkeypatch):
    _mongo(monkeypatch, fails=False)
    before = _failures()

    await _send()

    assert _failures() == before


def test_an_alert_fires_on_the_counter():
    import pathlib

    alerts = (
        pathlib.Path(__file__).resolve().parents[3]
        / "infra" / "monitoring" / "prometheus-rules" / "testlookup-alerts.yml"
    )
    if not alerts.exists():
        pytest.skip("alert rules not present")
    text = alerts.read_text(encoding="utf-8")
    assert "TestLookupLiveEventArchiveFailing" in text
    assert "testlookup_live_event_archive_failures_total" in text
