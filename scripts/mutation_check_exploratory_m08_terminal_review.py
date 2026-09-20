"""Prove rejected and superseded release narratives cannot be distributed."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "backend/app/services/report_distribution_policy.py"
MUTATIONS = [
    (
        "missing-terminal-states",
        '_TERMINAL_REVIEW_STATES = frozenset({"rejected", "superseded"})\n',
        "_TERMINAL_REVIEW_STATES = frozenset()\n",
    ),
    (
        "terminal-council-keeps-original-recommendation",
        "                reasoning=None,\n                original_recommendation=None,\n",
        "                reasoning=None,\n",
    ),
    (
        "terminal-webhook-keeps-original-recommendation",
        '            projected["reasoning"] = None\n            projected["original_recommendation"] = None\n',
        '            projected["reasoning"] = None\n',
    ),
    (
        "terminal-report-keeps-original-recommendation",
        '            projected["reasoning"] = None\n            projected["original_recommendation"] = None\n',
        '            projected["reasoning"] = None\n',
    ),
]
TESTS = [
    "backend/tests/test_distribution_gates.py::test_terminal_review_never_exposes_release_draft_content",
    "backend/tests/test_release_decided_webhook.py::test_terminal_review_never_leaves_in_release_webhook_content",
    "backend/tests/test_notification_distribution_gates.py::test_terminal_review_redacts_the_embedded_release_verdict",
]


def _run(label: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:testlookup",
            *TESTS,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def main() -> int:
    baseline = _run("baseline")
    if baseline.returncode != 0:
        raise AssertionError("mutation baseline failed\n" + baseline.stdout + baseline.stderr)
    for index, (name, good, bad) in enumerate(MUTATIONS):
        original = TARGET.read_bytes()
        source = original.decode("utf-8")
        expected_count = 2 if name.startswith("terminal-") and "council" not in name else 1
        if source.count(good) != expected_count:
            raise AssertionError(f"{name} mutation must find {expected_count} target(s)")
        occurrence = 0 if name != "terminal-report-keeps-original-recommendation" else 1
        split = source.split(good)
        mutated_source = good.join(split[: occurrence + 1]) + bad + good.join(split[occurrence + 1 :])
        try:
            TARGET.write_text(mutated_source, encoding="utf-8", newline="")
            mutated = _run(f"{index}-{name}")
            if mutated.returncode != 1:
                raise AssertionError(
                    f"{name} mutation was not killed with pytest exit 1\n"
                    + mutated.stdout
                    + mutated.stderr
                )
        finally:
            TARGET.write_bytes(original)
        if TARGET.read_bytes() != original:
            raise AssertionError(f"{name} mutation did not restore its target")
    print(f"M08 terminal-review mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
