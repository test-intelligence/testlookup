"""Prove rejected and superseded release narratives cannot be distributed."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "backend/app/services/report_distribution_policy.py"
GOOD = '_TERMINAL_REVIEW_STATES = frozenset({"rejected", "superseded"})\n'
BAD = "_TERMINAL_REVIEW_STATES = frozenset()\n"
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
            "--basetemp",
            f".pytest-tmp-exploratory-m08-terminal-{label}",
            *TESTS,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def main() -> int:
    original = TARGET.read_bytes()
    source = original.decode("utf-8")
    if source.count(GOOD) != 1:
        raise AssertionError("terminal-review mutation must apply exactly once")
    baseline = _run("baseline")
    if baseline.returncode != 0:
        raise AssertionError("mutation baseline failed\n" + baseline.stdout + baseline.stderr)
    try:
        TARGET.write_text(source.replace(GOOD, BAD, 1), encoding="utf-8", newline="")
        mutated = _run("missing-terminal-states")
        if mutated.returncode != 1:
            raise AssertionError(
                "terminal-review mutation was not killed with pytest exit 1\n"
                + mutated.stdout
                + mutated.stderr
            )
    finally:
        TARGET.write_bytes(original)
    if TARGET.read_bytes() != original:
        raise AssertionError("terminal-review mutation did not restore its target")
    print("M08 terminal-review mutation check: 1 mutation killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
