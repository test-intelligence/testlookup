"""Keep the M06 override-race mutation proof wired into the backend suite."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_m06_override_race_mutation_harness() -> None:
    root = Path(__file__).resolve().parents[2]
    run = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "mutation_check_exploratory_m06_override_race.py"),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )

    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "M06 override-race mutation check: 1 mutation killed"
