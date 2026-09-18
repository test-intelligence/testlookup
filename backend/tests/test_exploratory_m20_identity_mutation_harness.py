"""CI wrapper for the M20 identity mutation check."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_m20_identity_mutation_harness() -> None:
    root = Path(__file__).resolve().parents[2]
    run = subprocess.run(
        [sys.executable, str(root / "scripts" / "mutation_check_exploratory_m20_identity.py")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )

    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "M20 identity mutation check: 3 mutations killed"
