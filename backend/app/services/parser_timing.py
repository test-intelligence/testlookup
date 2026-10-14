"""EXEC-01: two helpers the JSON / Robot report parsers share to put timing on their cases.

Builds on the contract in :mod:`app.services.report_timings` (key names, UTC parsing, millisecond bounds, suite
merging). Nothing here invents a value: :func:`set_case_timing` leaves a key out of the case dict when it has no
value, so a format that carries no timing produces cases with no timing keys at all.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.services.report_timings import (
    CASE_TIMING_SOURCE_KEY,
    TIMING_SOURCE_REPORT,
    WORKER_ID_MAX_CHARS,
    to_utc,
)


def set_case_timing(
    case: dict,
    *,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
    setup_ms: Optional[int] = None,
    call_ms: Optional[int] = None,
    teardown_ms: Optional[int] = None,
    worker_id: Any = None,
    source: str = TIMING_SOURCE_REPORT,
) -> dict:
    """Add each timing key that has a value; stamp ``timing_source`` when at least one did.

    ``source`` is ``derived`` when the parser computed part of the timing (a finish from start + duration, a call
    time from duration − hooks, a fixture attributed through an Allure container), else ``report``.
    """
    present = False
    for key, value in (
        ("started_at", started_at),
        ("finished_at", finished_at),
        ("setup_ms", setup_ms),
        ("call_ms", call_ms),
        ("teardown_ms", teardown_ms),
    ):
        if value is not None:
            case[key] = value
            present = True
    if worker_id is not None and not isinstance(worker_id, bool):
        text = str(worker_id).strip()[:WORKER_ID_MAX_CHARS]
        if text:
            case["worker_id"] = text
            present = True
    if present:
        case[CASE_TIMING_SOURCE_KEY] = source
    return case


def epoch_ms(value: Any) -> Optional[int]:
    """A report timestamp (ISO text, epoch, datetime) as epoch milliseconds — the unit of ``test_steps.start_ms``."""
    moment = to_utc(value)
    if moment is None:
        return None
    return int(round(moment.timestamp() * 1000))
