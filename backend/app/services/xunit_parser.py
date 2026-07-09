"""xUnit.net v2 XML parser (``xunit.runner`` XML / ``dotnet test --logger:xunit``).

The xUnit.net v2 result format is rooted at ``<assemblies>`` (a bare
``<assembly>`` root from single-assembly runners is tolerated), with
``<assembly><collection><test>`` leaves:

.. code-block:: xml

    <assemblies timestamp="07/09/2026 10:00:00">
      <assembly name="C:/ci/Tests.dll" test-framework="xUnit.net 2.4.2"
                total="3" passed="1" failed="1" skipped="1">
        <collection name="Test collection for MyApp.CalculatorTests" total="3">
          <test name="MyApp.CalculatorTests.Adds" type="MyApp.CalculatorTests"
                method="Adds" time="0.052" result="Pass">
            <traits><trait name="category" value="smoke"/></traits>
          </test>
          <test name="MyApp.CalculatorTests.Divides" type="MyApp.CalculatorTests"
                method="Divides" time="0.010" result="Fail">
            <failure exception-type="Xunit.Sdk.EqualException">
              <message><![CDATA[Assert.Equal() Failure ...]]></message>
              <stack-trace><![CDATA[at MyApp.CalculatorTests.Divides()]]></stack-trace>
            </failure>
          </test>
          <test name="MyApp.CalculatorTests.Slow" result="Skip">
            <reason><![CDATA[flaky on CI]]></reason>
          </test>
        </collection>
      </assembly>
    </assemblies>

Key nuances:

* ``time`` is **float seconds** — converted to ms here.
* ``result`` vocabulary is Pass / Fail / Skip → PASSED / FAILED / SKIPPED
  (xUnit.net does not distinguish assertion failures from unexpected errors,
  so no BROKEN mapping exists for this format).
* ``name`` is the full display name (``Ns.Class.Method``); ``method`` is the
  short method name (used as ``test_name`` when present); ``type`` is the
  class → ``class_name`` / ``package_name``.
* ``suite_name`` = the collection name, falling back to the assembly's file
  basename.
* ``<reason>`` carries the skip reason (CDATA text, or a nested ``<message>``
  from some runners — both handled).
* ``<traits><trait name=... value=.../>`` → tags as ``name:value``.

Steps: xUnit XML has no step hierarchy — like the TestNG parser, one
*outcome* pseudo-step in the common cross-framework shape is emitted for
failing cases. Step status vocab MUST stay within the strict TestLookup set
(PASSED/FAILED/SKIPPED/BROKEN/UNKNOWN) — the granular ``test_steps.status``
column is fronted by a strict enum that silently 422s reads on drift.

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

# xUnit result → TestLookup status (uppercase; ingestion lowercases on persist).
_RESULT_MAP = {
    "pass": "PASSED",
    "fail": "FAILED",
    "skip": "SKIPPED",
    "notrun": "SKIPPED",
}


def _clean(text: Optional[str]) -> str:
    return (text or "").strip()


def _time_ms(value: Optional[str]) -> Optional[int]:
    """xUnit ``time`` is float seconds."""
    value = _clean(value)
    if not value:
        return None
    try:
        return int(float(value) * 1000)
    except ValueError:
        return None


def _map_result(raw: Optional[str]) -> str:
    status = _RESULT_MAP.get(_clean(raw).lower())
    if status is None:
        logger.debug("Unknown xUnit result '%s' — defaulting to UNKNOWN", raw)
        return "UNKNOWN"
    return status


def _collect_tags(test_el) -> List[str]:
    """``<traits><trait name=... value=.../>`` → ``name:value`` tags."""
    tags: List[str] = []
    traits = test_el.find("traits")
    if traits is None:
        return tags
    for trait in traits.findall("trait"):
        name = _clean(trait.get("name"))
        value = _clean(trait.get("value"))
        if name and value:
            tags.append(f"{name}:{value}")
        elif name:
            tags.append(name)
    return tags


def _test_error(test_el, status: str) -> tuple:
    """(error_message, stack_trace) for a ``<test>`` by verdict shape."""
    failure = test_el.find("failure")
    if failure is not None:
        msg_el = failure.find("message")
        trace_el = failure.find("stack-trace")
        message = _clean(msg_el.text if msg_el is not None else None)[:_MAX_MESSAGE_CHARS] or None
        trace = _clean(trace_el.text if trace_el is not None else None)[:_MAX_TRACE_CHARS] or None
        exc_type = _clean(failure.get("exception-type"))
        if exc_type and not message:
            message = exc_type[:_MAX_MESSAGE_CHARS]
        return message, trace
    reason = test_el.find("reason")
    if reason is not None and status == "SKIPPED":
        # CDATA text directly, or a nested <message> from some runners.
        msg_el = reason.find("message")
        message = _clean(msg_el.text if msg_el is not None else reason.text)[:_MAX_MESSAGE_CHARS] or None
        return message, None
    return None, None


def _outcome_step(status: str, message: Optional[str], trace: Optional[str]) -> dict:
    """The single outcome pseudo-step for a failing case (common shape)."""
    return {
        "name": "failure",
        "keyword": "failure",
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


def _assembly_label(assembly_el) -> Optional[str]:
    """Assembly file basename (``name`` is usually a full dll path)."""
    name = _clean(assembly_el.get("name"))
    if not name:
        return None
    return name.replace("\\", "/").rsplit("/", 1)[-1] or None


def _parse_assembly(assembly_el, run_id: str, out: List[dict]) -> None:
    assembly_name = _assembly_label(assembly_el)
    for collection in assembly_el.findall("collection"):
        collection_name = _clean(collection.get("name")) or None
        suite_name = collection_name or assembly_name or "Unknown Suite"
        for test_el in collection.findall("test"):
            display_name = test_el.get("name") or "Unknown"
            class_name = _clean(test_el.get("type")) or None
            method = _clean(test_el.get("method")) or None
            status = _map_result(test_el.get("result"))
            message, trace = _test_error(test_el, status)

            steps: List[dict] = []
            if status == "FAILED":
                steps.append(_outcome_step(status, message, trace))

            out.append({
                "test_run_id": run_id,
                "test_name": method or display_name,
                "full_name": display_name,
                "suite_name": suite_name,
                "class_name": class_name,
                "package_name": class_name.rsplit(".", 1)[0] if class_name and "." in class_name else None,
                "status": status,
                "duration_ms": _time_ms(test_el.get("time")),
                "error_message": message,
                "stack_trace": trace,
                "attachments": [],
                "tags": _collect_tags(test_el),
                "steps": steps,
                "framework": "xunit",
            })


def parse_xunit_xml(content: str, test_run_id: str) -> List[dict]:
    """Parse an xUnit.net v2 XML report into normalized test case dicts.

    Returns the same shape produced by ``robot_parser``/``testng_parser`` so
    the downstream ``_upsert_test_case`` pipeline is unchanged. Malformed
    input logs and returns ``[]``.
    """
    results: List[dict] = []
    try:
        root = ET.fromstring(content)
    except Exception as exc:
        logger.warning("Failed to parse xUnit XML: %s", exc)
        return results

    if root.tag == "assemblies":
        assemblies = root.findall("assembly")
    elif root.tag == "assembly":
        assemblies = [root]
    else:
        logger.warning("xUnit XML root must be <assemblies> or <assembly>, got <%s>", root.tag)
        return results

    for assembly in assemblies:
        _parse_assembly(assembly, test_run_id, results)

    return results
