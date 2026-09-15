"""Prove every T21 catalog-input model is required by the completeness guard."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "backend/app/models/agent_input_contracts.py"
MODEL_NAMES = (
    "TestRun",
    "MetricSnapshotV1",
    "AuthoritativeFailureClusterV1",
    "ClusterInvestigationExpansionPlanV1",
    "ClusterScopedEvidenceBundleV1",
    "PreliminarySummary",
    "DecisionEvidenceSnapshotV3",
    "InvestigationRequestV1",
    "InvestigationPlanV1",
)


def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "backend")
    original = SOURCE.read_bytes()
    for name in MODEL_NAMES:
        good = f"class {name}(".encode()
        bad = f"class {name}Mutation(".encode()
        if original.count(good) != 1:
            raise AssertionError(f"mutation did not apply exactly once: {name}")
        mutated = original.replace(good, bad)
        if mutated == original:
            raise AssertionError(f"mutation did not change source: {name}")
        SOURCE.write_bytes(mutated)
        try:
            run = subprocess.run(
                (
                    sys.executable,
                    "scripts/quality_gate.py",
                    "--only",
                    "agents.catalog-schema-complete",
                ),
                cwd=ROOT,
                env=env,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            if run.returncode == 0:
                raise AssertionError(f"mutation survived: {name}")
        finally:
            SOURCE.write_bytes(original)
    print(f"T21 mutation check: {len(MODEL_NAMES)} mutations killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
