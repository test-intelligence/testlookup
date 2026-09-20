"""Prove the release-axis regression test kills a detached-defect mutation."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "backend/app/services/defect_promotion_service.py"
TEST = (
    "backend/tests/regression/test_defect_promotion_tenant_and_offline.py::"
    "test_promote_carries_the_source_runs_release_to_the_defect"
)
MUTATIONS = (
    ("release_id=run_release_id,", "release_id=None,"),
)


def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "backend")
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    original = TARGET.read_text(encoding="utf-8")
    for good, bad in MUTATIONS:
        count = original.count(good)
        if count != 1:
            raise AssertionError(
                f"mutation did not apply exactly once: {TARGET}: {good!r} (found {count})"
            )
        mutated = original.replace(good, bad)
        if mutated == original:
            raise AssertionError(f"mutation did not change source: {good!r}")
        TARGET.write_text(mutated, encoding="utf-8")
        try:
            run = subprocess.run(
                [
                    str(python),
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:testlookup",
                    TEST,
                ],
                cwd=ROOT,
                env=env,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            if run.returncode == 0:
                raise AssertionError(f"mutation survived: {TARGET}: {bad!r}")
        finally:
            TARGET.write_text(original, encoding="utf-8")
    print(f"Coverage release journey mutation check: {len(MUTATIONS)} mutation killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
