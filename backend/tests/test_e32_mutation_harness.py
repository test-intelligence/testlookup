"""Keep the E3.2 workflow-compiler mutation proof wired into CI."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_e32_mutation_harness() -> None:
    root = Path(__file__).resolve().parents[2]
    run = subprocess.run(
        [sys.executable, str(root / "scripts" / "mutation_check_e32.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "E3.2 mutation check: 11 mutations killed"
