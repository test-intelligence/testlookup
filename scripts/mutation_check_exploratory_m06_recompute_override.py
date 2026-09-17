"""Prove M06 tests reject recompute/override serialization regressions."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "backend/app/agents/release_risk_agent.py"
TEST = "tests/regression/test_release_recompute_override.py"
MUTATIONS = (
    ("                .with_for_update()\n", ""),
    (
        "                # Risk is an observed automated fact, not part of the human\n"
        "                # verdict. Keep it current even while the override stands.\n"
        "                record.risk_score = decision[\"risk_score\"]\n"
        "                if record.human_override is None:\n"
        "                    record.recommendation = decision[\"recommendation\"]\n"
        "                # Once an override exists, recommendation is the explicit human\n"
        "                # verdict and original_* remains its immutable pre-override\n"
        "                # snapshot. The row lock serializes this with apply_override,\n"
        "                # whichever transaction starts first.\n",
        "                record.recommendation = decision[\"recommendation\"]\n"
        "                record.risk_score = decision[\"risk_score\"]\n",
    ),
)


def main() -> None:
    original = TARGET.read_bytes()
    source = original.decode("utf-8")
    killed = 0
    try:
        for index, (good, bad) in enumerate(MUTATIONS, 1):
            count = source.count(good)
            if count != 1:
                raise AssertionError(
                    f"mutation must apply exactly once: {good!r}; found {count}"
                )
            mutated = source.replace(good, bad, 1)
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
                    "--basetemp",
                    f".pytest-tmp-exploratory-m06-recompute-mutation-{index}",
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
                raise AssertionError(f"mutation survived: {bad!r}")
            killed += 1
    finally:
        TARGET.write_bytes(original)

    print(f"M06 recompute-override mutation check: {killed} mutations killed")


if __name__ == "__main__":
    main()
