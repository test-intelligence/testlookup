"""Prove M06 tests reject removal of release-override serialization."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "backend/app/services/release_council_service.py"
TEST = "tests/regression/test_release_override_concurrency.py"
GOOD = "        .with_for_update()\n"
BAD = ""


def main() -> None:
    original = TARGET.read_bytes()
    source = original.decode("utf-8")
    try:
        count = source.count(GOOD)
        if count != 1:
            raise AssertionError(
                f"mutation must apply exactly once: {GOOD!r}; found {count}"
            )
        mutated = source.replace(GOOD, BAD, 1)
        if mutated == source:
            raise AssertionError("mutation did not change source")
        TARGET.write_text(mutated, encoding="utf-8", newline="")
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                TEST,
                "-q",
                "-p",
                "no:testlookup",
            ],
            cwd=ROOT / "backend",
            capture_output=True,
            text=True,
            timeout=60,
        )
        TARGET.write_bytes(original)
        if TARGET.read_bytes() != original:
            raise AssertionError("mutation restoration failed")
        if run.returncode == 0:
            raise AssertionError("release-override row-lock mutation survived")
    finally:
        TARGET.write_bytes(original)

    print("M06 override-race mutation check: 1 mutation killed")


if __name__ == "__main__":
    main()
