"""Keep the T3 trigger-attribution mutation proof wired into CI."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_t3_mutation_harness() -> None:
    root = Path(__file__).resolve().parents[2]
    run = subprocess.run(
        [sys.executable, str(root / "scripts" / "mutation_check_t3.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "T3 mutation check: 9 mutations killed"
