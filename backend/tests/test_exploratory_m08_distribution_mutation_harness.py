"""CI wrapper for the M08 distribution mutation check."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_m08_distribution_mutation_harness() -> None:
    root = Path(__file__).resolve().parents[2]
    run = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "mutation_check_exploratory_m08_distribution.py"),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )

    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "M08 distribution mutation check: 2 mutations killed"
