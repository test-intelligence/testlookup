"""Regression: in-progress live-stream runs showed an empty per-test
table on /runs/{id} and /reports/summary while /live showed live counts.

Bug pinned (2026-05-20): during a live-stream run the per-test rows
live in the Redis event buffer (``LIVE_TESTCASES_KEY``) and are only
materialised into ``test_cases`` by the Phase 4.5 drainer (every 30s)
or at close_session. In the window before the first drain tick — or
when Celery delivery is degraded — ``/runs/{id}/tests`` read empty
Postgres even though ``/live`` reads the HINCRBY hash directly and
showed counts. See ``live-run-inprogress-display`` in memory and
commit "improved test coverage and bug fixes" (c8717a3).

Fix: ``runs_service.list_run_test_cases`` falls back to
``_live_buffer_test_cases`` when Postgres has zero rows for the run.
The helper reads the Redis buffer and synthesises ``TestCaseSummary``-
shaped rows so the run-detail page reflects the same real-time data
the /live page does.

CRITICAL DESIGN PIN (see ``project_regression_complete_2026_05_19`` /
the live-run-recovery follow-up): this fallback is an **inline Redis
read**, NOT an inline Celery ``apply_async`` dispatch. An earlier
attempt wired ``persist_live_session.apply_async`` inline into the
list endpoint; ``apply_async`` is a synchronous broker call that
blocks the asyncio event loop and produced a 120000ms axios-timeout
regression. ``test_list_run_test_cases_fallback_is_a_redis_read_not_celery_dispatch``
guards against re-introducing that.

What this file pins:

  * Empty buffer ⇒ ``None`` (caller keeps the empty DB result).
  * Buffer rows ⇒ deterministic uuid5 id, ``TestCaseSummary`` shape.
  * Dedup by fingerprint (md5(test_name:class_name)); latest wins.
  * ``status`` + ``suite`` filters honoured.
  * Non-``test_result`` events (heartbeats/logs) excluded.
  * Pagination slices the deduped set; ``total`` is the deduped count.
  * Redis-down / malformed JSON degrade to ``None``/skip (never 500).
  * ``list_run_test_cases`` only fires the fallback when the DB has
    zero rows, and does so via a Redis read (no Celery dispatch).
"""
from __future__ import annotations

import hashlib
import inspect
import json
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

RUN_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _expected_id(run_id: uuid.UUID, test_name: str, class_name: str) -> uuid.UUID:
    fp = hashlib.md5(f"{test_name}:{class_name}".encode()).hexdigest()
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:{fp}")


def _ev(
    test_name: str,
    *,
    status: str = "PASSED",
    class_name: str = "com.example.Tests",
    suite_name: str | None = "API Regression",
    event_type: str | None = None,
    timestamp_ms: int | None = None,
    duration_ms=None,
) -> str:
    d: dict = {
        "test_name": test_name,
        "status": status,
        "class_name": class_name,
        "suite_name": suite_name,
    }
    if event_type is not None:
        d["event_type"] = event_type
    if timestamp_ms is not None:
        d["timestamp_ms"] = timestamp_ms
    if duration_ms is not None:
        d["duration_ms"] = duration_ms
    return json.dumps(d)


def _redis_returning(entries):
    redis = AsyncMock()
    redis.lrange = AsyncMock(return_value=entries)
    return redis


async def _call(entries, *, page=1, size=50, status=None, suite=None):
    from app.services.runs_service import _live_buffer_test_cases

    with patch("app.db.redis_client.get_redis", return_value=_redis_returning(entries)):
        return await _live_buffer_test_cases(
            RUN_ID, page, size, status=status, suite=suite,
        )


# ── empty / degraded paths ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_buffer_returns_none():
    """No buffered events ⇒ ``None`` so the caller keeps the (empty) DB
    result rather than fabricating an empty live response."""
    assert await _call([]) is None


@pytest.mark.asyncio
async def test_redis_unavailable_returns_none_not_500():
    """``get_redis()`` raising must degrade to ``None``, never bubble a
    500 to the run-detail page."""
    from app.services.runs_service import _live_buffer_test_cases

    def _boom():
        raise RuntimeError("redis down")

    with patch("app.db.redis_client.get_redis", side_effect=_boom):
        assert await _live_buffer_test_cases(RUN_ID, 1, 50) is None


@pytest.mark.asyncio
async def test_lrange_error_returns_none():
    """A Redis read error (not just an empty list) also degrades to
    ``None`` instead of 500ing."""
    from app.services.runs_service import _live_buffer_test_cases

    redis = AsyncMock()
    redis.lrange = AsyncMock(side_effect=ConnectionError("boom"))
    with patch("app.db.redis_client.get_redis", return_value=redis):
        assert await _live_buffer_test_cases(RUN_ID, 1, 50) is None


@pytest.mark.asyncio
async def test_malformed_json_entries_are_skipped():
    """A corrupt buffer entry must not poison the whole response — the
    good rows still surface."""
    result = await _call(["{not valid json", _ev("test_ok", status="FAILED")])
    assert result is not None
    items, total, _pages = result
    assert total == 1
    assert items[0]["test_name"] == "test_ok"


# ── happy path: shape + determinism ────────────────────────────────────────


@pytest.mark.asyncio
async def test_synthesises_rows_with_testcasesummary_shape():
    items, total, pages = await _call([
        _ev("test_a", status="PASSED", duration_ms=120, timestamp_ms=1_716_163_200_000),
        _ev("test_b", status="FAILED"),
    ])
    assert total == 2
    assert pages == 1
    row = next(r for r in items if r["test_name"] == "test_a")
    # Shape mirrors schemas.TestCaseSummary so the response model
    # serialises the live rows identically to DB-backed rows.
    for key in (
        "id", "test_run_id", "test_name", "suite_name", "class_name",
        "status", "duration_ms", "created_at",
    ):
        assert key in row
    assert row["test_run_id"] == RUN_ID
    assert row["status"] == "PASSED"
    assert row["duration_ms"] == 120
    assert isinstance(row["created_at"], datetime)
    assert row["created_at"].tzinfo is not None


@pytest.mark.asyncio
async def test_id_is_deterministic_uuid5_for_stable_react_keys():
    """The id must be uuid5(run_id, fingerprint) so React keys stay
    stable across the page's polling and rows don't flicker as new
    events arrive."""
    items, _total, _pages = await _call([_ev("test_a", class_name="C")])
    assert items[0]["id"] == _expected_id(RUN_ID, "test_a", "C")


@pytest.mark.asyncio
async def test_status_defaults_to_unknown_when_absent():
    result = await _call([json.dumps({"test_name": "t", "class_name": "C"})])
    items, total, _ = result
    assert total == 1
    assert items[0]["status"] == "UNKNOWN"


# ── dedup ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dedup_by_fingerprint_latest_execution_wins():
    """The append-only buffer holds one entry per execution; the per-test
    table is keyed by logical test. Same test_name+class_name collapses
    to one row, and the later (most recent) execution's status wins."""
    items, total, _ = await _call([
        _ev("test_flaky", status="PASSED", class_name="C"),
        _ev("test_flaky", status="FAILED", class_name="C"),
    ])
    assert total == 1
    assert items[0]["status"] == "FAILED"
    assert items[0]["id"] == _expected_id(RUN_ID, "test_flaky", "C")


# ── filters ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_filter_is_honoured():
    result = await _call(
        [
            _ev("p1", status="PASSED"),
            _ev("f1", status="FAILED"),
            _ev("f2", status="FAILED"),
        ],
        status="failed",  # case-insensitive — helper upper()s it
    )
    items, total, _ = result
    assert total == 2
    assert {r["test_name"] for r in items} == {"f1", "f2"}
    assert all(r["status"] == "FAILED" for r in items)


@pytest.mark.asyncio
async def test_suite_filter_is_honoured():
    result = await _call(
        [
            _ev("a", suite_name="Auth"),
            _ev("b", suite_name="Orders"),
        ],
        suite="auth",  # case-insensitive
    )
    items, total, _ = result
    assert total == 1
    assert items[0]["test_name"] == "a"


@pytest.mark.asyncio
async def test_non_test_result_events_excluded():
    """Heartbeats / logs / metrics carry an explicit ``event_type`` that
    isn't ``test_result`` and must never land in the per-test table.
    Events with no ``event_type`` (the SDK's per-test default) ARE
    included."""
    result = await _call([
        _ev("heartbeat", event_type="heartbeat"),
        _ev("log_line", event_type="log"),
        _ev("real_test", event_type="test_result"),
        _ev("legacy_no_type"),  # no event_type ⇒ treated as a test row
    ])
    items, total, _ = result
    assert total == 2
    assert {r["test_name"] for r in items} == {"real_test", "legacy_no_type"}


# ── pagination ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pagination_slices_deduped_set():
    entries = [_ev(f"t{i}", status="PASSED", class_name=f"C{i}") for i in range(5)]
    page1 = await _call(entries, page=1, size=2)
    items1, total1, pages1 = page1
    assert total1 == 5
    assert pages1 == 3          # ceil(5/2)
    assert len(items1) == 2

    items3, total3, _ = await _call(entries, page=3, size=2)
    assert total3 == 5
    assert len(items3) == 1     # last page has the remainder


# ── list_run_test_cases wiring ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_run_test_cases_uses_buffer_only_when_db_empty():
    """When Postgres returns rows, the live buffer must NOT be consulted —
    the DB path is authoritative once real rows land."""
    import app.services.runs_service as rs

    db_rows = ([{"test_name": "real"}], 1, 1)
    with patch.object(rs, "paginate_query", new=AsyncMock(return_value=db_rows)), \
         patch.object(rs, "_live_buffer_test_cases", new=AsyncMock()) as buf:
        out = await rs.list_run_test_cases(AsyncMock(), RUN_ID, 1, 50)

    assert out == db_rows
    buf.assert_not_called()


@pytest.mark.asyncio
async def test_list_run_test_cases_falls_back_to_buffer_when_db_empty():
    import app.services.runs_service as rs

    live = (["synthesised"], 7, 1)
    with patch.object(rs, "paginate_query", new=AsyncMock(return_value=([], 0, 0))), \
         patch.object(rs, "_live_buffer_test_cases", new=AsyncMock(return_value=live)) as buf:
        out = await rs.list_run_test_cases(
            AsyncMock(), RUN_ID, 1, 50, status="FAILED", suite="Auth",
        )

    assert out == live
    # Filters propagate into the buffer read so /runs?status=&suite= match.
    buf.assert_awaited_once_with(RUN_ID, 1, 50, "FAILED", "Auth")


@pytest.mark.asyncio
async def test_list_run_test_cases_returns_empty_db_result_when_buffer_also_empty():
    """DB empty AND buffer empty ⇒ the empty DB tuple is returned (not a
    fabricated live result)."""
    import app.services.runs_service as rs

    empty = ([], 0, 0)
    with patch.object(rs, "paginate_query", new=AsyncMock(return_value=empty)), \
         patch.object(rs, "_live_buffer_test_cases", new=AsyncMock(return_value=None)):
        out = await rs.list_run_test_cases(AsyncMock(), RUN_ID, 1, 50)

    assert out == empty


def test_list_run_test_cases_fallback_is_a_redis_read_not_celery_dispatch():
    """The fallback must stay an inline Redis read. Wiring
    ``persist_live_session.apply_async`` / ``.delay`` inline blocks the
    asyncio event loop (synchronous broker call) and caused a
    120000ms axios timeout. Pin that the list path never dispatches
    Celery and routes through the buffer helper instead."""
    from app.services.runs_service import (
        _live_buffer_test_cases,
        list_run_test_cases,
    )

    list_src = inspect.getsource(list_run_test_cases)
    buf_src = inspect.getsource(_live_buffer_test_cases)
    combined = list_src + buf_src

    assert "apply_async" not in combined, (
        "list_run_test_cases / _live_buffer_test_cases must not dispatch "
        "Celery inline — apply_async is a blocking broker call on the "
        "asyncio event loop (120s timeout regression)."
    )
    assert ".delay(" not in combined
    assert "_live_buffer_test_cases" in list_src, (
        "list_run_test_cases must route its empty-DB fallback through the "
        "inline Redis-read helper."
    )
