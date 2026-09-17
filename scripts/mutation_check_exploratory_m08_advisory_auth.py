"""Prove advisory release values cannot bypass the reviewer role boundary."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "backend/app/routers/release_readiness.py"
GOOD = """    if allow_advisory:\n        role = getattr(current_user.role, \"value\", current_user.role)\n        if role not in {UserRole.QA_LEAD.value, UserRole.ADMIN.value}:\n            raise HTTPException(\n                status_code=status.HTTP_403_FORBIDDEN,\n                detail=\"Advisory release values require the QA Lead role.\",\n            )\n"""
TEST = (
    "backend/tests/test_distribution_gates.py::"
    "test_release_advisory_override_requires_qa_lead"
)


def main() -> int:
    original = TARGET.read_bytes()
    source = original.decode("utf-8")
    if source.count(GOOD) != 1:
        raise AssertionError("advisory-authorisation mutation must apply exactly once")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:testlookup",
        "--basetemp",
        ".pytest-tmp-exploratory-m08-advisory-mutation",
        TEST,
    ]
    baseline = subprocess.run(
        command, cwd=ROOT, check=False, capture_output=True, text=True
    )
    if baseline.returncode != 0:
        raise AssertionError("mutation baseline failed\n" + baseline.stdout + baseline.stderr)
    try:
        TARGET.write_text(source.replace(GOOD, "", 1), encoding="utf-8", newline="")
        mutated = subprocess.run(
            command, cwd=ROOT, check=False, capture_output=True, text=True
        )
        if mutated.returncode != 1:
            raise AssertionError(
                "advisory-authorisation mutation was not killed with pytest exit 1\n"
                + mutated.stdout
                + mutated.stderr
            )
    finally:
        TARGET.write_bytes(original)
    if TARGET.read_bytes() != original:
        raise AssertionError("advisory-authorisation mutation did not restore its target")
    print("M08 advisory-authorisation mutation check: 1 mutation killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
