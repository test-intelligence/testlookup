"""NUnit3 XML result parser (``nunit3-console`` / ``dotnet test --logger:nunit``).

NUnit3's result file is rooted at ``<test-run>`` with arbitrarily nested
``<test-suite>`` elements (``type`` attribute: Assembly / TestSuite /
TestFixture / ParameterizedMethod / ...) and leaf ``<test-case>`` elements:

.. code-block:: xml

    <test-run id="2" testcasecount="4" result="Failed">
      <test-suite type="Assembly" name="Tests.dll" fullname="C:/ci/Tests.dll">
        <test-suite type="TestSuite" name="MyApp" fullname="MyApp">
          <test-suite type="TestFixture" name="CalculatorTests"
                      fullname="MyApp.CalculatorTests">
            <test-case name="Adds" fullname="MyApp.CalculatorTests.Adds"
                       classname="MyApp.CalculatorTests" methodname="Adds"
                       result="Passed" duration="0.052">
              <properties><property name="Category" value="smoke"/></properties>
            </test-case>
            <test-case name="Divides" result="Failed" label="Error" duration="0.010">
              <failure>
                <message><![CDATA[System.DivideByZeroException: ...]]></message>
                <stack-trace><![CDATA[at MyApp.Calculator.Divide(...)]]></stack-trace>
              </failure>
            </test-case>
          </test-suite>
        </test-suite>
      </test-suite>
    </test-run>

Key nuances:

* ``duration`` is **float seconds** — converted to ms here.
* ``result="Failed"`` alone means an assertion failure → ``FAILED``. A
  ``label="Error"`` on a Failed case marks an *unexpected exception* (setup
  blow-up, unhandled throw) → ``BROKEN`` — the same FAILED-vs-BROKEN
  vocabulary the TestNG parser preserves for ``<failure>`` vs ``<error>``.
* ``result="Inconclusive"`` → ``SKIPPED`` (reason recorded as "inconclusive")
  — an inconclusive verdict carries no pass/fail signal.
* ``<reason><message>`` carries the skip/ignore reason; ``<failure>`` carries
  ``<message>`` + ``<stack-trace>`` children (not attributes like JUnit).
* Suite name = the nearest **TestFixture** ancestor's ``fullname`` (already a
  natural dotted path), falling back to the assembly name. Parameterized
  test-cases are plain leaf ``<test-case>`` nodes nested one suite deeper —
  the fixture context still applies.
* ``Category`` properties (and legacy ``<categories><category>``) → tags.

Steps: NUnit XML has no native step hierarchy, so — like the TestNG parser —
one *outcome* pseudo-step in the common cross-framework shape is emitted for
non-passing cases, preserving FAILED-vs-BROKEN into the step status. Step
status vocab MUST stay within the strict TestLookup set (PASSED/FAILED/
SKIPPED/BROKEN/UNKNOWN) — the granular ``test_steps.status`` column is
fronted by a strict enum that silently 422s reads on drift.

Malformed input returns an empty list rather than raising — ingestion tasks
are fire-and-forget; one bad upload must not wedge the worker.

Timing (EXEC-01, contract in ``report_timings``): each ``<test-case>``'s
``start-time`` / ``end-time`` become ``started_at`` / ``finished_at`` (NUnit
writes UTC with a ``Z``; a clock without one is read as UTC and flagged
``timing_tz=assumed_utc``). NUnit reports no per-test setup/teardown split. The
suite timing is the ``start-time`` / ``end-time`` / ``duration`` of the element
that names the cases' suite (the TestFixture, else the assembly); the run timing
is ``<test-run>``'s. ``parse_nunit_xml`` keeps returning ``list[dict]``;
``parse_nunit_xml_with_timings`` also returns the ``ReportTimings``.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import defusedxml.ElementTree as ET

from app.services.report_timings import (
    ReportTimings,
    add_suite_timing,
    apply_case_clock,
    empty_timings,
    parse_report_time,
    seconds_to_ms,
)

logger = logging.getLogger(__name__)

_MAX_TRACE_CHARS = 8000
_MAX_MESSAGE_CHARS = 2000


def _clean(text: Optional[str]) -> str:
    return (text or "").strip()


def _duration_ms(value: Optional[str]) -> Optional[int]:
    """NUnit ``duration`` is float seconds."""
    value = _clean(value)
    if not value:
        return None
    try:
        return int(float(value) * 1000)
    except ValueError:
        return None


def _map_status(result: Optional[str], label: Optional[str]) -> str:
    """Map NUnit3 ``result`` (+ ``label``) to the TestLookup status vocab.

    ``label="Error"`` on a Failed case = an unexpected exception rather than
    an assertion failure → BROKEN (FAILED-vs-BROKEN vocab discipline).
    """
    raw = _clean(result).lower()
    if raw == "passed":
        return "PASSED"
    if raw == "failed":
        if _clean(label).lower() in ("error", "cancelled"):
            return "BROKEN"
        return "FAILED"
    if raw == "skipped":
        return "SKIPPED"
    if raw == "inconclusive":
        return "SKIPPED"
    logger.debug("Unknown NUnit result '%s' — defaulting to UNKNOWN", result)
    return "UNKNOWN"


def _collect_tags(case_el) -> List[str]:
    """Category properties + legacy ``<categories>`` blocks → tags."""
    tags: List[str] = []
    props = case_el.find("properties")
    if props is not None:
        for prop in props.findall("property"):
            if _clean(prop.get("name")).lower() == "category" and _clean(prop.get("value")):
                tags.append(_clean(prop.get("value")))
    categories = case_el.find("categories")
    if categories is not None:
        for cat in categories.findall("category"):
            if _clean(cat.get("name")):
                tags.append(_clean(cat.get("name")))
    return tags


def _outcome_step(status: str, keyword: str, message: Optional[str], trace: Optional[str]) -> dict:
    """The single outcome pseudo-step for a non-passing case (common shape)."""
    return {
        "name": keyword,
        "keyword": keyword,
        "status": status,
        "start_ms": None,
        "duration_ms": None,
        "assertion_message": message,
        "assertion_trace": trace,
        "expected": None,
        "actual": None,
        "parameters": [],
        "attachments": [],
        "steps": [],
    }


def _case_error(case_el, status: str) -> tuple:
    """(error_message, stack_trace) for a test-case, by verdict shape."""
    failure = case_el.find("failure")
    if failure is not None:
        msg_el = failure.find("message")
        trace_el = failure.find("stack-trace")
        message = _clean(msg_el.text if msg_el is not None else None)[:_MAX_MESSAGE_CHARS] or None
        trace = _clean(trace_el.text if trace_el is not None else None)[:_MAX_TRACE_CHARS] or None
        return message, trace
    reason = case_el.find("reason")
    if reason is not None and status != "PASSED":
        msg_el = reason.find("message")
        message = _clean(msg_el.text if msg_el is not None else reason.text)[:_MAX_MESSAGE_CHARS] or None
        return message, None
    if status == "SKIPPED" and _clean(case_el.get("result")).lower() == "inconclusive":
        return "inconclusive", None
    return None, None


def _walk_suites(
    suite_el, run_id: str, out: List[dict],
    assembly_name: Optional[str], fixture_fullname: Optional[str],
    suite_sources: Optional[Dict[str, list]] = None,
    named_by=None,
) -> None:
    """Collect the ``<test-case>`` leaves under ``suite_el`` into ``out``.

    ``suite_sources`` (when given) maps each ``suite_name`` handed to a case to
    the ``<test-suite>`` element(s) that named it, so their timing can become
    that suite's; ``named_by`` is the element behind the current name.
    """
    suite_type = _clean(suite_el.get("type")).lower()
    if suite_type == "assembly":
        # Assembly name attr is the dll basename; fullname is the full path.
        if _clean(suite_el.get("name")):
            assembly_name = _clean(suite_el.get("name"))
            if not fixture_fullname:
                named_by = suite_el
    elif suite_type == "testfixture":
        own_name = _clean(suite_el.get("fullname")) or _clean(suite_el.get("name"))
        if own_name:
            fixture_fullname = own_name
            named_by = suite_el

    for case_el in suite_el.findall("test-case"):
        name = case_el.get("name") or "Unknown"
        fullname = _clean(case_el.get("fullname"))
        classname = _clean(case_el.get("classname")) or None
        status = _map_status(case_el.get("result"), case_el.get("label"))
        error_message, stack_trace = _case_error(case_el, status)

        steps: List[dict] = []
        if status in ("FAILED", "BROKEN"):
            steps.append(_outcome_step(
                status, "error" if status == "BROKEN" else "failure",
                error_message, stack_trace,
            ))
        elif status == "SKIPPED" and error_message:
            steps.append(_outcome_step("SKIPPED", "skipped", error_message, None))

        suite_name = fixture_fullname or assembly_name or "Unknown Suite"
        case = {
            "test_run_id": run_id,
            "test_name": name,
            "full_name": fullname or (f"{classname}.{name}" if classname else name),
            "suite_name": suite_name,
            "class_name": classname,
            "package_name": classname.rsplit(".", 1)[0] if classname and "." in classname else None,
            "status": status,
            "duration_ms": _duration_ms(case_el.get("duration")),
            "error_message": error_message,
            "stack_trace": stack_trace,
            "attachments": [],
            "tags": _collect_tags(case_el),
            "steps": steps,
            "framework": "nunit",
        }
        apply_case_clock(case, case_el.get("start-time"), case_el.get("end-time"))
        out.append(case)
        if suite_sources is not None and named_by is not None and suite_name != "Unknown Suite":
            bucket = suite_sources.setdefault(suite_name, [])
            if not any(element is named_by for element in bucket):
                bucket.append(named_by)

    for child in suite_el.findall("test-suite"):
        _walk_suites(child, run_id, out, assembly_name, fixture_fullname, suite_sources, named_by)


def _element_timing(element) -> tuple:
    """(started_at, finished_at, wall_ms) of a ``<test-run>`` / ``<test-suite>``."""
    started, _ = parse_report_time(element.get("start-time"))
    finished, _ = parse_report_time(element.get("end-time"))
    return started, finished, seconds_to_ms(element.get("duration"))


def parse_nunit_xml(content: str, test_run_id: str) -> List[dict]:
    """Parse an NUnit3 XML result file into normalized test case dicts.

    Returns the same shape produced by ``robot_parser``/``testng_parser`` so
    the downstream ``_upsert_test_case`` pipeline is unchanged. Malformed
    input logs and returns ``[]``.
    """
    return _parse(content, test_run_id)[0]


def parse_nunit_xml_with_timings(content: str, test_run_id: str) -> tuple[List[dict], ReportTimings]:
    """:func:`parse_nunit_xml`, plus the report's run and suite timing (``ReportTimings``)."""
    return _parse(content, test_run_id)


def _parse(content: str, test_run_id: str) -> tuple[List[dict], ReportTimings]:
    results: List[dict] = []
    timings = empty_timings()
    try:
        root = ET.fromstring(content)
    except Exception as exc:
        logger.warning("Failed to parse NUnit XML: %s", exc)
        return results, timings

    if root.tag != "test-run":
        logger.warning("NUnit XML root must be <test-run>, got <%s>", root.tag)
        return results, timings

    suite_sources: Dict[str, list] = {}
    for suite in root.findall("test-suite"):
        _walk_suites(suite, test_run_id, results, None, None, suite_sources)

    timings["run_started_at"], timings["run_finished_at"], timings["run_wall_ms"] = _element_timing(root)
    for suite_name, elements in suite_sources.items():
        for element in elements:
            started, finished, wall = _element_timing(element)
            add_suite_timing(timings, {
                "name": suite_name, "started_at": started, "finished_at": finished, "wall_ms": wall,
            })

    return results, timings
