"""EXEC-01/02: the timing contract between report parsers, ingestion and the suite-execution writer.

A parser keeps returning ``list[dict]`` of test results (every existing caller is unchanged). Each result dict MAY
carry the optional timing keys below; a parser that can read more from its format also returns a
:class:`ReportTimings` beside the cases through its ``parse_*_with_timings`` twin.

Nothing here is invented: a key is present only when the report (or live event) said it. ``duration_ms`` keeps its
meaning — the total the framework reported for the test.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, TypedDict

#: Optional per-test keys a parser may add to a result dict (all absent when the format does not carry them).
CASE_TIMING_KEYS: tuple[str, ...] = (
    "started_at",   # datetime (UTC) the test started
    "finished_at",  # datetime (UTC) the test finished
    "setup_ms",     # int, fixture / before-hook time attributed to this test
    "call_ms",      # int, the test body itself
    "teardown_ms",  # int, fixture / after-hook time attributed to this test
    "worker_id",    # str (<= 64 chars), the parallel worker / runner slot that ran it
)

#: Where a test's timing came from (``test_cases.timing_source``).
TIMING_SOURCE_REPORT = "report"   # read from the uploaded report
TIMING_SOURCE_LIVE = "live"       # read from live-stream events
TIMING_SOURCE_DERIVED = "derived"  # computed by TestLookup (e.g. finished = started + duration)


class SuiteTiming(TypedDict, total=False):
    name: str                      # suite name exactly as the cases carry it in ``suite_name``
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    wall_ms: Optional[int]         # the report's own suite time, when it states one
    setup_ms: Optional[int]        # suite-level fixtures (before-all / OneTimeSetUp / suite setup)
    teardown_ms: Optional[int]     # suite-level fixtures (after-all / OneTimeTearDown / suite teardown)


class ReportTimings(TypedDict, total=False):
    run_started_at: Optional[datetime]
    run_finished_at: Optional[datetime]
    run_wall_ms: Optional[int]
    suites: list[SuiteTiming]


def empty_timings() -> ReportTimings:
    return {"run_started_at": None, "run_finished_at": None, "run_wall_ms": None, "suites": []}


def to_utc(value: Any) -> Optional[datetime]:
    """A report timestamp as an aware UTC datetime, or None.

    Accepts an aware/naive ``datetime`` (naive = UTC), ISO-8601 text (``Z`` or an offset), or epoch milliseconds
    (int/float, or digits as text). Anything unreadable is None — never "now".
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _from_epoch_ms(float(value))
    text = str(value).strip()
    if text.isdigit():
        return _from_epoch_ms(float(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _from_epoch_ms(ms: float) -> Optional[datetime]:
    # Seconds-resolution epochs (10 digits) are scaled up; anything before 2000 or absurdly far ahead is refused.
    if ms < 10_000_000_000:
        ms *= 1000
    if not 946_684_800_000 <= ms <= 4_102_444_800_000:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def non_negative_ms(value: Any) -> Optional[int]:
    """A millisecond count as a non-negative int, or None for missing / negative / unreadable values."""
    if value is None or isinstance(value, bool):
        return None
    try:
        ms = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return ms if ms >= 0 else None


def merge_timings(parts: list[ReportTimings]) -> ReportTimings:
    """Combine the timings of several files of one upload (an archive): earliest start, latest finish, all suites.

    ``run_wall_ms`` is recomputed from the merged start/finish when both exist; otherwise it is the sum of the parts'
    stated walls only when every part stated one (files may run in parallel, so a partial sum would mislead).
    """
    merged = empty_timings()
    starts = [p.get("run_started_at") for p in parts if p.get("run_started_at")]
    ends = [p.get("run_finished_at") for p in parts if p.get("run_finished_at")]
    merged["run_started_at"] = min(starts) if starts else None
    merged["run_finished_at"] = max(ends) if ends else None
    if merged["run_started_at"] and merged["run_finished_at"]:
        merged["run_wall_ms"] = max(
            0, int((merged["run_finished_at"] - merged["run_started_at"]).total_seconds() * 1000)
        )
    else:
        walls = [p.get("run_wall_ms") for p in parts]
        merged["run_wall_ms"] = sum(walls) if walls and all(w is not None for w in walls) else None  # type: ignore[misc]
    for part in parts:
        merged["suites"].extend(part.get("suites") or [])
    return merged
