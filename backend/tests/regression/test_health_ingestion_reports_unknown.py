"""``/health/ingestion`` reported ``status: "ok"`` while Redis was unreachable.

Redis *is* the subject of this endpoint — the ingest and reject counters, the
memory snapshot, the queue depths, the live-session count and the DLQ depth all
live there. Yet every one of those signals initialised to an all-clear value
inside a swallowing ``try/except``, and ``status`` was computed from exactly
those defaults. With Redis down, live-stream ingestion dead and both admission
gates failing open, on-call received::

    {"status": "ok",
     "redis": {"used_pct": 0.0},
     "recent": {"reject_count_this_minute": 0},
     "dlq": {"persist_live_session": 0},
     "ai_pipeline": {"pending_dispatches": 0}}

Every number in that payload is the most reassuring value it could hold, and
each one means "we could not look". ``0.0%`` reads as headroom; ``dlq: 0`` reads
as "nothing has permanently failed". The docstring says the endpoint exists for
"support / on-call humans when a customer reports my runs are slow / 429ing" —
it answered the one question it exists to answer with a confident lie.

The endpoint already knew how to express uncertainty: queue depths were already
``None`` per queue. The three fields that actually drove ``status`` were not.

This is the repo's own "absence is not health" class, on the endpoint whose
entire job is health.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _DeadRedis:
    """Every access raises, as a refused connection would."""

    def _boom(self, *a, **kw):
        raise ConnectionError("Redis is unreachable")

    def scan_iter(self, *a, **kw):
        raise ConnectionError("Redis is unreachable")

    async def get(self, *a, **kw):
        raise ConnectionError("Redis is unreachable")

    async def llen(self, *a, **kw):
        raise ConnectionError("Redis is unreachable")

    async def zcard(self, *a, **kw):
        raise ConnectionError("Redis is unreachable")


async def _payload_with_dead_redis() -> dict:
    from app.routers import health

    with (
        patch("app.db.redis_client.get_redis", MagicMock(return_value=_DeadRedis())),
        patch(
            "app.services.ingestion_backpressure.get_redis_memory_snapshot",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.ingestion_dlq.get_dlq_count",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.run_downstream_outbox.pending_dispatch_count",
            AsyncMock(return_value=None),
        ),
    ):
        return await health.health_ingestion()


@pytest.mark.asyncio
async def test_status_is_unknown_when_redis_is_unreachable():
    """The regression: this said "ok"."""
    payload = await _payload_with_dead_redis()
    assert payload["status"] == "unknown", (
        "an unreachable Redis means the questions were not answered, not that "
        "the answers were reassuring"
    )
    assert payload["redis_reachable"] is False


@pytest.mark.asyncio
async def test_the_endpoint_does_not_raise_when_redis_is_down():
    """Still best-effort: a health endpoint must not 500."""
    payload = await _payload_with_dead_redis()
    assert isinstance(payload, dict)
    assert "timestamp" in payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        ("redis", "used_pct"),
        ("recent", "ingest_count_this_minute"),
        ("recent", "reject_count_this_minute"),
        ("recent", "active_projects_this_minute"),
        ("live_sessions", "active"),
        ("dlq", "persist_live_session"),
        ("ai_pipeline", "pending_dispatches"),
    ],
    ids=lambda p: "/".join(p),
)
async def test_unreadable_signals_are_none_not_zero(path):
    """``0`` is a measurement. ``None`` is "we could not look"."""
    payload = await _payload_with_dead_redis()
    section, key = path
    value = payload[section][key]
    assert value is None, (
        f"{section}.{key} reported {value!r} while Redis was unreachable — a "
        "number here is read as a measurement that was actually taken"
    )


@pytest.mark.asyncio
async def test_a_forced_refresh_never_returns_a_stale_snapshot():
    """It used to hand back ``_CACHE``, presenting a minutes-old number as now.

    The admission gate calls without ``force_refresh`` and still gets the
    cache — there, a slightly stale number beats failing open. Only the caller
    that explicitly asked for a fresh reading is told there isn't one.
    """
    from app.services import ingestion_backpressure as bp

    stale = bp._MemorySnapshot(used_bytes=1, max_bytes=2, captured_at=0.0)
    with (
        patch.object(bp, "_CACHE", stale),
        patch.object(bp, "_read_memory_snapshot", AsyncMock(return_value=None)),
    ):
        assert await bp.get_redis_memory_snapshot(force_refresh=True) is None
        assert await bp.get_redis_memory_snapshot(force_refresh=False) is stale


@pytest.mark.asyncio
async def test_helpers_report_none_rather_than_zero():
    """A count of 0 from a helper that could not read is the same lie."""
    from app.services.ingestion_dlq import get_dlq_count
    from app.services.run_downstream_outbox import pending_dispatch_count

    with patch("app.db.redis_client.get_redis", MagicMock(return_value=_DeadRedis())):
        assert await get_dlq_count("persist_live_session") is None

    class _DeadDb:
        async def execute(self, _stmt):
            raise RuntimeError("database unreachable")

    # The AI-pipeline backlog moved from Redis to Postgres with the debouncer's
    # removal (BUG-010), so its "could not look" case is a failing query rather
    # than a dead Redis -- but the contract is identical.
    assert await pending_dispatch_count(_DeadDb()) is None


@pytest.mark.asyncio
async def test_a_healthy_redis_still_reports_ok():
    """The guard must not turn every healthy deployment into "unknown"."""
    from app.routers import health

    class _LiveRedis:
        async def get(self, *a, **kw):
            return b"0"

        async def llen(self, *a, **kw):
            return 0

        async def zcard(self, *a, **kw):
            return 0

        def scan_iter(self, *a, **kw):
            async def _empty():
                return
                yield  # pragma: no cover — makes this an async generator

            return _empty()

    snapshot = MagicMock(used_bytes=10, max_bytes=1000, used_pct=1.0)
    with (
        patch("app.db.redis_client.get_redis", MagicMock(return_value=_LiveRedis())),
        patch(
            "app.services.ingestion_backpressure.get_redis_memory_snapshot",
            AsyncMock(return_value=snapshot),
        ),
        patch("app.services.ingestion_dlq.get_dlq_count", AsyncMock(return_value=0)),
        patch(
            "app.services.run_downstream_outbox.pending_dispatch_count",
            AsyncMock(return_value=0),
        ),
    ):
        payload = await health.health_ingestion()

    assert payload["status"] == "ok"
    assert payload["redis_reachable"] is True
    assert payload["recent"]["reject_count_this_minute"] == 0


@pytest.mark.asyncio
async def test_both_ingest_budgets_are_reported(monkeypatch):
    """Re-audit M3/N9: the single-event budget is a second knob, so show it.

    The thresholds are configuration, known even while Redis is not -- and an
    operator reading a 429 from /ws/events needs the limit that produced it.
    Distinct values prove each field is read from its own setting.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "INGEST_RATE_LIMIT_PER_MINUTE", 321)
    monkeypatch.setattr(settings, "INGEST_EVENT_RATE_LIMIT_PER_MINUTE", 12345)

    thresholds = (await _payload_with_dead_redis())["thresholds"]

    assert thresholds["rate_limit_per_minute"] == 321
    assert thresholds["event_rate_limit_per_minute"] == 12345, (
        "/health/ingestion does not report the single-event budget"
    )
