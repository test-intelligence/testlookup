"""Keep the T22 mutation harness wired into the backend suite."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_agentic_live_dod_mutation_harness():
    root = Path(__file__).resolve().parents[2]
    run = subprocess.run(
        [sys.executable, str(root / "scripts" / "mutation_check_t22.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "T22 mutation check: 7 mutations killed"
