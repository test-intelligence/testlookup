"""Visual Studio TRX parser (``dotnet test --logger trx`` / MSTest / vstest).

TRX is rooted at ``<TestRun>`` in the VisualStudio TeamTest namespace
(``http://microsoft.com/schemas/VisualStudio/TeamTest/2010``). Outcomes live
under ``<Results>``; class/method identity lives under ``<TestDefinitions>``
and must be JOINED via ``testId``:

.. code-block:: xml

    <TestRun id="..." xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
      <Results>
        <UnitTestResult testId="guid-1" testName="Adds" outcome="Passed"
                        duration="00:00:00.0520000">
          <Output>
            <ErrorInfo><Message>...</Message><StackTrace>...</StackTrace></ErrorInfo>
          </Output>
        </UnitTestResult>
      </Results>
      <TestDefinitions>
        <UnitTest id="guid-1" name="Adds">
          <TestMethod className="MyApp.CalculatorTests" name="Adds"/>
        </UnitTest>
      </TestDefinitions>
    </TestRun>

Key nuances:

* All elements are namespace-qualified — matching is done on the LOCAL tag
  name so namespace-less exports from older tooling still parse.
* ``duration`` is ``HH:MM:SS.fffffff`` (7 fractional digits) — converted to
  ms here. It may be absent for NotExecuted rows.
* ``className`` may carry assembly qualification
  (``Ns.Class, Assembly, Version=...``) — everything after the first comma is
  stripped.
* Outcome vocabulary (FAILED-vs-BROKEN discipline): ``Passed`` → PASSED;
  ``Failed`` → FAILED (an assertion verdict); ``NotExecuted`` /
  ``Inconclusive`` → SKIPPED; ``Timeout`` / ``Aborted`` / ``Error`` → BROKEN
  (infrastructure/unexpected-error shapes, not product-assertion failures).
* ``suite_name`` = the namespace-qualified class; ``package_name`` = its
  namespace portion.

Steps: TRX has no step hierarchy — like the TestNG parser, one *outcome*
pseudo-step in the common cross-framework shape is emitted for non-passing
cases. Step status vocab MUST stay within the strict TestLookup set (PASSED/
FAILED/SKIPPED/BROKEN/UNKNOWN) — the granular ``test_steps.status`` column is
fronted by a strict enum that silently 422s reads on drift.

Malformed input returns an empty list rather than raising — ingestion tasks
are fire-and-forget; one bad upload must not wedge the worker.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import defusedxml.ElementTree as ET

logger = logging.getLogger(__name__)

_MAX_TRACE_CHARS = 8000
_MAX_MESSAGE_CHARS = 2000

# TRX outcome → TestLookup status (uppercase; ingestion lowercases on persist).
_OUTCOME_MAP = {
    "passed": "PASSED",
    "failed": "FAILED",
    "notexecuted": "SKIPPED",
    "inconclusive": "SKIPPED",
    "timeout": "BROKEN",
    "aborted": "BROKEN",
    "error": "BROKEN",
}


def _clean(text: Optional[str]) -> str:
    return (text or "").strip()


def _local(tag) -> str:
    """Local tag name, tolerating the ``{namespace}`` ElementTree prefix."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _find_local(parent, name):
    """First DIRECT child whose local tag name matches ``name``."""
    for child in parent:
        if _local(child.tag) == name:
            return child
    return None


def _iter_local(parent, name):
    """All descendants (any depth) whose local tag name matches ``name``."""
    for el in parent.iter():
        if _local(el.tag) == name:
            yield el


def _duration_ms(value: Optional[str]) -> Optional[int]:
    """TRX ``duration`` is ``HH:MM:SS.fffffff`` — convert to ms."""
    value = _clean(value)
    if not value:
        return None
    try:
        parts = value.split(":")
        if len(parts) != 3:
            return None
        hours, minutes, seconds = int(parts[0]), int(parts[1]), float(parts[2])
        return int((hours * 3600 + minutes * 60 + seconds) * 1000)
    except ValueError:
        return None


def _map_outcome(raw: Optional[str]) -> str:
    status = _OUTCOME_MAP.get(_clean(raw).lower())
    if status is None:
        logger.debug("Unknown TRX outcome '%s' — defaulting to UNKNOWN", raw)
        return "UNKNOWN"
    return status


def _definitions(root) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    """``testId`` → (className, methodName) from ``<TestDefinitions>``."""
    defs: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    for unit_test in _iter_local(root, "UnitTest"):
        test_id = _clean(unit_test.get("id"))
        if not test_id:
            continue
        method_el = _find_local(unit_test, "TestMethod")
        class_name = None
        method_name = None
        if method_el is not None:
            # className may be assembly-qualified ("Ns.Class, Assembly, ...").
            class_name = _clean(method_el.get("className")).split(",", 1)[0].strip() or None
            method_name = _clean(method_el.get("name")) or None
        defs[test_id] = (class_name, method_name)
    return defs


def _error_info(result_el) -> Tuple[Optional[str], Optional[str]]:
    """(message, stack_trace) from ``<Output><ErrorInfo>``."""
    output = _find_local(result_el, "Output")
    if output is None:
        return None, None
    error_info = _find_local(output, "ErrorInfo")
    if error_info is None:
        return None, None
    msg_el = _find_local(error_info, "Message")
    trace_el = _find_local(error_info, "StackTrace")
    message = _clean(msg_el.text if msg_el is not None else None)[:_MAX_MESSAGE_CHARS] or None
    trace = _clean(trace_el.text if trace_el is not None else None)[:_MAX_TRACE_CHARS] or None
    return message, trace


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


def parse_trx_xml(content: str, test_run_id: str) -> List[dict]:
    """Parse a Visual Studio TRX file into normalized test case dicts.

    Returns the same shape produced by ``robot_parser``/``testng_parser`` so
    the downstream ``_upsert_test_case`` pipeline is unchanged. Malformed
    input logs and returns ``[]``.
    """
    results: List[dict] = []
    try:
        root = ET.fromstring(content)
    except Exception as exc:
        logger.warning("Failed to parse TRX XML: %s", exc)
        return results

    if _local(root.tag) != "TestRun":
        logger.warning("TRX root must be <TestRun>, got <%s>", _local(root.tag))
        return results

    defs = _definitions(root)

    for result_el in _iter_local(root, "UnitTestResult"):
        test_name = result_el.get("testName") or "Unknown"
        status = _map_outcome(result_el.get("outcome"))
        class_name, method_name = defs.get(_clean(result_el.get("testId")), (None, None))
        message, trace = _error_info(result_el)

        # Timeout/Aborted rows may carry no ErrorInfo — surface the raw
        # outcome so triage isn't left with a bare BROKEN.
        if status == "BROKEN" and not message:
            message = _clean(result_el.get("outcome")).lower() or None

        steps: List[dict] = []
        if status in ("FAILED", "BROKEN"):
            steps.append(_outcome_step(
                status, "error" if status == "BROKEN" else "failure", message, trace,
            ))

        short_name = method_name or test_name
        results.append({
            "test_run_id": test_run_id,
            "test_name": short_name,
            "full_name": f"{class_name}.{short_name}" if class_name else test_name,
            "suite_name": class_name or "Unknown Suite",
            "class_name": class_name,
            "package_name": class_name.rsplit(".", 1)[0] if class_name and "." in class_name else None,
            "status": status,
            "duration_ms": _duration_ms(result_el.get("duration")),
            "error_message": message,
            "stack_trace": trace,
            "attachments": [],
            "steps": steps,
            "framework": "trx",
        })

    return results
