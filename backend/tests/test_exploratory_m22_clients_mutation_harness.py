"""CI wrapper for the M22 client-contract mutation check."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_m22_client_mutations_are_applied_and_killed() -> None:
    root = Path(__file__).resolve().parents[2]
    run = subprocess.run(
        [sys.executable, str(root / "scripts" / "mutation_check_exploratory_m22_clients.py")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "M22 client mutation check: 10 mutations killed"
