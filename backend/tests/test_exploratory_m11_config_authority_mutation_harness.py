"""CI wrapper for the M11 configuration-authority mutation check."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_m11_configuration_authority_mutation_harness() -> None:
    root = Path(__file__).resolve().parents[2]
    run = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "mutation_check_exploratory_m11_config_authority.py"),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == (
        "M11 configuration-authority mutation check: 25 mutations killed"
    )
