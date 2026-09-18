"""Prove M06 release-history tests reject phase-scope contamination."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "backend/app/services/release_gate_decision_service.py"
TEST = "tests/regression/test_release_gate_decision.py"

MUTATIONS = (
    (
        "    stmt = select(ReleaseGateDecision).where(ReleaseGateDecision.release_id == release_id)\n"
        "    if phase_id is None:\n"
        "        stmt = stmt.where(ReleaseGateDecision.phase_id.is_(None))\n"
        "    else:\n"
        "        stmt = stmt.where(ReleaseGateDecision.phase_id == phase_id)\n",
        "    stmt = select(ReleaseGateDecision).where(ReleaseGateDecision.release_id == release_id)\n"
        "    if phase_id is not None:\n"
        "        stmt = stmt.where(ReleaseGateDecision.phase_id == phase_id)\n",
    ),
    (
        "    stmt = select(ReleaseGateDecision).where(ReleaseGateDecision.release_id == release_id)\n"
        "    if phase_id is None:\n"
        "        stmt = stmt.where(ReleaseGateDecision.phase_id.is_(None))\n",
        "    stmt = select(ReleaseGateDecision).where(ReleaseGateDecision.release_id == release_id)\n"
        "    if phase_id is None:\n"
        "        stmt = stmt.where(ReleaseGateDecision.phase_id.is_not(None))\n",
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
                raise AssertionError(f"mutation did not change source: {good!r}")
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
                    "-k",
                    "DecisionHistoryKeepsReleaseAndPhaseScopesSeparate",
                    "--basetemp",
                    f".pytest-tmp-exploratory-m06-mutation-{index}",
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

    print(f"M06 release-history mutation check: {killed} mutations killed")


if __name__ == "__main__":
    main()
