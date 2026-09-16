"""Prove M02's feature-flag tenant guard cannot be removed silently."""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "backend" / "app" / "routers" / "feature_flags.py"
TESTS = (
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:testlookup",
    "--basetemp=.pytest-tmp-exploratory-m02-feature-flag-mutation",
    "backend/tests/integration/test_feature_flags_api.py",
)

GOOD = """    if project_id:\n        parsed_project, _ = await resolve_project_scope(\n            db,\n            current_user,\n            project_id,\n        )\n"""
BAD = """    if project_id:\n        parsed_project = None\n"""


def main() -> int:
    original = SOURCE.read_bytes()
    original_hash = hashlib.sha256(original).hexdigest()
    text = original.decode("utf-8")
    count = text.count(GOOD)
    if count != 1:
        raise AssertionError(
            f"mutation must apply exactly once: {GOOD!r}; found {count}"
        )
    mutated = text.replace(GOOD, BAD, 1)
    if mutated == text:
        raise AssertionError("mutation did not change source")

    SOURCE.write_text(mutated, encoding="utf-8")
    try:
        run = subprocess.run(
            TESTS,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
    finally:
        SOURCE.write_bytes(original)

    if run.returncode == 0:
        raise AssertionError("mutation survived: feature-flag project authorization removed")
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != original_hash:
        raise AssertionError("source restoration changed the original bytes")

    print("M02 feature-flag authorization mutation check: 1 mutation killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
