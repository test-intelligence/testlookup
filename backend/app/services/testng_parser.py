"""TestNG surefire XML report parser."""
import logging
from typing import List

import defusedxml.ElementTree as ET

logger = logging.getLogger(__name__)


def parse_testng_xml(xml_content: str, test_run_id: str) -> List[dict]:
    """Parse a TestNG surefire XML report into a list of normalised test case dicts."""
    results: List[dict] = []
    try:
        root = ET.fromstring(xml_content)
    except Exception as e:
        logger.warning(f"Failed to parse TestNG XML: {e}")
        return results

    # Handle both <testsuite> and <testsuites> root elements
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

            # Determine status
            failure = testcase.find("failure")
            error = testcase.find("error")
            skipped = testcase.find("skipped")

            if failure is not None:
                status = "failed"
                error_message = (failure.get("message") or failure.text or "")[:2000]
            elif error is not None:
                status = "broken"
                error_message = (error.get("message") or error.text or "")[:2000]
            elif skipped is not None:
                status = "skipped"
                error_message = None
            else:
                status = "passed"
                error_message = None

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
                "attachments": [],
                "steps": [],
            })

    return results
