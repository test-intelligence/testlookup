"""EXEC-01 (group B): live ``test_result`` events become per-test start / finish / phase / worker columns.

The live clock is the CLIENT's, so it is bounded against the server's before it is stored: a value from the future
(beyond a few minutes of drift) or from well before the session started means that client's clock is off, and both
ends of the pair are dropped rather than stored wrong. Phase times are durations and survive either way.
``timing_source`` is ``live`` whenever anything survived, and NULL when nothing did.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.models.schemas import LiveEvent
from app.services.live_session_drainer import (
    LIVE_TIMING_COLUMNS,
    _event_to_row,
    _upsert_projection_rows,
    live_timing_columns,
)

NOW = datetime(2026, 10, 14, 10, 5, 0, tzinfo=timezone.utc)
SESSION_START = datetime(2026, 10, 14, 10, 0, 0, tzinfo=timezone.utc)
RUN = uuid.UUID("00000000-0000-0000-0000-00000000e002")


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _at(seconds: float) -> datetime:
    return SESSION_START + timedelta(seconds=seconds)


# ── the column mapping ───────────────────────────────────────────────────────


def test_a_sane_client_clock_is_kept_with_the_phase_split():
    columns = live_timing_columns(
        {
            "timestamp_ms": _ms(_at(3.05)),
            "started_at_ms": _ms(_at(1.25)),
            "setup_ms": 120, "call_ms": 1600, "teardown_ms": 80, "worker_id": "gw1",
        },
        session_started_at=SESSION_START, now=NOW,
    )
    assert columns == {
        "started_at": _at(1.25),
        "finished_at": _at(3.05),
        "setup_ms": 120,
        "call_ms": 1600,
        "teardown_ms": 80,
        "worker_id": "gw1",
        "timing_source": "live",
    }


def test_a_client_clock_in_the_future_drops_both_ends_but_keeps_the_phases():
    ahead = timedelta(hours=2)  # the runner's clock is two hours fast
    columns = live_timing_columns(
        {"timestamp_ms": _ms(_at(3) + ahead), "started_at_ms": _ms(_at(1) + ahead), "call_ms": 2000},
        session_started_at=SESSION_START, now=NOW,
    )
    assert columns["started_at"] is None and columns["finished_at"] is None
    assert columns["call_ms"] == 2000
    assert columns["timing_source"] == "live"


def test_a_client_clock_hours_behind_the_session_is_ignored():
    behind = timedelta(hours=3)
    columns = live_timing_columns(
        {"timestamp_ms": _ms(_at(3) - behind), "started_at_ms": _ms(_at(1) - behind)},
        session_started_at=SESSION_START, now=NOW,
    )
    assert all(columns[key] is None for key in LIVE_TIMING_COLUMNS)  # nothing survived: no source claimed


def test_one_bad_end_discredits_the_pair():
    columns = live_timing_columns(
        {"timestamp_ms": _ms(_at(3)), "started_at_ms": 12},  # a seconds-vs-ms mix-up on the start
        session_started_at=SESSION_START, now=NOW,
    )
    assert columns["started_at"] is None and columns["finished_at"] is None


def test_small_forward_drift_is_tolerated():
    columns = live_timing_columns(
        {"timestamp_ms": _ms(NOW + timedelta(minutes=2))}, session_started_at=SESSION_START, now=NOW,
    )
    assert columns["finished_at"] == NOW + timedelta(minutes=2)


def test_an_event_without_timing_claims_no_source():
    columns = live_timing_columns({"test_name": "t"}, session_started_at=SESSION_START, now=NOW)
    assert set(columns) == set(LIVE_TIMING_COLUMNS)
    assert all(value is None for value in columns.values())


def test_phase_values_out_of_range_are_dropped():
    columns = live_timing_columns(
        {"setup_ms": 25 * 60 * 60 * 1000, "call_ms": -5, "teardown_ms": 40, "worker_id": "w" * 100},
        session_started_at=SESSION_START, now=NOW,
    )
    assert (columns["setup_ms"], columns["call_ms"], columns["teardown_ms"]) == (None, None, 40)
    assert columns["worker_id"] == "w" * 64


def test_every_row_carries_the_same_key_set():
    """Bulk inserts bind one key set per statement: a row without timing still has every timing key."""
    bare = _event_to_row({"test_name": "a", "status": "PASSED"}, run_uuid=RUN, default_suite=None)
    timed = _event_to_row(
        {"test_name": "b", "status": "PASSED", "timestamp_ms": _ms(_at(2))},
        run_uuid=RUN, default_suite=None, session_started_at=SESSION_START, now=NOW,
    )
    assert set(bare) == set(timed)
    assert bare["finished_at"] is None and bare["timing_source"] is None
    assert timed["finished_at"] == _at(2) and timed["timing_source"] == "live"


# ── the projection: a test_start in the batch pairs with its result ──────────


class _RecordingDb:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    async def execute(self, statement, params=None):
        self.calls.append((statement, params))
        return SimpleNamespace(scalar_one_or_none=lambda: None)


def _item(index: int, event_type: str, payload: dict) -> dict:
    return {
        "stream_id": f"1-{index}", "event_id": f"e{index:063d}", "batch_id": "b1", "batch_event_count": 3,
        "session_id": "s1", "event_index": index, "event_type": event_type, "payload": payload,
    }


def _case_insert(db: _RecordingDb):
    return next(
        (statement, params) for statement, params in db.calls
        if isinstance(params, list) and params and "test_fingerprint" in params[0]
    )


@pytest.mark.asyncio
async def test_a_test_start_in_the_batch_gives_the_result_its_start():
    db = _RecordingDb()
    evidence = [
        _item(0, "test_start", {"event_type": "test_start", "test_name": "checkout", "class_name": "Shop",
                                "timestamp_ms": _ms(_at(1))}),
        _item(1, "test_result", {"event_type": "test_result", "test_name": "checkout", "class_name": "Shop",
                                 "status": "PASSED", "duration_ms": 2000, "timestamp_ms": _ms(_at(3))}),
        # Another test's result: a start of a DIFFERENT test never pairs with it.
        _item(2, "test_result", {"event_type": "test_result", "test_name": "refund", "class_name": "Shop",
                                 "status": "FAILED", "duration_ms": 500, "timestamp_ms": _ms(_at(4))}),
    ]
    await _upsert_projection_rows(
        db, evidence, run_uuid=RUN, stream_key="k", session_suite="Smoke", now=NOW,
        session_started_at=SESSION_START,
    )

    statement, rows = _case_insert(db)
    by_name = {row["test_name"]: row for row in rows}
    assert by_name["checkout"]["started_at"] == _at(1)
    assert by_name["checkout"]["finished_at"] == _at(3)
    assert by_name["checkout"]["timing_source"] == "live"
    assert by_name["refund"]["started_at"] is None
    assert by_name["refund"]["finished_at"] == _at(4)

    # A re-drained or repeated result updates its timing like every other column (latest result wins).
    sql = str(statement.compile(dialect=postgresql.dialect()))
    for column in LIVE_TIMING_COLUMNS:
        assert f"{column} = excluded.{column}" in sql


@pytest.mark.asyncio
async def test_the_drainer_bounds_the_clock_by_the_runs_server_start(monkeypatch):
    """End to end through ``drain_run_buffer``: the run row's start bounds the live clock."""
    from app.services.live_session_drainer import drain_run_buffer
    from tests.test_live_session_drainer import _FakeRedis, _FakeSession, _patch_targets

    real_now = datetime.now(timezone.utc)
    sane = json.dumps({"test_name": "sane", "status": "PASSED", "timestamp_ms": _ms(real_now - timedelta(seconds=5))})
    skewed = json.dumps({"test_name": "skewed", "status": "PASSED",
                         "timestamp_ms": _ms(real_now + timedelta(hours=6))})
    existing = SimpleNamespace(
        id=RUN, start_time=real_now - timedelta(minutes=1), primary_suite_name=None,
        total_tests=0, end_time=None,
    )
    session = _FakeSession(existing_run=existing)
    _patch_targets(monkeypatch, _FakeRedis(buffer_entries=[sane, skewed]), session, state={"passed": 2, "total": 2})

    result = await drain_run_buffer(run_id="run-timing", project_id=str(uuid.uuid4()))

    assert result["drained"] == 2
    rows = next(params for kind, params in session.executes if kind == "insert" and "test_fingerprint" in params[0])
    by_name = {row["test_name"]: row for row in rows}
    assert by_name["sane"]["finished_at"] is not None and by_name["sane"]["timing_source"] == "live"
    assert by_name["skewed"]["finished_at"] is None and by_name["skewed"]["timing_source"] is None


# ── the wire model ───────────────────────────────────────────────────────────


def test_live_event_accepts_the_timing_fields():
    event = LiveEvent(
        event_type="test_result", test_name="t", status="PASSED", started_at_ms=_ms(_at(1)),
        setup_ms=120, call_ms=1600.4, teardown_ms=80, worker_id=3,
    )
    dumped = event.model_dump()
    assert (dumped["setup_ms"], dumped["call_ms"], dumped["teardown_ms"]) == (120, 1600, 80)
    assert dumped["worker_id"] == "3" and dumped["started_at_ms"] == _ms(_at(1))


def test_an_unusable_timing_value_is_dropped_not_a_422():
    """A bad figure from a buggy SDK must not cost the batch its results."""
    event = LiveEvent(
        event_type="test_result", test_name="t", status="PASSED",
        setup_ms=-1, call_ms="fast", teardown_ms=25 * 60 * 60 * 1000, worker_id={"x": 1}, started_at_ms=True,
    )
    assert event.status == "PASSED"
    assert all(getattr(event, key) is None for key in ("setup_ms", "call_ms", "teardown_ms", "worker_id",
                                                        "started_at_ms"))


def test_an_event_without_timing_dumps_the_keys_it_always_had():
    """The stored payload and the batch-admission digest of an old SDK's event must not change."""
    event = LiveEvent(event_type="test_result", test_name="t", status="PASSED", duration_ms=5, timestamp_ms=1)
    assert sorted(event.model_dump()) == sorted([
        "event_type", "test_name", "status", "duration_ms", "error_message", "stack_trace", "suite_name",
        "class_name", "tags", "timestamp_ms", "metadata",
    ])
