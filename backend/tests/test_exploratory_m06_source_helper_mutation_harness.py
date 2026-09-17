"""Bound the M06 source-helper mutation proof in the regular test suite."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_source_helper_mutation_is_killed() -> None:
    run = subprocess.run(
        [sys.executable, str(ROOT / "scripts/mutation_check_exploratory_m06_source_helper.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "1 mutation killed" in run.stdout
