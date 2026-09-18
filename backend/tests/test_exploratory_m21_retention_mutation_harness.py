"""CI wrapper for the M21 retention mutation check."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_m21_retention_mutations_are_applied_and_killed():
    root = Path(__file__).resolve().parents[2]
    run = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "mutation_check_exploratory_m21_retention.py"),
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "M21 retention mutation check: 12 mutations killed"
