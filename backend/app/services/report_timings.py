"""EXEC-01/02: the timing contract between report parsers, ingestion and the suite-execution writer.

A parser keeps returning ``list[dict]`` of test results (every existing caller is unchanged). Each result dict MAY
carry the optional timing keys below; a parser that can read more from its format also returns a
:class:`ReportTimings` beside the cases through its ``parse_*_with_timings`` twin.

Nothing here is invented: a key is present only when the report (or live event) said it. ``duration_ms`` keeps its
meaning — the total the framework reported for the test.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
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
    starts = [start for p in parts if (start := p.get("run_started_at")) is not None]
    ends = [end for p in parts if (end := p.get("run_finished_at")) is not None]
    run_started = min(starts) if starts else None
    run_finished = max(ends) if ends else None
    merged["run_started_at"] = run_started
    merged["run_finished_at"] = run_finished
    if run_started is not None and run_finished is not None:
        merged["run_wall_ms"] = max(0, int((run_finished - run_started).total_seconds() * 1000))
    else:
        walls = [p.get("run_wall_ms") for p in parts]
        stated = [wall for wall in walls if wall is not None]
        merged["run_wall_ms"] = sum(stated) if walls and len(stated) == len(walls) else None
    for part in parts:
        merged["suites"].extend(part.get("suites") or [])
    return merged


# ── Reading report clocks, and turning a result's timing keys into column values (EXEC-01) ──────────────────────

#: Every value ``test_cases.timing_source`` may hold.
TIMING_SOURCES: frozenset[str] = frozenset({TIMING_SOURCE_REPORT, TIMING_SOURCE_LIVE, TIMING_SOURCE_DERIVED})

#: Optional result-dict key naming where the timing came from. A parser sets it to ``TIMING_SOURCE_DERIVED`` when it
#: computed part of the timing itself (``finished_at`` = ``started_at`` + ``duration_ms``); absent, a result that
#: carries any timing key is ``TIMING_SOURCE_REPORT``.
CASE_TIMING_SOURCE_KEY = "timing_source"

#: ``source_extensions["timing_tz"]`` when the report's clock had no zone and UTC was assumed.
TIMING_TZ_KEY = "timing_tz"
TIMING_TZ_ASSUMED_UTC = "assumed_utc"

#: One phase (setup / call / teardown) longer than a day is a unit mix-up, not a measurement.
MAX_PHASE_MS = 24 * 60 * 60 * 1000

#: ``test_cases.worker_id`` is VARCHAR(64).
WORKER_ID_MAX_CHARS = 64

# A trailing zone word after the clock, as TestNG writes it ("2026-10-14T10:00:01 UTC", "... IST").
_ZONE_WORD = re.compile(r"\s+([A-Za-z]{1,5})$")
_UTC_WORDS = frozenset({"UTC", "GMT", "UT", "Z"})
# The same plausibility window ``_from_epoch_ms`` applies: 2000-01-01 .. 2100-01-01 (UTC).
_EARLIEST = datetime(2000, 1, 1, tzinfo=timezone.utc)
_LATEST = datetime(2100, 1, 1, tzinfo=timezone.utc)


def parse_report_time(value: Any) -> tuple[Optional[datetime], bool]:
    """``(utc, assumed_utc)`` for a report timestamp: like :func:`to_utc`, and says whether UTC was assumed.

    ``assumed_utc`` is True when the text carried no zone (Robot, NUnit or JUnit clocks without ``Z``) or a zone
    abbreviation that cannot be resolved without guessing (TestNG writes its JVM's zone, e.g. ``IST``, which names
    three different offsets). The wall time is then kept and read as UTC; callers flag the row with
    ``source_extensions.timing_tz = "assumed_utc"``. ``UTC``/``GMT``/``Z`` words and numeric offsets are exact.
    A clock outside 2000..2100 (TRX can write ``0001-01-01`` for a test that never ran) is None, never "now".
    """
    if value is None or value == "" or isinstance(value, bool):
        return None, False
    if isinstance(value, datetime):
        return to_utc(value), value.tzinfo is None
    if isinstance(value, (int, float)):
        return to_utc(value), False  # an epoch is UTC by definition
    text = str(value).strip()
    if not text:
        return None, False
    if text.isdigit():
        return to_utc(text), False
    assumed = False
    zone = _ZONE_WORD.search(text)
    if zone:
        text = text[: zone.start()]
        if zone.group(1).upper() in _UTC_WORDS:
            text += "+00:00"
        else:
            assumed = True
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None, False
    if parsed.tzinfo is None:
        parsed, assumed = parsed.replace(tzinfo=timezone.utc), True
    else:
        parsed = parsed.astimezone(timezone.utc)
    if not _EARLIEST <= parsed < _LATEST:
        return None, False
    return parsed, assumed


def seconds_to_ms(value: Any) -> Optional[int]:
    """A report's float-seconds figure (``time="1.234"``) as non-negative milliseconds, or None."""
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        return None
    try:
        return non_negative_ms(float(str(value).strip()) * 1000)
    except (TypeError, ValueError):
        return None


def finish_from_duration(started_at: Optional[datetime], duration_ms: Any) -> Optional[datetime]:
    """``started_at + duration_ms`` (the ``derived`` finish), or None when either is unknown."""
    ms = non_negative_ms(duration_ms)
    if started_at is None or ms is None:
        return None
    return started_at + timedelta(milliseconds=ms)


def wall_ms_between(started_at: Optional[datetime], finished_at: Optional[datetime]) -> Optional[int]:
    """Milliseconds from ``started_at`` to ``finished_at``; None when either is unknown or the finish comes first."""
    if started_at is None or finished_at is None or finished_at < started_at:
        return None
    return int(round((finished_at - started_at).total_seconds() * 1000))


def flag_assumed_utc(case: dict) -> None:
    """Mark a result whose clock had no zone: ``source_extensions.timing_tz = "assumed_utc"`` (merged, not replaced)."""
    extensions = case.get("source_extensions")
    extensions = dict(extensions) if isinstance(extensions, Mapping) else {}
    extensions[TIMING_TZ_KEY] = TIMING_TZ_ASSUMED_UTC
    case["source_extensions"] = extensions


def apply_case_clock(case: dict, started_raw: Any, finished_raw: Any = None) -> None:
    """Add ``started_at`` / ``finished_at`` to a result dict from the report's own clock values.

    Nothing is added when the report gave neither. With a start and no finish, the finish is ``started_at +
    duration_ms`` and the result is marked ``timing_source = derived``. A zone-less clock flags the result
    ``source_extensions.timing_tz = "assumed_utc"`` (see :func:`parse_report_time`).
    """
    started, start_assumed = parse_report_time(started_raw)
    finished, finish_assumed = parse_report_time(finished_raw)
    if started is None and finished is None:
        return
    if started is not None:
        case["started_at"] = started
    if finished is not None:
        case["finished_at"] = finished
    else:
        derived = finish_from_duration(started, case.get("duration_ms"))
        if derived is not None:
            case["finished_at"] = derived
            case[CASE_TIMING_SOURCE_KEY] = TIMING_SOURCE_DERIVED
    if (started is not None and start_assumed) or (finished is not None and finish_assumed):
        flag_assumed_utc(case)


_SUITE_VALUE_KEYS = ("started_at", "finished_at", "wall_ms", "setup_ms", "teardown_ms")


def add_suite_timing(timings: ReportTimings, suite: SuiteTiming) -> None:
    """Add one suite's timing to ``timings["suites"]``; a suite that says nothing is not added.

    One file can name a suite twice (two ``<testsuite name="pytest">``, two NUnit fixtures of one name): the second
    is merged into the first, so a file yields one entry per name. The merged span is the earliest start to the
    latest finish; the merged wall is that span when both ends are known, else the sum of both walls when both are
    known, else unknown (a partial sum would understate it). Fixture times add up.
    """
    if not any(suite.get(key) is not None for key in _SUITE_VALUE_KEYS):
        return
    suites = timings.setdefault("suites", [])
    for existing in suites:
        if existing.get("name") == suite.get("name"):
            _merge_suite(existing, suite)
            return
    suites.append({
        "name": suite.get("name") or "",
        "started_at": suite.get("started_at"),
        "finished_at": suite.get("finished_at"),
        "wall_ms": suite.get("wall_ms"),
        "setup_ms": suite.get("setup_ms"),
        "teardown_ms": suite.get("teardown_ms"),
    })


def _sum_known(*values: Optional[int]) -> Optional[int]:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _merge_suite(into: SuiteTiming, other: SuiteTiming) -> None:
    starts = [s for s in (into.get("started_at"), other.get("started_at")) if s is not None]
    ends = [e for e in (into.get("finished_at"), other.get("finished_at")) if e is not None]
    wall_a, wall_b = into.get("wall_ms"), other.get("wall_ms")
    into["started_at"] = min(starts) if starts else None
    into["finished_at"] = max(ends) if ends else None
    span = wall_ms_between(into["started_at"], into["finished_at"])
    if span is not None and len(starts) == 2 and len(ends) == 2:
        into["wall_ms"] = span
    else:
        into["wall_ms"] = wall_a + wall_b if wall_a is not None and wall_b is not None else None
    into["setup_ms"] = _sum_known(into.get("setup_ms"), other.get("setup_ms"))
    into["teardown_ms"] = _sum_known(into.get("teardown_ms"), other.get("teardown_ms"))


def _phase_ms(value: Any) -> Optional[int]:
    ms = non_negative_ms(value)
    return ms if ms is not None and ms <= MAX_PHASE_MS else None


def case_timing_columns(case: Mapping[str, Any]) -> dict[str, Any]:
    """The seven ``test_cases`` timing columns for one result dict; every key present, None when unknown.

    Values are bounded the way the columns need them: timestamps as aware UTC (a finish before the start is
    dropped, the start kept), phases 0..24 h, ``worker_id`` at most 64 characters. ``timing_source`` is the result's
    own valid value, else ``report`` when any timing value survived, else None: a row with no timing claims no source.
    """
    started = to_utc(case.get("started_at"))
    finished = to_utc(case.get("finished_at"))
    if started is not None and finished is not None and finished < started:
        finished = None
    worker = case.get("worker_id")
    worker_text = str(worker).strip()[:WORKER_ID_MAX_CHARS] if worker is not None else ""
    columns: dict[str, Any] = {
        "started_at": started,
        "finished_at": finished,
        "setup_ms": _phase_ms(case.get("setup_ms")),
        "call_ms": _phase_ms(case.get("call_ms")),
        "teardown_ms": _phase_ms(case.get("teardown_ms")),
        "worker_id": worker_text or None,
    }
    if all(value is None for value in columns.values()):
        columns[CASE_TIMING_SOURCE_KEY] = None
    else:
        source = case.get(CASE_TIMING_SOURCE_KEY)
        columns[CASE_TIMING_SOURCE_KEY] = source if source in TIMING_SOURCES else TIMING_SOURCE_REPORT
    return columns
