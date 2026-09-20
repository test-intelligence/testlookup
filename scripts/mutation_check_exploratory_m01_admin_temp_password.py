"""Prove admin-created bootstrap credentials cannot become permanent silently."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "backend" / "app" / "routers" / "users.py"
TEST = (
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:testlookup",
    "backend/tests/test_user_management.py::TestAdminCreateUser::test_happy_path_returns_forced_reset_temp_password",
)
GOOD = """        role=payload.role,\n        must_change_password=True,\n"""
BAD = """        role=payload.role,\n"""


def main() -> int:
    original = SOURCE.read_bytes()
    original_hash = hashlib.sha256(original).digest()
    text = original.decode("utf-8")
    count = text.count(GOOD)
    if count != 1:
        raise AssertionError(f"mutation must apply exactly once; found {count}")
    mutated = text.replace(GOOD, BAD, 1)
    if mutated == text:
        raise AssertionError("mutation did not change source")

    SOURCE.write_text(mutated, encoding="utf-8")
    try:
        run = subprocess.run(
            TEST,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
    finally:
        SOURCE.write_bytes(original)

    if run.returncode == 0:
        raise AssertionError("mutation survived: forced reset flag removed")
    if hashlib.sha256(SOURCE.read_bytes()).digest() != original_hash:
        raise AssertionError("source restoration changed the original bytes")

    print("M01 admin temporary-password mutation check: 1 mutation killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
