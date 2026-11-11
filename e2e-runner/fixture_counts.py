"""Independent oracle. Count test leaves, ignoring declared suite totals."""

import json
import sys
import xml.etree.ElementTree as ET

cases = ET.parse(sys.argv[1]).findall(".//testcase")
outcomes = {}
for case in cases:
    name = case.attrib["name"]
    if name in outcomes:
        raise ValueError("This fixture must use unique leaf names")
    outcomes[name] = next(
        (
            status
            for tag, status in (
                ("failure", "FAILED"),
                ("error", "BROKEN"),
                ("skipped", "SKIPPED"),
            )
            if case.find(tag) is not None
        ),
        "PASSED",
    )
if not outcomes or "FAILED" not in outcomes.values():
    raise ValueError("Golden fixture must include a failing leaf")
print(json.dumps(outcomes))
