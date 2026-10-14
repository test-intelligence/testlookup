"""EXEC-01: per-test timing survives every entry path, not only file uploads.

* the WebSocket live path forwards the timing fields the HTTP live path already accepts;
* the JSON batch ingest model keeps optional timing instead of dropping it;
* the hand-built test-case detail contract (the test page's Details tab) carries the timing, in the flat fields and
  in the ``execution`` section, and None — never 0 — when a report did not say.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.regression


def test_websocket_events_forward_the_timing_fields():
    from app.services.ws_event_ingest import to_live_event

    event = to_live_event({
        "type": "test_result", "test_name": "t", "status": "PASSED", "duration_ms": 1500,
        "timestamp_ms": 1791991777000, "started_at_ms": 1791991775500,
        "setup_ms": 100, "call_ms": 1300, "teardown_ms": 100, "worker_id": "gw3",
    })
    assert (event.started_at_ms, event.setup_ms, event.call_ms, event.teardown_ms, event.worker_id) == (
        1791991775500, 100, 1300, 100, "gw3",
    )


def test_websocket_events_without_timing_are_unchanged():
    from app.services.ws_event_ingest import to_live_event

    event = to_live_event({"type": "test_result", "test_name": "t", "status": "PASSED"})
    assert (event.started_at_ms, event.setup_ms, event.call_ms, event.teardown_ms, event.worker_id) == (None,) * 5


def test_batch_ingest_keeps_optional_timing():
    from app.models.schemas import IngestTestResult

    item = IngestTestResult(
        test_name="t", status="PASSED", duration_ms=10,
        started_at="2026-10-14T10:00:01Z", finished_at="2026-10-14T10:00:03Z",
        setup_ms=120, call_ms=1600, teardown_ms=80, worker_id="w1",
    )
    dumped = item.model_dump()
    assert dumped["started_at"] == datetime(2026, 10, 14, 10, 0, 1, tzinfo=timezone.utc)
    assert (dumped["setup_ms"], dumped["call_ms"], dumped["teardown_ms"], dumped["worker_id"]) == (120, 1600, 80, "w1")
    plain = IngestTestResult(test_name="t", status="PASSED").model_dump()
    assert (plain["started_at"], plain["setup_ms"]) == (None, None)


def test_batch_ingest_refuses_negative_phase_times():
    from pydantic import ValidationError

    from app.models.schemas import IngestTestResult

    with pytest.raises(ValidationError):
        IngestTestResult(test_name="t", status="PASSED", setup_ms=-1)


def _case(**timing):
    # The same shape the rich-detail contract tests use (tests/test_rich_test_case_detail.py).
    base = dict(
        id=uuid.uuid4(), test_run_id=uuid.uuid4(), canonical_test_case_id=uuid.uuid4(),
        test_fingerprint="fp", test_name="adds", full_name=None,
        suite_name="Cart", class_name="CartTest", package_name=None, status="PASSED",
        duration_ms=1600, severity=None, feature=None, story=None, epic=None, owner=None,
        tags=[], failure_category=None, error_message=None, minio_s3_prefix=None,
        has_attachments=False, step_count=0, steps_present=False, retry_count=0,
        is_flaky_run=False, stack_trace=None, source_uuid=None,
        source_history_id=None, source_test_case_id=None, parser_format="testng",
        parser_version=None, created_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
    )
    base.update(timing)
    return SimpleNamespace(**base)


def test_rich_detail_carries_timing_flat_and_in_execution():
    from app.services.runs_service import build_enriched_test_case_detail

    started = datetime(2026, 10, 14, 10, 0, 1, tzinfo=timezone.utc)
    detail = build_enriched_test_case_detail(
        _case(started_at=started, finished_at=None, setup_ms=120, call_ms=1600, teardown_ms=80,
              worker_id="w1", timing_source="report"),
        {"steps": [], "attachments": []},
    )
    for section in (detail, detail["execution"]):
        assert section["started_at"] == started.isoformat()
        assert (section["setup_ms"], section["call_ms"], section["teardown_ms"]) == (120, 1600, 80)
        assert (section["worker_id"], section["timing_source"]) == ("w1", "report")


def test_rich_detail_reports_unknown_timing_as_none_not_zero():
    from app.services.runs_service import build_enriched_test_case_detail

    detail = build_enriched_test_case_detail(_case(), {"steps": [], "attachments": []})
    for key in ("started_at", "finished_at", "setup_ms", "call_ms", "teardown_ms", "worker_id", "timing_source"):
        assert detail[key] is None and detail["execution"][key] is None, key
