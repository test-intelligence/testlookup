"""Prove CI guard tests reject weakened MCP or CLI coverage ratchets."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".github/workflows/ci.yml"
TESTS = (
    "scripts/test_ci_security.py::test_mcp_coverage_has_its_own_measured_floor",
    "scripts/test_ci_security.py::test_cli_coverage_has_an_independent_measured_floor",
)
MUTATIONS = (
    ("--cov-fail-under=41", "--cov-fail-under=40"),
    ("--cov-fail-under=62", "--cov-fail-under=61"),
    ("--cov=testlookup_cli", "--cov=testlookup"),
)


def _write_bytes_with_retry(path: Path, content: bytes) -> None:
    for attempt in range(10):
        try:
            path.write_bytes(content)
            return
        except OSError:
            if attempt == 9:
                raise
            time.sleep(0.1)


def main() -> int:
    original_bytes = TARGET.read_bytes()
    original = original_bytes.decode("utf-8")
    python = ROOT / ".venv311" / "Scripts" / "python.exe"
    for good, bad in MUTATIONS:
        count = original.count(good)
        if count != 1:
            raise AssertionError(
                f"mutation did not apply exactly once: {TARGET}: {good!r} (found {count})"
            )
        mutated = original.replace(good, bad)
        if mutated == original:
            raise AssertionError(f"mutation did not change source: {good!r}")
        _write_bytes_with_retry(TARGET, mutated.encode("utf-8"))
        try:
            run = subprocess.run(
                [str(python), "-m", "pytest", "-q", "-p", "no:testlookup", *TESTS],
                cwd=ROOT,
                env=dict(os.environ),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            if run.returncode == 0:
                raise AssertionError(f"mutation survived: {TARGET}: {bad!r}")
        finally:
            _write_bytes_with_retry(TARGET, original_bytes)
    print(f"Package coverage-floor mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
