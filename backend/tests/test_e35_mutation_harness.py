"""Keep the E3.5 built-in workflow parity mutation proof wired into CI."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_e35_mutation_harness() -> None:
    root = Path(__file__).resolve().parents[2]
    run = subprocess.run(
        [sys.executable, str(root / "scripts" / "mutation_check_e35.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "E3.5 mutation check: 4 mutations killed"
