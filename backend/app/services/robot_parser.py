"""Robot Framework ``output.xml`` parser.

Robot's canonical result artifact is ``output.xml`` (root element ``<robot>``)
with arbitrarily nested ``<suite>`` elements, each containing ``<test>``
elements. A test's outcome lives on its child ``<status>`` element:

.. code-block:: xml

    <robot generator="Robot 6.1.1 (Python 3.11)" generated="...">
      <suite name="Regression" source="/tests">
        <suite name="Login">
          <test name="Valid Login">
            <kw name="Open Browser">
              <msg timestamp="..." level="INFO">Opening browser</msg>
              <status status="PASS" starttime="20260709 10:00:00.000"
                      endtime="20260709 10:00:01.250"/>
            </kw>
            <tag>smoke</tag>
            <status status="FAIL" starttime="..." endtime="...">
              Element 'id=dashboard' not visible after 5 seconds.
            </status>
          </test>
          <status status="FAIL" .../>
        </suite>
      </suite>
      <statistics>...</statistics>
    </robot>

Timing varies by Robot major version and BOTH shapes must parse:

* RF 3–6: ``starttime``/``endtime`` attributes in ``%Y%m%d %H:%M:%S.%f``.
* RF 7+:  ``start`` (ISO 8601) + ``elapsed`` (float seconds) attributes.

Status vocabulary: Robot emits ``PASS`` / ``FAIL`` / ``SKIP`` (RF4+) /
``NOT RUN``. Mapping to the TestLookup vocab:

* ``PASS``    → ``PASSED``
* ``FAIL``    → ``FAILED`` (the ``<status>`` body text is the failure message)
* ``SKIP``    → ``SKIPPED`` (body text preserved as the skip reason)
* ``NOT RUN`` → ``SKIPPED`` (dry-run / skipped-by-failure teardown artifacts)

Steps: Robot has a real keyword tree — the finest granularity of any format we
ingest — but persisting every nested keyword would explode ``test_steps`` on
keyword-heavy suites. We surface the test's DIRECT child keywords only (bounded
by ``_MAX_STEPS``), each as one common-shape step carrying its own status
(keyword FAIL → step ``FAILED``; the shared FAILED-vs-BROKEN vocab does not
apply here because Robot does not distinguish assertion vs error at the
keyword level). Step status vocab MUST stay within the strict TestLookup set
(PASSED/FAILED/SKIPPED/BROKEN/UNKNOWN) — the granular ``test_steps.status``
column is fronted by a strict enum that silently 422s reads on drift.

Malformed input returns an empty list rather than raising — ingestion tasks
are fire-and-forget; one bad upload must not wedge the worker.

Timing (EXEC-01):

* Test start / finish from its ``<status>``: RF 3–6 ``starttime`` / ``endtime``
  (``timing_source='report'``); RF 7 ``start`` + ``elapsed`` (the finish is
  computed, so ``derived``). Robot writes the runner's LOCAL time with no
  zone; it is stored as UTC and flagged ``source_extensions.timing_tz =
  'assumed_utc'`` so a skewed zone can be found.
* Setup / teardown: RF 3–6 ``<kw type="setup|teardown">`` (any case), RF 7
  ``<setup>`` / ``<teardown>`` elements. When a test has either, ``setup_ms`` /
  ``teardown_ms`` are their durations (0 for the one it lacks) and ``call_ms``
  = duration − both.
* :func:`parse_robot_xml_with_timings` returns the top-level suite span as the
  run timing and, for every suite that holds tests, its span and its own
  suite setup / teardown.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import defusedxml.ElementTree as ET

from app.services.parser_timing import set_case_timing
from app.services.report_timings import (
    TIMING_SOURCE_DERIVED,
    TIMING_SOURCE_REPORT,
    ReportTimings,
    add_suite_timing,
    empty_timings,
    flag_assumed_utc,
    to_utc,
    wall_ms_between,
)

logger = logging.getLogger(__name__)

_MAX_STEPS = 50
_MAX_TRACE_CHARS = 8000
_MAX_MESSAGE_CHARS = 2000

# Robot → TestLookup status mapping (uppercase inputs; emit uppercase — the
# ingestion status_map lowercases on persist).
_STATUS_MAP = {
    "PASS": "PASSED",
    "FAIL": "FAILED",
    "SKIP": "SKIPPED",
    "NOT RUN": "SKIPPED",
}

# RF 3–6 timestamp attribute format.
_RF_TIME_FORMAT = "%Y%m%d %H:%M:%S.%f"


def _clean(text: Optional[str]) -> str:
    return (text or "").strip()


def _parse_rf_timestamp(value: Optional[str]) -> Optional[datetime]:
    value = _clean(value)
    if not value:
        return None
    try:
        return datetime.strptime(value, _RF_TIME_FORMAT)
    except ValueError:
        # RF 7 emits ISO 8601 in ``start``; tolerate it here too.
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


def _status_duration_ms(status_el) -> Optional[int]:
    """Duration from a ``<status>`` element, handling RF3–6 AND RF7 shapes."""
    if status_el is None:
        return None
    # RF 7+: ``elapsed`` is float seconds.
    elapsed = status_el.get("elapsed")
    if elapsed is not None:
        try:
            return int(float(elapsed) * 1000)
        except ValueError:
            return None
    # RF 3–6: starttime/endtime pair.
    start = _parse_rf_timestamp(status_el.get("starttime") or status_el.get("start"))
    end = _parse_rf_timestamp(status_el.get("endtime"))
    if start is not None and end is not None:
        return int((end - start).total_seconds() * 1000)
    return None


def _status_span(status_el) -> Tuple[Optional[datetime], Optional[datetime], bool, bool]:
    """(start, end, end_is_derived, naive) of a ``<status>`` element, as UTC datetimes.

    RF 7 states ``start`` + ``elapsed`` (the end is computed); RF 3–6 states ``starttime`` + ``endtime``.
    ``naive`` says the report gave no zone, so UTC was assumed.
    """
    if status_el is None:
        return None, None, False, False
    elapsed = status_el.get("elapsed")
    raw_start = _parse_rf_timestamp(status_el.get("starttime") or status_el.get("start"))
    naive = raw_start is not None and raw_start.tzinfo is None
    start = to_utc(raw_start)
    if elapsed is not None:
        try:
            seconds = float(elapsed)
        except ValueError:
            return start, None, False, naive
        if start is None or seconds < 0:
            return start, None, False, naive
        return start, start + timedelta(seconds=seconds), True, naive
    raw_end = _parse_rf_timestamp(status_el.get("endtime"))
    naive = naive or (raw_end is not None and raw_end.tzinfo is None)
    return start, to_utc(raw_end), False, naive


def _fixture_ms(owner_el, kind: str) -> Optional[int]:
    """Duration of the ``kind`` (``setup`` / ``teardown``) fixture directly under a test or suite.

    RF 7 writes ``<setup>`` / ``<teardown>`` elements; RF 3–6 writes ``<kw type="setup">`` (lowercase in RF 3,
    uppercase from RF 4). None when the element has no such fixture.
    """
    el = owner_el.find(kind)
    if el is None:
        el = next((kw for kw in owner_el.findall("kw") if (kw.get("type") or "").lower() == kind), None)
    if el is None:
        return None
    duration = _status_duration_ms(el.find("status"))
    return duration if duration is not None and duration >= 0 else None


def _map_status(raw: Optional[str]) -> str:
    status = _STATUS_MAP.get((raw or "").strip().upper())
    if status is None:
        logger.debug("Unknown Robot status '%s' — defaulting to UNKNOWN", raw)
        return "UNKNOWN"
    return status


def _keyword_steps(test_el) -> List[dict]:
    """Surface the test's DIRECT child keywords as bounded common-shape steps.

    Each keyword carries its own ``<status>``; a FAIL keyword's status body (or
    its last ERROR/FAIL ``<msg>``) becomes the step's assertion message. Nested
    keywords are intentionally NOT descended into (bounded persistence).
    """
    steps: List[dict] = []
    for kw in test_el.findall("kw"):
        if len(steps) >= _MAX_STEPS:
            break
        kw_status_el = kw.find("status")
        kw_status = _map_status(kw_status_el.get("status") if kw_status_el is not None else None)
        # NOT RUN keywords after a failure are noise at the step level.
        if kw_status_el is not None and (kw_status_el.get("status") or "").upper() == "NOT RUN":
            continue

        message = None
        trace = None
        if kw_status is not None and kw_status_el is not None:
            body = _clean(kw_status_el.text)
            if body:
                message = body[:_MAX_MESSAGE_CHARS]
        # Failing keywords: collect FAIL/ERROR level messages as the trace.
        if kw_status == "FAILED":
            fail_msgs = [
                _clean(m.text)
                for m in kw.findall("msg")
                if (m.get("level") or "").upper() in ("FAIL", "ERROR") and _clean(m.text)
            ]
            if fail_msgs:
                trace = "\n".join(fail_msgs)[:_MAX_TRACE_CHARS]
                if not message:
                    message = fail_msgs[-1][:_MAX_MESSAGE_CHARS]

        steps.append({
            "name": kw.get("name") or "keyword",
            "keyword": kw.get("type") or "kw",
            "status": kw_status,
            "start_ms": None,
            "duration_ms": _status_duration_ms(kw_status_el),
            "assertion_message": message,
            "assertion_trace": trace,
            "expected": None,
            "actual": None,
            "parameters": [],
            "attachments": [],
            "steps": [],
        })
    return steps


def _collect_tags(test_el) -> List[str]:
    """Tags: RF4+ puts ``<tag>`` directly under ``<test>``; RF3 nests them
    under a ``<tags>`` wrapper. Handle both."""
    tags = [_clean(t.text) for t in test_el.findall("tag")]
    wrapper = test_el.find("tags")
    if wrapper is not None:
        tags.extend(_clean(t.text) for t in wrapper.findall("tag"))
    return [t for t in tags if t]


def _attach_test_timing(case: dict, test_el, status_el) -> None:
    started, finished, end_derived, naive = _status_span(status_el)
    setup = _fixture_ms(test_el, "setup")
    teardown = _fixture_ms(test_el, "teardown")
    call: Optional[int] = None
    if setup is not None or teardown is not None:
        setup, teardown = setup or 0, teardown or 0
        duration = case.get("duration_ms")
        if duration is not None:
            call = max(0, duration - setup - teardown)
    set_case_timing(
        case,
        started_at=started,
        finished_at=finished,
        setup_ms=setup,
        call_ms=call,
        teardown_ms=teardown,
        source=TIMING_SOURCE_DERIVED if end_derived else TIMING_SOURCE_REPORT,
    )
    if naive and (started is not None or finished is not None):
        flag_assumed_utc(case)


def _walk_suites(
    suite_el, parents: tuple, run_id: str, out: List[dict], timings: Optional[ReportTimings] = None,
) -> None:
    name = suite_el.get("name") or "Unknown Suite"
    path = parents + (name,)
    suite_name = " > ".join(path)

    tests = suite_el.findall("test")
    if tests and timings is not None:
        started, finished, _, _ = _status_span(suite_el.find("status"))
        add_suite_timing(timings, {
            "name": suite_name,
            "started_at": started,
            "finished_at": finished,
            "wall_ms": wall_ms_between(started, finished),
            "setup_ms": _fixture_ms(suite_el, "setup"),
            "teardown_ms": _fixture_ms(suite_el, "teardown"),
        })

    for test_el in tests:
        test_name = test_el.get("name") or "Unknown"
        status_el = test_el.find("status")
        raw_status = status_el.get("status") if status_el is not None else None
        status = _map_status(raw_status)

        # The test <status> body text is the failure message (FAIL) or the
        # skip reason (SKIP). Robot has no separate stack trace at test level.
        body = _clean(status_el.text) if status_el is not None else ""
        error_message = body[:_MAX_MESSAGE_CHARS] if body and status != "PASSED" else None

        # For failures, aggregate failing keywords' FAIL/ERROR messages as a
        # coarse trace so triage sees where inside the test it blew up.
        stack_trace = None
        if status == "FAILED":
            fail_lines = []
            for kw in test_el.findall("kw"):
                kw_status_el = kw.find("status")
                if kw_status_el is not None and (kw_status_el.get("status") or "").upper() == "FAIL":
                    kw_body = _clean(kw_status_el.text)
                    fail_lines.append(f"{kw.get('name') or 'keyword'}: {kw_body or 'FAIL'}")
            if fail_lines:
                stack_trace = "\n".join(fail_lines)[:_MAX_TRACE_CHARS]

        case = {
            "test_run_id": run_id,
            "test_name": test_name,
            "full_name": f"{suite_name}.{test_name}",
            "suite_name": suite_name,
            "class_name": None,
            "package_name": None,
            "status": status,
            "duration_ms": _status_duration_ms(status_el),
            "error_message": error_message,
            "stack_trace": stack_trace,
            "attachments": [],
            "tags": _collect_tags(test_el),
            "steps": _keyword_steps(test_el),
            "framework": "robot",
        }
        _attach_test_timing(case, test_el, status_el)
        out.append(case)

    for child in suite_el.findall("suite"):
        _walk_suites(child, path, run_id, out, timings)


def parse_robot_xml(content: str, test_run_id: str) -> List[dict]:
    """Parse a Robot Framework ``output.xml`` into normalized test case dicts.

    Returns the same shape produced by ``cypress_parser``/``testng_parser`` so
    the downstream ``_upsert_test_case`` pipeline is unchanged. Malformed input
    logs and returns ``[]``.
    """
    return _parse(content, test_run_id)[0]


def parse_robot_xml_with_timings(content: str, test_run_id: str) -> Tuple[List[dict], ReportTimings]:
    """The cases of :func:`parse_robot_xml` plus the run (top suite) and per-suite timing."""
    return _parse(content, test_run_id)


def _parse(content: str, test_run_id: str) -> Tuple[List[dict], ReportTimings]:
    results: List[dict] = []
    try:
        root = ET.fromstring(content)
    except Exception as exc:
        logger.warning("Failed to parse Robot Framework XML: %s", exc)
        return results, empty_timings()

    if root.tag != "robot":
        logger.warning("Robot XML root must be <robot>, got <%s>", root.tag)
        return results, empty_timings()

    timings = empty_timings()
    starts: List[datetime] = []
    ends: List[datetime] = []
    for suite in root.findall("suite"):
        _walk_suites(suite, (), test_run_id, results, timings)
        started, finished, _, _ = _status_span(suite.find("status"))
        if started is not None:
            starts.append(started)
        if finished is not None:
            ends.append(finished)

    run_started = min(starts) if starts else None
    run_finished = max(ends) if ends else None
    timings["run_started_at"] = run_started
    timings["run_finished_at"] = run_finished
    timings["run_wall_ms"] = wall_ms_between(run_started, run_finished)
    return results, timings
