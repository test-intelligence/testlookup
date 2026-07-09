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
"""
from __future__ import annotations

import logging
from typing import List, Optional

import defusedxml.ElementTree as ET

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
) -> None:
    suite_type = _clean(suite_el.get("type")).lower()
    if suite_type == "assembly":
        # Assembly name attr is the dll basename; fullname is the full path.
        assembly_name = _clean(suite_el.get("name")) or assembly_name
    elif suite_type == "testfixture":
        fixture_fullname = _clean(suite_el.get("fullname")) or _clean(suite_el.get("name")) or fixture_fullname

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
        out.append({
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
        })

    for child in suite_el.findall("test-suite"):
        _walk_suites(child, run_id, out, assembly_name, fixture_fullname)


def parse_nunit_xml(content: str, test_run_id: str) -> List[dict]:
    """Parse an NUnit3 XML result file into normalized test case dicts.

    Returns the same shape produced by ``robot_parser``/``testng_parser`` so
    the downstream ``_upsert_test_case`` pipeline is unchanged. Malformed
    input logs and returns ``[]``.
    """
    results: List[dict] = []
    try:
        root = ET.fromstring(content)
    except Exception as exc:
        logger.warning("Failed to parse NUnit XML: %s", exc)
        return results

    if root.tag != "test-run":
        logger.warning("NUnit XML root must be <test-run>, got <%s>", root.tag)
        return results

    for suite in root.findall("test-suite"):
        _walk_suites(suite, test_run_id, results, None, None)

    return results
