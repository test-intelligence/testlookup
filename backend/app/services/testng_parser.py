"""TestNG report parser — BOTH the native and the surefire document shapes.

TestNG emits two different files. Maven surefire writes JUnit-shaped
``TEST-*.xml`` (``<testsuite>/<testcase>``); TestNG itself writes
``testng-results.xml`` (``<suite>/<test>/<class>/<test-method>``). This
module handled only the first for a long time while ``_detect_format``
routed both here, so a native report parsed to zero results behind an
HTTP 202 that reported success.

TestNG XML has **no native step hierarchy** — a ``<testcase>`` is the finest
granularity it reports. To still feed the Phase 1 granular snapshot pipeline we
synthesize coarse *pseudo-steps* in the common cross-framework step dict shape
(same keys Allure/pytest emit), so they flow unchanged through
``ingestion._upsert_test_case`` → ``_persist_step_snapshot`` → ``_insert_step``.

Pseudo-steps surfaced (in order, only when present):
  * one **outcome** step carrying the test result — and CRUCIALLY preserving the
    ``<failure>`` vs ``<error>`` distinction into its status:
      - ``<failure>``  → ``FAILED``  (an assertion failed — a product bug signal)
      - ``<error>``    → ``BROKEN``  (an unexpected exception / infra error)
      - ``<skipped>``  → ``SKIPPED``
      - otherwise      → ``PASSED``
    The exception message → ``assertion_message``, the stack/body →
    ``assertion_trace`` (this is the same FAILED-vs-BROKEN vocab the rest of the
    repo's producer<->consumer contracts rely on).
  * one **log** step per ``<reporter-output>``/``<system-out>``/``<system-err>``
    block, each non-empty line becoming nothing finer than the block (TestNG
    gives no per-line timing) — surfaced as a single PASSED log pseudo-step
    carrying the captured text in ``assertion_trace``.

Status vocab MUST be the strict TestLookup set (PASSED/FAILED/SKIPPED/BROKEN/
UNKNOWN) — the granular ``test_steps.status`` column is fronted by a strict enum
that silently 422s the read endpoint on any value outside it.
"""
import logging
from typing import List, Optional

import defusedxml.ElementTree as ET

logger = logging.getLogger(__name__)

# Per-line / per-block caps so a runaway ``<system-out>`` (TestNG can dump
# megabytes of captured stdout) cannot materialise an unbounded number of
# ``test_steps`` rows or blow the assertion-trace column at the ingest boundary.
# (The shared write-side caps in ``ingestion._insert_step`` still apply on top.)
_MAX_LOG_STEPS = 50
_MAX_TRACE_CHARS = 8000
_MAX_MESSAGE_CHARS = 2000


def _clean(text: Optional[str]) -> str:
    return (text or "").strip()


def _failure_step(node, status: str, keyword: str) -> dict:
    """Build the outcome pseudo-step for a ``<failure>``/``<error>`` element."""
    message = _clean(node.get("message"))[:_MAX_MESSAGE_CHARS] or None
    trace = _clean(node.text)[:_MAX_TRACE_CHARS] or None
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


def _log_steps(testcase) -> List[dict]:
    """Surface ``<reporter-output>``/``<system-out>``/``<system-err>`` as coarse
    PASSED log pseudo-steps. TestNG gives no per-line timing, so each captured
    block becomes a single bounded log step rather than per-line steps."""
    out: List[dict] = []
    # ``reporter-output`` is TestNG-native (Reporter.log); system-out/err are the
    # surefire-standard capture blocks. Treat all three the same way.
    for tag in ("reporter-output", "system-out", "system-err"):
        for block in testcase.findall(tag):
            # ``<reporter-output>`` wraps each entry in a <line> child; the
            # system-* blocks carry raw text. Handle both.
            lines = [_clean(ln.text) for ln in block.findall("line")]
            text = "\n".join(ln for ln in lines if ln) if lines else _clean(block.text)
            if not text:
                continue
            if len(out) >= _MAX_LOG_STEPS:
                break
            out.append({
                "name": tag,
                "keyword": "log",
                "status": "PASSED",
                "start_ms": None,
                "duration_ms": None,
                "assertion_message": None,
                "assertion_trace": text[:_MAX_TRACE_CHARS],
                "expected": None,
                "actual": None,
                "parameters": [],
                "attachments": [],
                "steps": [],
            })
    return out


def _build_steps(testcase, status: str) -> List[dict]:
    """Assemble the common-shape pseudo-step list for one ``<testcase>``.

    Order: the outcome step (failure/error preserved as FAILED/BROKEN) first,
    then any captured log blocks. A pure pass with no logs yields no steps —
    there is nothing finer than the case itself to surface.
    """
    steps: List[dict] = []
    failure = testcase.find("failure")
    error = testcase.find("error")
    skipped = testcase.find("skipped")

    if failure is not None:
        steps.append(_failure_step(failure, "FAILED", "failure"))
    elif error is not None:
        steps.append(_failure_step(error, "BROKEN", "error"))
    elif skipped is not None:
        msg = _clean(skipped.get("message"))[:_MAX_MESSAGE_CHARS] or None
        steps.append({
            "name": "skipped",
            "keyword": "skipped",
            "status": "SKIPPED",
            "start_ms": None,
            "duration_ms": None,
            "assertion_message": msg,
            "assertion_trace": _clean(skipped.text)[:_MAX_TRACE_CHARS] or None,
            "expected": None,
            "actual": None,
            "parameters": [],
            "attachments": [],
            "steps": [],
        })

    steps.extend(_log_steps(testcase))
    return steps


# ── Native testng-results.xml ────────────────────────────────────────────────
#
# TestNG emits TWO different files and this module has only ever understood one
# of them. Under Maven, surefire writes JUnit-shaped
# ``TEST-*.xml`` (``<testsuite>/<testcase>``) — handled below. TestNG *itself*
# writes ``testng-results.xml``, whose shape is entirely different:
#
#     <testng-results total="6" passed="3" failed="2" skipped="1">
#       <suite name="…"><test name="…"><class name="…">
#         <test-method status="PASS|FAIL|SKIP" name="…" duration-ms="…">
#           <exception class="…"><message/><full-stacktrace/></exception>
#
# ``_detect_format`` explicitly routes ``<testng-results`` here, so the product
# *claimed* to support it — but ``root.findall("testsuite")`` matches nothing in
# that document, so a valid six-test report parsed to ZERO results and the
# upload still answered ``202 {"total_results": 0}``. Silent, and on one of the
# eight formats the product advertises.
_TESTNG_STATUS = {
    "PASS": "passed",
    "FAIL": "failed",
    "SKIP": "skipped",
    # TestNG's own vocabulary for a method skipped because a dependency failed.
    # It did not run, so it is not a failure.
    "SKIPPED": "skipped",
}

# Configuration methods (@BeforeMethod/@AfterSuite/…) are reported as
# <test-method is-config="true">. They are setup, not tests: counting them
# would inflate every total and make a suite of 6 tests report 14.
def _is_config_method(node) -> bool:
    return str(node.get("is-config") or "").lower() == "true"


def _testng_exception(method):
    """(message, stack) from a <test-method>'s exception, if any."""
    exc = method.find("exception")
    if exc is None:
        return None, None
    message = _clean(exc.findtext("message"))
    if not message:
        # Some writers put the text on the element itself.
        message = _clean(exc.get("class"))
    stack = _clean(exc.findtext("full-stacktrace"))
    return (
        (message[:_MAX_MESSAGE_CHARS] or None),
        (stack[:_MAX_TRACE_CHARS] or None),
    )


def _parse_testng_results(root, test_run_id: str) -> List[dict]:
    """Parse a native ``testng-results.xml`` document."""
    results: List[dict] = []
    for suite in root.findall("suite"):
        suite_name = suite.get("name") or "Unknown Suite"
        for test in suite.findall("test"):
            for klass in test.findall("class"):
                class_name = klass.get("name") or ""
                for method in klass.findall("test-method"):
                    if _is_config_method(method):
                        continue
                    raw_status = str(method.get("status") or "").upper()
                    # An unrecognised status becomes UNKNOWN rather than
                    # silently PASSED — a status this parser does not know is
                    # not evidence that the test passed.
                    status = _TESTNG_STATUS.get(raw_status, "unknown")
                    name = method.get("name") or "Unknown"
                    try:
                        duration_ms = int(float(method.get("duration-ms") or 0))
                    except (TypeError, ValueError):
                        duration_ms = None
                    message, stack = _testng_exception(method)
                    results.append({
                        "test_run_id": test_run_id,
                        "test_name": name,
                        "full_name": f"{class_name}.{name}" if class_name else name,
                        "suite_name": suite_name,
                        "class_name": class_name,
                        "package_name": (
                            class_name.rsplit(".", 1)[0] if "." in class_name else None
                        ),
                        "status": status,
                        "duration_ms": duration_ms,
                        "error_message": message,
                        "stack_trace": stack,
                        "attachments": [],
                        # One coarse outcome pseudo-step, same cross-framework
                        # shape the surefire path emits.
                        "steps": [{
                            "name": "outcome",
                            "keyword": "outcome",
                            "status": status.upper(),
                            "start_ms": None,
                            "duration_ms": duration_ms,
                            "assertion_message": message,
                            "assertion_trace": stack,
                            "expected": None,
                            "actual": None,
                        }],
                    })
    return results


def parse_testng_xml(xml_content: str, test_run_id: str) -> List[dict]:
    """Parse a TestNG surefire XML report into a list of normalised test case dicts."""
    results: List[dict] = []
    try:
        root = ET.fromstring(xml_content)
    except Exception as e:
        logger.warning(f"Failed to parse TestNG XML: {e}")
        return results

    # Native testng-results.xml — a completely different document shape from
    # surefire's. Checked FIRST because its root tag is unambiguous.
    if root.tag == "testng-results":
        return _parse_testng_results(root, test_run_id)

    # Handle both <testsuite> and <testsuites> root elements (surefire/JUnit).
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")

    for suite in suites:
        suite_name = suite.get("name", "Unknown Suite")
        for testcase in suite.findall("testcase"):
            name = testcase.get("name", "Unknown")
            classname = testcase.get("classname", "")
            time_str = testcase.get("time", "0")
            try:
                duration_ms = int(float(time_str) * 1000)
            except ValueError:
                duration_ms = None

            # Determine status — PRESERVE the <failure> vs <error> distinction:
            # an assertion <failure> is a real test FAILED; an <error> (unexpected
            # exception / setup blow-up) is BROKEN. This must match the granular
            # step status produced in ``_build_steps`` below (producer<->consumer
            # vocab discipline).
            failure = testcase.find("failure")
            error = testcase.find("error")
            skipped = testcase.find("skipped")

            if failure is not None:
                status = "failed"
                error_node = failure
            elif error is not None:
                status = "broken"
                error_node = error
            elif skipped is not None:
                status = "skipped"
                error_node = None
            else:
                status = "passed"
                error_node = None

            if error_node is not None:
                error_message = (error_node.get("message") or error_node.text or "")[:_MAX_MESSAGE_CHARS]
                stack_trace = (_clean(error_node.text) or None)
                if stack_trace:
                    stack_trace = stack_trace[:_MAX_TRACE_CHARS]
            else:
                error_message = None
                stack_trace = None

            results.append({
                "test_run_id": test_run_id,
                "test_name": name,
                "full_name": f"{classname}.{name}" if classname else name,
                "suite_name": suite_name,
                "class_name": classname,
                "package_name": classname.rsplit(".", 1)[0] if "." in classname else None,
                "status": status,
                "duration_ms": duration_ms,
                "error_message": error_message,
                "stack_trace": stack_trace,
                "attachments": [],
                # Coarse pseudo-steps in the common cross-framework shape: the
                # outcome (failure→FAILED / error→BROKEN / skipped) + captured
                # reporter/system log blocks. TestNG has no native step tree.
                "steps": _build_steps(testcase, status),
            })

    return results
